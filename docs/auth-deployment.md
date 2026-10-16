# 可选认证与 HTTPS 公网入口

默认仍仅本机使用。**只有有效认证配置与明确 HTTPS origin 同时启用时，才允许公网入口。** 本文命令供人类/父操作会话私下执行；编码会话未执行部署、未读取任何真实凭据、未触碰运行中的 8100 服务或 Cloudflare。

## 配置与私下创建密码

不把密码发给 AI，不放入命令行、shell 历史、Git、聊天或日志。此密码仅用于 WZY，不能复用平台密码。没有默认密码。用户名由操作方选择，当前部署意图为 `admin`。

在仓库根目录，操作方执行：

```bash
umask 077
install -d -m 700 "$HOME/.config/wzy"
.venv/bin/python -m wzy.auth set-password \
  --file "$HOME/.config/wzy/auth.json" --username admin
```

终端交互使用 getpass 输入两次，至少 12、最多 1024 字符；建议密码管理器生成足够长的独立随机密码。辅助命令只生成随机盐的 PBKDF2-HMAC-SHA256（600000 次）摘要与独立随机 256-bit 会话签名密钥，不输出任何秘密。文件以 0600 排他创建，**已有文件或符号链接不会覆盖**。可选 `--stdin` 仅供可信秘密管理器通过管道输入一行；不要 `echo 明文密码`，不要 shell tracing。没有 `--password` 参数。

环境变量：

| 变量 | 含义 |
|---|---|
| `WZY_AUTH_FILE` | 私有 JSON 文件绝对路径。启用认证；文件须为当前运行用户所有的普通文件，无组/其他人权限，不能为符号链接，最大 4096 bytes。父目录也应私有。 |
| `WZY_PUBLIC_ORIGIN` | 精确 HTTPS origin，例如 `https://wzy.blueforestfuture.com`。不允许路径（包括末尾 `/`）、query、fragment、userinfo、通配符或 HTTP。必须同时有有效认证。 |

两个变量都不设置时保留原本机免登录模式；仅设置 AUTH_FILE 时为本机认证模式。变量存在但为空、文件缺失/权限不安全/格式不正确或 public 无认证均在数据库初始化前拒绝启动，错误仅 `invalid_auth_config`。不会自动生成密码或降级为免登录。

## 私有环境文件与 systemd user 示例（非自动部署）

人类审查路径后，创建 `$HOME/.config/wzy/service.env`（0600），用绝对路径替换 `/home/USER` 与 `/path/to/wzy`；systemd EnvironmentFile 不展开 `$HOME`：

```ini
WZY_AUTH_FILE=/home/USER/.config/wzy/auth.json
WZY_PUBLIC_ORIGIN=https://wzy.blueforestfuture.com
WZY_DB=/path/to/wzy/data/wzy.sqlite3
WZY_CDP_URL=http://127.0.0.1:9224
WZY_ACCOUNT_LOCK=/home/USER/.cache/wzy/account.lock
WZY_LISTING_WRITES_ENABLED=false
```

示例故意禁用上下架。部署不得无意沿用/修改父服务的现有写开关、白名单或调度设置；实际授权由操作方单独决定。不会自动读取 `.env`。

可供审查的新 user unit `$HOME/.config/systemd/user/wzy-auth.service`：

```ini
[Unit]
Description=WZY authenticated workbench
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/wzy
EnvironmentFile=%h/.config/wzy/service.env
ExecStart=/path/to/wzy/.venv/bin/wzy --port 8100
UMask=0077
NoNewPrivileges=true
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

**不要直接启动第二个进程或覆盖现有 unit。** 父会话先安排旧进程退出与同库迁移；旧服务不一定支持实例锁。确认端口和库已独占后，操作方才能选择执行：

```bash
chmod 600 "$HOME/.config/wzy/service.env"
systemctl --user daemon-reload
systemctl --user enable --now wzy-auth.service
```

这些是待执行示例，不是本轮已执行操作。认证不修改持久调度开关；已有启用的调度会按既有语义继续，不依赖浏览器里是否有已登录的工作台会话。退出工作台登录不暂停调度，也不撤销已派发的商品操作。

## Tunnel / 反向代理契约

- Web 仍只绑定 `127.0.0.1`；tunnel 仅转发到此 Web 端口。**不得转发 CDP、浏览器 profile 或其他服务。** 不新增公网监听参数。
- 外部只允许 HTTPS，HTTP 在边缘拒绝或重定向；公网 Host 原样保留为配置 origin 的 authority。该配置不包含端口时，显式 `:443` Host 也不会匹配。
- 不把公网 Host 重写为 loopback；不依赖 `X-Forwarded-Host` / `X-Forwarded-Proto` / `Forwarded`。入口 CLI 显式 `proxy_headers=False`，不得通过另一个 uvicorn 启动方式恢复自动信任 XFF。
- 应用不可能从 loopback HTTP 本身证明外部 TLS；TLS、HTTP 拒绝、Host 保留是代理契约，必须由父操作会话验收。应用不修改 Cloudflare。
- 请求和响应体、Cookie、Authorization、密码、认证文件内容都不要记录在代理日志中；不要启用调试转储。应用关闭访问日志，无认证载荷日志；错误不回显密码/用户名输入。不要在 URL 中携带凭据。
- 同一 transport peer 每 60 秒最多 5 次登录尝试（包括成功）；最多 1024 个活动 peer，满时拒绝新 peer，过期回收；最多 2 个处理中登录（含读取请求体与哈希计算），请求体读取最多 10 秒；断连取消不会提前释放仍在运行的哈希槽位。没有按用户名的持久锁定，一个远程 peer 不能锁住其他直接 peer 或已有有效会话。
- **Tunnel 后所有外部用户通常共享一个 loopback peer 的额度。** 不信任可伪造 XFF 来绕过限制；攻击流量可能使同一代理入口暂时无法新登录。父操作方应在边缘设置独立来源限流/访问控制，应用不宣称解决分布式攻击或入口可用性。已登录会话不受登录限流影响；限流无自动延长锁定，窗口到期恢复。

## 会话、API 与 CSRF

- 未登录仅公开 `/login`、`/auth/login.js`、`POST /api/auth/login`。业务首页跳转登录；所有业务 API、静态文件、导出（包括 loopback 请求）返回 401。无公共业务 health 信息。docs/redoc/openapi 全部关闭。
- 登录 JSON 为 `username` / `password`，错误统一，最大请求体 4096 bytes。密码校验与用户名比较均常量时间比较摘要/字节，未知用户名也计算密码摘要。
- `wzy_session` 为随机 ID + HMAC-SHA256 签名，服务端保存活跃 ID。HttpOnly、SameSite=Strict、Path=/、无 Domain；公网模式始终 Secure（不依赖代理声称的 scheme）。无 query token/localStorage/sessionStorage。
- 会话固定 8 小时失效，不滑动续期；最多 128 个，超出移除最旧。重新登录更换当前会话；POST logout 服务端撤销并删除 Cookie。重启/换密码后所有旧会话无效。不支持多 worker，多用户权限或持久会话。
- 公网配置下 loopback 也要求认证，Cookie 仍 Secure；不要为使 HTTP localhost 浏览器方便而关闭 Secure。正常操作走 HTTPS 公网入口。本机认证但无 public origin 时可用 loopback HTTP Cookie。
- **所有修改请求，包括 login/logout，都须 `Origin` 精确等于当前 Host 的允许 origin，且 `X-WZY-Request: 1`。** 公网为配置 HTTPS origin，loopback 为 `http://<Host>`（含端口）；拒绝跨站 Fetch Metadata。无 CORS。认证不代替 CSRF，不允许 public Origin 配 loopback Host。
- 中文登录页使用同源 fetch；浏览器自动发送 Origin。401 时业务 UI 清空并回到登录；退出成功清空业务界面。浏览器后退缓存/已下载导出不是服务器可撤销的文件，操作方应使用可信终端。
- README/Skill 旧 curl 示例仅适用于默认本机免登录；启用认证后必须先通过受保护登录取得 Cookie 并随请求发送。不要把 Cookie 发给 AI，或放进可共享命令/文件；推荐通过网页操作。无认证旁路、URL token、API key 替代接口。

密码轮换：用 helper 创建另一个私有文件（例如 `auth-next.json`），私下更新 EnvironmentFile 路径，由父操作方安排服务重启，再按自己的安全保留策略处理旧文件。helper 不盲目覆盖已有秘密。丢失密码只能由有本机文件权限的人重新配置；没有在线重置/恢复后门。

## 离线验证与独立部署验收

```bash
.venv/bin/python -m pytest -q
npm test
node --check src/wzy/static/app.js
node --check src/wzy/static/login.js
.venv/bin/python -m compileall -q src/wzy
.venv/bin/python -m pip check
git diff --check
```

测试只用随机合成凭据、临时文件/SQLite、TestClient 和 jsdom，不连接真实站点/CDP。部署后由父会话分别验证 HTTPS/HTTP、Host 保留、未登录业务/导出拒绝、正确登录、错误登录限流、跨源修改拒绝、退出后旧会话失效、重启失效。只查看本地业务数据完成认证验收，不因认证测试触发采集或真实上下架。真实浏览器、边缘 TLS/缓存策略、Cloudflare 和 systemd 本轮未验收。
