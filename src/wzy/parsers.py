from collections import Counter
from hashlib import sha256
import json
import re

from bs4 import BeautifulSoup

from .domain import Product, Result, money


def visible(node):
    for parent in [node, *node.parents]:
        if getattr(parent, 'attrs', None) is None:
            continue
        style = re.sub(r'\s+', '', parent.get('style', '')).lower()
        if parent.has_attr('hidden') or parent.get('aria-hidden') == 'true' or 'display:none' in style or 'visibility:hidden' in style:
            return False
    return True


def text(node, selector=None):
    target = node.select_one(selector) if selector else node
    return target.get_text(' ', strip=True) if target else ''


def guard(soup):
    # Only structural/login messages, never product descriptions, trigger a pause.
    for node in soup.select('section.group-box, .arco-table'):
        node.decompose()
    content = text(soup)
    return bool(re.search(r'安全验证|人机验证|访问受限|请登录|登录已过期|captcha|access denied', content, re.I) or soup.select_one('input[type=password]'))


def price(node, selector, errors):
    try:
        return str(money(text(node, selector)))
    except ValueError:
        errors.append('price_unrecognized')
        return None


def mark_collisions(products, errors):
    counts = Counter(p.key for p in products)
    indexes = Counter()
    for p in products:
        if counts[p.key] > 1:
            indexes[p.key] += 1
            p.matchable = False
            p.key += f':collision:{indexes[p.key]}'
    if any(n > 1 for n in counts.values()):
        errors.append('identity_collision')


def declared_count(html, pattern, errors):
    metadata = BeautifulSoup(html, 'html.parser')
    for node in metadata.select('section.group-box, .arco-table, .goods-group-item'):
        node.decompose()
    counts = set()
    for node in metadata.find_all(['div', 'span', 'p', 'li']):
        if visible(node):
            match = re.fullmatch(pattern, text(node))
            if match:
                counts.add(int(match[1]))
    if len(counts) > 1:
        errors.append('conflicting_counts')
        return None
    return next(iter(counts), None)


def parse_shop(html: str, shop_id: str, *, stable: bool) -> Result:
    soup = BeautifulSoup(html, 'html.parser')
    if guard(BeautifulSoup(html, 'html.parser')):
        return Result(status='paused', errors=['login_or_challenge'])
    result = Result()
    if soup.select_one('[data-wzy-loading="true"]'):
        result.errors.append('page_loading')
    result.declared_count = declared_count(html, r'数字卡密\s*[（(]?\s*(\d+)\s*[）)]?', result.errors)
    sections = [s for s in soup.select('section.group-box') if visible(s)]
    for section in sections:
        category = text(section, '.goods-group-title')
        for card in section.select('.goods-group-content .goods-group-item'):
            if not visible(card):
                continue
            title = text(card, '.goods-item-info-title')
            if not title or not category:
                result.errors.append('missing_identity')
            identity = json.dumps([shop_id, category, title], ensure_ascii=False, separators=(',', ':'))
            key = 'shop:' + shop_id + ':' + sha256(identity.encode()).hexdigest()[:24]
            result.products.append(Product(key=key, title=title, category=category,
                description=text(card, '.goods-item-info-desc'), price=price(card, '.goods-item-price-main .primary', result.errors),
                stock=text(card, '.stock'), matchable=bool(title and category)))
    mark_collisions(result.products, result.errors)
    if not sections or not result.products:
        result.status = 'failed'
        result.errors.append('no_product_evidence')
    elif result.errors or not stable or (result.declared_count is not None and result.declared_count != len(result.products)):
        result.status = 'partial'
        if result.declared_count is not None and result.declared_count != len(result.products):
            result.errors.append('count_mismatch')
        if not stable:
            result.errors.append('scroll_bound')
    else:
        result.status = 'complete' if result.declared_count is not None else 'unknown'
    return result


def parse_inventory(html: str) -> Result:
    soup = BeautifulSoup(html, 'html.parser')
    if guard(BeautifulSoup(html, 'html.parser')):
        return Result(status='paused', errors=['login_or_challenge'])
    tables = [table for table in soup.select('.arco-table') if visible(table)]
    if len(tables) != 1:
        return Result(status='failed', errors=['main_table_not_unique'])
    result = Result()
    if soup.select_one('[data-wzy-loading="true"]'):
        result.errors.append('page_loading')
    for row in tables[0].select('tbody tr'):
        if not visible(row):
            continue
        cells = row.find_all('td', recursive=False)
        if len(cells) != 8:
            result.errors.append('row_shape_unknown')
            continue
        link = cells[1].select_one('a.name')
        href = link.get('href', '') if link else ''
        match = re.fullmatch(r'(?:https://wzyp\.cn)?/item/([A-Za-z0-9_-]{1,128})', href)
        if not match:
            result.errors.append('item_identity_unknown')
            continue
        result.products.append(Product(key='own:' + match[1], title=text(link),
            price=price(cells[2], '.item-span', result.errors), status=text(cells[3], '.item-span'),
            stock=text(cells[5]), url='https://wzyp.cn/item/' + match[1], consignment='代销' in text(cells[1])))
    mark_collisions(result.products, result.errors)
    result.declared_count = declared_count(html, r'共\s*(\d+)\s*条', result.errors)
    empty = any(visible(e) and text(e) in {'暂无数据', '暂无商品', 'No data'} for e in tables[0].select('.arco-empty'))
    if not result.products and not (empty and result.declared_count == 0):
        result.errors.append('empty_not_verified')
    result.status = 'partial' if result.errors else 'unknown'
    return result
