# 跨 AI 工作入口

首先阅读 README.md、docs/handoff.md、skills/wzy-workbench/SKILL.md。

## 不可越过的边界

- 第一阶段只读采集与本地匹配继续支持；第二阶段仅授权受控真实上下架：默认禁用，显式 WZY_LISTING_WRITES_ENABLED=true，逐次指定自有商品 ID、预期/目标状态、确认及幂等键，可限制 WZY_LISTING_ALLOWED_ITEMS。不得扩展为编辑、删除、购买、支付或库存秘密读取；不得调用猜测的平台 API。
- 编码会话仅实现并用合成页面/临时库测试。真实上下架验收由父会话在已授权的一次性零库存测试商品上执行；不连接真实 CDP、不读取真实数据库、不停止父服务。
- 写操作先持久化审计，再最多一次精确 DOM 点击；状态不确定、断连、取消或重启均不得自动重试写入。只读核验与采集/写入共用账号锁。
- 开发测试只用合成 fixtures；未经独立授权不得连接真实浏览器。真实验收由父会话/人类执行。
- 不读取、复制、搜索浏览器 profile、Cookie、storage、凭据、其他项目 `.env` 或账号信息。不读取订单/客户数据。
- 不启动/关闭现有浏览器，不关闭 context 或非任务页。仅创建并清理自己的任务页；同账号串行执行。
- 不绕过挑战/验证码，遇到失败暂停人工处理；不激进重试。
- 外部地址仅为严格 `https://wzyp.cn/shop/<id>` 与固定 merchant goods list。CDP 仅显式 loopback。
- 未明确要求不提交、不推送。数据库、日志、个人配置不得入库。

## 第三阶段边界

- 支持确定性匹配建议、全商品多店人工分组比较、默认关闭的持久只读调度、持久站内通知。建议不自动保存；未知/冲突字段与 SKU 未知必须展示。
- 时间新鲜度（2 小时）独立于列表完整性。最低价仅来自人工确认且两侧新鲜完整、竞品明确有货的证据；partial/unknown/过期/未知库存排除。较新观察独立展示，不替换完整快照。
- 调度每来源 15–1440 分钟（默认 60），全局暂停不阻断手动；登录挑战来源阻断到人工 resume。普通失败有界退避，重启不补跑。所有采集共用 coordinator 与账号锁，调度不触发任何写操作。
- 新版同库服务先取得实例 flock 再恢复；旧版父服务不支持此锁，禁止借此并行使用父库。不要删除运行中的锁文件或用数据库硬链接别名。编码会话不改父服务启用状态/白名单。
- 通知只比较实际连续观察，首次基线不报警；partial 变化标警告，失败/暂停只按状态转换诊断，缺失不等于删除。无钉钉或其他外部推送，不访问无关设置/token。UI 产品内容只用 textContent。

## 开发地图

- `src/wzy/domain.py`：数据结构、URL、Decimal。
- `src/wzy/parsers.py`：纯 HTML 解析与完整性证据。
- `src/wzy/collector.py`：Playwright 只读 DOM、分页/滚动边界、任务页、账号锁。
- `src/wzy/listings.py`：自有主表全页核验、精确上下架、独立读回与任务页清理。
- `src/wzy/store.py`：SQLite 运行/观察/匹配、上下架幂等审计与完整快照优先策略。
- `src/wzy/comparison.py`：透明字段提取、建议、时间证据与多店分组/Decimal 最低价。
- `src/wzy/notifications.py`：事务内按运行聚合观察变化、诊断去重。
- `src/wzy/scheduler.py`：持久调度设置、同库实例锁、手动/自动共享 coordinator。
- `src/wzy/app.py`：本地 FastAPI 边界与接口、lifespan；`static/`：中文界面。
- `tests/`：合成页面与临时数据库测试；不使用真实浏览器。

运行 `.venv/bin/python -m pytest -q`，修改 JS 后运行 `node --check src/wzy/static/app.js`。先加失败回归测试再修复。未知 DOM 应降级，不可为通过验收硬编码商品数量 21 或凭空编造商品 ID。列表完整性和 SKU 覆盖独立报告。产品字段不可信，渲染只用 textContent；日志/API 不返回原始浏览器异常或完整异常 URL。
