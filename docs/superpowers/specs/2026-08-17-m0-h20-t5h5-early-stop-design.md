# M0 H20 top5×h5 扣费净年化早停设计

## 目标

把 Phase M v1 当前基线 **M0 H20** 再训一轮：训练配方不变，只把早停尺子从「冻结 499 天次日 RankIC」换成与正式评估主格同一把尺子——**全A 连续评估窗上的 top5×h5 扣费净年化**。

本实验只回答：早停对齐主格之后，五种子主格数字相对现基线是否拉开。不自动晋升基线，不改 `EXPERIMENT_STANDARD.md` 第 1.5 节。

## 已锁定决策

| 项 | 值 |
|---|---|
| 早停最大化 | top5×h5 **扣费净年化**（`net_ann_excess`） |
| 早停 / 正式评估日期 | 同一段：全A，`2020-08-03 ~ 2026-07-31` 全部交易日（报告口径 1454 天） |
| 过滤 | 上市 ≥ 60 日、ST、成交额 ≥ 1000 万、剔 t+1 涨停/零量；top-k 与等权基准同一掩码 |
| 本轮范围 | 只重训 M0 H20，种子 `[42, 1000, 2000, 3000, 4000]` |
| 后续 | 数字出来后再决定是否把同一早停铺到其他训练标签期限 |

## 冻结对照

对照行是现基线，不是重算旧 session。

- 对照：`regime-adapt/m0-h20-label-v4`（报告主格约净年化 +24.7%、波动 33.3%、夏普 0.74；以 registry / eval JSON 为准）
- 处理：同一训练配方 + 新早停
- 轨道：Phase M v1（全A / regime-adapt）；只改模型早停，不改策略
- 训练池 / 窗：全A，`2004-01-02 ~ 2020-07-31`
- 训练标签：H20 + CSRankNorm，`Ref($close, -21)/Ref($close, -1) - 1`
- 特征：Alpha158 + range，无 regime 特征
- 日权重：M0 自然分布
- 模型：`RegimeSingleLGBMModel`，超参冻结 `FROZEN_SINGLE_KWARGS`（`lr=0.2`，`num_boost_round=200`，`early_stopping_rounds=20`）
- 正式评估：现口径 `eval_ic_multi_pool.py`，主格 top5×h5，不过滤日期清单

旧 session `regimeadaptfast_m0h20_s{seed}` 与 `valid_frame_70.pkl` **不得覆盖、不得复用为新 valid**。

## 早停分数（必须与评估主格同公式）

每一 boosting 轮在 valid 面板上：

1. 用当前树对 valid 特征打分。
2. 调用 `daily_head_panel(pred, h5_label, ks=[5], tradable=entry_tradable)`。
3. 调用 `summarize_head_series(..., horizon=5, k=5)`。
4. 取 `net_ann_excess`；越大越好。LightGBM `feval` 返回 `("top5_h5_net_ann", score, True)`，`metric=None`，`first_metric_only=True`。

公式与 `eval_ic_multi_pool.py` 一致，禁止另写一套年化/换手：

- H5 标签：`Ref($close, -6)/Ref($close, -1) - 1`（早停标签，不是训练 H20）
- 日超额 = top5 等权 H5 收益 − 同日可成交池等权 H5 收益
- 年化超额 = 日超额均值 × `238 / 5`
- 日换手 = 相隔 5 个评估日的单边换手 / 5
- 年化成本 = `238 × 日换手 × 0.00092`（买 0.00021 + 卖 0.00071）
- **扣费净年化** = 年化超额 − 年化成本

过滤顺序与评估相同，禁止只滤 top5、不滤基准：

1. 全A 股票（`_stock_only_mask`）
2. 上市 ≥ 60 交易日（`_listing_age_mask`）
3. ST 名单 `backtest/configs/regime-adapt/st_names.csv`
4. 成交额 ≥ 10_000_000 元（`amount_mask`，`$volume × ($close/$factor) × 100`）
5. `entry_tradable_mask`：t+1 未涨停封板且 t+1 成交量 > 0

某轮算不出有限 `net_ann_excess`（天数不足、换手缺失、退化）则该轮分数视为 −inf，不得用 RankIC 或非扣费年化顶替。

## Valid 面板

- 日期：评估窗全部交易日，**不**读 `test_dates_stratified_70.csv`。
- 特征：`Alpha158Technical` + range，DK_I，warmup 仍从 `2020-02-03` 起，与现 `build_valid_frame` 一致。
- 独立缓存：`prepare_regime_train_chunks.CACHE_ROOT / "m0" / "valid_frame_t5h5es.pkl"`，不得写入或读取 `valid_frame_70.pkl`。
- 缓存内容：特征帧 + 对齐的 H5 标签 + 过滤后标签掩码 + `entry_tradable` 掩码。种子之间复用。
- 日数：以评估脚本在同一窗、同一过滤下能给出主格 `n_days` 的日期为准；缺 H5 标签的尾部交易日与评估一起丢掉，不另补。

`valid=test` 且覆盖整段 1454 天：早停轮数是唯一自由度，但乐观偏差大于原 499 天分层子集。registry 必须写明 `valid_equals_test: true` 与 `early_stop_dates: "all_eval_days"`。本实验视为用户已批准该豁免；不改规范正文，除非用户另批。

## 实现边界

- 新早停只进 `RegimeSingleLGBMModel` 的可选模式：`es_metric="top5_h5_net_ann"`。默认 `es_metric="daily_rank_ic"`，避免改掉旧臂。
- `train_regime_arm.py` 增加 `--es-metric {daily_rank_ic,top5_h5_net_ann}`，默认 `daily_rank_ic`；未开时行为与现在兼容。
- DoubleEnsemble / M3 / 其他标签期限本轮不改、不训。
- 内存：valid 大约从 499 天扩到约 3 倍。若峰值 RSS 顶到本机 16GB，允许在 feval 内分段打分，**不得**改 k/h/过滤/成本公式。
- 禁止用 heredoc/stdin 跑会触发 Qlib 并行取数的代码（macOS）。

## 产物与登记

| 项 | 值 |
|---|---|
| session | `regimeadaptfast_m0h20_t5h5es_s{seed}` |
| exp_id | `regime-adapt/m0-h20-t5h5-es-v1` |
| baseline_ref | `regime-adapt/m0-h20-label-v4` |
| phase | `M`，`phase_m_protocol: "v1"` |
| 评估 JSON | `backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json` |
| 详细报告 | `backtest/experiments/regime_adapt_m0h20_t5h5es_report.html`：第一行固定现基线 M0 H20，第二行本实验。总报告 `phase_m_v1_report.html` 该方向表第一行仍是 M0 H20，本实验另占一行 |
| train_summary | 必须含 `es_metric`、`best_iteration`、`best_score`（早停净年化）、`valid_days` |

训完用现脚本评估五种子，登记 registry，更新 HTML。数字从 JSON 读，禁止手写死数字。不晋升基线。磁盘清理按规范 6.3：本候选未超过对照主格夏普则训练产物可清；对照基线产物保留。

## 决策规则

- 主比较：五种子均值主格 **扣费净年化 / 扣费波动 / 扣费夏普**，对照 `m0-h20-label-v4`。
- 辅看：网格与分年是否只在个别格子变好。
- 主格三列相对对照的升降都记；不设自动「有效」门槛，**不自动晋升**。
- 是否把同一早停铺到 H1–H40，等用户看完主格与分年后再定；本实验默认到此结束。

## 测试

先写失败测试再改生产代码：

1. 合成面板：给定 pred / H5 label / tradable，`feval` 分数等于手算 `summarize_head_series` 的 `net_ann_excess`。
2. 不可成交样本同时退出 top5 与等权基准（复用现有 `daily_head_panel` 行为）。
3. 未开新开关时，`RegimeSingleLGBMModel` 仍走 `daily_rank_ic`。
4. 新 valid 构建拒绝再读 70% 日清单；覆盖评估窗全部交易日（允许因 H5 标签缺失少掉尾部几天）。

## 非范围

- 不重训 H1/H2/H3/H5/H10/H40
- 不改训练标签、特征、日权重、LGB 超参
- 不跑 TopkDropout / Phase S，不改 CSI1000 轨道
- 不把本结果写成新的 Phase M v1 基线
- 不把早停改成夏普或非扣费年化
