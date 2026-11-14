"""Model-free commercial measurements and opportunity ordering.

Only allowlisted marketplace metadata is public. Proposal text is encrypted by
worker before persistence. Missing source fields stay unknown, never zero.
"""
from datetime import datetime, timezone, timedelta
import re

APPROVAL_HOLD_IDS = frozenset()  # Historical owner approval holds have been released.
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


def is_fresh(item, state, hours=2, now=None):
    now = now or datetime.now(timezone.utc)
    record = state.get('commercial', {}).get(str(item.get('_id')), {})
    posted = utc_time(record.get('posted_at'))
    if not posted:
        posted = next((utc_time(item[k]) for k in TIME_FIELDS if utc_time(item.get(k))), None)
    return bool(posted and now - posted <= timedelta(hours=hours))


def introductory_price(state, item, proposed_cents):
    """Apply the owner-approved cold-start rate for Gambit's first five completed jobs."""
    posted = int(item.get('budget_cents') or proposed_cents or 0)
    proposed = int(proposed_cents or posted or 0)
    completed = len(state.get('settlements', {}))
    if completed >= 5 or posted < 500:
        return proposed, None
    discounted = max(500, int(round((posted * 0.80) / 100.0)) * 100)
    return min(posted, discounted), {
        'pricing_mode': 'introductory_first_five',
        'discount_percent': 20,
        'posted_budget_cents': posted,
    }


def followup_due(record, now=None):
    """Exactly one commercial follow-up after 24h with no buyer activity."""
    if not isinstance(record, dict) or record.get('followup_sent_at'):
        return False
    now = now or datetime.now(timezone.utc)
    bid_at = utc_time(record.get('bid_at'))
    if not bid_at or now - bid_at < timedelta(hours=24):
        return False
    incoming = utc_time((record.get('conversation') or {}).get('last_incoming_at'))
    outgoing = utc_time((record.get('conversation') or {}).get('last_outgoing_at'))
    if incoming and incoming > bid_at:
        return False
    return not outgoing or outgoing <= bid_at



def priority(item, state, now=None):
    now = now or datetime.now(timezone.utc)
    record = state.get('commercial', {}).get(str(item.get('_id')), {})
    posted = utc_time(record.get('posted_at'))
    if not posted:
        posted = next((utc_time(item[k]) for k in TIME_FIELDS if utc_time(item.get(k))), None)
    activity = next((utc_time(item.get(k) or record.get('source_fields', {}).get(k))
                     for k in ('owner_last_active_at', 'last_activity_at')
                     if utc_time(item.get(k) or record.get('source_fields', {}).get(k))), None)
    if posted and now - posted <= timedelta(hours=2):
        group = -1
    elif posted and now - posted <= timedelta(hours=24):
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
Samples are preliminary excerpts, not claims that the full job is done. Pricing
is applied deterministically by the worker: for Gambit's first five completed
jobs the owner-approved introductory rate is 20% below the posted budget,
rounded to the nearest dollar and never below $5. Do not invent other discounts.'''


def record_event(state, event):
    """Record shapes and outcome timestamps without payload values or text."""
    analytics = state.setdefault('analytics', {})
    seen = analytics.setdefault('event_ids', [])
    ident = event.get('id')
    if ident and ident in seen:
        return
    kind = str(event.get('type') or 'unknown')
    now = datetime.now(timezone.utc).isoformat()
    row = analytics.setdefault('event_types', {}).setdefault(kind,
        {'count': 0, 'first_seen': now, 'last_seen': now, 'data_keys': []})
    row.update(count=row['count'] + 1, last_seen=now,
               data_keys=sorted(set(row['data_keys']) | set((event.get('data') or {}).keys())))
    if ident:
        seen.append(ident)
    data = event.get('data') or {}
    bounty_id = data.get('bounty_id')
    if bounty_id:
        item = state.setdefault('commercial', {}).setdefault(str(bounty_id), {})
        when = utc_time(event.get('created_at'))
        at = when.isoformat() if when else now
        field = {'submission.accepted': 'accepted_at', 'submission.review_opened': 'delivered_at',
                 'bid.hired': 'hired_at', 'bid.declined': 'declined_at',
                 'bid.withdrawn': 'withdrawn_at'}.get(kind)
        if field:
            item.setdefault(field, at)
        if kind == 'submission.accepted' and str(bounty_id) in state.get('settlements', {}):
            state['settlements'][str(bounty_id)].setdefault('accepted_at', at)
            if state['settlements'][str(bounty_id)].get('accepted_at') is None:
                state['settlements'][str(bounty_id)]['accepted_at'] = at
        if kind in {'bid.declined', 'bid.withdrawn', 'bid.hired'}:
            item['bid_status'] = kind.split('.')[1]
            item['bid_status_changed_at'] = at


def record_bid_shape(state, bid):
    analytics = state.setdefault('analytics', {})
    status = bid.get('status')
    analytics['bid_statuses_seen'] = sorted(set(analytics.get('bid_statuses_seen', [])) |
                                           ({str(status)} if status is not None else set()))
    analytics['bid_keys_seen'] = sorted(set(analytics.get('bid_keys_seen', [])) | set(bid.keys()))
    nested = bid.get('bounty') or {}
    ident = bid.get('bounty_id') or bid.get('bountyId') or (nested.get('_id') if isinstance(nested, dict) else None)
    if not ident:
        return None
    row = state.setdefault('commercial', {}).setdefault(str(ident), {})
    now = datetime.now(timezone.utc).isoformat()
    if row.get('bid_status') != status:
        row['bid_status_changed_at'] = now
    row['bid_status'] = status
    for source, target in [('payout_cents', 'bid_amount_cents'), ('created_at', 'bid_created_at'),
                            ('updated_at', 'bid_updated_at')]:
        row[target] = bid.get(source)
    row['bid_at'] = bid.get('created_at') or row.get('bid_at')
    if status in {'hired', 'declined', 'withdrawn', 'closed_no_hire'}:
        row['outcome'] = status
        row.setdefault(status + '_at', row['bid_status_changed_at'])
    return str(ident)


def record_bid_snapshot(state, bids):
    """A complete paginated snapshot only; a missing bid is not a rejection."""
    found = {ident for bid in sorted((b for b in bids if isinstance(b, dict)),
                 key=lambda b: utc_time(b.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
             for ident in [record_bid_shape(state, bid)] if ident}
    analytics = state.setdefault('analytics', {})
    for ident in set(analytics.get('bid_bounty_ids', [])) - found:
        row = state['commercial'][ident]
        if row.get('bid_status') not in {'hired', 'declined', 'withdrawn', 'closed_no_hire'}:
            row.update(bid_status='missing_unconfirmed',
                       bid_status_changed_at=datetime.now(timezone.utc).isoformat())
    analytics['bid_bounty_ids'] = sorted(found)


def record_conversation(state, bounty_id, messages):
    times = sorted((utc_time(m.get('created_at')), m.get('author_type')) for m in messages
                   if utc_time(m.get('created_at')) and m.get('author_type') in {'user', 'agent'})
    incoming = [at for at, role in times if role == 'user']
    outgoing = [at for at, role in times if role == 'agent']
    last_in, last_out = (max(incoming) if incoming else None), (max(outgoing) if outgoing else None)
    row = state.setdefault('commercial', {}).setdefault(bounty_id, {})
    row['conversation'] = {'message_count': len(messages),
        'unknown_timestamp_count': sum(utc_time(m.get('created_at')) is None for m in messages),
        'last_incoming_at': last_in.isoformat() if last_in else None,
        'last_outgoing_at': last_out.isoformat() if last_out else None,
        'unanswered': bool(last_in and (not last_out or last_in > last_out)),
        'response_lag_minutes': [(min(later) - at).total_seconds()/60 for at in incoming
                                 for later in [[out for out in outgoing if out >= at]] if later]}


def record_action(state, action, bounty_id, at, reason=None):
    """Durable unique bounty/action/day counters survive recent_outcomes rotation."""
    if not bounty_id:
        return
    item = state.setdefault('commercial', {}).setdefault(str(bounty_id), {})
    field = {'claimed':'hired_at', 'submitted':'delivered_at', 'bid_sent':'bid_at'}.get(action)
    if field:
        item.setdefault(field, at)
        if item.get(field) is None:
            item[field] = at
    counters = state.setdefault('analytics', {}).setdefault('actions', {})
    row = counters.setdefault(str(bounty_id), {}).setdefault(action, {'first_at': at, 'days': []})
    day = at[:10]
    if day not in row['days']:
        row['days'].append(day)
    if isinstance(reason, str) and re.fullmatch(r"[a-z_]+", reason):
        row['reason'] = reason


def summarize(state, now=None):
    from statistics import median
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    records = {k: v for k, v in state.get('commercial', {}).items() if not v.get('sample_listing')}
    actions = state.get('analytics', {}).get('actions', {})
    counts = {}
    names = ('seen sample_excluded retry_pending asked bids_sent bids_active declined withdrawn '
             'missing_unconfirmed hired delivered accepted settled settled_cents').split()
    for scope in ('today', 'cumulative'):
        values = dict.fromkeys(names, 0)
        values['passed_by_reason'] = {}
        for ident, row in state.get('commercial', {}).items():
            entry = state.get('bounties', {}).get(ident, {})
            def on_date(value):
                parsed = utc_time(value)
                return bool(parsed and (scope == 'cumulative' or parsed.date().isoformat() == today))
            if row.get('sample_listing'):
                values['sample_excluded'] += int(on_date(row.get('first_detected_at')))
                continue
            values['seen'] += int(on_date(row.get('first_detected_at')))
            action = entry.get('action', '')
            values['retry_pending'] += int(action in {'retry_evaluation', 'retry_search', 'proposal_fallback_pending'} and on_date(entry.get('at')))
            values['asked'] += int(on_date(actions.get(ident, {}).get('asked', {}).get('first_at') or
                                          (entry.get('at') if action == 'asked' else None)))
            bid_at = row.get('bid_at') or row.get('bid_created_at')
            values['bids_sent'] += int(on_date(bid_at))
            values['bids_active'] += int(
                row.get('bid_status') == 'active'
                and row.get('platform_status') not in {'canceled', 'closed'}
                and on_date(bid_at))
            for status in ('declined', 'withdrawn', 'missing_unconfirmed'):
                values[status] += int(row.get('bid_status') == status and on_date(row.get('bid_status_changed_at')))
            for status in ('hired', 'delivered', 'accepted'):
                fallback = entry.get('at') if action == {'hired':'claimed','delivered':'submitted','accepted':'accepted'}[status] else None
                values[status] += int(on_date(row.get(status + '_at') or fallback))
            passed = {r.get('reason') or 'unknown' for name, r in actions.get(ident, {}).items()
                      if name.startswith('pass') and
                      (scope == 'cumulative' or today in r.get('days', []))}
            if action.startswith('pass') and on_date(entry.get('at')):
                passed.add(entry.get('reason') or 'unknown')
            for reason in passed:
                values['passed_by_reason'][reason] = values['passed_by_reason'].get(reason, 0) + 1
        for ident, settlement in state.get('settlements', {}).items():
            if state.get('commercial', {}).get(ident, {}).get('sample_listing'):
                continue
            at = utc_time(settlement.get('settled_at'))
            if at and (scope == 'cumulative' or at.date().isoformat() == today):
                values['settled'] += 1
                if isinstance(settlement.get('payout_cents'), int):
                    values['settled_cents'] += settlement['payout_cents']
        counts[scope] = values
    totals = counts['cumulative']
    reply_count = sum(bool(row.get('conversation', {}).get('last_incoming_at')) and bool(row.get('bid_at')) for row in records.values())
    def ratio(n, d):
        return n/d if d else None
    def latency(field, since=None):
        pairs = [(utc_time(r.get('posted_at')), utc_time(r.get(field))) for r in records.values()]
        values = [(end-start).total_seconds()/60 for start, end in pairs
                  if start and end and end >= start and (since is None or end >= since)]
        return median(values) if values else None
    unanswered = [r['conversation']['last_incoming_at'] for r in records.values()
                  if r.get('conversation', {}).get('unanswered')]
    fields = ('posted_at', 'first_detected_at', 'budget_cents', 'bid_at', 'bid_amount_cents',
              'bid_status', 'owner_activity_status', 'accepted_at', 'hired_at', 'delivered_at', 'conversation')
    return {'generated_at': now.isoformat(), **counts,
        'conversion_rates': {'bid_per_seen':ratio(totals['bids_sent'], totals['seen']),
            'hire_per_bid':ratio(totals['hired'], totals['bids_sent']), 'buyer_reply_per_bid':ratio(reply_count, totals['bids_sent'])},
        'median_posting_to_detection_minutes':latency('first_detected_at'),
        'median_posting_to_detection_minutes_24h':latency(
            'first_detected_at', now - timedelta(hours=24)),
        'median_posting_to_bid_minutes':latency('bid_at'),
        'aging_active_bids_days': {k:(now - utc_time(r['bid_at'])).total_seconds()/86400
             if utc_time(r.get('bid_at')) else None for k,r in records.items()
             if r.get('bid_status') == 'active'
             and r.get('platform_status') not in {'canceled', 'closed'}},
        'unanswered_threads': {'count':len(unanswered), 'oldest_last_incoming_at':min(unanswered) if unanswered else None},
        'model_budget':state.get('analytics', {}).get('model_budget', {'call_count':None,'reserved_micro_usd':None}),
        'unknown_fields':{f:sum(f not in r for r in records.values()) for f in fields},
        'null_fields':{f:sum(f in r and r[f] is None for r in records.values()) for f in fields},
        'browser':state.get('analytics', {}).get('browser', {}),
        'browser_manual_snapshot':state.get('analytics', {}).get('browser_manual_snapshot'),
        'market_observation':state.get('market_observation'),
        'market_observation_history':state.get('market_observation_history', [])[-288:],
        'coverage':'Observed unique bounties; historical events before instrumentation may be incomplete.'}
