from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from .domain import DEFAULT_SHOP, OWN_URL, Result, shop_url


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS stores (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, url TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, store_id INTEGER REFERENCES stores(id), status TEXT NOT NULL,
                    started TEXT NOT NULL, finished TEXT, declared_count INTEGER, actual_count INTEGER,
                    errors TEXT NOT NULL DEFAULT '[]', variant_coverage TEXT NOT NULL DEFAULT 'unknown');
                CREATE TABLE IF NOT EXISTS observations (
                    run_id INTEGER REFERENCES runs(id), key TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(run_id, key));
                CREATE TABLE IF NOT EXISTS matches (
                    competitor_key TEXT PRIMARY KEY, own_key TEXT NOT NULL, confirmed_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS observations_key ON observations(key);
            ''')
            if 'pagination_terminal' not in {r['name'] for r in db.execute('PRAGMA table_info(runs)')}:
                db.execute("ALTER TABLE runs ADD COLUMN pagination_terminal TEXT NOT NULL DEFAULT 'not_observed'")
            db.execute('INSERT OR IGNORE INTO stores(kind,url,name) VALUES(?,?,?)', ('own', OWN_URL, '自有库存'))
            db.execute('INSERT OR IGNORE INTO stores(kind,url,name) VALUES(?,?,?)', ('competitor', DEFAULT_SHOP, 'AI杂货铺'))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def shops(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM stores ORDER BY id')]

    def add_shop(self, url):
        identity = shop_url(url)
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO stores(kind,url,name) VALUES(?,?,?)', ('competitor', url, identity))
            return db.execute('SELECT id FROM stores WHERE url=?', (url,)).fetchone()[0]

    def start_run(self, store_id):
        with self.connection() as db:
            return db.execute('INSERT INTO runs(store_id,status,started) VALUES(?,?,?)', (store_id, 'running', now())).lastrowid

    def finish_run(self, run_id, result: Result):
        if result.status not in {'complete', 'partial', 'unknown', 'failed', 'paused'}:
            raise ValueError('无效运行状态')
        with self.connection() as db:
            db.execute('UPDATE runs SET status=?,finished=?,declared_count=?,actual_count=?,errors=?,variant_coverage=?,pagination_terminal=? WHERE id=?',
                (result.status, now(), result.declared_count, len(result.products), json.dumps(result.errors), result.variant_coverage, result.pagination_terminal, run_id))
            db.executemany('INSERT INTO observations(run_id,key,payload) VALUES(?,?,?)',
                [(run_id, p.key, json.dumps(asdict(p), ensure_ascii=False)) for p in result.products])

    def recover_interrupted(self):
        with self.connection() as db:
            db.execute("UPDATE runs SET status='failed',finished=?,errors='[\"interrupted\"]' WHERE status='running'", (now(),))

    def runs(self):
        with self.connection() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 100')]
        for row in rows:
            row['errors'] = json.loads(row['errors'])
        return rows

    def history(self, key):
        with self.connection() as db:
            return [dict(json.loads(r['payload']), run_id=r['id'], observed_at=r['finished'], run_status=r['status'])
                for r in db.execute('SELECT o.payload,r.id,r.finished,r.status FROM observations o JOIN runs r ON r.id=o.run_id WHERE o.key=? ORDER BY r.id DESC', (key,))]

    def state(self):
        stores, products = self.shops(), []
        with self.connection() as db:
            for source in stores:
                latest = db.execute('SELECT * FROM runs WHERE store_id=? ORDER BY id DESC LIMIT 1', (source['id'],)).fetchone()
                complete = db.execute("SELECT id FROM runs WHERE store_id=? AND status='complete' ORDER BY id DESC LIMIT 1", (source['id'],)).fetchone()
                baseline = complete['id'] if complete else 0
                source.update(snapshot_run=baseline or None, latest_status=latest['status'] if latest else 'never',
                    latest_at=latest['finished'] if latest else None, stale=not latest or latest['status'] != 'complete',
                    declared_count=latest['declared_count'] if latest else None, actual_count=latest['actual_count'] if latest else None,
                    errors=json.loads(latest['errors']) if latest else [], variant_coverage='unknown',
                    pagination_terminal=latest['pagination_terminal'] if latest else 'not_observed')
                rows = db.execute('SELECT o.*,r.finished,r.status FROM observations o JOIN runs r ON r.id=o.run_id WHERE r.store_id=? AND r.id>=? ORDER BY r.id DESC', (source['id'], baseline)).fetchall()
                selected = {}
                # Complete baseline takes precedence; newer incomplete discoveries remain visible as provisional.
                for row in rows:
                    if row['key'] not in selected or row['run_id'] == baseline:
                        selected[row['key']] = row
                for row in selected.values():
                    products.append(dict(json.loads(row['payload']), store_id=source['id'], kind=source['kind'],
                        run_id=row['run_id'], observed_at=row['finished'], provisional=row['run_id'] != baseline,
                        stale=source['stale']))
            matches = [dict(r) for r in db.execute('SELECT * FROM matches ORDER BY competitor_key')]
        by_key = {p['key']: p for p in products}
        comparisons = []
        matched_own, matched_comp = set(), set()
        for match in matches:
            own, comp = by_key.get(match['own_key']), by_key.get(match['competitor_key'])
            needs_review = not own or not comp or not own['matchable'] or not comp['matchable']
            difference = str(Decimal(own['price']) - Decimal(comp['price'])) if not needs_review and own['price'] and comp['price'] else None
            comparisons.append(dict(match, own=own, competitor=comp, difference=difference, needs_review=needs_review,
                stale=needs_review or own['stale'] or comp['stale']))
            if not needs_review:
                matched_own.add(own['key'])
                matched_comp.add(comp['key'])
        own_only = sum(p['kind'] == 'own' and p['key'] not in matched_own for p in products)
        competitor_only = sum(p['kind'] == 'competitor' and p['key'] not in matched_comp for p in products)
        return dict(stores=stores, products=products, comparisons=comparisons,
            counts=dict(matched=len(matched_comp), pending=own_only + competitor_only, own_only=own_only, competitor_only=competitor_only),
            listing_writes_enabled=False)

    def add_match(self, own_key, competitor_key, *, confirmed):
        products = {p['key']: p for p in self.state()['products']}
        own, comp = products.get(own_key), products.get(competitor_key)
        if not confirmed or not own or not comp or own['kind'] != 'own' or comp['kind'] != 'competitor' or not own['matchable'] or not comp['matchable']:
            raise ValueError('匹配必须由人工确认，且两侧身份明确')
        with self.connection() as db:
            db.execute('INSERT INTO matches VALUES(?,?,?) ON CONFLICT(competitor_key) DO UPDATE SET own_key=excluded.own_key,confirmed_at=excluded.confirmed_at', (competitor_key, own_key, now()))

    def remove_match(self, competitor_key):
        with self.connection() as db:
            db.execute('DELETE FROM matches WHERE competitor_key=?', (competitor_key,))
