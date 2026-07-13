# TopkDropout 缓建仓策略 Design

**Date:** 2026-07-12  
**Status:** Cancelled — 用户判断缓建仓实验意义不大，不做实现  
**Approach:** A — 新建 `TopkDropoutRampStrategy`

## Goal

回测实验：在持仓未满 `topk` 前，每天最多买入 `n_drop` 只，每只按约 `总资产/topk` 下单；满仓后恢复标准 TopkDropout 换仓。与「首日买满 topk」对比。

## Non-Goals

- 本期不改 `paper_trading` / `live_trading` OrderManager
- 不修改 `TopkDropoutStrategy` 默认行为
- 不做建仓期再平衡（已持仓份额不因股价波动每日重配）

## Behavior

记当前持仓数 `h = len(last)`，目标 `K = topk`，每日换仓额度 `D = n_drop`。

### 建仓期（`h < K`）

1. **不卖出**（不做 dropout）。
2. 从当日预测分由高到低、且不在持仓中的标的里，取至多 `min(D, K - h)` 只买入。
3. 单票目标金额：`value = position_total_value * risk_degree / K`（与满仓后单票目标一致的「1/K」口径；用总资产而非仅现金，避免定价偏差）。
4. 若现金不足买满当日计划只数，能买几只买几只（保持与现有 exchange 撮合一致）。

### 满仓期（`h >= K`）

完全委托父类 `TopkDropoutStrategy.generate_trade_decision`（卖 `n_drop`、买 `n_drop`，金额按卖出后现金 / 买入只数）。

## Code

| 路径 | 动作 |
|------|------|
| `qlib/contrib/strategy/signal_strategy.py` | 新增 `TopkDropoutRampStrategy(TopkDropoutStrategy)` |
| `backtest/configs/csi300_lgbm_bt_only_2006_top10_ramp.yaml` | 新增：`backtest_only`，源 session `20260711_223223_train_start_2006`，`topk=10`/`n_drop=2`，strategy class 指向 Ramp |
| 单元测试（可选小测） | 用假 position/score 断言：空仓日买入数 ≤ n_drop；满仓走 dropout |

YAML 示例关键段：

```yaml
strategy:
  class: "TopkDropoutRampStrategy"
  module_path: "qlib.contrib.strategy.signal_strategy"
  topk: 10
  n_drop: 2
```

## Experiment

- **Base**：已有 `20260712_223639_bt_only_2006_top10_drop2`（首日买满）
- **Ramp**：同模型、同区间、同费用，仅换 Ramp 策略
- 对比：组合/超额年化、IR、最大回撤、累计收益；可附建仓期前 ~5 个交易日持仓只数（确认每天 +≤2）

## Risks / Notes

- 建仓约 `ceil(topk/n_drop)` 个交易日（10/2=5 日）内现金利用率低于首日满仓，短期曲线会不同，属预期。
- `risk_degree` 默认 0.95（父类），单票金额为 `总资产 * 0.95 / topk`。
