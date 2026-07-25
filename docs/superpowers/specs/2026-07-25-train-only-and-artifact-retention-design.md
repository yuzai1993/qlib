# Phase M Train-Only 与实验产物保留设计

## 目标

Phase M 模型实验不再隐式执行策略回测。每次实验收尾统一清理 `mlruns/` 和
`backtest/result/`，只保留当前 baseline 五种子组，以及确实超过当前 baseline 的
最佳候选五种子组。

## 运行模式

`run_backtest.py` 新增 `train_only`：

- 初始化模型和 Dataset；
- 执行 `model.fit(dataset)`；
- 保存 `artifacts/trained_model`；
- 在 result session 中保存 `meta.json`、`summary.json`、`metrics.json`、
  `mlruns_link.json` 和轻量 HTML；
- 不生成 SignalRecord、PortAnaRecord、预测文件、持仓、策略指标或 backtest
  recorder。

保留 `train_backtest` 作为显式兼容模式，`backtest_only` 继续用于冻结模型的策略
回测。所有 Phase M YAML 改成显式 `train_only`。`train_only` 配置只强制要求
data、segments 和 model；即使历史 YAML 仍带 strategy/backtest 字段，也完全忽略。

训练模型仍通过 `eval_ic_multi_pool.py` 在 CSI300、CSI500、CSI1000 上统一评估。

## 产物保留

新增 `backtest/scripts/cleanup_experiment_artifacts.py`，默认只输出计划，传
`--apply` 才删除。

当前 baseline 取 registry 中最后一个 `direction=baseline` 且
`conclusion=baseline` 的锚点。Phase M 候选必须：

1. `baseline_ref` 与当前 baseline 相同；
2. phase 为 M；
3. CSI300、CSI500、CSI1000 均有 RankIC；
4. 三池五种子平均 RankIC 均严格高于 baseline。

合格候选按三池 RankIC 平均增量排序，三池 RankICIR 平均增量为并列规则。最多保留
一个候选五种子组，不按单种子挑选。没有合格候选时只保留 baseline。

`backtest/result/` 保留 baseline 与最佳候选 registry 行中所有以
`backtest/result/` 开头的 session 目录，其他 session 全部删除。

`mlruns/` 保留上述组的 train recorder；实盘模型继续由 Git 跟踪目录长期维护。
删除所有 backtest recorder、落选/失败 train recorder 和 `.trash`。如果历史候选
MLflow 已在本规则生效前删除，清理工具不重建模型，只保留仍存在的候选 result。

registry、配置、`backtest/experiments/ic/*.json` 和自动 HTML 不参与清理。

## 安全边界

- 删除目标必须是 `backtest/result/` 的直接子目录或 `mlruns/` 的直接数字实验目录；
- 禁止跟随 registry 中指向仓库外的路径；
- baseline 和候选白名单在删除前打印；
- 缺失 session/meta 仅记录警告，不扩大删除白名单；
- 脚本默认 dry-run，只有显式 `--apply` 才修改磁盘。

## 规范更新

实验规范升级为 v1.3，明确：

- Phase M 必须 `train_only`；
- 策略参考回测不再随模型训练自动执行；
- 两个产物目录的统一保留规则；
- 标准 checklist 在登记和生成报告后运行统一清理命令。
