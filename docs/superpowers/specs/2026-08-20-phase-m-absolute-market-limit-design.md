# Phase M 改绝对收益 + 板块涨跌停

日期：2026-08-20  
状态：已批准（用户确认评估改绝对、年化仍 ×238/h、全A 评估与回测共用板块阈值、重跑评估和 BT ensemble、不重训现模型）

## 背景

Phase M 官方主格一直报超额（`top_mean − bench`），报告列名一度写成「年化」，和执行层回测的账户绝对收益对不上。回测 `limit_threshold: 0.095` 对创业板/科创板合法涨幅误拒。用户要求两边都评绝对值，并按市场类型设涨跌停。

## 决策

1. **Phase M 官方数字改头部绝对收益**。用 `daily_head_panel` 的 `port`（top-k 等权标签），不再减全市场。扣费净年化 / 波动 / 夏普都在这串绝对收益上算。年化仍是 `均值 × 238 / h`。JSON 保留 `ann_excess` / `net_ann_excess` 作审计，报告和 registry 官方字段改读 `ann` / `net_ann`。
2. **238 的来源**：Qlib `risk_analysis` 的 A 股日频常数（`qlib/contrib/evaluate.py`）。回测继续 ×250。两套都是绝对值，仍不能直接相减。
3. **涨跌停一套函数**：主板 9.5%、科创板 `SH68*` 19.5%、创业板 `SZ30*` 自 2020-08-24 起 19.5%（之前 9.5%）。评估的 `entry_tradable_mask` 与全A 回测 Exchange 共用。配置写 `limit_threshold: market_cn`。CSI1000 / 实盘本次不动。北交所不在全A 池内。
4. **重跑**：现成预测重评 Phase M v1 / v2；重跑 BT v1 / v2 ensemble。不重训。以后新训若仍用 `top5_h5_net_ann`，早停跟着改成绝对净年化。
5. **范围外**：不改持有期规则、不统一 238/250、不加北交所 30% / ST 5% / 新股不设限。

## 实现要点

- 新模块 `qlib/backtest/cn_limit.py`：`limit_cap_array`、`apply_market_cn_limits`。
- `Exchange` 增加 `limit_threshold == "market_cn"`。
- `summarize_head_series` 在传入 `port` 时写入 `ann` / `net_ann`，且 `net_ann_vol` / `net_sharpe` 改基于 `port`。
- 总报告列名改回「扣费净年化 / 非扣费年化」。规范第 5.1.2 / 5.1.3 节同步。
