# 全 A 2016 起点：训练前内存诊断基线

状态：`train-data/all-start2016` 已完成五个固定种子；2016 起点在当前 16GB M1
上可重复完成训练。

## 旧 `train-data/all` 失败证据

- `backtest/experiments/registry.jsonl` 中的 `train-data/all` 条目（2026-07-24）记录：训练在 LGBM `fit`（即 `model.fit`）阶段被 OOM 杀掉；该条目的 `result_dirs` 为空。
- 同一 registry `note` 将运行主机描述为 16GB M1，并写有“全A 2005-2026 Alpha158 超内存”。这是历史运行说明，不是可复核的峰值内存测量。
- 原配置 `backtest/configs/train-data/all/td_all_lgbm_s42.yaml` 的有效训练窗口为 `2006-01-02` 至 `2020-01-10`：`handler.fit_start_time` 与 `segments.train[0]` 均为 `2006-01-02`，终点均为 `2020-01-10`。其 handler 数据覆盖范围是 `2005-06-01` 至 `2026-07-16`。
- 仓库内没有保存该失败运行的 stdout/stderr、Python/LightGBM 堆栈、退出状态、`/usr/bin/time -l` 峰值常驻内存记录或 macOS 系统杀进程事件。因此不能从现有材料确认内存峰值、触发者或更具体的根因。

## 本变体与后续采证

`train-data/all-start2016` 仅将 handler 起点、fit 起点和 train 段起点改为 `2016-01-02`，训练终点仍为 `2020-01-10`；`instruments: all`、Alpha158、LGBM 参数、valid/test、策略及费率均与旧配置一致。

实测按五种子串行执行，并通过 `/usr/bin/time -l` 采集退出状态与 maximum
resident set size；未改变模型设置。

## 2026-07-24 实测结果

五个固定种子均顺序完成训练、预测和回测，未发生 OOM，`/usr/bin/time -l`
观测到的代表性 maximum resident set size 如下：

| seed | session | 最大 RSS |
|---:|---|---:|
| 42 | `20260724_215315_td_all_start2016_lgbm_s42` | 10,030,972,928 bytes（约 9.34 GiB） |
| 1000 | `20260724_220316_td_all_start2016_lgbm_s1000` | 10,787,782,656 bytes（约 10.05 GiB） |
| 2000 | `20260724_221300_td_all_start2016_lgbm_s2000` | 8,963,178,496 bytes（约 8.35 GiB） |
| 4000 | `20260724_223522_td_all_start2016_lgbm_s4000` | 8,124,153,856 bytes（约 7.57 GiB） |

已完整保留数值的 seed 42、1000、2000、4000 终端观测均为 0 swaps；原始
`/usr/bin/time -l` 输出未单独落盘，因此这里将其视为运行时观测而非可独立复核的
原始日志。seed 42 全流程约 580 秒，其中：

- Loading data：约 205 秒；
- ProcessInf：约 104 秒；
- handler 全部初始化：约 440 秒；
- LightGBM 训练在 handler 初始化完成后正常结束。

seed 3000 同样成功；其 `time -l` 最大 RSS 行未从终端截断输出中完整保留，
因此不补写推测值。该缺口不影响“五种子均成功”和已观测峰值范围的结论。

## 样本规模解释

用同一份本地数据的 `$close` 非空行数做低内存诊断：

| 口径 | 旧起点 | 2016 起点 | 降幅 |
|---|---:|---:|---:|
| train 窗口至 2020-01-10 | 7,100,707 | 2,933,129 | 58.7% |
| handler 窗口至 2026-07-16 | 14,593,806 | 10,261,457 | 29.7% |

因此 2016 起点同时减少了完整 Alpha158 handler 矩阵和进入 LightGBM 的训练矩阵；
后者减少约 59%，足以把实测峰值压到 16GB 物理内存以内。当前证据支持“旧窗口的
样本矩阵及其处理中间副本超过可用内存”，但旧运行缺少遥测，不能断言唯一峰值一定
发生在 LightGBM 内部。

## 可行方案

1. 当前配置已验证可在本机从 2016 开始、五种子严格串行运行；这只证明 OOM
   层面的可行性，是否采纳该训练池由用户决定。
2. 若必须保留 2006 起点，优先把训练与 test 推理解耦：训练进程的 handler
   `end_time` 只覆盖 valid 末日，模型保存后再由 `eval_ic_multi_pool.py`
   按测试池构造短推理 handler，避免训练进程同时持有 2005~2026 全矩阵。
3. 其次才考虑独立实验中的分层抽样、样本权重或特征降维；这些会改变训练样本或模型，
   不能与本次“仅改起点”的结论混在一起。

补充：本地 `all` universe 是当前/幸存者式股票池，不是 point-in-time 历史全 A。
复核发现 features 中有 183 个曾在 2016~2020 年有数据的 SH/SZ 代码不在
`all.txt`，其中包含后来退市股票；`all.txt` 还包含 4 个指数代码。因此本实验的
性能结果存在幸存者选择，只能解释为“本地当前 `all` universe”的诊断结果，不能
外推为真实历史全 A 的模型结论。该问题不影响本次 OOM 可运行性结论；若要评估真实
全 A，应另建无指数、point-in-time 的纯股票 universe 后作为新实验重跑。
