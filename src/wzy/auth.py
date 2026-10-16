"""Optional single-operator authentication; no browser/platform credentials."""
import argparse
import asyncio
from collections import OrderedDict
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from urllib.parse import urlsplit

from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

COOKIE = 'wzy_session'
TTL = 8 * 60 * 60
ITERATIONS = 600000
MAX_PEERS = 1024
MAX_SESSIONS = 128
PUBLIC_PATHS = {'/login', '/auth/login.js', '/api/auth/login'}


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}'


def read_config(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 4096:
            raise ValueError('invalid_auth_config')
        data = json.load(stream)
    if set(data) != {'username', 'password_hash', 'session_secret'}:
        raise ValueError('invalid_auth_config')
    if not isinstance(data['username'], str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,64}', data['username']):
        raise ValueError('invalid_auth_config')
    if not isinstance(data['password_hash'], str) or not re.fullmatch(r'pbkdf2_sha256\$600000\$[a-f0-9]{32}\$[a-f0-9]{64}', data['password_hash']):
        raise ValueError('invalid_auth_config')
    if not isinstance(data['session_secret'], str) or not re.fullmatch(r'[a-f0-9]{64}', data['session_secret']):
        raise ValueError('invalid_auth_config')
    return data


class Auth:
    def __init__(self):
        self.origin = os.environ.get('WZY_PUBLIC_ORIGIN')
        path = os.environ.get('WZY_AUTH_FILE')
        self.config = None
        try:
            if self.origin is not None:
                parsed = urlsplit(self.origin)
                if not re.fullmatch(r'https://[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[1-9][0-9]{0,4})?', self.origin) or not parsed.hostname or parsed.port == 0:
                    raise ValueError()
                _ = parsed.port
            if path is not None:
                if not path or not Path(path).is_absolute():
                    raise ValueError()
                self.config = read_config(path)
            if self.origin is not None and self.config is None:
                raise ValueError()
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError('invalid_auth_config') from None
        self.sessions = OrderedDict()
        self.peers = {}
        self.hashing = 0

    def expected_origin(self, request):
        hosts = request.headers.getlist('host')
        if len(hosts) != 1:
            return None
        host = hosts[0]
        if self.origin and host == self.origin[len('https://'):]:
            return self.origin
        if re.fullmatch(r'(?:127\.0\.0\.1|localhost|\[::1\])(?::[1-9][0-9]{0,4})?', host):
            try:
                _ = urlsplit('http://' + host).port
                return 'http://' + host
            except ValueError:
                pass
        return None

    def prune(self):
        now = time.monotonic()
        self.sessions = OrderedDict((key, expiry) for key, expiry in self.sessions.items() if expiry > now)
        self.peers = {key: value for key, value in self.peers.items() if value[0] > now}

    def signature(self, sid):
        return hmac.new(bytes.fromhex(self.config['session_secret']), sid.encode(), 'sha256').hexdigest()

    def session(self, request):
        token = request.cookies.get(COOKIE, '')
        if not re.fullmatch(r'[a-f0-9]{64}\.[a-f0-9]{64}', token):
            return None
        sid, signature = token.split('.')
        if not hmac.compare_digest(signature, self.signature(sid)):
            return None
        if self.sessions.get(sid, 0) <= time.monotonic():
            self.sessions.pop(sid, None)
            return None
        return sid

    async def boundary(self, request, call_next):
        origin = self.expected_origin(request)
        if origin is None:
            response = JSONResponse({'detail': '访问主机不合法'}, status_code=400)
        elif request.method not in {'GET', 'HEAD', 'OPTIONS'} and (
            request.headers.getlist('origin') != [origin] or request.headers.getlist('x-wzy-request') != ['1'] or request.headers.get('sec-fetch-site') == 'cross-site'
        ):
            response = JSONResponse({'detail': '同源校验失败'}, status_code=403)
        elif self.config and request.url.path not in PUBLIC_PATHS and not self.session(request):
            response = RedirectResponse('/login', status_code=303) if request.url.path == '/' and request.method in {'GET', 'HEAD'} else JSONResponse({'detail': '请先登录'}, status_code=401)
        else:
            response = await call_next(request)
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    def release_slot(self):
        self.hashing -= 1

    def register(self, app, static):
        from fastapi import Request

        @app.get('/login')
        async def login_page():
            return FileResponse(static / 'login.html') if self.config else RedirectResponse('/', status_code=303)

        @app.get('/auth/login.js')
        async def login_script():
            return FileResponse(static / 'login.js', media_type='text/javascript')

        @app.post('/api/auth/login')
        async def login(request: Request):
            error = {'detail': '登录失败，请检查凭据或稍后重试'}
            if not self.config:
                return JSONResponse(error, status_code=404)
            self.prune()
            # Use only the transport peer. CLI disables uvicorn proxy-header rewriting.
            peer = request.client.host if request.client else 'unknown'
            now = time.monotonic()
            deadline, count = self.peers.get(peer, (now + 60, 0))
            if count >= 5 or (peer not in self.peers and len(self.peers) >= MAX_PEERS) or self.hashing >= 2:
                return JSONResponse(error, status_code=429, headers={'Retry-After': '60'})
            self.peers[peer] = (deadline, count + 1)
            self.hashing += 1
            worker = None
            try:
                body = bytearray()
                async with asyncio.timeout(10):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > 4096:
                            return JSONResponse(error, status_code=401)
                        body.extend(chunk)
                try:
                    data = json.loads(body)
                    if not isinstance(data, dict) or set(data) != {'username', 'password'} or not isinstance(data['username'], str) or not isinstance(data['password'], str) or len(data['username']) > 64 or not 1 <= len(data['password']) <= 1024:
                        raise ValueError()
                    password = data['password'].encode()
                    username = data['username'].encode()
                except (ValueError, TypeError, UnicodeError):
                    return JSONResponse(error, status_code=401)
                _, _, salt, expected = self.config['password_hash'].split('$')
                # Keep the slot until the worker really finishes, even if the client leaves.
                worker = asyncio.create_task(asyncio.to_thread(hashlib.pbkdf2_hmac, 'sha256', password, bytes.fromhex(salt), ITERATIONS))
                worker.add_done_callback(lambda _: self.release_slot())
                digest = await asyncio.shield(worker)
            except TimeoutError:
                return JSONResponse(error, status_code=408)
            finally:
                if worker is None:
                    self.release_slot()
            valid_password = hmac.compare_digest(digest.hex(), expected)
            valid_username = hmac.compare_digest(username, self.config['username'].encode())
            if not (valid_password and valid_username):
                return JSONResponse(error, status_code=401)
            old = self.session(request)
            if old:
                self.sessions.pop(old, None)
            sid = secrets.token_hex(32)
            self.sessions[sid] = time.monotonic() + TTL
            while len(self.sessions) > MAX_SESSIONS:
                self.sessions.popitem(last=False)
            response = JSONResponse({'ok': True})
            response.set_cookie(COOKIE, sid + '.' + self.signature(sid), max_age=TTL, httponly=True, secure=bool(self.origin) or request.url.scheme == 'https', samesite='strict', path='/')
            return response

        @app.post('/api/auth/logout')
        async def logout(request: Request):
            if self.config:
                self.sessions.pop(self.session(request), None)
            response = JSONResponse({'ok': True})
            response.delete_cookie(COOKIE, path='/', secure=bool(self.origin) or request.url.scheme == 'https', httponly=True, samesite='strict')
            return response


def main():
    parser = argparse.ArgumentParser(description='创建私有 WZY 认证文件；不接收命令行密码')
    parser.add_argument('command', choices=['set-password'])
    parser.add_argument('--file', required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--stdin', action='store_true', help='从标准输入读取一行密码（不要使用 shell 明文 echo）')
    args = parser.parse_args()
    try:
        path = Path(args.file)
        if not path.is_absolute() or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,64}', args.username):
            raise ValueError()
        if args.stdin:
            password = sys.stdin.readline(1026).rstrip('\r\n')
        else:
            password = getpass.getpass('新密码（至少 12 字符）：')
            if password != getpass.getpass('再次输入：'):
                raise ValueError()
        if not 12 <= len(password) <= 1024:
            raise ValueError()
        data = {'username': args.username, 'password_hash': password_hash(password), 'session_secret': secrets.token_hex(32)}
        # Never overwrite an existing file (including symlinks). Rotate via a new path.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError, EOFError):
        parser.exit(1, '认证文件创建失败；检查输入、路径及权限，已有文件不会覆盖。\n')
    print('认证文件已创建；请私下配置路径并由操作会话安排重启。')


if __name__ == '__main__':
    main()
