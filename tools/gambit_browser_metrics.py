"""Once-per-workflow, read-only dashboard measurements; never infer missing values.

GAMBIT_BROWSER_STORAGE_STATE is a GitHub secret containing an operator-provisioned
Playwright storage-state JSON. Never log, checkpoint, or export that credential.
Only the verified empty queue/earnings layouts and balance label are supported;
new/nonempty layouts remain unknown until their parser is verified.
"""
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
import json
import os
from urllib.parse import urlsplit

AGENT = 'jh77qs0tg2j4fmccbybdx4rqfs8h508g'
WORK = 'https://trybounty.ai/agents/work?agent=' + AGENT
PAYOUTS = 'https://trybounty.ai/agents?view=payouts'
FIELDS = ('unread_count', 'not_hired_count', 'earnings_count',
          'operator_balance_cents', 'transfer_count', 'last_disbursed_at',
          'public_rating', 'completed_job_count')


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.empty = []; self.parts = []; self.labels = []
        self.stack = []; self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.stack.append((tag, attrs))
        if attrs.get('aria-label'):
            self.labels.append(attrs['aria-label'])

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]; break

    def handle_data(self, value):
        value = ' '.join(value.split())
        if not value or any(t in {'script','style'} for t,a in self.stack):
            return
        self.parts.append(value)
        if any(a.get('data-slot') == 'empty-title' for t,a in self.stack):
            self.empty.append(value)


def measurement(value=None, error=None):
    return {'value': value, 'source': 'browser',
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'scrape_error': error}


def parse_page(html, kind):
    """Process sanitized HTML fixtures; do not persist the HTML or visible prose."""
    keys = {'unread': ('unread_count',), 'not_hired': ('not_hired_count',),
            'payouts': ('earnings_count','operator_balance_cents','transfer_count','last_disbursed_at')}
    result = {k: measurement(error='layout_unverified') for k in keys[kind]}
    page = Page(html)
    if kind in {'unread','not_hired'}:
        identity = any(x == 'Working as Ari Operations — Gambit. Switch agent' for x in page.labels)
        expected = 'No unread updates' if kind == 'unread' else 'No lost bids'
        if identity and expected in page.empty:
            result[keys[kind][0]] = measurement(0)
    elif 'Payouts' in page.parts and 'Earnings' in page.parts:
        if 'No earnings yet' in page.empty:
            result['earnings_count'] = measurement(0)
        # Balance is operator-wide, never represented as settled revenue.
        if 'Balance' in page.parts and 'Total balance' in page.parts:
            i = page.parts.index('Total balance')
            if i+1 < len(page.parts):
                import re
                value = page.parts[i+1]
                if re.fullmatch(r'\$[\d,]+\.\d{2}', value):
                    result['operator_balance_cents'] = measurement(int(Decimal(value[1:].replace(',',''))*100))
        for k in ('transfer_count','last_disbursed_at'):
            result[k] = measurement(error='not_displayed')
    return result


def validated_session(raw):
    state = json.loads(raw)
    def owned(host):
        return host.lstrip('.') == 'trybounty.ai' or host.lstrip('.').endswith('.trybounty.ai')
    if not isinstance(state, dict) or not state.get('cookies'):
        raise ValueError('invalid_session')
    for c in state['cookies']:
        if not owned(str(c.get('domain',''))):
            raise ValueError('invalid_session')
    for origin in state.get('origins', []):
        if not owned(urlsplit(origin.get('origin','')).hostname or ''):
            raise ValueError('invalid_session')
    return state


def collect():
    result = {k:measurement(error='authentication_not_configured') for k in FIELDS}
    raw = os.getenv('GAMBIT_BROWSER_STORAGE_STATE')
    if not raw:
        return result
    try:
        session = validated_session(raw)
    except (ValueError, TypeError, KeyError):
        return {k:measurement(error='invalid_session_configuration') for k in FIELDS}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(storage_state=session)
            page = context.new_page()
            page.set_default_timeout(15000)
            for kind, url in [('unread',WORK+'&queue=unread'),('not_hired',WORK+'&queue=not_hired'),('payouts',PAYOUTS)]:
                keys = ('unread_count',) if kind=='unread' else ('not_hired_count',) if kind=='not_hired' else ('earnings_count','operator_balance_cents','transfer_count','last_disbursed_at')
                try:
                    page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    if urlsplit(page.url).hostname != 'trybounty.ai':
                        raise PermissionError()
                    if kind=='payouts':
                        page.get_by_role('heading', name='Earnings', exact=True).wait_for()
                        refresh = page.get_by_role('button', name='Refresh balance', exact=True)
                        if refresh.is_enabled():
                            refresh.click()
                        page.get_by_text('Total balance', exact=True).wait_for()
                    else:
                        page.get_by_role('button', name='Working as Ari Operations — Gambit. Switch agent', exact=True).wait_for()
                        # Missing/nonempty layouts time out to unknown, never zero.
                        page.locator('[data-slot="empty-title"]').wait_for()
                    result.update(parse_page(page.content(), kind))
                except PermissionError:
                    for key in keys: result[key] = measurement(error='authentication_required')
                    break
                except Exception:
                    for key in keys: result[key] = measurement(error='page_unavailable_or_layout_changed')
            for key in ('public_rating','completed_job_count'):
                result[key] = measurement(error='not_displayed_on_scoped_pages')
            context.close(); browser.close()
    except Exception:
        return {k:measurement(error='browser_unavailable') for k in FIELDS}
    return result


def main():
    from tools import gambit_worker as worker
    state = worker.load_state()
    analytics = state.setdefault('analytics', {})
    # The existing workflow invokes this once, outside the resident poll loop.
    analytics['browser'] = collect()
    worker.save_state(state)
    print(json.dumps({'browser_fields':len(analytics['browser']),
                      'available':sum(v['value'] is not None for v in analytics['browser'].values())}))


if __name__ == '__main__':
    main()
