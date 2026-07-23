# 特征优化实验

基于 Alpha158 追加三组扩展特征，并做 E0~E4 消融对照。

## 特征组

| 组 | 开关名 | 规模 | 内容 |
|----|--------|------|------|
| 动量 | `mom` | 14 | MOMRA / MOMACC / NHIGH / NLOW / VWMOM |
| 布林线 | `boll` | 12 | BOLLPB / BOLLBW / BOLLXU / BOLLXD (+ 5日频率) |
| 趋势 | `trend` | 16 | TRSTR / CHOP / KER / MABULL / MABEAR / ABOVEMA |

实现：[`expressions.py`](expressions.py) + [`handler.py`](handler.py)（`Alpha158Ext`）。

## 实验组

| 组号 | 特征集 |
|------|--------|
| E0 | Alpha158 基线 |
| E1 | Alpha158 + mom |
| E2 | Alpha158 + boll |
| E3 | Alpha158 + trend |
| E4 | Alpha158 + 全部三组 |

## 用法

```bash
# 1) 表达式校验（无需行情）
python backtest/scripts/validate_features.py --parse-only

# 2) 完整校验（需要 cn_data + 已编译 Cython 扩展）
python setup.py build_ext --inplace
python backtest/scripts/validate_features.py --corr-threshold 0.95

# 3) 消融实验（与 backtest/scripts/run_backtest.py 同配置）
python backtest/scripts/run_feature_experiment.py --exp all --seeds 3

# 4) 仅汇总已有结果
python backtest/scripts/summarize_feature_exp.py
```

结果目录：`backtest/result/feature_exp/`
- `{E0..E4}/seed_XX/metrics.json`
- `{E0..E4}/seed_XX/feature_importance.csv`
- `comparison.csv`
