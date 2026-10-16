import asyncio

import pytest
from fastapi.testclient import TestClient

from wzy.app import create_app
from wzy.listings import ListingBrowser, ListingError, scan_inventory, change_listing
from wzy.store import Store


def payload(**changes):
    return dict(item_id='synthetic', desired='up', expected='down', confirmed=True,
                idempotency_key='test-request-001', **changes)


HEADERS = {'Origin': 'http://127.0.0.1:8100', 'X-WZY-Request': '1'}


class Page:
    def __init__(self, pages=None, failure=None):
        self.pages = pages or [[{'id': 'synthetic', 'state': 'down', 'action': True}]]
        self.index = 0
        self.clicks = 0
        self.failure = failure
        self.closed = False

    async def goto(self, *args, **kwargs):
        self.index = 0

    async def wait_for_timeout(self, delay):
        pass

    async def evaluate(self, script, arg=None):
        if 'WZY_LISTING_CLICK' in script:
            self.clicks += 1
            if self.failure == 'before':
                raise TimeoutError('SECRET')
            self.pages[self.index][0]['state'] = arg['desired']
            if self.failure == 'after':
                raise TimeoutError('SECRET')
            return True
        if 'WZY_LISTING_PROBE' in script:
            if self.failure == 'readback' and self.clicks:
                raise TimeoutError('SECRET')
            return {'rows': [dict(r) for r in self.pages[self.index]], 'blocked': False}
        if 'WZY_NEXT_STATE' in script:
            return 'disabled' if self.index == len(self.pages) - 1 else 'enabled'
        if 'WZY_NEXT_CLICK' in script:
            self.index += 1
            return True
        raise AssertionError(script)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize('initial,desired', [('down', 'up'), ('up', 'down')])
async def test_changes_once_and_reads_back(initial, desired):
    p = Page([[{'id': 'synthetic', 'state': initial, 'action': True}]])
    marks = []
    result = await change_listing(p, 'synthetic', desired, initial, lambda before: marks.append(before))
    assert result == {'status': 'succeeded', 'before_state': initial, 'after_state': desired, 'error': None}
    assert p.clicks == 1 and marks == [initial]


@pytest.mark.asyncio
async def test_noop_and_stale():
    p = Page()
    assert (await change_listing(p, 'synthetic', 'down', 'down', lambda _: None))['status'] == 'noop'
    with pytest.raises(ListingError, match='stale_state'):
        await change_listing(p, 'synthetic', 'down', 'up', lambda _: None)
    assert p.clicks == 0


@pytest.mark.asyncio
async def test_scan_all_pages_duplicates_missing_and_bound():
    p = Page([[{'id': 'other', 'state': 'up', 'action': True}], [{'id': 'synthetic', 'state': 'down', 'action': True}]])
    assert (await scan_inventory(p, 'synthetic'))['page'] == 1
    p.index = 0
    assert (await change_listing(p, 'synthetic', 'up', 'down', lambda _: None))['status'] == 'succeeded'
    assert p.clicks == 1
    for pages, error in [([[{'id': 'other', 'state': 'up'}]], 'not_found'),
                         ([[{'id': 'synthetic', 'state': 'up'}, {'id': 'synthetic', 'state': 'up'}]], 'duplicate_target')]:
        with pytest.raises(ListingError, match=error):
            await scan_inventory(Page(pages), 'synthetic')
    with pytest.raises(ListingError, match='page_bound'):
        await scan_inventory(Page([[{'id': 'other'}], [{'id': 'synthetic'}]]), 'synthetic', max_pages=1)


@pytest.mark.asyncio
async def test_cross_page_duplicate_and_unavailable_action_never_click():
    p = Page([[{'id': 'synthetic', 'state': 'down', 'action': True}],
              [{'id': 'other', 'state': 'up', 'action': True}, {'id': 'synthetic', 'state': 'down', 'action': True}]])
    with pytest.raises(ListingError, match='duplicate_target'):
        await change_listing(p, 'synthetic', 'up', 'down', lambda _: None)
    assert p.clicks == 0
    p = Page([[{'id': 'synthetic', 'state': 'down', 'action': False}]])
    with pytest.raises(ListingError, match='action_missing'):
        await change_listing(p, 'synthetic', 'up', 'down', lambda _: None)
    assert p.clicks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,status,after', [('after', 'succeeded', 'up'), ('before', 'uncertain', 'down'), ('readback', 'uncertain', None)])
async def test_timeout_never_retries_click(failure, status, after):
    p = Page(failure=failure)
    result = await change_listing(p, 'synthetic', 'up', 'down', lambda _: None)
    assert result['status'] == status and result['after_state'] == after
    assert p.clicks == 1
    assert 'SECRET' not in str(result)


class FakeBrowser:
    def __init__(self):
        self.calls = 0

    async def change(self, item_id, desired, expected, dispatch):
        self.calls += 1
        dispatch(expected)
        return dict(status='succeeded', before_state=expected, after_state=desired, error=None)

    async def read(self, item_id):
        return {'item_id': item_id, 'state': 'up'}


def test_api_guards_idempotency_audit_and_read(tmp_path, monkeypatch):
    fake = FakeBrowser()
    monkeypatch.setenv('WZY_LISTING_WRITES_ENABLED', 'true')
    monkeypatch.setenv('WZY_LISTING_ALLOWED_ITEMS', 'synthetic')
    with TestClient(create_app(tmp_path / 'db', listing_browser=fake), base_url='http://127.0.0.1:8100') as c:
        assert c.get('/api/state').json()['listing_allowed_items'] == ['synthetic']
        assert c.post('/api/listings/state', json=payload()).status_code == 403
        denied = payload(); denied['item_id'] = 'foreign'
        assert c.post('/api/listings/state', json=denied, headers=HEADERS).status_code == 403
        denied['item_id'] = 'shop:fake'
        assert c.post('/api/listings/state', json=denied, headers=HEADERS).status_code == 422
        denied = payload(); denied['confirmed'] = False
        assert c.post('/api/listings/state', json=denied, headers=HEADERS).status_code == 422
        first = c.post('/api/listings/state', json=payload(), headers=HEADERS).json()
        assert first['status'] == 'succeeded'
        assert c.post('/api/listings/state', json=payload(), headers=HEADERS).json() == first
        conflict = payload(); conflict['desired'] = 'down'
        assert c.post('/api/listings/state', json=conflict, headers=HEADERS).status_code == 409
        assert fake.calls == 1
        assert c.get('/api/listings/operations').json()[0] == first
        assert c.get('/api/listings/operations/' + first['id']).json() == first
        assert c.get('/api/listings/items/synthetic').json()['state'] == 'up'
        assert fake.calls == 1


def test_disabled_and_recovery(tmp_path, monkeypatch):
    monkeypatch.delenv('WZY_LISTING_WRITES_ENABLED', raising=False)
    fake = FakeBrowser()
    app = create_app(tmp_path / 'db', listing_browser=fake)
    op, _ = app.state.store.start_listing(payload())
    with TestClient(app, base_url='http://127.0.0.1:8100') as c:
        assert c.post('/api/listings/state', json=payload(), headers=HEADERS).status_code == 403
        assert c.get('/api/listings/operations/' + op['id']).json()['status'] == 'uncertain'
        assert fake.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True, 'cancel'])
async def test_task_page_cleanup_without_borrowed_close(tmp_path, failure):
    from types import SimpleNamespace
    p = Page()
    async def route(*args):
        pass
    p.route = route
    async def new_page():
        return p
    browser = SimpleNamespace(contexts=[SimpleNamespace(new_page=new_page)])
    async def connect(*args, **kwargs):
        return browser
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    async def operation(page):
        if failure == 'cancel':
            raise asyncio.CancelledError()
        if failure:
            raise RuntimeError('secret')
        return 'ok'
    listing = ListingBrowser('http://127.0.0.1:9224', tmp_path / 'lock')
    if failure:
        with pytest.raises(asyncio.CancelledError if failure == 'cancel' else RuntimeError):
            await listing.with_page(playwright, operation)
    else:
        assert await listing.with_page(playwright, operation) == 'ok'
    assert p.closed


@pytest.mark.asyncio
async def test_shared_account_lock_prevents_browser_connection(tmp_path):
    from wzy.collector import account_lock, BrowserCollector
    from wzy.domain import OWN_URL
    lock = tmp_path / 'account.lock'
    async with account_lock(lock):
        with pytest.raises(RuntimeError, match='account_busy'):
            await ListingBrowser('http://127.0.0.1:9224', lock).read('synthetic')
        result = await BrowserCollector('http://127.0.0.1:9224', lock)({'kind': 'own', 'url': OWN_URL})
        assert result.status == 'failed'


def test_collection_and_listing_share_app_serialization(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from wzy.domain import Result
    entered, release = threading.Event(), threading.Event()
    async def collect(source):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return Result(status='unknown')
    monkeypatch.setenv('WZY_LISTING_WRITES_ENABLED', 'true')
    fake = FakeBrowser()
    with TestClient(create_app(tmp_path / 'db', collector=collect, listing_browser=fake), base_url='http://127.0.0.1:8100') as c:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(c.post, '/api/collect', json={'store_id': 1}, headers=HEADERS)
            assert entered.wait(3)
            try:
                assert c.post('/api/listings/state', json=payload(), headers=HEADERS).status_code == 409
                assert c.get('/api/listings/items/synthetic').status_code == 409
                assert fake.calls == 0
            finally:
                release.set()
            assert future.result().status_code == 200


def test_cancellation_persists_audit(tmp_path, monkeypatch):
    class Cancel(FakeBrowser):
        async def change(self, item_id, desired, expected, dispatch):
            dispatch(expected)
            raise asyncio.CancelledError()
    monkeypatch.setenv('WZY_LISTING_WRITES_ENABLED', 'true')
    app = create_app(tmp_path / 'db', listing_browser=Cancel())
    with TestClient(app, base_url='http://127.0.0.1:8100') as c:
        try:
            c.post('/api/listings/state', json=payload(), headers=HEADERS)
        except BaseException:
            pass
        op = c.get('/api/listings/operations').json()[0]
        assert op['status'] == 'uncertain' and op['error'] == 'interrupted'
        assert op['dispatched_at'] and op['finished_at']
