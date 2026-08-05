# CSI1000 实盘账户一手验收设计

## 目标

将当前 CSI1000 B6-M + B2-S 盘后策略切换到资金账号 `8890116049`，以2026-08-06 为首个实盘交易日做每单最多 100 股的验收。账户初始普通股票空仓，可用资金和总资产均为 1,000,000 元。

## 边界

- 新建 `csi1000_b6m_b2s_postclose_real` 配置和 SQLite 账本；不复用模拟盘批次、现金或价值调整。
- 协议允许 `REAL`，但配置必须满足 `broker_environment=REAL` 与 `allow_real_money=true` 配对；发布只读 `QMT_REAL_ACCOUNT_ID`。
- 实盘发布必须是 `mode=LIVE`，并要求 `LIVE_TRADING_CONFIRM=YES`；QMT 还要求账户 ID 精确匹配、`ALLOW_REAL_MONEY=True` 和当日 `LIVE_OK_YYYY-MM-DD`。
- QMT 保留 `MAX_ORDER_QUANTITY=100`，不得自动晋级。首次委托前查询券商账户：返回账号必须一致、普通股票必须空仓、可用资金必须在 1,000,000±100 元。任一不符即整批 `SKIPPED`。
- 每个交易日的 `LIVE_OK` 不持久化到后续日期。模拟 QMT 策略必须停止，实盘策略编译后才可创建 2026-08-06 授权。

## 数据流与故障处理

16:00 单一 cron 仍按 postclose → publish → evening 串行。主机睡眠仍会使 cron 错过，因此运行日需保持 Mac 唤醒。实盘批次回执必须在下一批发布前达到 terminal=planned；账户对账差异、QMT 无法追踪委托或缺少账户快照均停止晋级。
