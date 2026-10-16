"""Existing stage-two schema/data must survive additive stage-three setup."""
import sqlite3

from wzy.domain import Product, Result
from wzy.store import Store


def test_legacy_matches_audits_and_observation_baseline_survive(tmp_path):
    path = tmp_path / 'legacy.sqlite'
    # These tables are exactly the pre-batch persistent contracts.
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE matches (
                competitor_key TEXT PRIMARY KEY, own_key TEXT NOT NULL, confirmed_at TEXT NOT NULL);
            CREATE TABLE listing_operations (
                id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                item_id TEXT NOT NULL, desired TEXT NOT NULL, expected TEXT NOT NULL,
                status TEXT NOT NULL, before_state TEXT, after_state TEXT, error TEXT,
                created_at TEXT NOT NULL, dispatched_at TEXT, finished_at TEXT);
            INSERT INTO matches VALUES('shop:a','own:a','2026-01-01T00:00:00+00:00');
            INSERT INTO listing_operations(id,idempotency_key,item_id,desired,expected,status,created_at)
                VALUES('legacy-operation','legacy-key','synthetic','down','up','succeeded','2026-01-01');
        ''')
    store = Store(path)
    assert store.state()['comparisons'][0]['own_key'] == 'own:a'
    assert store.listing_operation('legacy-operation')['status'] == 'succeeded'
    assert not any(s['enabled'] for s in store.schedules()['sources'])
    assert not store.inbox()['events']
    run = store.start_run(2)
    store.finish_run(run, Result([Product('shop:a', 'PPO', price='10.00')], status='complete'))
    # Mimic a database that predates inbox/scheduler tables but already has observations.
    with sqlite3.connect(path) as db:
        db.executescript('DROP TABLE notifications; DROP TABLE schedules; DROP TABLE scheduler_settings;')
    store = Store(path)
    assert not store.inbox()['events']
    store.finish_run(store.start_run(2), Result([Product('shop:a', 'PPO', price='9.00')], status='partial'))
    assert store.inbox()['events'][0]['changes'][0]['before'] == '10.00'
    assert store.listing_operation(key='legacy-key')['status'] == 'succeeded'
    assert store.state()['comparisons'][0]['competitor_key'] == 'shop:a'
