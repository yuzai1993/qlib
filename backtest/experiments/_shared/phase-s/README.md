# CSI1000 历史预测输入已退役

用户于 **2026-11-01** 明确要求删除 `predictions/b6-m/csi1000_full.pkl`，原文件19,052,371字节。此目录保留历史资料，不再提供可运行的冻结预测。

- `prediction_manifest.json` 及其中的预测条目均标为 `status: retired`，保留原模型绑定、预测SHA-256、覆盖范围和来源。
- `protocol.json`、历史实验结果和当前生产模型继续保留。后续models整理已另行删除`backtest/models/baselines/`中的B1-M/B6-M研究副本，来源清单及指纹见[模型删除凭证](../../_catalog/audits/20261101-model-baseline-retirement/README.md)；实盘导出与其他训练原件保留。
- 退役输入的验证、策略运行和依赖它的清理门禁会明确报错，不会生成替代预测或放松校验。

需要重新研究时，须显式重建并验证研究模型及清单，再选择新输出目录，提供并重新验证预测输入。`prepare_strategy_stability_predictions.py` 必须指定 `--output-root`，不能覆盖本目录的退役清单；新清单仍须遵守既有模型绑定、SHA和完整覆盖校验。

删除前的文件SHA、字节数、原预测条目和保留模型核验见[删除审计](../../_catalog/audits/20261101-shared-catalog-cleanup/prediction-deletion.json)。
