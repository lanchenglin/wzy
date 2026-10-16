from datetime import datetime, timezone
from wzy.store import Store
from wzy.domain import Product, Result


def observe(store, price='10.00', stock='有货', status='complete', products=None):
    run = store.start_run(2)
    result = Result(products if products is not None else [Product('shop:a', 'PPO', price=price, stock=stock)], status=status)
    store.finish_run(run, result)
    return run, result


def test_observation_baseline_partial_dedupe_and_restart(tmp_path):
    path = tmp_path / 'db'
    store = Store(path)
    observe(store)
    assert store.inbox()['events'] == []
    run, result = observe(store, '8.00', '缺货', 'partial')
    events = store.inbox()['events']
    assert len(events) == 1
    assert events[0]['run_id'] == run
    assert events[0]['quality'] == 'partial'
    assert {c['type'] for c in events[0]['changes']} == {'price_down', 'stock'}
    assert events[0]['changes'][0]['before'] == '10.00'
    store.finish_run(run, result)
    assert store.inbox()['unread'] == 1
    store = Store(path)
    observe(store, '7.00', status='partial')
    latest = store.inbox()['events'][0]
    assert latest['changes'][0]['before'] == '8.00'
    observe(store, status='partial', products=[])
    assert len(store.inbox()['events']) == 2
    store.update_notification(latest['id'], read=True, acknowledged=True)
    assert store.inbox()['unread'] == 1
    assert Store(path).inbox()['events'][0]['acknowledged'] is True


def test_failed_paused_diagnostic_transitions_and_unknown_stock(tmp_path):
    store = Store(tmp_path / 'db')
    observe(store, stock='未知')
    observe(store, stock='有货')
    assert not store.inbox()['events']
    observe(store, '1.00', status='failed')
    observe(store, '2.00', status='failed')
    assert len(store.inbox()['events']) == 1
    assert store.inbox()['events'][0]['kind'] == 'diagnostic'
    observe(store, '3.00', status='paused')
    assert len(store.inbox()['events']) == 2
    observe(store, '9.00')
    changes = store.inbox()['events'][0]['changes']
    assert changes[0]['before'] == '10.00'
    assert changes[0]['after'] == '9.00'


def test_older_unread_notifications_are_reachable(tmp_path):
    store = Store(tmp_path / 'db')
    observe(store)
    for price in range(11, 113):
        observe(store, str(price))
    page = store.inbox()
    assert len(page['events']) == 100
    older = store.inbox(before_run=page['next_before_run'])
    assert len(older['events']) == 2
    for event in page['events'] + older['events']:
        store.update_notification(event['id'], read=True)
    assert store.inbox()['unread'] == 0


def test_notification_uses_injected_clock(tmp_path):
    store = Store(tmp_path / 'db', clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    observe(store)
    observe(store, '11.00')
    event = store.inbox()['events'][0]
    assert event['created_at'] == '2026-01-01T00:00:00+00:00'
    assert event['changes'][0]['type'] == 'price_up'
