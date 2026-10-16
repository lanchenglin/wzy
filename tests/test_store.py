import pytest
from wzy.domain import Product, Result
from wzy.store import Store


def save(db, store, products, status='complete'):
    run = db.start_run(store)
    db.finish_run(run, Result(products=products, status=status, declared_count=len(products)))
    return run


def test_history_snapshot_and_partial_safety(tmp_path):
    path = tmp_path / 'db.sqlite'
    db = Store(path)
    shop = db.shops()[1]['id']
    p = Product('shop:test', '测试', price='10.10')
    first = save(db, shop, [p])
    p.price = '12.10'
    save(db, shop, [p, Product('shop:extra', '新发现', price='2.00')], 'partial')
    state = db.state()
    source = next(s for s in state['stores'] if s['id'] == shop)
    assert source['snapshot_run'] == first
    assert source['latest_status'] == 'partial'
    assert source['stale']
    assert {p['key'] for p in state['products']} == {'shop:test', 'shop:extra'}
    assert next(p for p in state['products'] if p['key'] == 'shop:test')['price'] == '10.10'
    assert len(db.history('shop:test')) == 2
    save(db, shop, [], 'failed')
    assert len(Store(path).state()['products']) == 2


def test_manual_match_persistence_decimal_and_all_unmatched(tmp_path):
    db = Store(tmp_path / 'db.sqlite')
    own, shop = [s['id'] for s in db.shops()]
    save(db, own, [Product('own:a', 'A', price='10.20'), Product('own:b', 'B', price='2.00')])
    save(db, shop, [Product('shop:a', 'A', price='10.10'), Product('shop:b', 'C', price='3.00')])
    with pytest.raises(ValueError):
        db.add_match('own:a', 'shop:a', confirmed=False)
    db.add_match('own:a', 'shop:a', confirmed=True)
    state = Store(db.path).state()
    assert state['comparisons'][0]['difference'] == '0.10'
    assert len(state['products']) == 4
    assert state['counts'] == {'matched': 1, 'pending': 2, 'own_only': 1, 'competitor_only': 1}
    with pytest.raises(ValueError):
        db.add_match('shop:a', 'own:a', confirmed=True)
    db.remove_match('shop:a')
    assert not db.state()['comparisons']


def test_collision_not_matchable_and_store_input(tmp_path):
    db = Store(tmp_path / 'db.sqlite')
    own, shop = [s['id'] for s in db.shops()]
    save(db, own, [Product('own:a', 'A')])
    save(db, shop, [Product('shop:a', 'A', matchable=False)])
    with pytest.raises(ValueError):
        db.add_match('own:a', 'shop:a', confirmed=True)
    with pytest.raises(ValueError):
        db.add_shop('https://evil.com')
    assert db.add_shop('https://wzyp.cn/shop/85G05YIY') == shop


def test_terminal_evidence_persisted_and_legacy_database_migrated(tmp_path):
    import sqlite3
    path = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(path) as legacy:
        legacy.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, store_id INTEGER, status TEXT NOT NULL, started TEXT NOT NULL, finished TEXT, declared_count INTEGER, actual_count INTEGER, errors TEXT NOT NULL DEFAULT '[]', variant_coverage TEXT NOT NULL DEFAULT 'unknown')")
    db = Store(path)
    run = db.start_run(1)
    db.finish_run(run, Result(products=[Product('own:a', 'A')], status='unknown', pagination_terminal='next_disabled'))
    reopened = Store(path)
    assert reopened.runs()[0]['pagination_terminal'] == 'next_disabled'
    source = reopened.state()['stores'][0]
    assert source['pagination_terminal'] == 'next_disabled'
    assert source['declared_count'] is None
    assert source['snapshot_run'] is None


def test_persistent_comparison_partial_failure_and_missing_side(tmp_path):
    db = Store(tmp_path / 'comparison.sqlite')
    save(db, 1, [Product('own:a', 'A', price='10.20')])
    save(db, 2, [Product('shop:a', 'B', price='10.10')])
    db.add_match('own:a', 'shop:a', confirmed=True)
    save(db, 2, [Product('shop:a', 'B', price='99.00')], 'partial')
    save(db, 1, [], 'failed')
    comparison = Store(db.path).state()['comparisons'][0]
    assert comparison['difference'] == '0.10'
    assert comparison['stale']
    save(db, 2, [])
    comparison = Store(db.path).state()['comparisons'][0]
    assert comparison['competitor'] is None
    assert comparison['difference'] is None
    assert comparison['stale']
    assert comparison['competitor_key'] == 'shop:a'


def test_persisted_match_with_ambiguous_identity_needs_review(tmp_path):
    db = Store(tmp_path / 'ambiguous.sqlite')
    save(db, 1, [Product('own:a', 'A', price='10.20')])
    save(db, 2, [Product('shop:a', 'B', price='10.10')])
    db.add_match('own:a', 'shop:a', confirmed=True)
    save(db, 2, [Product('shop:a', 'B', price='20.00', matchable=False)])
    comparison = Store(db.path).state()['comparisons'][0]
    assert comparison['needs_review']
    assert comparison['difference'] is None
    assert comparison['stale']


def test_interrupted_run_recovery(tmp_path):
    db = Store(tmp_path / 'db.sqlite')
    run = db.start_run(db.shops()[0]['id'])
    db.recover_interrupted()
    assert db.runs()[0]['id'] == run
    assert db.runs()[0]['status'] == 'failed'
