from datetime import datetime, timezone, timedelta

from wzy import comparison
from wzy.domain import Product, Result
from wzy.store import Store


NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)


def test_extraction_conflicts_and_unknown_are_not_equivalence():
    a = {'title': 'PPO Pro 100次 30天 充值 质保7天'}
    b = {'title': 'PPO Plus 200次 365天 成品 无质保'}
    fields = comparison.extract(a)
    assert fields['family'] == 'ppo'
    assert fields['duration'] == '30天'
    assert fields['warranty'] == '7天'
    result = comparison.assess(a, b)
    assert set(result['conflicts']) >= {'plan', 'quota', 'duration', 'delivery', 'warranty'}
    assert result['confidence'] == 'conflict'
    unknown = comparison.assess({'title': 'PPO'}, {'title': 'PPO'})
    assert 'duration' in unknown['unknown']
    assert unknown['confidence'] == 'low'
    assert comparison.assess({'title': 'GPT-4o 成品'}, {'title': 'GPT-4 充值'})['conflicts']


def test_fractional_duration_and_adjacent_chinese_model():
    assert comparison.assess({'title': 'PPO Pro 1.5个月 充值'}, {'title': 'PPO Pro 5个月 充值'})['conflicts'] == ['duration']
    assert comparison.extract({'title': 'GPT-4o成品'})['model'] == 'gpt-4o'
    assert not comparison.assess({'title': 'GPT-4o成品'}, {'title': 'GPT-4o 成品'})['conflicts']


def test_groups_minima_freshness_and_decimal(tmp_path):
    clock = [NOW]
    store = Store(tmp_path / 'db', clock=lambda: clock[0])
    third = store.add_shop('https://wzyp.cn/shop/THIRD')
    for sid, product in [(1, Product('own:a', 'PPO', price='10.00')),
                         (2, Product('shop:a', 'PPO', price='8.00', stock='有货')),
                         (third, Product('shop:b', 'PPO', price='0.00', stock='未知'))]:
        store.finish_run(store.start_run(sid), Result([product], status='complete'))
    for key in ['shop:a', 'shop:b']:
        store.add_match('own:a', key, confirmed=True)
    state = store.state()
    group = state['groups'][0]
    assert len(group['competitors']) == 2
    assert group['lowest_available']['price'] == '8.00'
    assert group['competitors'][0]['difference'] == '2.00'
    assert group['competitors'][0]['percentage'] == '25.00'
    assert group['competitors'][1]['percentage'] is None
    assert state['products'][0]['freshness'] == 'fresh'
    clock[0] += timedelta(hours=3)
    assert store.state()['comparisons'][0]['stale'] is True
    assert store.state()['groups'][0]['lowest_available'] is None
    assert all(p['freshness'] == 'stale' for p in store.state()['products'])


def test_partial_observation_separate_and_all_unmatched_visible(tmp_path):
    store = Store(tmp_path / 'db', clock=lambda: NOW)
    store.finish_run(store.start_run(2), Result([Product('shop:a', 'PPO', price='8.00', stock='3')], status='complete'))
    store.finish_run(store.start_run(2), Result([Product('shop:a', 'PPO', price='7.00', stock='3'), Product('shop:b', 'Other')], status='partial'))
    state = store.state()
    p = next(p for p in state['products'] if p['key'] == 'shop:a')
    assert p['price'] == '8.00'
    assert p['latest_observation']['price'] == '7.00'
    assert p['latest_observation']['quality'] == 'partial'
    assert p['usable'] is False
    assert {g['competitors'][0]['product']['key'] for g in state['groups']} == {'shop:a', 'shop:b'}
    assert not state['comparisons']


def test_suggestions_never_save_and_exclude_unrelated(tmp_path):
    store = Store(tmp_path / 'db', clock=lambda: NOW)
    for sid, p in [(1, Product('own:a', 'PPO Pro 30天 充值')), (2, Product('shop:a', 'PPO Pro 30天 充值'))]:
        store.finish_run(store.start_run(sid), Result([p], status='complete'))
    assert store.state()['suggestions'][0]['own_key'] == 'own:a'
    assert store.state()['comparisons'] == []
    assert comparison.assess({'title': '陌生甲'}, {'title': '陌生乙'})['candidate'] is False
