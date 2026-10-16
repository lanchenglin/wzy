import argparse
import asyncio
from contextlib import asynccontextmanager
import csv
import io
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .collector import BrowserCollector
from .domain import Result
from .store import Store

STATIC = Path(__file__).parent / 'static'


class ShopInput(BaseModel):
    url: str = Field(max_length=160)


class CollectInput(BaseModel):
    store_id: int = Field(gt=0)


class MatchInput(BaseModel):
    own_key: str = Field(max_length=256)
    competitor_key: str = Field(max_length=256)
    confirmed: bool = False


class RemoveInput(BaseModel):
    competitor_key: str = Field(max_length=256)


def safe_csv(value):
    value = str(value if value is not None else '')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


def create_app(db_path=None, *, collector=None):
    store = Store(db_path or os.environ.get('WZY_DB', 'data/wzy.sqlite3'))
    collect = collector or BrowserCollector(os.environ.get('WZY_CDP_URL', 'http://127.0.0.1:9224'), os.environ.get('WZY_ACCOUNT_LOCK'))
    busy = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        store.recover_interrupted()
        yield

    app = FastAPI(title='WZY 只读工作台', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        host = request.headers.get('host', '')
        try:
            parsed = urlsplit('http://' + host)
            valid = parsed.hostname in {'127.0.0.1', 'localhost', '::1'} and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid:
            return JSONResponse({'detail': '仅允许本机访问'}, status_code=400)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if origin != f'http://{host}' or request.headers.get('x-wzy-request') != '1' or request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': '本地同源校验失败'}, status_code=403)
        response = await call_next(request)
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ValueError)
    async def validation_error(request, exc):
        return JSONResponse({'detail': '输入不合法或匹配身份不明确'}, status_code=422)

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'index.html')

    @app.get('/api/state')
    async def state():
        return store.state()

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
        if busy.locked():
            raise HTTPException(409, '已有采集任务，请等待完成')
        async with busy:
            run_id = store.start_run(source['id'])
            try:
                result = await collect(source)
            except asyncio.CancelledError:
                store.finish_run(run_id, Result(status='failed', errors=['interrupted']))
                raise
            except Exception:
                result = Result(status='failed', errors=['collection_failed'])
            store.finish_run(run_id, result)
            return {'run_id': run_id, 'status': result.status, 'errors': result.errors}

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

    @app.api_route('/api/listings/{path:path}', methods=['POST', 'PUT', 'PATCH', 'DELETE'])
    async def writes_disabled(path: str):
        raise HTTPException(403, '第二阶段未授权：商品上架、编辑、删除功能禁用')

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
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port, access_log=False)
