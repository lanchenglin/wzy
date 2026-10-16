"""Transactional, run-aggregated notifications from actual observations."""
from decimal import Decimal
import json

from .comparison import stock_state


def record(db, run_id, result, timestamp):
    source = db.execute('SELECT store_id FROM runs WHERE id=?', (run_id,)).fetchone()['store_id']
    previous = db.execute("SELECT status FROM runs WHERE store_id=? AND id<? AND status!='running' ORDER BY id DESC LIMIT 1", (source, run_id)).fetchone()
    kind, changes = 'changes', []
    if result.status in {'failed', 'paused'}:
        if previous and previous['status'] == result.status:
            return
        kind = 'diagnostic'
    else:
        for product in result.products:
            if not product.matchable:
                continue
            old = db.execute("SELECT o.payload FROM observations o JOIN runs r ON r.id=o.run_id WHERE r.store_id=? AND o.key=? AND r.id<? AND r.status IN ('complete','partial','unknown') ORDER BY r.id DESC LIMIT 1", (source, product.key, run_id)).fetchone()
            if not old:
                continue
            before = json.loads(old['payload'])
            if not before.get('matchable', True):
                continue
            base = dict(key=product.key, title=product.title)
            if before['price'] is not None and product.price is not None:
                a, b = Decimal(before['price']), Decimal(product.price)
                if a != b:
                    changes.append(dict(base, type='price_up' if b > a else 'price_down', before=before['price'], after=product.price))
            a, b = stock_state(before['stock']), stock_state(product.stock)
            if a != 'unknown' and b != 'unknown' and a != b:
                changes.append(dict(base, type='stock', before=a, after=b))
        if not changes:
            return
    db.execute('INSERT OR IGNORE INTO notifications(id,run_id,store_id,kind,quality,changes,created_at) VALUES(?,?,?,?,?,?,?)',
               (f'run:{run_id}:{kind}', run_id, source, kind, result.status, json.dumps(changes, ensure_ascii=False), timestamp))
