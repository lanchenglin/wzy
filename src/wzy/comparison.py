"""Advisory lexical evidence, never an SKU identity or automatic match."""
from datetime import datetime
from decimal import Decimal
import re
import unicodedata

FIELDS = ('family', 'model', 'plan', 'quota', 'duration', 'delivery', 'warranty')
FRESH_SECONDS = 7200


def extract(product):
    text = unicodedata.normalize('NFKC', ' '.join(product.get(k, '') for k in ('title', 'category', 'description'))).lower()
    def one(pattern):
        values = sorted(set(re.findall(pattern, text)))
        return '|'.join(values) or None
    warranty = one(r'(?:质保|保修)\s*(\d+(?:\.\d+)?\s*(?:天|个月|月|年))')
    if '无质保' in text or '不质保' in text:
        warranty = '无质保' if not warranty else warranty + '|无质保'
    duration_text = re.sub(r'(?:质保|保修)\s*\d+(?:\.\d+)?\s*(?:天|个月|月|年)', '', text)
    durations = sorted(set(re.findall(r'(?<![\d.])\d+(?:\.\d+)?\s*(?:天|个月|月|年)', duration_text)))
    return dict(family=one(r'(?<![a-z])(?:ppo|chatgpt|gpt|claude|gemini|netflix|spotify|midjourney)(?![a-z])'),
                model=one(r'(?<![a-z])(?:gpt[- ]?\d[a-z0-9.-]*|opus[- ]?\d[a-z0-9.-]*|sonnet[- ]?\d[a-z0-9.-]*|gemini[- ]?\d[a-z0-9.-]*)'),
                plan=one(r'(?<![a-z])(?:plus|pro|max|team|ultra|premium)(?![a-z])'),
                quota=one(r'\d+(?:\.\d+)?\s*(?:次|积分|额度|gb|tb|tokens?)'),
                duration='|'.join(durations) or None, delivery=one(r'充值|成品|镜像'), warranty=warranty)


def assess(own, competitor):
    left, right = extract(own), extract(competitor)
    conflicts = [k for k in FIELDS if left[k] and right[k] and (left[k] != right[k] or '|' in left[k])]
    unknown = [k for k in FIELDS if not left[k] or not right[k]]
    agreement = [k for k in FIELDS if left[k] and left[k] == right[k] and k not in conflicts]
    candidate = 'family' in agreement or 'model' in agreement
    confidence = 'conflict' if conflicts else 'medium' if candidate and len(agreement) >= 4 else 'low'
    return dict(own_fields=left, competitor_fields=right, conflicts=conflicts, unknown=unknown,
                reasons=agreement, confidence=confidence, candidate=candidate)


def stock_state(value):
    text = unicodedata.normalize('NFKC', value or '').strip()
    if text in {'缺货', '无货', '售罄', '已售罄', '0', '库存:0', '库存：0'}:
        return 'unavailable'
    if text in {'有货', '现货', '库存充足'}:
        return 'available'
    count = re.fullmatch(r'(?:库存\s*[:：]?\s*)?(\d+)\s*(?:件|个|张)?', text)
    return ('available' if int(count[1]) > 0 else 'unavailable') if count else 'unknown'


def evidence(product, current):
    try:
        age = (current - datetime.fromisoformat(product['observed_at'])).total_seconds()
        fresh = 0 <= age <= FRESH_SECONDS
    except (ValueError, TypeError, KeyError):
        fresh = False
    product.update(freshness='fresh' if fresh else 'stale', stock_state=stock_state(product.get('stock')),
                   coverage='unknown', collected_at=product.get('observed_at'))
    product['usable'] = fresh and not product['stale'] and not product['provisional'] and product['matchable']


def delta(own, comp):
    if not own or not comp or not own['matchable'] or not comp['matchable'] or own['price'] is None or comp['price'] is None:
        return None, None
    a, b = Decimal(own['price']), Decimal(comp['price'])
    return str(a - b), str(((a - b) / b * 100).quantize(Decimal('0.01'))) if b else None


def enrich(state, current):
    for p in state['products']:
        evidence(p, current)
        p['details'] = extract(p)
    products = {p['key']: p for p in state['products']}
    groups = {p['key']: dict(own=p, own_key=p['key'], competitors=[], lowest_available=None) for p in state['products'] if p['kind'] == 'own'}
    matched = set()
    for m in state['comparisons']:
        own, comp = m['own'], m['competitor']
        difference, percentage = delta(own, comp)
        usable = bool(own and comp and own['usable'] and comp['usable'])
        m.update(percentage=percentage, usable=usable, stale=not usable)
        group = groups.setdefault(m['own_key'], dict(own=own, own_key=m['own_key'], competitors=[], lowest_available=None))
        group['competitors'].append(dict(product=comp, competitor_key=m['competitor_key'], confirmed=True,
                                         difference=difference, percentage=percentage, usable=m['usable']))
        matched.add(m['competitor_key'])
    unmatched = []
    for p in state['products']:
        if p['kind'] == 'competitor' and p['key'] not in matched:
            unmatched.append(dict(own=None, own_key=None, lowest_available=None, competitors=[dict(product=p,
                competitor_key=p['key'], confirmed=False, difference=None, percentage=None, usable=False)]))
    for group in groups.values():
        eligible = [c['product'] for c in group['competitors'] if c['usable'] and c['product']['stock_state'] == 'available' and c['product']['price'] is not None]
        if eligible:
            group['lowest_available'] = min(eligible, key=lambda p: (Decimal(p['price']), p['key']))
    state['groups'] = list(groups.values()) + unmatched
    suggestions = []
    for own in products.values():
        if own['kind'] != 'own' or not own['matchable']:
            continue
        for comp in products.values():
            if comp['kind'] != 'competitor' or not comp['matchable'] or comp['key'] in matched:
                continue
            result = assess(own, comp)
            if result['candidate']:
                suggestions.append(dict(result, own_key=own['key'], competitor_key=comp['key']))
    state['suggestions'] = sorted(suggestions, key=lambda s: (s['confidence'] == 'conflict', -len(s['reasons']), s['own_key'], s['competitor_key']))
    state['freshness_seconds'] = FRESH_SECONDS
    return state
