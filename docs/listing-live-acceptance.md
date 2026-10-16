# 受控上下架真实验收

父操作会话在用户明确授权后执行，编码代理未访问真实浏览器。

## 验收条件

- 原有专用 Chromium/CDP 9224，保留用户页面。
- 本地服务8100，WZY_LISTING_WRITES_ENABLED=true，验收时 WZY_LISTING_ALLOWED_ITEMS=k8f0bn。
- 测试商品 k8f0bn：自动化验收测试勿拍-零库存。复制自用户自己的非代销商品，未复制卡密，已确认缺货。未复制同行文案。
- 数据库变更前使用 SQLite backup 留存本机私有备份，不进入仓库。

## 真实执行结果

通过工作台 API 而非绕过应用：

1. GET /api/listings/items/k8f0bn 确认初始 down。
2. POST /api/listings/state desired=up expected=down confirmed=true：operation 0047fb6f-caf2-4d72-9c8b-28a437948a8d succeeded，before=down after=up。
3. 独立 GET 再次确认 up。
4. 使用相同幂等键重复提交，返回完全相同操作记录，没有重复执行。
5. POST desired=down expected=up：operation fb4b4b2c-586b-4694-a01e-37b90a273532 succeeded，before=up after=down。
6. 独立 GET 确认最终 down。
7. GET /api/listings/operations 回读上述两条持久化记录。

原有正式商品未改动。测试商品留存下架，未购买、未发卡、未删除。

## 回归与边界

- 父会话独立复跑62项 Python测试、7项DOM测试通过；git diff --check通过。
- 超时、断线、冲突、分页等由合成测试覆盖，不宣称这些异常已在生产故障注入验证。
- 支持明确目标状态的人工确认上下架，不包含定时规则、自动改价、删除或付款。
- 商品复制仅作为父会话测试准备，尚无通用复制商品工作台功能。
- 默认写入开关关闭；显式启用后可用允许商品清单收窄范围。部署按 README 配置。
