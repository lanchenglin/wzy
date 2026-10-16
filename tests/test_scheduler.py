import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient
from wzy.app import create_app
from wzy.domain import Result
from wzy.store import Store
from wzy.scheduler import Coordinator, instance_lease


class Clock:
    value = datetime(2026, 10, 16, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, minutes):
        self.value += timedelta(minutes=minutes)


def setup(tmp_path, collector):
    clock = Clock()
    store = Store(tmp_path / 'db', clock=clock)
    return clock, store, Coordinator(store, collector, asyncio.Lock())


def test_defaultoff_interval_restart_and_global_pause(tmp_path):
    async def scenario():
        calls = []
        async def collect(source):
            calls.append(source['id'])
            return Result(status='partial', errors=['count_mismatch'])
        clock, store, coordinator = setup(tmp_path, collect)
        assert not store.schedules()['paused']
        assert all(not s['enabled'] for s in store.schedules()['sources'])
        clock.advance(100)
        assert await coordinator.tick() is None
        for interval in [14, 1441]:
            with pytest.raises(ValueError):
                store.configure_schedule(2, enabled=True, interval_minutes=interval)
        store.configure_schedule(2, enabled=True, interval_minutes=60)
        clock.advance(60)
        store.pause_scheduler(True)
        assert await coordinator.tick() is None
        assert (await coordinator.collect(1))['status'] == 'partial'
        store.pause_scheduler(False)
        clock.advance(60)
        assert (await coordinator.tick())['status'] == 'partial'
        schedule = store.schedules()['sources'][1]
        assert schedule['failures'] == 0
        assert schedule['last_outcome'] == 'partial'
        assert schedule['last_success'] is None
        assert schedule['browser_evidence'] == 'observed'
        clock.advance(1000)
        store.rebase_schedules()
        assert await coordinator.tick() is None
        assert len(calls) == 2
    asyncio.run(scenario())


def test_pause_challenge_manual_resume_and_backoff(tmp_path):
    async def scenario():
        result = [Result(status='paused', errors=['login_or_challenge'])]
        async def collect(source):
            return result[0]
        clock, store, coordinator = setup(tmp_path, collect)
        store.configure_schedule(2, enabled=True, interval_minutes=15)
        clock.advance(15)
        await coordinator.tick()
        assert store.schedules()['sources'][1]['blocked'] is True
        clock.advance(100)
        assert await coordinator.tick() is None
        result[0] = Result(status='complete')
        await coordinator.collect(2)
        assert store.schedules()['sources'][1]['blocked'] is True
        store.configure_schedule(2, enabled=True, interval_minutes=15, resume=True)
        result[0] = Result(status='failed', errors=['collection_unavailable'])
        clock.advance(15)
        await coordinator.tick()
        s = store.schedules()['sources'][1]
        assert s['failures'] == 1
        assert datetime.fromisoformat(s['next_run']) == clock() + timedelta(minutes=30)
        for _ in range(10):
            clock.advance(1440)
            await coordinator.tick()
        s = store.schedules()['sources'][1]
        assert datetime.fromisoformat(s['next_run']) <= clock() + timedelta(hours=24)
    asyncio.run(scenario())


def test_shared_lock_no_overlap_and_cancel_persist(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def collect(source):
            entered.set()
            await release.wait()
            return Result(status='complete')
        clock, store, coordinator = setup(tmp_path, collect)
        store.configure_schedule(2, enabled=True, interval_minutes=15)
        clock.advance(15)
        task = asyncio.create_task(coordinator.tick())
        await entered.wait()
        assert await coordinator.tick() is None
        with pytest.raises(RuntimeError, match='busy'):
            await coordinator.collect(1)
        assert coordinator.busy.locked()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store.runs()[0]['errors'] == ['interrupted']
        assert store.schedules()['sources'][1]['last_outcome'] == 'failed'
        assert not coordinator.busy.locked()
    asyncio.run(scenario())


def test_restart_reconciles_interrupted_outcome_and_diagnostic(tmp_path):
    store = Store(tmp_path / 'db')
    store.configure_schedule(2, enabled=True)
    store.start_run(2)
    store.recover_interrupted()
    schedule = store.schedules()['sources'][1]
    assert schedule['last_outcome'] == 'failed'
    assert schedule['failures'] == 1
    assert store.inbox()['events'][0]['kind'] == 'diagnostic'
    store.recover_interrupted()
    assert len(store.inbox()['events']) == 1


def test_repeated_cancellation_retains_child_ownership(tmp_path):
    async def scenario():
        entered, cleanup, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def collect(source):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    exited.set()
        clock, store, coordinator = setup(tmp_path, collect)
        coordinator.cleanup_timeout = 0.05
        task = asyncio.create_task(coordinator.collect(2))
        await entered.wait()
        task.cancel()
        await cleanup.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert coordinator.busy.locked()
        await coordinator.close()
        assert exited.is_set()
        assert not coordinator.children
    asyncio.run(scenario())


def test_restart_preserves_failure_delay(tmp_path):
    async def collect(source):
        return Result(status='failed')
    clock, store, _ = setup(tmp_path, collect)
    store.configure_schedule(2, enabled=True, interval_minutes=15)
    store.start_run(2)
    store.recover_interrupted()
    store.rebase_schedules()
    s = store.schedules()['sources'][1]
    assert datetime.fromisoformat(s['next_run']) == clock() + timedelta(minutes=30)


def test_shutdown_bounds_stalled_cleanup(tmp_path):
    async def scenario():
        entered, cleanup = asyncio.Event(), asyncio.Event()
        async def collect(source):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await asyncio.Event().wait()
        clock, store, coordinator = setup(tmp_path, collect)
        coordinator.cleanup_timeout = 0.02
        task = asyncio.create_task(coordinator.collect(2))
        await entered.wait()
        closing = asyncio.create_task(coordinator.close())
        await asyncio.wait_for(cleanup.wait(), 1)
        await asyncio.wait_for(closing, 1)
        assert task.done()
        assert store.runs()[0]['status'] == 'failed'
        assert not coordinator.busy.locked()
    asyncio.run(scenario())


def test_instance_lease_precedes_recovery_and_is_released(tmp_path):
    path = tmp_path / 'db'
    async def collect(source):
        return Result(status='complete')
    first = create_app(path, collector=collect)
    second = create_app(path, collector=collect)
    with TestClient(first, base_url='http://127.0.0.1'):
        run = first.state.store.start_run(1)
        with pytest.raises(RuntimeError, match='database_in_use'):
            with TestClient(second, base_url='http://127.0.0.1'):
                pass
        assert first.state.store.runs()[0]['status'] == 'running'
    with TestClient(second, base_url='http://127.0.0.1'):
        assert second.state.store.runs()[0]['status'] == 'failed'
    with instance_lease(path):
        with pytest.raises(RuntimeError, match='database_in_use'):
            with instance_lease(path):
                pass
