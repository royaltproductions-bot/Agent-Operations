"""Model-free commercial measurements and opportunity ordering.

Only allowlisted marketplace metadata is public. Proposal text is encrypted by
worker before persistence. Missing source fields stay unknown, never zero.
"""
from datetime import datetime, timezone, timedelta
import re

APPROVAL_HOLD_IDS = frozenset({
    'mx7802nm08nqrb6rhb2kdaygr18gszcr', 'mx78rdqaddbvhpg4hyfkd8v3e98gpcc8',
    'mx78wzkqdghteet6beyw1567mx8hewfj', 'mx7dam93m4q8spz893n4zfsyzn8hgg6r'})
SAMPLE_IDS = frozenset({'mx7a4tvzg7v95yjn9mxrjd0nb98gqtdd',
                        'mx7aw00zmf8ksqwqw2w284hx3d8gptjp'})
TIME_FIELDS = ('created_at', 'posted_at', '_creationTime', 'createdAt')
META_FIELDS = TIME_FIELDS + ('budget_cents', 'currency', 'status', 'bid_count',
    'bids_count', 'awarded_at', 'winner_id', 'winner_rating', 'agent_id',
    'last_activity_at', 'owner_last_active_at', 'updated_at')


def utc_time(value):
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value,
                                          timezone.utc)
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def is_sample(item, record=None):
    return (str(item.get('_id')) in SAMPLE_IDS or
            bool((record or {}).get('sample_listing')) or
            bool(re.search(r'\bsample\s+bounty\b', ' '.join(str(item.get(k) or '')
                for k in ('title', 'description')), re.I)))


def observe(state, item, now=None):
    bid = str(item.get('_id') or '')
    if not bid:
        return False
    now = now or datetime.now(timezone.utc)
    archive = state.setdefault('bounty_archive', {}).get(bid) or {}
    records = state.setdefault('commercial', {})
    old = records.get(bid, {})
    record = dict(old)
    record.setdefault('first_detected_at', archive.get('first_detected_at') or now.isoformat())
    raw = dict(record.get('source_fields') or {})
    for field in META_FIELDS:
        if field in item and (item[field] is None or isinstance(item[field], (str, int, float, bool))):
            raw[field] = item[field]
    record['source_fields'] = raw
    record['sample_listing'] = is_sample(item, old)
    record.setdefault('budget_cents', None)
    if 'budget_cents' in item:
        record['budget_cents'] = item['budget_cents']
    for field in TIME_FIELDS:
        if field in raw:
            parsed = utc_time(raw[field])
            if parsed:
                record.update(posted_at=parsed.isoformat(), posted_time_field=field)
                break
    record.setdefault('posted_at', None)
    record.setdefault('posted_time_field', None)
    record.setdefault('bid_id', None)
    record.setdefault('bid_at', None)
    record.setdefault('bid_amount_cents', None)
    record.setdefault('owner_activity_status', None)
    record['platform_status'] = item.get('status', record.get('platform_status'))
    changed = record != old
    if changed:
        record['observed_at'] = now.isoformat()
        records[bid] = record
    return changed


def priority(item, state, now=None):
    now = now or datetime.now(timezone.utc)
    record = state.get('commercial', {}).get(str(item.get('_id')), {})
    posted = utc_time(record.get('posted_at'))
    if not posted:
        posted = next((utc_time(item[k]) for k in TIME_FIELDS if utc_time(item.get(k))), None)
    activity = next((utc_time(item.get(k) or record.get('source_fields', {}).get(k))
                     for k in ('owner_last_active_at', 'last_activity_at')
                     if utc_time(item.get(k) or record.get('source_fields', {}).get(k))), None)
    if posted and now - posted <= timedelta(hours=24):
        group = 0
    elif posted and now - posted > timedelta(days=5) and not (
            activity and now - activity <= timedelta(days=5)):
        group = 3
    else:
        group = 1 if posted else 2
    return group, -(posted.timestamp() if posted else 0)


def volume(state, now=None):
    now = now or datetime.now(timezone.utc)
    days = {(now - timedelta(days=i)).date().isoformat(): 0 for i in range(7)}
    unknown = 0
    for row in state.get('commercial', {}).values():
        if row.get('sample_listing') or row.get('platform_status') != 'open':
            continue
        posted = utc_time(row.get('posted_at'))
        if posted is None:
            unknown += 1
        elif posted.date().isoformat() in days:
            days[posted.date().isoformat()] += 1
    return {'open_non_sample_by_posted_utc_day': days,
            'open_non_sample_unknown_posted_time': unknown,
            'scope': 'currently open listings observed; not all marketplace postings'}


def verified_samples(rows, evidence):
    """Only literal source-backed sample values survive; never cite snippets."""
    pages = {p.get('url'): p.get('page_text') for p in evidence.get('results', [])
             if p.get('evidence_level') == 'page_read' and p.get('page_text')}
    result, seen = [], set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        url, name, fact = (str(row.get(k) or '').strip() for k in ('source_url', 'name', 'fact'))
        body = pages.get(url, '')
        if not name or len(fact) < 15 or len(fact) > 180 or name.casefold() in seen:
            continue
        if name.casefold() not in body.casefold() or fact not in body:
            continue
        result.append(f'{name} | {fact} | {url}')
        seen.add(name.casefold())
        if len(result) == 3:
            break
    return result


PROPOSAL_RULES = '''Lead with the exact deliverable and its format, then concrete
samples and turnaround. Keep at most one short line of material limits; omit
unrequested disclaimers. Small research jobs should normally be same-day or
within 24 hours AFTER hire when feasible given workload and delivery_window_ms;
never promise a deadline the runtime cannot meet. Match the buyer's requested
format. Never call HTML/CSS ZIP Figma-equivalent. If native Figma is required,
say plainly that it is unavailable; do not substitute silently. If alternatives
are explicitly allowed, label the offered source format accurately. For research
include 2-3 real sample_rows using exact short facts from actually read pages.
For non-web writing or supplied-data work, provide a brief sample_excerpt
grounded in the supplied facts and label it preliminary. Keep note under 800
characters to leave room for samples in the 2000-character platform limit.
Samples are preliminary excerpts, not claims that the full job is done. Price
experiments are on hold: do not introduce an under-budget experiment.'''
