# Agent 指南

## 实验规范（强制）

凡涉及模型训练、因子/标签设计、策略调参、回测对比等实验类任务，**必须先阅读并遵循**：

- `backtest/EXPERIMENT_STANDARD.md`（单一事实来源）

不可协商的要点：

1. 基线 B0 固定（实盘 `live_trading/configs/csi300_topk10_live.yaml`：CSI300 · Alpha158 · LGBM · TopkDropout(10,2)，实盘费率 0.00021/0.00071），不得自行变更。
2. 模型与策略分开迭代：Phase M 只改模型（看 IC/RankIC），Phase S 只改策略（看扣费超额 IR/年化/最大回撤）。
3. 固定 5 种子 [42, 1000, 2000, 3000, 4000]；默认只在基线训练池（CSI300）训练，在 4 个测试集（csi300/csi500/csi1000/全A）上评估；仅训练样本类实验才更换训练池。
4. 时间划分固定：valid 2020-01-13~2021-07-15，test 2021-07-16~2026-07-16；禁止用 test 调参。
5. 每个实验（含失败的）登记 `backtest/experiments/registry.jsonl` 并更新 HTML 报告（每个方向独立表格）。

## 环境注意事项

- macOS 下禁止用 heredoc/stdin 运行会触发 Qlib 并行取数的代码（详见 `.cursor/rules/qlib-shell-multiprocessing.mdc`）。
- Python 解释器：`/opt/anaconda3/envs/qlib/bin/python`。
