from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import uuid

from .domain import DEFAULT_SHOP, OWN_URL, Result, shop_url


def now():
    return datetime.now(timezone.utc).isoformat()


from .scheduler import ScheduleStore


class Store(ScheduleStore):
    def __init__(self, path, *, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
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
                CREATE TABLE IF NOT EXISTS scheduler_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO scheduler_settings(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS schedules (
                    store_id INTEGER PRIMARY KEY REFERENCES stores(id), enabled INTEGER NOT NULL DEFAULT 0,
                    interval_minutes INTEGER NOT NULL DEFAULT 60 CHECK(interval_minutes BETWEEN 15 AND 1440),
                    blocked INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
                    next_run TEXT, last_attempt TEXT, last_success TEXT, last_outcome TEXT,
                    browser_evidence TEXT NOT NULL DEFAULT 'not_observed');
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
                    store_id INTEGER NOT NULL REFERENCES stores(id), kind TEXT NOT NULL,
                    quality TEXT NOT NULL, changes TEXT NOT NULL, created_at TEXT NOT NULL,
                    read INTEGER NOT NULL DEFAULT 0, acknowledged INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS listing_operations (
                    id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                    item_id TEXT NOT NULL, desired TEXT NOT NULL, expected TEXT NOT NULL,
                    status TEXT NOT NULL, before_state TEXT, after_state TEXT, error TEXT,
                    created_at TEXT NOT NULL, dispatched_at TEXT, finished_at TEXT);
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
            return db.execute('INSERT INTO runs(store_id,status,started) VALUES(?,?,?)', (store_id, 'running', self.clock().isoformat())).lastrowid

    def finish_run(self, run_id, result: Result):
        if result.status not in {'complete', 'partial', 'unknown', 'failed', 'paused'}:
            raise ValueError('无效运行状态')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()
            if not run:
                raise ValueError('无效运行')
            if run['status'] != 'running':
                return
            from .notifications import record
            record(db, run_id, result, self.clock().isoformat())
            db.execute('UPDATE runs SET status=?,finished=?,declared_count=?,actual_count=?,errors=?,variant_coverage=?,pagination_terminal=? WHERE id=?',
                (result.status, self.clock().isoformat(), result.declared_count, len(result.products), json.dumps(result.errors), result.variant_coverage, result.pagination_terminal, run_id))
            db.executemany('INSERT INTO observations(run_id,key,payload) VALUES(?,?,?)',
                [(run_id, p.key, json.dumps(asdict(p), ensure_ascii=False)) for p in result.products])

    def inbox(self, *, before_run=None):
        with self.connection() as db:
            events = [dict(r) for r in db.execute('SELECT * FROM notifications WHERE (? IS NULL OR run_id<?) ORDER BY run_id DESC LIMIT 101', (before_run, before_run))]
            next_before_run = events[99]['run_id'] if len(events) > 100 else None
            events = events[:100]
            unread = db.execute('SELECT COUNT(*) FROM notifications WHERE read=0').fetchone()[0]
        for event in events:
            event['changes'] = json.loads(event['changes'])
            event['read'] = bool(event['read'])
            event['acknowledged'] = bool(event['acknowledged'])
        return dict(events=events, unread=unread, transport='in_app_only', next_before_run=next_before_run)

    def update_notification(self, event_id, *, read, acknowledged=False):
        with self.connection() as db:
            return db.execute('UPDATE notifications SET read=?,acknowledged=? WHERE id=?',
                (bool(read or acknowledged), bool(acknowledged), event_id)).rowcount > 0

    def listing_operation(self, operation_id=None, *, key=None):
        with self.connection() as db:
            row = db.execute('SELECT * FROM listing_operations WHERE ' + ('id=?' if operation_id else 'idempotency_key=?'),
                             (operation_id or key,)).fetchone()
            return dict(row) if row else None

    def listing_operations(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM listing_operations ORDER BY created_at DESC LIMIT 100')]

    def start_listing(self, body):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM listing_operations WHERE idempotency_key=?', (body['idempotency_key'],)).fetchone()
            if row:
                if any(row[k] != body[k] for k in ('item_id', 'desired', 'expected')):
                    raise ValueError('idempotency_conflict')
                return dict(row), False
            operation_id = str(uuid.uuid4())
            db.execute('INSERT INTO listing_operations(id,idempotency_key,item_id,desired,expected,status,created_at) VALUES(?,?,?,?,?,?,?)',
                       (operation_id, body['idempotency_key'], body['item_id'], body['desired'], body['expected'], 'pending', self.clock().isoformat()))
            return dict(db.execute('SELECT * FROM listing_operations WHERE id=?', (operation_id,)).fetchone()), True

    def dispatch_listing(self, operation_id, before):
        with self.connection() as db:
            db.execute("UPDATE listing_operations SET status='dispatched',before_state=?,dispatched_at=? WHERE id=?", (before, self.clock().isoformat(), operation_id))

    def finish_listing(self, operation_id, *, status, before_state=None, after_state=None, error=None):
        with self.connection() as db:
            db.execute('UPDATE listing_operations SET status=?,before_state=COALESCE(?,before_state),after_state=?,error=?,finished_at=? WHERE id=?',
                       (status, before_state, after_state, error, self.clock().isoformat(), operation_id))

    def recover_interrupted(self):
        with self.connection() as db:
            db.execute("UPDATE listing_operations SET status='uncertain',error='interrupted',finished_at=? WHERE status IN ('pending','dispatched')", (self.clock().isoformat(),))
            interrupted = [dict(r) for r in db.execute("SELECT id,store_id FROM runs WHERE status='running' ORDER BY id")]
        for run in interrupted:
            result = Result(status='failed', errors=['interrupted'])
            self.finish_run(run['id'], result)
            self.schedule_outcome(run['store_id'], result)

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
                selected, newest = {}, {}
                # Complete baseline takes precedence; newer incomplete discoveries remain visible as provisional.
                for row in rows:
                    newest.setdefault(row['key'], row)
                    if row['key'] not in selected or row['run_id'] == baseline:
                        selected[row['key']] = row
                for row in selected.values():
                    products.append(dict(json.loads(row['payload']), store_id=source['id'], kind=source['kind'],
                        run_id=row['run_id'], observed_at=row['finished'], provisional=row['run_id'] != baseline,
                        stale=source['stale'], quality=row['status'],
                        latest_observation=dict(json.loads(newest[row['key']]['payload']),
                            run_id=newest[row['key']]['run_id'], collected_at=newest[row['key']]['finished'],
                            quality=newest[row['key']]['status'])))
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
        from .comparison import enrich
        return enrich(dict(stores=stores, products=products, comparisons=comparisons,
            counts=dict(matched=len(matched_comp), pending=own_only + competitor_only, own_only=own_only, competitor_only=competitor_only),
            listing_writes_enabled=False), self.clock())

    def add_match(self, own_key, competitor_key, *, confirmed):
        products = {p['key']: p for p in self.state()['products']}
        own, comp = products.get(own_key), products.get(competitor_key)
        if not confirmed or not own or not comp or own['kind'] != 'own' or comp['kind'] != 'competitor' or not own['matchable'] or not comp['matchable']:
            raise ValueError('匹配必须由人工确认，且两侧身份明确')
        with self.connection() as db:
            db.execute('INSERT INTO matches VALUES(?,?,?) ON CONFLICT(competitor_key) DO UPDATE SET own_key=excluded.own_key,confirmed_at=excluded.confirmed_at', (competitor_key, own_key, self.clock().isoformat()))

    def remove_match(self, competitor_key):
        with self.connection() as db:
            db.execute('DELETE FROM matches WHERE competitor_key=?', (competitor_key,))
