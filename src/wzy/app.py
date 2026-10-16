import argparse
import asyncio
from contextlib import asynccontextmanager
import csv
import io
import os
from pathlib import Path
from typing import Literal
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import Auth
from .collector import BrowserCollector
from .domain import Result
from .store import Store
from .listings import ListingBrowser, ListingError
from .scheduler import Coordinator, instance_lease


class ListingInput(BaseModel):
    model_config = {'extra': 'forbid'}
    item_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    desired: Literal['up', 'down']
    expected: Literal['up', 'down']
    confirmed: Literal[True]
    idempotency_key: str = Field(pattern=r'^[A-Za-z0-9_-]{8,128}$')

STATIC = Path(__file__).parent / 'static'


class ShopInput(BaseModel):
    url: str = Field(max_length=160)


class CollectInput(BaseModel):
    store_id: int = Field(gt=0)


class MatchInput(BaseModel):
    own_key: str = Field(max_length=256)
    competitor_key: str = Field(max_length=256)
    confirmed: bool = False


class ScheduleInput(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    enabled: bool
    interval_minutes: int = Field(default=60, ge=15, le=1440)
    resume: bool = False


class PauseInput(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    paused: bool


class NotificationInput(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    read: bool
    acknowledged: bool = False


class RemoveInput(BaseModel):
    competitor_key: str = Field(max_length=256)


def safe_csv(value):
    value = str(value if value is not None else '')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


def create_app(db_path=None, *, collector=None, listing_browser=None, clock=None):
    auth = Auth()
    store = Store(db_path or os.environ.get('WZY_DB', 'data/wzy.sqlite3'), clock=clock)
    collect = collector or BrowserCollector(os.environ.get('WZY_CDP_URL', 'http://127.0.0.1:9224'), os.environ.get('WZY_ACCOUNT_LOCK'))
    busy = asyncio.Lock()
    listing = listing_browser or ListingBrowser(os.environ.get('WZY_CDP_URL', 'http://127.0.0.1:9224'),
        os.environ.get('WZY_ACCOUNT_LOCK') or Path.home() / '.cache/wzy/account.lock')
    writes_enabled = os.environ.get('WZY_LISTING_WRITES_ENABLED', '').lower() == 'true'
    raw_allowlist = os.environ.get('WZY_LISTING_ALLOWED_ITEMS')
    allowed_items = None if raw_allowlist is None else sorted(set(x.strip() for x in raw_allowlist.split(',') if x.strip()))
    if allowed_items is not None and any(not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', x) for x in allowed_items):
        raise ValueError('invalid_listing_allowlist')

    coordinator = Coordinator(store, collect, busy)

    @asynccontextmanager
    async def lifespan(app):
        with instance_lease(store.path):
            store.recover_interrupted()
            store.rebase_schedules()
            task = asyncio.create_task(coordinator.run())
            try:
                yield
            finally:
                task.cancel()
                await coordinator.close()
                await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title='WZY 只读工作台', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.coordinator = coordinator

    app.middleware('http')(auth.boundary)
    auth.register(app, STATIC)

    @app.exception_handler(ValueError)
    async def validation_error(request, exc):
        return JSONResponse({'detail': '输入不合法或匹配身份不明确'}, status_code=422)

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'index.html')

    @app.get('/api/state')
    async def state():
        return dict(store.state(), auth_enabled=bool(auth.config), listing_writes_enabled=writes_enabled, listing_allowed_items=allowed_items)

    @app.get('/api/scheduler')
    async def schedules():
        return dict(store.schedules(), busy=busy.locked())

    @app.put('/api/scheduler')
    async def pause_schedule(body: PauseInput):
        store.pause_scheduler(body.paused)
        return store.schedules()

    @app.put('/api/scheduler/sources/{store_id}')
    async def configure_schedule(store_id: int, body: ScheduleInput):
        if not any(s['id'] == store_id for s in store.shops()):
            raise HTTPException(404, '采集来源不存在')
        store.configure_schedule(store_id, **body.model_dump())
        return store.schedules()

    @app.get('/api/notifications')
    async def notifications(before_run: int | None = None):
        if before_run is not None and before_run < 1:
            raise HTTPException(422, '通知游标不合法')
        return store.inbox(before_run=before_run)

    @app.patch('/api/notifications/{event_id}')
    async def update_notification(event_id: str, body: NotificationInput):
        if len(event_id) > 128 or not store.update_notification(event_id, **body.model_dump()):
            raise HTTPException(404, '通知不存在')
        return {'ok': True}

    @app.get('/api/runs')
    async def runs():
        return store.runs()

    @app.post('/api/shops')
    async def add_shop(body: ShopInput):
        return {'id': store.add_shop(body.url)}

    @app.post('/api/collect')
    async def collection(body: CollectInput):
        source = next((s for s in store.shops() if s['id'] == body.store_id), None)
        if not source:
            raise HTTPException(404, '采集来源不存在')
        try:
            return await coordinator.collect(body.store_id)
        except RuntimeError:
            raise HTTPException(409, '已有账号任务，请等待完成') from None

    @app.get('/api/history')
    async def history(key: str):
        if len(key) > 256:
            raise HTTPException(422, '商品标识过长')
        return store.history(key)

    @app.post('/api/matches')
    async def add_match(body: MatchInput):
        store.add_match(body.own_key, body.competitor_key, confirmed=body.confirmed)
        return {'ok': True}

    @app.delete('/api/matches')
    async def remove_match(body: RemoveInput):
        store.remove_match(body.competitor_key)
        return {'ok': True}

    @app.get('/api/listings/operations')
    async def listing_operations():
        return store.listing_operations()

    @app.get('/api/listings/operations/{operation_id}')
    async def listing_operation(operation_id: str):
        operation = store.listing_operation(operation_id[:128])
        if not operation:
            raise HTTPException(404, '操作不存在')
        return operation

    @app.get('/api/listings/items/{item_id}')
    async def listing_read(item_id: str):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', item_id):
            raise HTTPException(422, '商品 ID 不合法')
        if busy.locked() or coordinator.closing:
            raise HTTPException(409, '账号任务执行中')
        async with busy:
            try:
                return await listing.read(item_id)
            except Exception:
                raise HTTPException(409, '状态读回失败，请人工检查登录、页面与账号锁') from None

    @app.post('/api/listings/state')
    async def listing_change(body: ListingInput):
        if not writes_enabled:
            raise HTTPException(403, '上下架写入未启用')
        if allowed_items is not None and body.item_id not in allowed_items:
            raise HTTPException(403, '商品不在允许写入列表')
        data = body.model_dump()
        previous = store.listing_operation(key=body.idempotency_key)
        if previous:
            if any(previous[k] != data[k] for k in ('item_id', 'desired', 'expected')):
                raise HTTPException(409, '幂等键已用于其他请求')
            return previous
        if busy.locked() or coordinator.closing:
            raise HTTPException(409, '账号任务执行中')
        async with busy:
            try:
                operation, created = store.start_listing(data)
            except ValueError:
                raise HTTPException(409, '幂等键已用于其他请求') from None
            if not created:
                return operation
            operation_id = operation['id']
            try:
                result = await listing.change(body.item_id, body.desired, body.expected,
                    lambda before: store.dispatch_listing(operation_id, before))
                store.finish_listing(operation_id, **result)
            except asyncio.CancelledError:
                store.finish_listing(operation_id, status='uncertain', error='interrupted')
                raise
            except Exception as exc:
                dispatched = store.listing_operation(operation_id)['dispatched_at'] is not None
                safe_errors = {'stale_state', 'not_found', 'duplicate_target', 'unknown_state', 'page_unstable',
                               'repeated_page', 'page_bound', 'pagination_unknown', 'pagination_changed',
                               'action_missing', 'target_changed', 'no_existing_context'}
                error = str(exc) if isinstance(exc, ListingError) and str(exc) in safe_errors else 'browser_unavailable'
                store.finish_listing(operation_id, status='uncertain' if dispatched else 'rejected', error=error,
                    before_state=exc.before_state if isinstance(exc, ListingError) else None)
            return store.listing_operation(operation_id)

    @app.api_route('/api/listings/{path:path}', methods=['POST', 'PUT', 'PATCH', 'DELETE'])
    async def writes_disabled(path: str):
        raise HTTPException(403, '仅授权受控上下架；编辑、删除、购买等功能禁用')

    @app.get('/api/export.json')
    async def export_json():
        return store.state()

    @app.get('/api/export.csv')
    async def export_csv():
        output = io.StringIO()
        fields = ['key', 'store_id', 'title', 'category', 'price', 'stock', 'status', 'observed_at', 'provisional', 'stale']
        writer = csv.writer(output)
        writer.writerow(fields)
        for p in store.state()['products']:
            writer.writerow([safe_csv(p.get(f)) for f in fields])
        return Response('﻿' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="wzy-products.csv"'})

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app


def main():
    parser = argparse.ArgumentParser(description='WZY 本机只读工作台')
    parser.add_argument('--port', type=int, default=8100)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port, access_log=False, proxy_headers=False)
