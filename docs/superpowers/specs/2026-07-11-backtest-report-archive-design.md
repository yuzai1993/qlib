# 回测结果归档与 HTML 报告设计

**日期:** 2026-07-11  
**状态:** 已批准，进入实现

## 目标

优化 `backtest/scripts/run_backtest.py`：

1. 每次运行产出带时间戳（及可选说明）的独立结果目录
2. 生成 notebook 同款四类分析图的静态 PNG
3. 将关键指标与图片汇总为 HTML
4. 将 `mlruns/` 中训练/回测 recorder 与本次结果目录对应起来

## 目录结构

```
backtest/result/
  YYYYMMDD_HHMMSS[_note]/
    meta.json
    summary.json
    all_runs_results.csv
    index.html
    run_01/
      metrics.json
      report_normal.csv
      report.html
      figures/
        report_graph.png
        risk_analysis.png
        score_ic.png
        model_performance.png
      mlruns_link.json
    run_02/
      ...
```

- `--note` 经 sanitize（仅保留字母数字、中文、`-_`，空格改 `_`，截断）后作为目录名后缀
- `N_RUNS>1` 时：一次命令一个父目录，其下 `run_XX/`，父目录有汇总 `index.html`

## CLI

```bash
python backtest/scripts/run_backtest.py
python backtest/scripts/run_backtest.py --note "157维+ProcessInf"
python backtest/scripts/run_backtest.py --note "baseline" --n-runs 3
```

## 图表

调用 qlib 报告 API（`show_notebook=False`）得到 Plotly figure，再 `write_image(..., format="png")`：

| 文件 | 来源 |
|------|------|
| report_graph.png | `analysis_position.report_graph` |
| risk_analysis.png | `analysis_position.risk_analysis_graph` |
| score_ic.png | `analysis_position.score_ic_graph` |
| model_performance.png | `analysis_model.model_performance_graph` |

依赖：`kaleido`（Plotly 静态导出）。

## mlruns 对应

训练/回测仍写入仓库根目录 `mlruns/`。每个 `run_XX/mlruns_link.json` 与父目录 `meta.json` 记录：

- train / backtest 的 `experiment_name`、`experiment_id`、`recorder_id`
- artifacts 相对路径

HTML 中以表格展示，便于跳转核对。

## HTML

- `run_XX/report.html`：本 run 指标表 + 四张 PNG
- 父目录 `index.html`：多 run 对比表 + 链到各 run 报告 + mlruns 映射摘要

## 实现拆分

- `backtest/scripts/report_utils.py`：出图、HTML、目录/meta 辅助
- `backtest/scripts/run_backtest.py`：argparse、结果目录编排、调用 report_utils、写入 mlruns_link
