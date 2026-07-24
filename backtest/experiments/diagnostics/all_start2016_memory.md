# 全 A 2016 起点：训练前内存诊断基线

状态：未运行 `train-data/all-start2016`；本文件仅记录旧失败的可得证据和待采集项目。

## 旧 `train-data/all` 失败证据

- `backtest/experiments/registry.jsonl` 中的 `train-data/all` 条目（2026-07-24）记录：训练在 LGBM `fit`（即 `model.fit`）阶段被 OOM 杀掉；该条目的 `result_dirs` 为空。
- 同一 registry `note` 将运行主机描述为 16GB M1，并写有“全A 2005-2026 Alpha158 超内存”。这是历史运行说明，不是可复核的峰值内存测量。
- 原配置 `backtest/configs/train-data/all/td_all_lgbm_s42.yaml` 的有效训练窗口为 `2006-01-02` 至 `2020-01-10`：`handler.fit_start_time` 与 `segments.train[0]` 均为 `2006-01-02`，终点均为 `2020-01-10`。其 handler 数据覆盖范围是 `2005-06-01` 至 `2026-07-16`。
- 仓库内没有保存该失败运行的 stdout/stderr、Python/LightGBM 堆栈、退出状态、`/usr/bin/time -l` 峰值常驻内存记录或 macOS 系统杀进程事件。因此不能从现有材料确认内存峰值、触发者或更具体的根因。

## 本变体与后续采证

`train-data/all-start2016` 仅将 handler 起点、fit 起点和 train 段起点改为 `2016-01-02`，训练终点仍为 `2020-01-10`；`instruments: all`、Alpha158、LGBM 参数、valid/test、策略及费率均与旧配置一致。

执行 seed 42 时，应串行运行并通过 `/usr/bin/time -l` 保留 stdout/stderr、退出状态与 maximum resident set size。若失败，在不改变模型设置的前提下，另行测量 handler 初始化、train/valid prepare 的数据形状与内存，并与成功的 CSI1000-2016 对照；在取得这些测量前，不把旧记录归因为任何单一实现阶段或参数。
