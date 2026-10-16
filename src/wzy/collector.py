import asyncio
from contextlib import asynccontextmanager
import fcntl
from pathlib import Path

from playwright.async_api import async_playwright

from .domain import OWN_URL, Result, cdp_url, shop_url
from .parsers import parse_inventory, parse_shop

# Extract only product DOM, never the full page, sales, storage or account details.
EXTRACT = r'''() => { // WZY_EXTRACT
    const visible = e => {
        if (!e || !e.getClientRects().length) return false;
        for (let p = e; p; p = p.parentElement) {
            if (p.hidden || p.getAttribute('aria-hidden') === 'true' || getComputedStyle(p).visibility === 'hidden' || getComputedStyle(p).display === 'none') return false;
        }
        return true;
    };
    const safeText = e => {
        if (!visible(e) || e.matches('script,style,input,textarea,select,iframe')) return '';
        return [...e.childNodes].map(n => n.nodeType === 3 ? n.textContent : n.nodeType === 1 ? safeText(n) : '').join('').trim();
    };
    const field = (parent, tag, cls, source) => {
        const node = document.createElement(tag); node.className = cls;
        node.textContent = safeText(source); parent.append(node); return node;
    };
    const out = document.createElement('div');
    if ([...document.querySelectorAll('[aria-busy="true"], .arco-spin-loading')].some(visible)) {
        const loading = document.createElement('div'); loading.dataset.wzyLoading = 'true'; out.append(loading);
    }
    const message = document.createElement('p');
    if (document.querySelector('input[type="password"]') || /\/login(?:[/?#]|$)/.test(location.pathname)) {
        message.textContent = '请登录'; out.append(message); return out.innerHTML;
    }
    const notices = [...document.querySelectorAll('[role="alert"], .arco-message, .captcha, #captcha, h1, h2')].filter(visible);
    if (notices.some(e => /安全验证|人机验证|访问受限|captcha|access denied/i.test(e.textContent))) {
        message.textContent = '安全验证'; out.append(message); return out.innerHTML;
    }
    for (const section of document.querySelectorAll('section.group-box')) {
        if (!visible(section)) continue;
        const group = document.createElement('section'); group.className = 'group-box';
        field(group, 'div', 'goods-group-title', section.querySelector('.goods-group-title'));
        const content = document.createElement('div'); content.className = 'goods-group-content';
        for (const card of section.querySelectorAll('.goods-group-content .goods-group-item')) {
            if (!visible(card)) continue;
            const item = document.createElement('div'); item.className = 'goods-group-item';
            for (const cls of ['goods-item-info-title', 'goods-item-info-desc', 'stock']) field(item, 'div', cls, card.querySelector('.' + cls));
            const price = document.createElement('div'); price.className = 'goods-item-price-main';
            field(price, 'span', 'primary', card.querySelector('.goods-item-price-main .primary'));
            item.append(price); content.append(item);
        }
        group.append(content); out.append(group);
    }
    for (const table of document.querySelectorAll('.arco-table')) {
        if (!visible(table)) continue;
        const wrapper = document.createElement('div'); wrapper.className = 'arco-table';
        const t = document.createElement('table'), body = document.createElement('tbody');
        for (const row of table.querySelectorAll('tbody tr')) {
            if (!visible(row)) continue;
            const tr = document.createElement('tr');
            [...row.children].forEach((cell, i) => {
                const td = document.createElement('td');
                if (i === 1) {
                    const source = cell.querySelector('a.name');
                    const link = field(td, 'a', 'name', source);
                    const href = source?.getAttribute('href') || '';
                    if (/^(https:\/\/wzyp\.cn)?\/item\/[A-Za-z0-9_-]{1,128}$/.test(href)) link.setAttribute('href', href);
                    if (safeText(cell).includes('代销')) { const tag = document.createElement('span'); tag.textContent = '代销'; td.append(tag); }
                }
                if ([2,3].includes(i)) field(td, 'span', 'item-span', cell.querySelector('.item-span'));
                if (i === 5) td.textContent = safeText(cell);
                tr.append(td);
            });
            body.append(tr);
        }
        t.append(body); wrapper.append(t);
        for (const empty of table.querySelectorAll('.arco-empty')) {
            if (visible(empty)) field(wrapper, 'div', 'arco-empty', empty);
        }
        out.append(wrapper);
    }
    // Exact bounded count containers outside product content; nested number spans are valid.
    for (const e of document.querySelectorAll('span, div, p, li')) {
        if (!visible(e) || e.closest('section.group-box, .arco-table, .goods-group-item') || e.querySelector('section.group-box, .arco-table, .goods-group-item')) continue;
        const value = e.textContent.trim();
        if (value.length > 60) continue;
        if (/^(数字卡密\s*[（(]?\s*\d+\s*[）)]?|共\s*\d+\s*条)$/.test(value)) {
            const count = document.createElement('p'); count.textContent = value; out.append(count);
        }
    }
    return out.innerHTML;
}'''

NEXT_HELPER = r'''
    const visible = e => !!e.getClientRects().length;
    const bars = [...document.querySelectorAll('.arco-pagination')].filter(visible);
    if (bars.length === 0) return 'absent';
    if (bars.length !== 1) return 'unknown';
    const controls = [...bars[0].querySelectorAll('.arco-pagination-item-next, [aria-label="Next"], [aria-label="下一页"], [title="下一页"]')].filter(visible);
    if (controls.length !== 1) return 'unknown';
    const next = controls[0];
    if (next.matches('[disabled], [aria-disabled="true"], .arco-pagination-item-disabled')) return 'disabled';
'''
NEXT_STATE = '() => { // WZY_NEXT_STATE\n' + NEXT_HELPER + "return 'enabled'; }"
NEXT_CLICK = '() => { // WZY_NEXT_CLICK\n' + NEXT_HELPER + "next.click(); return true; }"


def semantic_signature(result):
    return (tuple(sorted(tuple(vars(p).items()) for p in result.products)),
            result.declared_count, tuple(sorted(result.errors)))


def ready(result):
    return (result.status not in {'failed', 'paused'} and 'page_loading' not in result.errors
            and (bool(result.products) or (result.declared_count == 0 and not result.errors)))


async def stable_html(page, *, attempts=30):
    previous = None
    repeats = 0
    html = ''
    for _ in range(attempts):
        html = await page.evaluate(EXTRACT)
        result = parse_inventory(html)
        if result.status == 'paused':
            return html, False
        signature = (semantic_signature(result), await page.evaluate(NEXT_STATE))
        repeats = repeats + 1 if ready(result) and signature == previous else 0
        if ready(result) and repeats >= 3:
            return html, True
        previous = signature if ready(result) else None
        await page.wait_for_timeout(350)
    return html, False


async def collect_shop(page, shop_id, *, max_scrolls=15):
    previous = None
    repeats = 0
    html = ''
    for _ in range(max_scrolls):
        html = await page.evaluate(EXTRACT)
        result = parse_shop(html, shop_id, stable=True)
        signature = semantic_signature(result)
        repeats = repeats + 1 if ready(result) and signature == previous else 0
        if result.status == 'paused' or (ready(result) and repeats >= 3):
            return result
        previous = signature if ready(result) else None
        await page.evaluate('() => window.scrollTo(0, document.documentElement.scrollHeight)')
        await page.wait_for_timeout(500)
    return parse_shop(html, shop_id, stable=False)


async def collect_inventory(page, *, max_pages=50):
    result = Result()
    seen_pages, seen_keys = set(), set()
    for index in range(max_pages):
        html, stable = await stable_html(page)
        parsed = parse_inventory(html)
        if parsed.status in {'failed', 'paused'}:
            if not result.products:
                return parsed
            result.errors.extend(parsed.errors)
            result.status = 'partial'
            return result
        signature = tuple(p.key for p in parsed.products)
        if signature in seen_pages:
            result.errors.append('repeated_page')
            break
        seen_pages.add(signature)
        if not stable:
            result.errors.append('page_unstable')
        if parsed.declared_count is not None:
            if result.declared_count is not None and result.declared_count != parsed.declared_count:
                result.errors.append('total_changed')
            result.declared_count = parsed.declared_count
        result.errors.extend(parsed.errors)
        for product in parsed.products:
            if product.key in seen_keys:
                result.errors.append('duplicate_across_pages')
            else:
                result.products.append(product)
                seen_keys.add(product.key)
        state = await page.evaluate(NEXT_STATE)
        result.pagination_terminal = {'disabled': 'next_disabled', 'absent': 'controls_absent'}.get(state, 'not_reached')
        if state in {'disabled', 'absent'}:
            if result.declared_count is not None and result.declared_count != len(result.products):
                result.errors.append('count_mismatch')
            result.status = 'partial' if result.errors else ('complete' if result.declared_count is not None else 'unknown')
            return result
        if state != 'enabled':
            result.errors.append('pagination_unknown')
            break
        if index + 1 == max_pages:
            result.errors.append('page_bound')
            break
        if await page.evaluate(NEXT_CLICK) is not True:
            result.errors.append('pagination_changed')
            break
        # Wait for a different row identity before accepting stability of the new page.
        for _ in range(20):
            await page.wait_for_timeout(350)
            changed = parse_inventory(await page.evaluate(EXTRACT))
            if changed.status == 'paused':
                break
            if ready(changed) and tuple(p.key for p in changed.products) != signature:
                break
        else:
            result.errors.append('repeated_page')
            break
    result.status = 'partial'
    return result


@asynccontextmanager
async def account_lock(path):
    """Linux flock coordinates all WZY processes using this account lock path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('account_busy') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class BrowserCollector:
    def __init__(self, endpoint, lock_path=None):
        self.endpoint = cdp_url(endpoint)
        self.lock_path = lock_path or Path.home() / '.cache/wzy/account.lock'
        self.lock = asyncio.Lock()

    async def __call__(self, source):
        async with self.lock:
            try:
                async with account_lock(self.lock_path):
                    async with async_playwright() as playwright:
                        return await asyncio.wait_for(self.collect_with(playwright, source), timeout=180)
            except Exception:
                return Result(status='failed', errors=['collection_unavailable'])

    async def collect_with(self, playwright, source):
        page = None
        try:
            if source['kind'] == 'own':
                if source['url'] != OWN_URL:
                    raise ValueError('invalid_target')
            else:
                shop_url(source['url'])
            browser = await playwright.chromium.connect_over_cdp(self.endpoint, timeout=10000)
            if not browser.contexts:
                return Result(status='failed', errors=['no_existing_context'])
            page = await browser.contexts[0].new_page()

            async def restrict_navigation(route):
                request = route.request
                if request.is_navigation_request() and request.frame == page.main_frame and request.url != source['url']:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route('**/*', restrict_navigation)
            await page.goto(source['url'], wait_until='domcontentloaded', timeout=30000)
            if source['kind'] == 'own':
                return await collect_inventory(page)
            return await collect_shop(page, shop_url(source['url']))
        except Exception:
            return Result(status='failed', errors=['browser_read_failed'])
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            # The Playwright driver disconnects at the outer context exit. Never close browser/context.
