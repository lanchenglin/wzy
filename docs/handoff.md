# 交接与验证记录

## 第二阶段受控上下架（2026-10-16，当前交付）

下文第一阶段禁写描述为历史记录，不再代表本次授权范围。本次只新增自有商品真实上下架，编辑/删除/购买/支付/读取卡密仍禁止。未提交、未推送；未停止或连接父会话 8100 服务，未访问真实 CDP、profile、Cookie、storage、真实数据库或站点。

### 实现

- `src/wzy/listings.py`：固定自有列表、有界全页唯一 ID 检查（含跨页重复）、稳定状态、精确状态单元格动作、最多一次点击、等待状态后刷新独立读回。超时/断连不重试写入；无法确认报告 uncertain。
- `src/wzy/app.py`：默认禁用的 POST `/api/listings/state`，环境开关/可选白名单、显式 expected/desired/confirmed、严格 ID 与幂等键边界；采集、读回、上下架共用应用互斥，浏览器任务共用账号 flock。
- `src/wzy/store.py`：操作意图/派发在点击前持久化，幂等同键同载荷返回原操作、冲突 409；取消/启动恢复为 uncertain，审计 before/after 和时间；不覆盖完整库存快照。GET 审计与独立浏览器核验 API。
- 中文界面展示真实开关与允许目标，先读回再逐次确认；网络错误保留原请求幂等键，禁止更改未确认载荷。审计使用 textContent；旧列表明确仍是采集快照。
- README、AGENTS、可携带 Skill 已更新第二阶段边界、准确命令与恢复协议。

### 合成验证（不是实时浏览器验收）

先新增 Python 测试，初次因 `wzy.listings` 缺失失败；新增 UI 测试初次因 listing-policy 缺失失败，再实现转绿。

```bash
.venv/bin/python -m pytest -q
npm test
node --check src/wzy/static/app.js
.venv/bin/python -m compileall -q src/wzy
.venv/bin/python -m pip check
git diff --check
```

结果：**62 pytest passed，7 DOM tests passed**，原 46 Python / 5 DOM 全部保留；JS/Python 编译、依赖、diff 检查通过。仍有一条 Starlette TestClient/httpx 上游弃用警告。未安装新依赖。

覆盖：up/down、noop、陈旧状态、缺失/重复/跨页目标、分页上限、精确可见状态-cell selector、白名单/默认禁用、同键重放/冲突、点击前后超时与不明确读回、取消审计、成功/异常/取消任务页清理、共用 flock 与应用串行、UI 确认/网络重放键/XSS。全部使用合成页面和临时数据库。

### 父会话待执行的真实验收

父会话提供的事实：自有主表 `a.name` 指向 `/item/<id>`；td index 3 的 `.item-span` 为「已下架」/「销售中」，精确「上架」/「下架」直接生效，无确认 modal，刷新可核验。父会话已准备一次性、零库存测试商品，最终已下架；真实 ID 不写入代码或测试。该观察不是本编码会话实时验证。

1. 父会话自行安排服务重启或使用空闲端口；同一数据库只能一个进程。按 README 命令设置 `WZY_LISTING_WRITES_ENABLED=true` 和 `WZY_LISTING_ALLOWED_ITEMS=<仅测试商品ID>`。
2. 查看 `/api/state` 开关/白名单，中文界面输入测试 ID，只读核验 down；逐次确认 up，新幂等键，检查 succeeded 与 before/after；独立刷新核验 up。
3. 逐次确认 down，另一个新幂等键；独立刷新核验 down。最终保持下架。原商品不得写入。
4. 重放相同键应返回同操作 ID、不再点击；不同载荷同键应 409；关闭开关后 POST 应 403；检查 GET 审计与重启持久化。
5. 核对任务新页结束清理、原有标签页/context/browser 未动。若真实 DOM 与 fixture 不符，先交回去敏结构补回归，不扩大 selector 或猜 API。
6. uncertain 不自动重试，不换键盲目重试；人工查当前状态与审计后再决定新的明确意图。列表/SKU 完整性独立报告，不因写入成功将快照标 complete。

---

# 第一阶段交接与验证记录（历史）

日期：2026-10-16。实现位于 `/home/lan/projects/wzy`，未提交、未推送。

## 已交付

- Python FastAPI / SQLite / 异步 Playwright CDP，只读采集中文工作台。
- 全分类竞品解析（含 PPO）、实际展示价、自有可见主表与有界分页；库存与上下架独立。
- 运行状态/时间/安全错误码、观察历史、完整快照优先、部分新发现保留。
- 明确人工确认的持久匹配、移除、全部商品列表、历史、JSON/安全 CSV。
- loopback 默认 8100、Host/Origin 修改边界、textContent 与 CSP；商品写入 API 明确 403。
- 跨进程 flock 和进程内互斥；任务新页 finally 清理；不关闭已有页/context/browser。
- 中文 README、AGENTS、CLAUDE 指针、随仓库同步 Skill、Linux CI。

## 实际执行的安装命令

工作目录 `/home/lan/projects/wzy`：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
npm install
```

第一次 pip 安装因 `src` 尚不存在报 `error in 'egg_base' option: 'src' does not exist`；创建包目录后重跑相同安装命令成功。npm 安装 39 个测试依赖、audit 0 vulnerabilities，存在 `whatwg-encoding@3.1.1` 上游弃用提示。npm 仅用于 jsdom 合成 DOM 测试，运行应用不需要 Node。

## 最终验证命令与结果

```bash
.venv/bin/python -m pytest -q
npm test
node --check src/wzy/static/app.js
.venv/bin/python -m compileall -q src/wzy
.venv/bin/python -m pip check
.venv/bin/python -m pip wheel --no-deps --wheel-dir /home/lan/.hermes/cache/scratch/wzy-wheel .
git diff --check
```

- 本轮 pytest：**46 passed**。1 条上游弃用警告：Starlette TestClient 使用 httpx，将来建议 httpx2；未屏蔽警告。
- 本轮 npm test：**5 passed**；执行真实提取 JS 与界面 JS，DOM 来自 jsdom 合成 HTML，不是 Chromium。
- JS 语法、Python 编译、依赖检查：成功；`No broken requirements found.`
- wheel：成功生成 `/home/lan/.hermes/cache/scratch/wzy-wheel/wzy_workbench-0.1.0-py3-none-any.whl`，静态资源由 package-data 打包。
- `git diff --check` 成功；仓库尚无初始提交，新文件未跟踪，故该命令不等价于对所有新文件的完整风格审计。
- 实际开发 Python 为 3.14；CI 配置 3.11 / 3.13，**远端 CI 尚未执行**。

测试先于各核心模块实施，首次运行因模块缺失失败，再实现并转绿；后续审查回归明确复现了计数信任、空状态、慢分页及清空旧行加载问题并修复。测试覆盖全部分类/旧价格/挑战/冲突/计数、分页去重与边界、Decimal、持久化与失败安全、账号锁、安全错误脱敏、本地 Host/Origin、输入、XSS、CSV 公式注入、匹配表单。

## 上一轮本地 HTTP 冒烟记录（没有浏览器采集）

下列复现示例已按要求改为用户 scratch 路径；上一轮实测使用系统临时目录，本轮没有重跑 HTTP，也没有将历史路径改写视作新的执行记录。**当前父会话服务占用 8100、运行旧代码；本轮未连接、停止或重启它。不要直接运行以下示例抢占该端口。**

上一轮曾先用 socket bind 确认可用，然后独立测试数据库启动；仅在父会话自行确认端口释放后使用此示例：

```bash
WZY_DB=/home/lan/.hermes/cache/scratch/wzy-smoke-20261016.sqlite3 .venv/bin/wzy --port 8100
```

另一个终端执行：

```bash
curl --fail --silent http://127.0.0.1:8100/api/state
curl --fail --silent http://127.0.0.1:8100/ -o /home/lan/.hermes/cache/scratch/wzy-smoke-index.html
curl --fail --silent -X POST http://127.0.0.1:8100/api/shops \
  -H 'Origin: http://127.0.0.1:8100' -H 'X-WZY-Request: 1' \
  -H 'Content-Type: application/json' \
  --data '{"url":"https://wzyp.cn/shop/SYNTHETIC"}'
curl --silent -o /home/lan/.hermes/cache/scratch/wzy-smoke-denied.json -w '\nCross-origin HTTP %{http_code}\n' \
  -X POST http://127.0.0.1:8100/api/shops \
  -H 'Origin: https://evil.invalid' -H 'Content-Type: application/json' \
  --data '{"url":"https://wzyp.cn/shop/BLOCKED"}'
```

结果：初始 2 个来源、0 商品、writes=false；首页 200；同源添加返回 `{"id":3}`；跨站添加 403。SYNTHETIC 只是临时库中的 URL 输入测试，未访问站点。测试服务已停止，未启动/关闭任何浏览器。临时文件只含合成配置，不进入仓库。

## 独立审查与 Skill 检验

独立只读代码审查提出计数伪造、嵌套计数标签、慢分页、明确空库存四类问题；修复后复审前三类与空库存通过，后续发现分页清空旧行的加载状态也会早停，已添加失败回归并修复（上一轮 37 项覆盖，本轮已增至 46 项）。

Skill 检验：无文档的基线无法提供安装/覆盖/入口事实；读取 Skill 后全部七项检索目标可找回。补充 clone、flock 协议与跨机器协调限制。新机器浏览器/CDP 由人类配置是刻意前提，不提供启动或复制 profile 自动化。

## 父会话提供的真实观察（不是本编码会话实测）

2026-10-16 父会话反馈：
- 旧代码自有库存运行 `partial`，错误 `page_unstable`，取得 10 行。
- 竞品运行 `partial / count_mismatch`，实际 19、声明 21。父会话独立确认真实公开 DOM 为 7 个分类共 19 张卡片，其中 PPO 3 张。**此差异是真实观察，保留 partial，不补造两条、不强制 complete。**
- 自有 DOM 有一个可见 `.arco-pagination`；next 为 `.arco-pagination-item-next.arco-pagination-item-disabled`，仅 active page 1，没有声明总数。末页控件证据不等于总数证明，允许保持 `unknown`。

## 本轮修复与合成回归

- 首屏库存就绪最多 30 次、每次间隔 350ms（等待预算约 10.5 秒，DOM 执行时间另计），对商品身份/名称/价格/库存/状态、声明数量、错误与 next 状态做语义稳定性比较；连续 4 次就绪采样一致才结束。不比较波动的完整 HTML 属性。
- 空白/缺表/未确认空表不作为稳定成功；可见 loading 标记也不允许确认空库存。明确空状态 + 零总数 + 非加载 + 稳定分页才可确认空库存。
- 竞品同样只在有可用商品证据后判断语义稳定；仍最多 15 次滚动/采样、500ms 间隔，保留 19/21 的 count_mismatch。
- `pagination_terminal` 记录 `next_disabled`、`controls_absent`、`not_reached`、`not_observed`，写入运行记录并在来源界面展示。缺计数时不会从禁用 next 推造总数。旧库启动时增列，历史记录默认为未观察；本轮只在临时测试库验证迁移，未接触父会话数据库。
- 提取改为白名单文本重建，不再克隆整段商品/单元格 HTML；不返回隐藏内容、事件属性、脚本、表单值或非商品链接。商品链接仅保留严格 item 路径。
- 持久比较回归验证失败/部分采集保留完整快照差价、重开数据库保留匹配、缺失一侧不删除匹配但差价未知。身份变得不明确时 `needs_review=true`、不计算差价。

## 仍由父会话完成的新版真实验收

**本编码会话本轮没有访问真实 CDP、站点、库存、父服务或真实数据库，也没有截图。** 上述真实信息来自父会话提供；本轮仅合成测试验证修复，新版仍需父会话自行安排重启与读回。

1. 人类确认现有专用可见 Chromium 已登录、CDP `http://127.0.0.1:9224` 仅本机可达；不要读取 `/home/lan/dianp/wzyweb`。
2. 使用 README 启动工作台，核对网页外观与键盘交互，然后主动采集默认竞品和自有库存。
3. 核对所有分类，特别 PPO；对照可见数字卡密声明与实际数量。不得硬编码历史观察的 21。
4. 检查声明数量的真实 DOM、分页 next 的真实类/ARIA、可见主表、加载/空状态标识；当前实现基于已知表结构和防守性 DOM 假设，缺证据会部分/未知。
5. 若 DOM 需调整，先复制**去敏合成结构**到 fixture，再修解析/选择器；禁止保存全页、账号、Cookie、订单或 profile。
6. 独立核对展示价格、库存、上下架，不点击「上架」等状态链接。只允许 Next 分页与读页面滚动。
7. 操作后读回 `/api/state`、`/api/runs` 和历史，核对时间/run_id、数目、错误、完整快照和暂存观察；确认任务页关闭、原有页未动。
8. 人工完成一对匹配，重启服务验证持久化，移除匹配；验证不会改变真实商品。
9. 列表成功仍报告 SKU/变体覆盖未知。名称/分类改动会改变竞品回退身份，需要重新人工匹配。

同一数据库只运行一个 Web 进程，所有同账号自动化协调独占。文件锁不防不合作进程，也不能跨机器互斥。突然进程终止可能留下任务页，不能自动清理无法证明归属的标签页。第二阶段写入保持禁用，父会话审查后再决定 commit/push。
