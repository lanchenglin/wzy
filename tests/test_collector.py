import pytest
from wzy.collector import collect_shop, collect_inventory, BrowserCollector, account_lock
from test_parsers import shop_html, inventory_html


class Page:
    def __init__(self, pages, next_states=None):
        self.pages = pages
        self.index = 0
        self.next_states = next_states or ['absent'] * len(pages)
        self.closed = False
        self.clicks = 0

    async def evaluate(self, script):
        if 'WZY_EXTRACT' in script:
            return self.pages[self.index]
        if 'WZY_NEXT_STATE' in script:
            return self.next_states[self.index]
        if 'WZY_NEXT_CLICK' in script:
            self.clicks += 1
            self.index = min(self.index + 1, len(self.pages) - 1)
            return True
        return None

    async def wait_for_timeout(self, ms):
        pass

    async def close(self):
        self.closed = True


async def test_shop_stability_and_bound():
    result = await collect_shop(Page([shop_html()]), 'abc', max_scrolls=5)
    assert result.status == 'complete'
    assert (await collect_shop(Page([shop_html()]), 'abc', max_scrolls=1)).status == 'partial'


async def test_inventory_pages_totals_duplicate_and_bound():
    pages = [inventory_html('a') + '<div>共 2 条</div>', inventory_html('b') + '<div>共 2 条</div>']
    page = Page(pages, ['enabled', 'disabled'])
    result = await collect_inventory(page)
    assert result.status == 'complete'
    assert len(result.products) == 2
    assert page.clicks == 1
    repeated = await collect_inventory(Page([pages[0], pages[0]], ['enabled', 'enabled']))
    assert repeated.status == 'partial'
    assert 'repeated_page' in repeated.errors
    bounded = await collect_inventory(Page(pages, ['enabled', 'disabled']), max_pages=1)
    assert bounded.status == 'partial'
    assert 'page_bound' in bounded.errors


async def test_inventory_unknown_controls_and_empty_not_complete():
    assert (await collect_inventory(Page([inventory_html()], ['unknown']))).status == 'partial'
    assert (await collect_inventory(Page([inventory_html()]))).status == 'unknown'
    assert (await collect_inventory(Page(['<div>请登录</div>']))).status == 'paused'


async def test_waits_for_page_change_before_stability():
    first = inventory_html('a') + '<div>共 2 条</div>'
    second = inventory_html('b') + '<div>共 2 条</div>'
    class SlowPage(Page):
        def __init__(self):
            super().__init__([first, second], ['enabled', 'disabled'])
            self.remaining = 0
        async def evaluate(self, script):
            if 'WZY_NEXT_CLICK' in script:
                self.remaining = 6
                return True
            if 'WZY_EXTRACT' in script and self.remaining:
                self.remaining -= 1
                if self.remaining == 0:
                    self.index = 1
            return await super().evaluate(script)
    result = await collect_inventory(SlowPage())
    assert result.status == 'complete'
    assert len(result.products) == 2


async def test_waits_through_transient_empty_page():
    first = inventory_html('a') + '<div>共 2 条</div>'
    second = inventory_html('b') + '<div>共 2 条</div>'
    loading = '<div class="arco-table"><table><tbody></tbody></table></div><div>共 2 条</div>'
    class LoadingPage(Page):
        def __init__(self):
            super().__init__([first, loading, second], ['enabled', 'absent', 'disabled'])
            self.remaining = 0
        async def evaluate(self, script):
            if 'WZY_NEXT_CLICK' in script:
                self.index = 1
                self.remaining = 6
                return True
            if 'WZY_EXTRACT' in script and self.remaining:
                self.remaining -= 1
                if not self.remaining:
                    self.index = 2
            return await super().evaluate(script)
    assert (await collect_inventory(LoadingPage())).status == 'complete'


async def test_account_lock_excludes_and_releases(tmp_path):
    path = tmp_path / 'account.lock'
    async with account_lock(path):
        with pytest.raises(RuntimeError, match='account_busy'):
            async with account_lock(path):
                pytest.fail('second owner entered')
    async with account_lock(path):
        pass


async def test_empty_with_zero_total_and_indicator():
    html = '<div class="arco-table"><table><tbody></tbody></table><div class="arco-empty">暂无数据</div></div><div>共 0 条</div>'
    assert (await collect_inventory(Page([html]))).status == 'complete'


async def test_task_page_closed_without_browser_close_on_error():
    page = Page([])
    async def fail(url, **kwargs):
        raise RuntimeError('secret URL https://example.com/?token=secret')
    page.goto = fail
    async def route(*args):
        pass
    page.route = route
    class Context:
        async def new_page(self):
            return page
    class Browser:
        contexts = [Context()]
        async def close(self):
            pytest.fail('must not close browser')
    class Chromium:
        async def connect_over_cdp(self, url, **kwargs):
            return Browser()
    class PW:
        chromium = Chromium()
    collector = BrowserCollector('http://127.0.0.1:9224')
    result = await collector.collect_with(PW(), {'kind': 'competitor', 'url': 'https://wzyp.cn/shop/abc'})
    assert page.closed
    assert result.status == 'failed'
    assert result.errors == ['browser_read_failed']
