import pytest

from wzy.collector import collect_inventory, collect_shop, stable_html
from wzy.parsers import parse_inventory
from test_collector import Page
from test_parsers import card, inventory_html, shop_html


class InitialPage(Page):
    def __init__(self, initial, ready, delay=9, volatile=False):
        super().__init__([initial, ready], ['disabled', 'disabled'])
        self.delay = delay
        self.samples = 0
        self.volatile = volatile

    async def evaluate(self, script):
        if 'WZY_EXTRACT' in script:
            self.samples += 1
            self.index = int(self.samples > self.delay)
            html = self.pages[self.index]
            return html.replace('<tr>', f'<tr data-render="{self.samples}">') if self.volatile else html
        return await super().evaluate(script)


@pytest.mark.parametrize('initial', ['', '<div class="arco-table"><table><tbody></tbody></table></div>'])
async def test_initial_inventory_waits_for_usable_rows(initial):
    page = InitialPage(initial, inventory_html(), volatile=True)
    result = await collect_inventory(page)
    assert len(result.products) == 1
    assert result.status == 'unknown'
    assert result.errors == []
    assert result.declared_count is None
    assert result.pagination_terminal == 'next_disabled'


async def test_semantic_stability_ignores_attributes_but_waits_for_prices():
    class ChangingPage(InitialPage):
        async def evaluate(self, script):
            html = await super().evaluate(script)
            if 'WZY_EXTRACT' in script and self.samples < 7:
                return html.replace('10.20', str(self.samples) + '.20')
            return html
    page = ChangingPage('', inventory_html(), delay=0, volatile=True)
    html, stable = await stable_html(page)
    assert stable
    assert parse_inventory(html).products[0].price == '10.20'


async def test_stable_unverified_empty_exhausts_bound_without_success():
    page = InitialPage('', '<div class="arco-table"><table><tbody></tbody></table></div>', delay=0)
    _, stable = await stable_html(page, attempts=12)
    assert not stable
    assert page.samples == 12


async def test_initial_shop_waits_and_preserves_parent_count_mismatch():
    categories = ['Plus', 'PPO官方正规直充', '成品号', 'Claude', '镜像', 'Gemini', 'gr0k']
    html = '<p>数字卡密 21</p>' + ''.join(
        '<section class="group-box"><div class="goods-group-title">' + category + '</div><div class="goods-group-content">' +
        ''.join(card(f'{category}-{i}') for i in range(3 if index < 5 else 2)) + '</div></section>'
        for index, category in enumerate(categories))
    result = await collect_shop(InitialPage('', html, delay=6), 'fixture')
    assert len(result.products) == 19
    assert len({p.category for p in result.products}) == 7
    assert sum(p.category == 'PPO官方正规直充' for p in result.products) == 3
    assert result.declared_count == 21
    assert result.status == 'partial'
    assert result.errors == ['count_mismatch']


async def test_loading_marker_does_not_confirm_zero_inventory():
    html = '<div data-wzy-loading="true"></div><div class="arco-table"><table><tbody></tbody></table><div class="arco-empty">暂无数据</div></div><p>共 0 条</p>'
    result = await collect_inventory(Page([html], ['disabled']))
    assert result.status != 'complete'
    assert 'page_loading' in result.errors
