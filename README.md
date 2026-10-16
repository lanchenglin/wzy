# WZY 商品核对台

独立、中文、本地优先的商品只读工作台。第一阶段支持竞品全分类采集、自有库存分页采集、SQLite 价格历史、人工确认匹配与 JSON/CSV 导出。**不支持、也不授权商品上架、下架、编辑、删除或购买。** 没有假定的平台写入 API。

## 安装和启动（Linux / WSL，Python 3.11+）

```bash
cd /path/to/wzy
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/wzy --port 8100
```

打开 <http://127.0.0.1:8100>。默认仅绑定 `127.0.0.1`，不要通过反向代理或公网监听暴露。端口被占用时会启动失败，可选择 `--port 8101`。不需要 `playwright install`：应用只连接已经运行的专用浏览器，不启动浏览器。

### 本机配置

使用进程环境变量，不需要 `.env` 文件，也不会自动读取 `.env`：

```bash
WZY_CDP_URL=http://127.0.0.1:9224 \
WZY_DB=data/wzy.sqlite3 \
.venv/bin/wzy --port 8100
```

- `WZY_CDP_URL`：仅允许显式 loopback HTTP + 端口；默认 `http://127.0.0.1:9224`。
- `WZY_DB`：默认工作目录下 `data/wzy.sqlite3`；请从仓库根目录启动。
- `WZY_ACCOUNT_LOCK`：默认 `~/.cache/wzy/account.lock`。同账号所有 WZY 实例和其他 AI 自动化应使用同一路径/同一互斥约定；不要同时操作账号。
- 仅启动一个 Web 服务进程；不使用多 worker。重启会将未完成运行标为失败。

现有本机专用可见 Chromium 的持久目录是 `/home/lan/dianp/wzyweb`，**它只是环境说明，不是应用读取路径**。请由人类在现有浏览器内登录；应用不读取/复制 profile，不导入导出 Cookie，不访问 storage、账号详情、订单或客户数据。编码阶段未访问此浏览器。

## 操作流程

1. 人类确认专用浏览器已运行、已登录，CDP 仅监听本机。
2. 启动工作台；默认来源为自有库存与竞品 `https://wzyp.cn/shop/85G05YIY`（AI杂货铺）。默认只有配置，没有伪造商品。
3. 对每个来源点击「只读采集」。会新建任务标签页，结束只关闭该页，不关闭原有页、context 或浏览器。
4. 查看声明数量、实际数量、采集状态和错误码；遇到登录/挑战或分页结构不明，人工检查后再主动采集，不自动重试。
5. 在「全部商品」核对所有分类（包括 PPO）、价格、库存与状态。人工选择两侧商品并勾选确认，保存或移除本地匹配。
6. 点击「历史」查看每次观察，或导出 JSON/CSV。CSV 文本防公式注入。

## 数据语义与限制

- `complete` 只代表**列表**已稳定、数量与页面声明一致且无解析错误；不代表所有 SKU/变体详情已覆盖。`variant_coverage` 始终为 `unknown`，直到独立观察后再扩展。
- `partial`：计数不符、身份冲突、滚动/分页上限、重复页、未知分页等；`unknown`：有商品但缺少足够的列表完整性证据；`paused`：登录/验证需人工处理；`failed`：无可信商品证据或连接失败。
- 默认最多 15 次滚动、50 页、单次采集 180 秒；库存每页就绪最多 30 次、350ms 间隔，商品字段与分页证据连续 4 次一致才视为稳定，忽略无关 HTML 属性波动。空白/未确认空表/加载中不视为就绪。
- `pagination_terminal=next_disabled` 表示观察到下一页禁用；`controls_absent` 仅表示没发现控件，不等于证明末页。没有声明数量时即使 next 禁用也保留 `unknown`，不编造总数。该证据持久化于运行记录并在来源界面展示。
- 竞品身份是 `店铺ID + 分类 + 标题` 的确定性 SHA-256 摘要（24 个十六进制字符）。它是**明确的临时回退身份**，不是已观测到的平台商品 ID。改名/换分类会产生新身份，需要人工重新匹配；同身份碰撞保留所有行、禁用匹配并降级。
- 自有商品用已观测的 `/item/<id>` 链接作为稳定身份。只读取商品名称、价格、状态、库存和代销标识；不保存销量。
- 最后完整快照优先展示。后续失败/部分结果不会把缺失商品当成删除；新发现商品作为「暂存观察」保留。历史记录包含不完整运行，需结合运行状态使用。
- 「待匹配」「自有侧」「竞品侧」是本地匹配状态，**不是**平台缺货/删除证据。名称相近不自动匹配。
- 陈旧标识表示最近一次采集未完整成功；所有记录显示时间，长时间未采集也应人工重新核对。
- 父会话已报告旧代码的真实观察：竞品 7 分类共 19 卡（PPO 3），页面声明 21，保持 `partial / count_mismatch`；自有 10 行、next 禁用、无总数，旧运行报 `page_unstable`。本轮修复只经合成测试，新版真实采集仍待父会话验证，详情见交接文档。
- 持久匹配不会因失败采集而消失；一侧缺失或身份不明确时标记 `needs_review` 并停止计算差价。旧库启动自动补充分页证据列，旧记录标为未观察。

## API 与安全

主要接口：`GET /api/state`、`GET /api/runs`、`GET /api/history?key=...`、`POST /api/shops`、`POST /api/collect`、`POST/DELETE /api/matches`、`GET /api/export.json`、`GET /api/export.csv`。接口用于本地工作台，不是站点商家 API。

本地修改请求须带与 Host 完全一致的 `Origin: http://127.0.0.1:8100` 以及 `X-WZY-Request: 1`；不开放 CORS。Host 限定 loopback。不能把此边界当成多用户认证；同机恶意进程不在防护范围。所有 `/api/listings/*` 修改请求返回 403。外部目标仅允许严格店铺 URL 与固定商家路径。

## 测试与交接

```bash
.venv/bin/python -m pytest -q
node --check src/wzy/static/app.js
npm ci
npm test
.venv/bin/python -m compileall -q src/wzy
.venv/bin/python -m pip check
```

测试仅使用合成 HTML、临时数据库和模拟页面，不访问真实站点/浏览器。详情见 [交接与验证记录](docs/handoff.md)。

其他 AI 从 [AGENTS.md](AGENTS.md) 和 [可同步 Skill](skills/wzy-workbench/SKILL.md) 开始；项目代码、说明和 Skill 一起随仓库同步，数据库与个人配置不进入 Git。
