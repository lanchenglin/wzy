# 父会话真实只读验收

## 已执行

- 独立复跑 Python 测试：46 passed；前端 DOM 测试：5 passed。保留 Starlette/httpx 上游弃用警告。
- 重启本地工作台后，通过 POST /api/collect 实际调用应用采集，GET /api/state 回读 SQLite 持久结果。
- 自有库存 run_id=3：10 条，errors=[]，pagination_terminal=next_disabled；由于站点没有声明总数，完整性诚实标为 unknown。
- 同行 run_id=4：19 条，站点声明 21，partial/count_mismatch。直接 DOM 独立核查也只有19张卡片、7个分类。PPO分类3条均保留；不硬编码补齐。
- 工作台 http://127.0.0.1:8100 的真实 Chromium DOM 验证：29 条商品行，来源状态、末页证据、价格、SKU未知提示均显示。只做文字DOM验收，未做视觉截图验收。
- 任务页已清理；原浏览器页面 URL 列表保持不变。未执行站点上下架、改价、删除或购买。
- data/、浏览器状态和凭据不提交 Git。

## 尚未完成/限制

- 商品匹配尚未由用户确认，当前没有已确认的比价对；商品全部展示不等于已逐项建立同规格关系。
- 同行标称21与展示19的差异原因未知，不宣称全量完整；SKU详情覆盖未知。
- 我方商品页虽观察到末页，也没有平台总数声明可供交叉核对。
- 当前仅手动采集，无定时任务、自启或公网服务；运行中的工作台为本次会话启动。
- 真实上下架仍禁用，属于下一阶段。

相关测试、架构和可移植 Skill 见 README、AGENTS.md、docs/handoff.md、skills/wzy-workbench/SKILL.md。
