"""Opt-in read-only scheduling and shared manual/automatic orchestration."""
import asyncio
from contextlib import contextmanager
from datetime import timedelta
import fcntl
from pathlib import Path

from .domain import Result


@contextmanager
def instance_lease(path):
    # Resolve symlinks so alternate spellings of one database share the lease.
    lock = Path(str(Path(path).resolve()) + '.instance.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('database_in_use') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class ScheduleStore:
    def schedules(self):
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO schedules(store_id) SELECT id FROM stores')
            sources = [dict(r) for r in db.execute('SELECT * FROM schedules ORDER BY store_id')]
            paused = bool(db.execute('SELECT paused FROM scheduler_settings WHERE id=1').fetchone()[0])
        for source in sources:
            source['enabled'] = bool(source['enabled'])
            source['blocked'] = bool(source['blocked'])
        return dict(paused=paused, sources=sources)

    def configure_schedule(self, store_id, *, enabled, interval_minutes=60, resume=False):
        if not 15 <= interval_minutes <= 1440:
            raise ValueError('invalid_interval')
        self.schedules()
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM schedules WHERE store_id=?', (store_id,)).fetchone():
                raise ValueError('unknown_source')
            db.execute('UPDATE schedules SET enabled=?,interval_minutes=?,next_run=?,blocked=CASE WHEN ? THEN 0 ELSE blocked END,failures=CASE WHEN ? THEN 0 ELSE failures END WHERE store_id=?',
                (enabled, interval_minutes, (self.clock() + timedelta(minutes=interval_minutes)).isoformat() if enabled else None, resume, resume, store_id))

    def rebase_schedules(self):
        self.schedules()
        with self.connection() as db:
            for row in db.execute('SELECT * FROM schedules').fetchall():
                db.execute('UPDATE schedules SET next_run=? WHERE store_id=?',
                    ((self.clock() + timedelta(minutes=min(1440, row['interval_minutes'] * 2 ** row['failures']))).isoformat() if row['enabled'] and not row['blocked'] else None, row['store_id']))

    def pause_scheduler(self, paused):
        with self.connection() as db:
            db.execute('UPDATE scheduler_settings SET paused=? WHERE id=1', (paused,))
        if not paused:
            self.rebase_schedules()

    def schedule_outcome(self, store_id, result):
        self.schedules()
        timestamp = self.clock().isoformat()
        with self.connection() as db:
            row = db.execute('SELECT * FROM schedules WHERE store_id=?', (store_id,)).fetchone()
            failures = min(row['failures'] + 1, 10) if result.status == 'failed' else 0
            blocked = row['blocked'] or result.status == 'paused' or 'login_or_challenge' in result.errors
            delay = min(1440, row['interval_minutes'] * 2 ** failures)
            next_run = (self.clock() + timedelta(minutes=delay)).isoformat() if row['enabled'] and not blocked else None
            # This is historical collection evidence, not a synthetic health probe.
            evidence = 'observed' if result.status in {'complete', 'partial', 'unknown'} else 'login_or_challenge' if result.status == 'paused' else 'unavailable_or_failed'
            db.execute('UPDATE schedules SET failures=?,blocked=?,next_run=?,last_outcome=?,last_attempt=?,last_success=?,browser_evidence=? WHERE store_id=?',
                (failures, blocked, next_run, result.status, timestamp, timestamp if result.status == 'complete' else row['last_success'], evidence, store_id))


class Coordinator:
    def __init__(self, store, collector, busy):
        self.store, self.collector, self.busy = store, collector, busy
        self.active = set()
        self.children = set()
        self.closing = False
        self.cleanup_timeout = 5

    async def stop_browser_task(self, task):
        cleanup = asyncio.create_task(self._stop_browser_task(task))
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _stop_browser_task(self, task):
        task.cancel()
        _, pending = await asyncio.wait({task}, timeout=self.cleanup_timeout)
        if pending:
            task.cancel()
            _, pending = await asyncio.wait({task}, timeout=self.cleanup_timeout)
        if pending:
            # Fail closed: do not start another account task after stuck cleanup.
            self.closing = True
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        elif not task.cancelled():
            task.exception()

    async def collect(self, store_id):
        if self.closing or self.busy.locked():
            raise RuntimeError('busy')
        source = next((s for s in self.store.shops() if s['id'] == store_id), None)
        if not source:
            raise ValueError('unknown_source')
        task = asyncio.current_task()
        self.active.add(task)
        try:
            async with self.busy:
                run_id = self.store.start_run(store_id)
                browser_task = asyncio.create_task(self.collector(source))
                self.children.add(browser_task)
                browser_task.add_done_callback(self.children.discard)
                try:
                    done, _ = await asyncio.wait({browser_task}, timeout=180)
                    if not done:
                        await self.stop_browser_task(browser_task)
                        result = Result(status='failed', errors=['collection_timeout'])
                    else:
                        result = browser_task.result()
                except asyncio.CancelledError:
                    result = Result(status='failed', errors=['interrupted'])
                    self.store.finish_run(run_id, result)
                    self.store.schedule_outcome(store_id, result)
                    await self.stop_browser_task(browser_task)
                    raise
                except Exception:
                    result = Result(status='failed', errors=['collection_failed'])
                self.store.finish_run(run_id, result)
                self.store.schedule_outcome(store_id, result)
                return dict(run_id=run_id, status=result.status, errors=result.errors)
        finally:
            self.active.discard(task)

    async def tick(self):
        settings = self.store.schedules()
        if settings['paused'] or self.busy.locked() or self.closing:
            return None
        now = self.store.clock().isoformat()
        due = sorted((s for s in settings['sources'] if s['enabled'] and not s['blocked'] and s['next_run'] and s['next_run'] <= now), key=lambda s: (s['next_run'], s['store_id']))
        if due:
            return await self.collect(due[0]['store_id'])
        return None

    async def run(self):
        while not self.closing:
            await self.tick()
            await asyncio.sleep(5)

    async def close(self):
        self.closing = True
        tasks = list(self.active)
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=2 * self.cleanup_timeout + 1)
            for task in pending:
                task.cancel()
            for task in done:
                if not task.cancelled():
                    task.exception()
        if self.children:
            await asyncio.gather(*(self.stop_browser_task(task) for task in list(self.children)))
