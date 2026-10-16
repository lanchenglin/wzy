from fastapi.testclient import TestClient
import pytest
from wzy.app import create_app, safe_csv
from wzy.domain import Product, Result


@pytest.fixture
def client(tmp_path):
    async def collect(source):
        return Result(products=[Product('shop:x', '<img src=x onerror=alert(1)>', price='1.00')], status='complete', declared_count=1)
    with TestClient(create_app(tmp_path / 'db.sqlite', collector=collect), base_url='http://127.0.0.1:8100') as c:
        yield c


def headers():
    return {'Origin': 'http://127.0.0.1:8100', 'X-WZY-Request': '1'}


def test_host_origin_and_write_boundaries(client):
    assert client.get('/api/state', headers={'Host': 'evil.com'}).status_code == 400
    for h in [{}, {'Origin': 'http://evil.com', 'X-WZY-Request': '1'}, {'Origin': 'http://127.0.0.1:9999', 'X-WZY-Request': '1'}]:
        assert client.post('/api/shops', json={'url': 'https://wzyp.cn/shop/abc'}, headers=h).status_code == 403
    assert client.post('/api/shops', json={'url': 'https://evil.com'}, headers=headers()).status_code == 422
    assert client.post('/api/shops', json={'url': 'https://wzyp.cn/shop/abc'}, headers=headers()).status_code == 200
    assert client.post('/api/listings/publish', json={}, headers=headers()).status_code == 403
    assert client.get('/api/state').json()['listing_writes_enabled'] is False


def test_collect_persist_history_export_and_static_xss(client):
    assert client.post('/api/collect', json={'store_id': 999}, headers=headers()).status_code == 404
    response = client.post('/api/collect', json={'store_id': 2}, headers=headers())
    assert response.status_code == 200
    data = client.get('/api/state').json()
    assert data['products'][0]['title'].startswith('<img')
    assert client.get('/api/history', params={'key': 'shop:x'}).json()[0]['price'] == '1.00'
    assert client.get('/api/export.json').json()['products']
    assert 'text/csv' in client.get('/api/export.csv').headers['content-type']
    html = client.get('/')
    assert '<img src=x' not in html.text
    assert "default-src 'self'" in html.headers['content-security-policy']
    js = client.get('/static/app.js').text
    assert 'textContent' in js
    assert 'innerHTML' not in js


def test_match_api(client):
    db = client.app.state.store
    for store_id, p in [(1, Product('own:a', '自有', price='1.20')), (2, Product('shop:a', '竞品', price='1.10'))]:
        run = db.start_run(store_id)
        db.finish_run(run, Result(products=[p], status='complete'))
    body = {'own_key': 'own:a', 'competitor_key': 'shop:a', 'confirmed': True}
    assert client.post('/api/matches', json=body, headers=headers()).status_code == 200
    assert client.get('/api/state').json()['comparisons'][0]['difference'] == '0.10'
    assert client.request('DELETE', '/api/matches', json={'competitor_key': 'shop:a'}, headers=headers()).status_code == 200
    assert not client.get('/api/state').json()['comparisons']


@pytest.mark.parametrize('text', ['=1+1', '+1', '-1', '@SUM(A1)', '\t=1', '\n=1', '  =1'])
def test_csv_formula_safety(text):
    assert safe_csv(text).startswith("'")
