from decimal import Decimal

import pytest

from wzy.domain import money, shop_url, cdp_url
from wzy.parsers import parse_shop, parse_inventory


def card(title='商品', price='19.90'):
    return f'''<div class="goods-group-item"><div class="goods-item-info-title">{title}</div>
    <div class="goods-item-info-desc">说明</div><del>999</del>
    <div class="goods-item-price-main"><span class="primary">¥{price}</span></div>
    <div class="stock">库存 3</div></div>'''


def shop_html(count=2):
    return f'''<div>数字卡密 {count}</div><section class="group-box">
    <h2 class="goods-group-title">Plus官方正规直充</h2><div class="goods-group-content">{card()}</div></section>
    <section class="group-box"><h2 class="goods-group-title">PPO官方正规直充</h2>
    <div class="goods-group-content">{card('PPO')}</div></section>'''


def row(key='abc', status='已下架'):
    return f'''<tr><td></td><td><a class="name" href="https://wzyp.cn/item/{key}">商品 {key}</a><span>代销</span></td>
    <td><span class="item-span">¥10.20</span></td><td><span class="item-span">{status}</span><a>上架</a></td>
    <td>不能保存的销量</td><td><span class="arco-tag">库存 5</span></td><td>日期</td><td><a>删除</a></td></tr>'''


def inventory_html(key='abc'):
    return f'<div class="arco-table"><table><tbody>{row(key)}</tbody></table></div>'


def test_all_categories_actual_price_and_completeness():
    result = parse_shop(shop_html(), '85G05YIY', stable=True)
    assert result.status == 'complete'
    assert result.declared_count == 2
    assert [p.category for p in result.products] == ['Plus官方正规直充', 'PPO官方正规直充']
    assert result.products[0].price == '19.90'
    assert result.variant_coverage == 'unknown'
    assert parse_shop(shop_html(21), '85G05YIY', stable=True).status == 'partial'
    assert parse_shop(shop_html(), '85G05YIY', stable=False).status == 'partial'


def test_empty_challenge_unknown_and_collisions():
    assert parse_shop('<html></html>', 'abc', stable=True).status == 'failed'
    assert parse_shop('<div>安全验证</div>', 'abc', stable=True).status == 'paused'
    result = parse_shop('<section class="group-box"><div class="goods-group-title">A</div><div class="goods-group-content">' + card() * 2 + '</div></section>', 'abc', stable=True)
    assert len(result.products) == 2
    assert result.status == 'partial'
    assert 'identity_collision' in result.errors
    assert len({p.key for p in result.products}) == 2
    assert all(not p.matchable for p in result.products)
    assert parse_shop(shop_html().replace('数字卡密 2', ''), 'abc', stable=True).status == 'unknown'


def test_inventory_ignores_hidden_recycle_and_sales():
    html = inventory_html() + '<div style="display: none">' + inventory_html('recycle') + '</div>'
    result = parse_inventory(html)
    assert len(result.products) == 1
    p = result.products[0]
    assert p.key == 'own:abc'
    assert p.status == '已下架'
    assert p.stock == '库存 5'
    assert p.consignment
    assert '销量' not in str(p)
    assert parse_inventory('<div>请登录</div>').status == 'paused'
    assert parse_inventory('<html></html>').status == 'failed'


@pytest.mark.parametrize('value', ['https://evil.com/shop/abc', 'https://wzyp.cn/shop/a?x=1', 'https://wzyp.cn/shop/a/b', 'http://wzyp.cn/shop/a', 'https://u@wzyp.cn/shop/a'])
def test_shop_url_rejects(value):
    with pytest.raises(ValueError):
        shop_url(value)


@pytest.mark.parametrize('value', ['http://localhost.evil:9224', 'http://192.168.1.1:9224', 'http://127.0.0.1:9224/path', 'http://user@localhost:9224'])
def test_cdp_rejects(value):
    with pytest.raises(ValueError):
        cdp_url(value)


def test_count_cannot_come_from_product_text():
    html = shop_html(21).replace('<div>数字卡密 21</div>', '').replace('>商品<', '>数字卡密 2<') + '<div>数字卡密 21</div>'
    result = parse_shop(html, 'abc', stable=True)
    assert result.declared_count == 21
    assert result.status == 'partial'
    own = inventory_html().replace('商品 abc', '共 1 条') + '<div>共 21 条</div>'
    assert parse_inventory(own).declared_count == 21
    conflicting = shop_html() + '<div>数字卡密 3</div>'
    assert parse_shop(conflicting, 'abc', stable=True).status != 'complete'


def test_explicit_empty_inventory_evidence():
    html = '<div class="arco-table"><table><tbody></tbody></table><div class="arco-empty">暂无数据</div></div><div>共 0 条</div>'
    assert 'empty_not_verified' not in parse_inventory(html).errors


def test_money_and_ids():
    assert money('￥1,234.50') == Decimal('1234.50')
    for bad in ['NaN', '-1', '1-2', '免费', '1.234', 'Infinity']:
        with pytest.raises(ValueError):
            money(bad)
    assert shop_url('https://wzyp.cn/shop/85G05YIY') == '85G05YIY'
    assert cdp_url('http://127.0.0.1:9224') == 'http://127.0.0.1:9224'
    a = parse_shop(shop_html(), 'abc', stable=True).products[0]
    b = parse_shop(shop_html(), 'def', stable=True).products[0]
    assert a.key != b.key
