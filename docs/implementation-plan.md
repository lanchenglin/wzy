# WZY 第一阶段实施计划

## 设计与约束
独立 Python 包：`domain.py` 定义严格 URL、Decimal 金额与采集结果；`parsers.py` 仅解析合成/采集得到的商品 DOM；`collector.py` 负责任务页、滚动、分页和只读导航；`store.py` 使用 SQLite 记录运行与观察并保留最后完整快照；`app.py` 提供本地安全 API；`static/` 为中文工作台。

用户已授权直接实施，无设计审批停顿。不提交/推送、不访问真实 CDP、不读取浏览器状态或其他项目配置。第二阶段写入没有实现入口。所有变更仅在本仓库。

## 执行清单
- [x] 解析与边界：先写 tests/test_parsers.py，运行 `.venv/bin/python -m pytest tests/test_parsers.py -q` 确认缺失实现；实现严格 URL、金额、全部分类、重复身份、声明数量与可见主表解析，再运行同一命令。
- [x] 持久化与匹配：先写 tests/test_store.py，验证历史、完整快照不被部分/失败覆盖、匹配持久化、Decimal 差价；实现 SQLite 事务，再运行该测试。
- [x] 只读采集：先写 tests/test_collector.py，使用模拟页面验证稳定滚动、分页上限、重复页、失败清理、串行化；实现 CDP 连接和任务页生命周期，再运行该测试。没有实时浏览器测试。
- [x] 本地 API 与 UI：先写 tests/test_api.py，验证 origin/host、输入、禁用写入、匹配与导出；实现中文静态页面与 API，运行该测试及 JS 语法检查。
- [x] 交付：补充中文 README、AGENTS、CLAUDE 指针、便携 Skill、交接与 CI；运行完整 pytest、安装与本地 HTTP 冒烟检查，记录准确命令和未验证限制。

## 验收要点
所有竞品均显示，未匹配不丢失；完整性与 SKU 覆盖分开；身份冲突使结果降级，不静默去重；失败不推断删除。自有库存只保留商品信息，不读取销量/客户数据。URL 只允许已知站点严格路径；CDP 仅 loopback；不导出 Cookie。手工匹配本地持久化且需明确确认。页面使用 textContent，CSV 防公式注入。真实分页 DOM 尚待父会话验证，无法识别时返回部分/未知而不是假装完整。
