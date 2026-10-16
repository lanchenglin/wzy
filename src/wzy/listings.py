"""Guarded own-inventory DOM actions; never call a guessed merchant API."""
import asyncio

from playwright.async_api import async_playwright

from .collector import NEXT_CLICK, NEXT_STATE, account_lock
from .domain import OWN_URL, cdp_url
from .store import now


class ListingError(Exception):
    def __init__(self, code, before_state=None):
        super().__init__(code)
        self.before_state = before_state


DOM_HELPER = r'''
    const visible = e => {
        if (!e || !e.getClientRects().length) return false;
        for (let p=e;p;p=p.parentElement) {
            if (p.hidden || p.getAttribute('aria-hidden') === 'true' || getComputedStyle(p).display === 'none' || getComputedStyle(p).visibility === 'hidden') return false;
        }
        return true;
    };
    const tables = [...document.querySelectorAll('.arco-table')].filter(visible);
    const blocked = location.href !== 'https://wzyp.cn/merchant/goods/list' ||
        tables.length !== 1 || !!document.querySelector('input[type="password"]') ||
        [...document.querySelectorAll('[aria-busy="true"], .arco-spin-loading, [role="dialog"], .arco-modal, .captcha, #captcha')].some(visible) ||
        [...document.querySelectorAll('[role="alert"], .arco-message, h1, h2')].filter(visible).some(e => /安全验证|人机验证|访问受限|captcha|access denied/i.test(e.textContent));
    const rows = [];
    if (!blocked) for (const tr of tables[0].querySelectorAll('tbody tr')) {
        if (!visible(tr)) continue;
        const links = [...tr.querySelectorAll('a.name')].filter(visible);
        const link = links.length === 1 ? links[0] : null;
        const match = link?.getAttribute('href')?.match(/^(?:https:\/\/wzyp\.cn)?\/item\/([A-Za-z0-9_-]{1,128})$/);
        if (!match) continue;
        const cell = tr.children[3];
        const statuses = cell ? [...cell.querySelectorAll('.item-span')].filter(visible) : [];
        const text = statuses.length === 1 ? statuses[0].textContent.trim() : '';
        const state = text === '销售中' ? 'up' : text === '已下架' ? 'down' : 'unknown';
        const label = state === 'up' ? '下架' : '上架';
        const actions = cell ? [...cell.querySelectorAll('a')].filter(e => visible(e) && e.textContent.trim() === label && !e.matches('[disabled], [aria-disabled="true"], .disabled')) : [];
        rows.push({id:match[1], state, action:actions.length === 1, node:actions[0]});
    }
'''
PROBE = '() => { // WZY_LISTING_PROBE\n' + DOM_HELPER + '''
    return {blocked, rows:rows.map(({id,state,action}) => ({id,state,action}))};
}'''
CLICK = '(args) => { // WZY_LISTING_CLICK\n' + DOM_HELPER + '''
    const matches = rows.filter(r => r.id === args.item_id);
    if (blocked || matches.length !== 1) return false;
    const row = matches[0];
    if (row.state !== args.expected || row.state === args.desired || !row.action) return false;
    row.node.click();
    return true;
}'''


async def stable_page(page):
    previous, repeats = None, 0
    for _ in range(30):
        data = await page.evaluate(PROBE)
        next_state = await page.evaluate(NEXT_STATE)
        signature = (data, next_state)
        repeats = repeats + 1 if not data['blocked'] and signature == previous else 0
        if repeats >= 3:
            return data['rows'], next_state
        previous = signature
        await page.wait_for_timeout(350)
    raise ListingError('page_unstable')


async def scan_inventory(page, item_id, *, max_pages=50):
    """Inspect the whole bounded list before acting, including duplicate targets."""
    found, seen = [], set()
    for index in range(max_pages):
        rows, next_state = await stable_page(page)
        signature = tuple(r['id'] for r in rows)
        if signature in seen:
            raise ListingError('repeated_page')
        seen.add(signature)
        found.extend(dict(r, page=index) for r in rows if r['id'] == item_id)
        if len(found) > 1:
            raise ListingError('duplicate_target')
        if next_state in {'disabled', 'absent'}:
            if not found:
                raise ListingError('not_found')
            if found[0]['state'] not in {'up', 'down'}:
                raise ListingError('unknown_state')
            return found[0]
        if next_state != 'enabled':
            raise ListingError('pagination_unknown')
        if index + 1 == max_pages:
            break
        if await page.evaluate(NEXT_CLICK) is not True:
            raise ListingError('pagination_changed')
        for _ in range(30):
            await page.wait_for_timeout(350)
            data = await page.evaluate(PROBE)
            if not data['blocked'] and data['rows'] and tuple(r['id'] for r in data['rows']) != signature:
                break
        else:
            raise ListingError('repeated_page')
    raise ListingError('page_bound')


async def open_inventory(page):
    await page.goto(OWN_URL, wait_until='domcontentloaded', timeout=30000)


async def change_listing(page, item_id, desired, expected, dispatch):
    target = await scan_inventory(page, item_id)
    before = target['state']
    if before != expected:
        raise ListingError('stale_state', before)
    if before == desired:
        return dict(status='noop', before_state=before, after_state=before, error=None)
    if not target['action']:
        raise ListingError('action_missing')
    # Re-open the list and locate the target again; never use a title or a cached node.
    await open_inventory(page)
    for _ in range(target['page']):
        rows, next_state = await stable_page(page)
        if next_state != 'enabled' or await page.evaluate(NEXT_CLICK) is not True:
            raise ListingError('pagination_changed')
        signature = tuple(r['id'] for r in rows)
        for _ in range(30):
            await page.wait_for_timeout(350)
            data = await page.evaluate(PROBE)
            if not data['blocked'] and data['rows'] and tuple(r['id'] for r in data['rows']) != signature:
                break
        else:
            raise ListingError('repeated_page')
    rows, _ = await stable_page(page)
    matches = [r for r in rows if r['id'] == item_id]
    if len(matches) != 1 or matches[0]['state'] != expected or not matches[0]['action']:
        raise ListingError('target_changed')
    # Commit dispatch intent before the only possible external state-changing click.
    dispatch(before)
    try:
        clicked = await page.evaluate(CLICK, dict(item_id=item_id, desired=desired, expected=expected))
        if clicked is False:
            return dict(status='rejected', before_state=before, after_state=None, error='target_changed')
    except Exception:
        pass  # A transport error cannot establish whether the click was delivered.
    # Give the site's async handler time to settle before navigating away. This is
    # observation only, not a second click; independent navigation still verifies it.
    for _ in range(20):
        try:
            await page.wait_for_timeout(350)
            data = await page.evaluate(PROBE)
            matches = [r for r in data['rows'] if r['id'] == item_id]
            if not data['blocked'] and len(matches) == 1 and matches[0]['state'] == desired:
                break
        except Exception:
            break
    after = None
    for _ in range(2):
        try:
            await open_inventory(page)
            after = (await scan_inventory(page, item_id))['state']
            if after == desired:
                return dict(status='succeeded', before_state=before, after_state=after, error=None)
        except Exception:
            pass
        await page.wait_for_timeout(350)
    return dict(status='uncertain', before_state=before, after_state=after, error='readback_unconfirmed')


class ListingBrowser:
    def __init__(self, endpoint, lock_path):
        self.endpoint = cdp_url(endpoint)
        self.lock_path = lock_path

    async def change(self, item_id, desired, expected, dispatch):
        return await self.run(lambda page: change_listing(page, item_id, desired, expected, dispatch))

    async def read(self, item_id):
        async def read_page(page):
            row = await scan_inventory(page, item_id)
            return dict(item_id=item_id, state=row['state'], observed_at=now(), source='own_inventory', list_complete=False)
        return await self.run(read_page)

    async def run(self, operation):
        async with account_lock(self.lock_path):
            async with async_playwright() as playwright:
                return await asyncio.wait_for(self.with_page(playwright, operation), timeout=180)

    async def with_page(self, playwright, operation):
        page = None
        try:
            browser = await playwright.chromium.connect_over_cdp(self.endpoint, timeout=10000)
            if not browser.contexts:
                raise ListingError('no_existing_context')
            page = await browser.contexts[0].new_page()

            async def restrict_navigation(route):
                request = route.request
                if request.is_navigation_request() and request.frame == page.main_frame and request.url != OWN_URL:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route('**/*', restrict_navigation)
            await open_inventory(page)
            return await operation(page)
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            # Disconnect the driver only; borrowed browser/context are never closed.
