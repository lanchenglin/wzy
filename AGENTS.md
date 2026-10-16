# 跨 AI 工作入口

首先阅读 README.md、docs/handoff.md、skills/wzy-workbench/SKILL.md。

## 不可越过的边界

- 当前仅授权第一阶段只读商品采集与本地匹配。没有上架/下架/编辑/删除/购买权限，不可实现或调用假定写入 API。
- 开发测试只用合成 fixtures；未经独立授权不得连接真实浏览器。真实验收由父会话/人类执行。
- 不读取、复制、搜索浏览器 profile、Cookie、storage、凭据、其他项目 `.env` 或账号信息。不读取订单/客户数据。
- 不启动/关闭现有浏览器，不关闭 context 或非任务页。仅创建并清理自己的任务页；同账号串行执行。
- 不绕过挑战/验证码，遇到失败暂停人工处理；不激进重试。
- 外部地址仅为严格 `https://wzyp.cn/shop/<id>` 与固定 merchant goods list。CDP 仅显式 loopback。
- 未明确要求不提交、不推送。数据库、日志、个人配置不得入库。

## 开发地图

- `src/wzy/domain.py`：数据结构、URL、Decimal。
- `src/wzy/parsers.py`：纯 HTML 解析与完整性证据。
- `src/wzy/collector.py`：Playwright 只读 DOM、分页/滚动边界、任务页、账号锁。
- `src/wzy/store.py`：SQLite 运行/观察/匹配与完整快照优先策略。
- `src/wzy/app.py`：本地 FastAPI 边界与接口；`static/`：中文界面。
- `tests/`：合成页面与临时数据库测试；不使用真实浏览器。

运行 `.venv/bin/python -m pytest -q`，修改 JS 后运行 `node --check src/wzy/static/app.js`。先加失败回归测试再修复。未知 DOM 应降级，不可为通过验收硬编码商品数量 21 或凭空编造商品 ID。列表完整性和 SKU 覆盖独立报告。产品字段不可信，渲染只用 textContent；日志/API 不返回原始浏览器异常或完整异常 URL。
