import hashlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient

from wzy.app import create_app

PUBLIC = 'https://wzy.example.invalid'


@pytest.fixture
def configured(tmp_path, monkeypatch):
    password = secrets.token_urlsafe(24)
    salt = secrets.token_bytes(16)
    config = {'username': 'synthetic-admin', 'password_hash': 'pbkdf2_sha256$600000$' + salt.hex() + '$' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000).hex(), 'session_secret': secrets.token_hex(32)}
    path = tmp_path / 'auth.json'
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    monkeypatch.setenv('WZY_AUTH_FILE', str(path))
    monkeypatch.setenv('WZY_PUBLIC_ORIGIN', PUBLIC)
    return path, config, password


def headers(origin=PUBLIC):
    return {'Origin': origin, 'X-WZY-Request': '1'}


def login(client, configured, **kwargs):
    return client.post('/api/auth/login', json={'username': configured[1]['username'], 'password': configured[2]}, headers=headers(), **kwargs)


@pytest.mark.parametrize('path', ['/', '/static/app.js', '/static/style.css', '/api/state', '/api/export.json', '/api/export.csv', '/api/scheduler', '/api/notifications', '/api/listings/operations', '/api/listings/items/synthetic', '/openapi.json', '/docs'])
def test_all_routes_protected(tmp_path, configured, path):
    app = create_app(tmp_path / 'db')
    with TestClient(app, base_url=PUBLIC, follow_redirects=False) as c:
        assert c.get(path).status_code in (401, 303, 404)
        assert c.get(path, headers={'Host': '127.0.0.1:8100'}).status_code in (401, 303, 404)
        assert c.get('/login').status_code == 200
        assert c.get('/auth/login.js').status_code == 200


def test_login_session_logout_tamper_expiry(tmp_path, configured, monkeypatch):
    app = create_app(tmp_path / 'db')
    with TestClient(app, base_url=PUBLIC) as c:
        r = login(c, configured)
        assert r.status_code == 200
        cookie = r.headers['set-cookie']
        assert all(x in cookie.lower() for x in ['httponly', 'secure', 'samesite=strict', 'path=/'])
        assert c.get('/api/state').status_code == 200
        token = c.cookies.get('wzy_session')
        c.cookies.clear()
        assert c.get('/api/state', headers={'Cookie': 'wzy_session=' + token + 'x'}).status_code == 401
        c.cookies.set('wzy_session', token)
        assert c.post('/api/auth/logout', headers=headers()).status_code == 200
        assert c.get('/api/state', headers={'Cookie': 'wzy_session=' + token}).status_code == 401
        assert login(c, configured).status_code == 200
        monkeypatch.setattr('wzy.auth.time.monotonic', lambda: 10**12)
        assert c.get('/api/state').status_code == 401


def test_origin_host_and_forwarding(tmp_path, configured):
    with TestClient(create_app(tmp_path / 'db'), base_url=PUBLIC) as c:
        for h in [{}, headers('https://evil.invalid'), headers('http://wzy.example.invalid'), dict(headers(), **{'Sec-Fetch-Site': 'cross-site'})]:
            assert c.post('/api/auth/login', json={}, headers=h).status_code == 403
        for host in ['evil.invalid', 'wzy.example.invalid.evil', 'wzy.example.invalid:443', 'user@wzy.example.invalid', '127.0.0.1/path']:
            assert c.get('/login', headers={'Host': host, 'X-Forwarded-Host': 'wzy.example.invalid', 'X-Forwarded-Proto': 'https'}).status_code == 400
        assert login(c, configured).status_code == 200
        assert c.put('/api/scheduler', json={'paused': True}).status_code == 403
        assert c.put('/api/scheduler', json={'paused': True}, headers=headers()).status_code == 200
        assert c.put('/api/scheduler', json={'paused': False}, headers=dict(headers(), Host='127.0.0.1:8100')).status_code == 403
        assert c.put('/api/scheduler', json={'paused': False}, headers=dict(headers('http://127.0.0.1:8100'), Host='127.0.0.1:8100')).status_code == 200


def test_generic_errors_limits_and_no_secret_echo(tmp_path, configured, caplog):
    app = create_app(tmp_path / 'db')
    with TestClient(app, base_url=PUBLIC, client=('192.0.2.1', 1000)) as c:
        responses = []
        for i in range(8):
            r = c.post('/api/auth/login', json={'username': 'absent' if i % 2 else configured[1]['username'], 'password': 'synthetic-wrong'}, headers=dict(headers(), **{'X-Forwarded-For': f'192.0.2.{i+10}'}))
            responses.append(r)
        assert responses[0].status_code == 401
        assert responses[0].json() == responses[1].json()
        assert responses[-1].status_code == 429
        assert 'retry-after' in responses[-1].headers
        assert configured[2] not in caplog.text
        assert configured[1]['session_secret'] not in caplog.text
    with TestClient(app, base_url=PUBLIC, client=('192.0.2.2', 1000)) as c:
        assert login(c, configured).status_code == 200


@pytest.mark.parametrize('origin', ['http://wzy.example.invalid', 'https://wzy.example.invalid/', 'https://u@wzy.example.invalid', 'https://wzy.example.invalid?q=x', 'https://*.example.invalid', 'https://wzy.example.invalid#x'])
def test_bad_origin_fails_before_db(tmp_path, configured, monkeypatch, origin):
    monkeypatch.setenv('WZY_PUBLIC_ORIGIN', origin)
    db = tmp_path / 'never-created'
    with pytest.raises(ValueError, match='invalid_auth_config'):
        create_app(db)
    assert not db.exists()


@pytest.mark.parametrize('fault', ['missing', 'permissions', 'symlink', 'json', 'hash', 'secret', 'username', 'no_auth'])
def test_bad_config_fails_closed(tmp_path, configured, monkeypatch, fault):
    path, data, _ = configured
    if fault == 'missing':
        path.unlink()
    elif fault == 'permissions':
        path.chmod(0o644)
    elif fault == 'symlink':
        other = tmp_path / 'other'
        path.rename(other)
        path.symlink_to(other)
    elif fault == 'no_auth':
        monkeypatch.delenv('WZY_AUTH_FILE')
    else:
        if fault == 'hash': data['password_hash'] = 'plaintext'
        if fault == 'secret': data['session_secret'] = 'short'
        if fault == 'username': data['username'] = ''
        path.write_text('not-json' if fault == 'json' else json.dumps(data))
    db = tmp_path / 'never-created'
    with pytest.raises(ValueError, match='invalid_auth_config'):
        create_app(db)
    assert not db.exists()


def test_local_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv('WZY_AUTH_FILE', raising=False)
    monkeypatch.delenv('WZY_PUBLIC_ORIGIN', raising=False)
    with TestClient(create_app(tmp_path / 'db'), base_url='http://127.0.0.1:8100') as c:
        assert c.get('/api/state').status_code == 200
        assert c.get('/api/state', headers={'Host': 'wzy.example.invalid'}).status_code == 400
        assert c.get('/openapi.json').status_code == 404


def test_password_helper_private_output_and_no_overwrite(tmp_path):
    import subprocess
    import sys
    path = tmp_path / 'created.json'
    password = secrets.token_urlsafe(24)
    command = [sys.executable, '-m', 'wzy.auth', 'set-password', '--file', str(path), '--username', 'synthetic', '--stdin']
    result = subprocess.run(command, input=password + '\n', text=True, capture_output=True)
    assert result.returncode == 0
    assert password not in result.stdout + result.stderr + path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    data = json.loads(path.read_text())
    _, rounds, salt, digest = data['password_hash'].split('$')
    assert hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex() == digest
    before = path.read_bytes()
    result = subprocess.run(command, input=password + '\n', text=True, capture_output=True)
    assert result.returncode != 0
    assert path.read_bytes() == before
    assert password not in result.stdout + result.stderr
    command[command.index(str(path))] = str(tmp_path / 'weak')
    assert subprocess.run(command, input='short\n', text=True, capture_output=True).returncode != 0
    assert not (tmp_path / 'weak').exists()


def test_auth_local_only_and_restart_invalidates_sessions(tmp_path, configured, monkeypatch):
    monkeypatch.delenv('WZY_PUBLIC_ORIGIN')
    base = 'http://127.0.0.1:8100'
    with TestClient(create_app(tmp_path / 'db'), base_url=base) as c:
        assert c.get('/api/state').status_code == 401
        r = c.post('/api/auth/login', json={'username': configured[1]['username'], 'password': configured[2]}, headers=headers(base))
        assert r.status_code == 200
        assert 'secure' not in r.headers['set-cookie'].lower()
        token = c.cookies.get('wzy_session')
        assert c.get('/api/state').status_code == 200
    with TestClient(create_app(tmp_path / 'db'), base_url=base) as c:
        assert c.get('/api/state', headers={'Cookie': 'wzy_session=' + token}).status_code == 401


@pytest.mark.parametrize('body', [[], None, {'username': 'synthetic', 'password': 123}, {'username': '<img>', 'password': 'x' * 5000}])
def test_malformed_login_generic_no_input_echo(tmp_path, configured, body):
    with TestClient(create_app(tmp_path / 'db'), base_url=PUBLIC) as c:
        r = c.post('/api/auth/login', content=json.dumps(body), headers=headers())
        assert r.status_code == 401
        assert 'synthetic' not in r.text and '<img>' not in r.text
        assert r.headers['cache-control'] == 'no-store'


def test_all_business_mutations_require_session(tmp_path, configured):
    with TestClient(create_app(tmp_path / 'db'), base_url=PUBLIC) as c:
        for method, path in [('POST', '/api/collect'), ('POST', '/api/shops'), ('POST', '/api/matches'), ('DELETE', '/api/matches'), ('PUT', '/api/scheduler'), ('PUT', '/api/scheduler/sources/2'), ('PATCH', '/api/notifications/x'), ('POST', '/api/listings/state'), ('POST', '/api/auth/logout')]:
            assert c.request(method, path, json={}, headers=headers()).status_code == 401


def test_limiter_bounded_and_recovers(tmp_path, configured, monkeypatch):
    from wzy.auth import Auth
    from starlette.requests import Request
    auth = Auth()
    monkeypatch.setattr('wzy.auth.time.monotonic', lambda: 1000)
    auth.peers = {str(i): (900, 5) for i in range(1024)}
    auth.sessions = {'expired': 900}
    auth.prune()
    assert not auth.peers and not auth.sessions


def test_cli_disables_proxy_peer_rewriting(monkeypatch, tmp_path):
    import sys
    import uvicorn
    from wzy.app import main
    monkeypatch.setenv('WZY_DB', str(tmp_path / 'db'))
    monkeypatch.setattr(sys, 'argv', ['wzy', '--port', '18100'])
    called = {}
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: called.update(kwargs))
    main()
    assert called['proxy_headers'] is False
    assert called['access_log'] is False
    assert called['host'] == '127.0.0.1'


def test_authenticated_business_operations_keep_existing_guards(tmp_path, configured, monkeypatch):
    from wzy.domain import Result
    async def collect(source):
        return Result([], status='complete', declared_count=0)
    monkeypatch.setenv('WZY_LISTING_WRITES_ENABLED', 'false')
    with TestClient(create_app(tmp_path / 'db', collector=collect), base_url=PUBLIC) as c:
        assert login(c, configured).status_code == 200
        for path in ['/static/app.js', '/static/style.css', '/api/export.csv', '/api/export.json', '/api/listings/operations', '/api/notifications', '/api/runs', '/api/scheduler']:
            assert c.get(path).status_code == 200
        assert c.get('/openapi.json').status_code == 404
        assert c.post('/api/collect', json={'store_id': 2}, headers=headers()).status_code == 200
        assert c.post('/api/listings/state', json={'item_id': 'synthetic', 'desired': 'up', 'expected': 'down', 'confirmed': True, 'idempotency_key': 'synthetic-auth'}, headers=headers()).status_code == 403
        assert c.post('/api/listings/delete', json={}, headers=headers()).status_code == 403


def test_transport_rate_limit_window_recovers_and_live_sessions_unaffected(tmp_path, configured, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr('wzy.auth.time.monotonic', lambda: now[0])
    with TestClient(create_app(tmp_path / 'db'), base_url=PUBLIC) as c:
        assert login(c, configured).status_code == 200
        for _ in range(4):
            assert c.post('/api/auth/login', json={'username': 'synthetic', 'password': 'wrong'}, headers=headers()).status_code == 401
        assert login(c, configured).status_code == 429
        assert c.get('/api/state').status_code == 200
        now[0] += 61
        assert login(c, configured).status_code == 200


def test_duplicate_security_headers_rejected(tmp_path, configured):
    with TestClient(create_app(tmp_path / 'db'), base_url=PUBLIC) as c:
        assert c.get('/login', headers=[('Host', 'wzy.example.invalid'), ('Host', '127.0.0.1')]).status_code == 400
        assert c.post('/api/auth/login', json={}, headers=[('Origin', PUBLIC), ('Origin', PUBLIC), ('X-WZY-Request', '1')]).status_code == 403


async def test_slow_login_bodies_cannot_overbook_hash_capacity(tmp_path, configured):
    import asyncio
    import httpx
    app = create_app(tmp_path / 'db')
    release = asyncio.Event()
    entered = 0
    ready = asyncio.Event()
    async def body():
        nonlocal entered
        entered += 1
        if entered == 2:
            ready.set()
        yield b'{'
        await release.wait()
        yield b'"username":"synthetic","password":"wrong"}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as c:
        tasks = [asyncio.create_task(c.post('/api/auth/login', content=body(), headers=headers())) for _ in range(2)]
        try:
            await asyncio.wait_for(ready.wait(), 2)
            response = await c.post('/api/auth/login', json={'username': 'synthetic', 'password': 'wrong'}, headers=headers())
            assert response.status_code == 429
        finally:
            release.set()
            await asyncio.gather(*tasks)


async def test_login_body_deadline_releases_capacity(tmp_path, configured):
    import asyncio
    import httpx
    async def stalled():
        yield b'{'
        await asyncio.sleep(30)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path / 'db')), base_url=PUBLIC) as c:
        response = await asyncio.wait_for(c.post('/api/auth/login', content=stalled(), headers=headers()), 15)
        assert response.status_code == 408
        response = await c.post('/api/auth/login', json={'username': configured[1]['username'], 'password': configured[2]}, headers=headers())
        assert response.status_code == 200


def test_invalid_unicode_login_is_generic(tmp_path, configured):
    with TestClient(create_app(tmp_path / 'db'), base_url=PUBLIC) as c:
        response = c.post('/api/auth/login', content=b'{"username":"synthetic","password":"\\ud800"}', headers=headers())
        assert response.status_code == 401
        assert 'synthetic' not in response.text
