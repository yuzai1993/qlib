# Alpha158 因子说明文档

## 概述

Alpha158 是 Qlib 内置的标准因子集，共包含 **158 个特征**，涵盖 K 线形态、价格相对值和滚动统计三大类。其设计目标是提供无量纲、可直接输入机器学习模型的特征表示。

**相关文件：**
- 因子定义：`qlib/contrib/data/loader.py` — `Alpha158DL.get_feature_config()`
- 数据处理器：`qlib/contrib/data/handler.py` — `Alpha158` / `Alpha158vwap`

**标签（Label）：**
```
Ref($close, -2) / Ref($close, -1) - 1   # 次日收益率（T+2 相对 T+1）
```
`Alpha158vwap` 变体将标签改为 `Ref($vwap, -2) / Ref($vwap, -1) - 1`。

**默认处理流程：**
- 学习阶段（learn_processors）：`DropnaLabel` → `CSZScoreNorm`（标签截面 Z-Score 标准化）
- 推理阶段（infer_processors）：`ProcessInf` → `ZScoreNorm` → `Fillna`

---

## 因子配置结构

```python
config = {
    "kbar":    {},                              # K 线形态因子（9 个）
    "price":   {"windows": [0],
                "feature": ["OPEN","HIGH","LOW","VWAP"]},  # 价格相对值（4 个）
    "rolling": {},                              # 滚动统计因子（145 个）
}
```

滚动窗口默认为 `[5, 10, 20, 30, 60]`（交易日），每种算子生成 5 个特征。

---

## 第一组：K 线形态因子（9 个）

描述单根 K 线的形状特征，所有因子均以 `$open` 或 `($high - $low)` 作为分母进行归一化。

| 因子名 | 公式 | 含义 |
|--------|------|------|
| KMID | `($close - $open) / $open` | 实体中心偏移（涨跌幅） |
| KLEN | `($high - $low) / $open` | K 线振幅 |
| KMID2 | `($close - $open) / ($high - $low + ε)` | 实体占振幅比 |
| KUP | `($high - max($open, $close)) / $open` | 上影线长度 / 开盘价 |
| KUP2 | `($high - max($open, $close)) / ($high - $low + ε)` | 上影线占振幅比 |
| KLOW | `(min($open, $close) - $low) / $open` | 下影线长度 / 开盘价 |
| KLOW2 | `(min($open, $close) - $low) / ($high - $low + ε)` | 下影线占振幅比 |
| KSFT | `(2×$close - $high - $low) / $open` | 收盘位置偏移 |
| KSFT2 | `(2×$close - $high - $low) / ($high - $low + ε)` | 收盘位置占振幅比 |

> ε = 1e-12，防止除零。

---

## 第二组：价格相对值因子（4 个）

各价格字段相对于当日收盘价的比值（`window=0` 表示当日）。

| 因子名 | 公式 | 含义 |
|--------|------|------|
| OPEN0 | `$open / $close` | 开盘价 / 收盘价 |
| HIGH0 | `$high / $close` | 最高价 / 收盘价 |
| LOW0  | `$low / $close`  | 最低价 / 收盘价 |
| VWAP0 | `$vwap / $close` | 成交均价 / 收盘价 |

> 可通过 `price.windows` 扩展历史窗口（如 `[0,1,2,3,4]`），并通过 `price.feature` 增减字段（含 `CLOSE`）。

---

## 第三组：滚动统计因子（29 种 × 5 窗口 = 145 个）

窗口 `d ∈ {5, 10, 20, 30, 60}`，每种因子生成 5 个特征（如 `ROC5`、`ROC10` …）。

### 价格趋势类

| 因子 | 公式 | 含义 | 参考指标 |
|------|------|------|----------|
| ROC | `Ref($close, d) / $close` | d 日前收盘价 / 今日收盘价（收益率反向） | Rate of Change |
| MA | `Mean($close, d) / $close` | d 日均价 / 今日收盘价 | 简单移动均线 |
| STD | `Std($close, d) / $close` | d 日收盘价标准差 / 今日收盘价 | 价格波动率 |
| BETA | `Slope($close, d) / $close` | d 日价格线性回归斜率 / 今日收盘价 | 价格趋势强度 |
| RSQR | `Rsquare($close, d)` | d 日价格线性回归 R² | 趋势线性度 |
| RESI | `Resi($close, d) / $close` | d 日价格线性回归残差 / 今日收盘价 | 趋势偏离度 |

### 价格区间类

| 因子 | 公式 | 含义 | 参考指标 |
|------|------|------|----------|
| MAX | `Max($high, d) / $close` | d 日最高价 / 今日收盘价 | 阻力位 |
| MIN | `Min($low, d) / $close` | d 日最低价 / 今日收盘价 | 支撑位 |
| QTLU | `Quantile($close, d, 0.8) / $close` | d 日收盘价 80% 分位 / 今日收盘价 | 上轨 |
| QTLD | `Quantile($close, d, 0.2) / $close` | d 日收盘价 20% 分位 / 今日收盘价 | 下轨 |
| RANK | `Rank($close, d)` | 今日收盘价在过去 d 日的百分位排名 | 相对强度 |
| RSV | `($close - Min($low,d)) / (Max($high,d) - Min($low,d) + ε)` | KDJ 的 RSV 值 | KDJ 指标 |

### 时间结构类（Aroon）

| 因子 | 公式 | 含义 |
|------|------|------|
| IMAX | `IdxMax($high, d) / d` | 过去 d 日最高价距今天数 / d |
| IMIN | `IdxMin($low, d) / d` | 过去 d 日最低价距今天数 / d |
| IMXD | `(IdxMax($high,d) - IdxMin($low,d)) / d` | 最高价与最低价出现时间差 / d |

### 价量相关类

| 因子 | 公式 | 含义 |
|------|------|------|
| CORR | `Corr($close, Log($volume+1), d)` | 收盘价与对数成交量的 d 日相关系数 |
| CORD | `Corr($close/Ref($close,1), Log($volume/Ref($volume,1)+1), d)` | 价格涨跌幅与量变化率的 d 日相关系数 |

### 涨跌天数类

| 因子 | 公式 | 含义 | 参考指标 |
|------|------|------|----------|
| CNTP | `Mean($close > Ref($close,1), d)` | 过去 d 日上涨天数占比 | RSI 变体 |
| CNTN | `Mean($close < Ref($close,1), d)` | 过去 d 日下跌天数占比 | RSI 变体 |
| CNTD | `CNTP - CNTN` | 涨跌天数差 | 趋势方向 |

### 涨跌幅强度类（类 RSI）

| 因子 | 公式 | 含义 | 参考指标 |
|------|------|------|----------|
| SUMP | `Sum(max(Δclose,0), d) / (Sum(|Δclose|, d) + ε)` | 上涨幅度 / 总变动幅度 | RSI |
| SUMN | `Sum(max(-Δclose,0), d) / (Sum(|Δclose|, d) + ε)` | 下跌幅度 / 总变动幅度 | RSI |
| SUMD | `SUMP - SUMN` | 涨跌幅度差 | RSI 差分 |

> `SUMN = 1 - SUMP`（数学上等价，但两者均保留以供模型选择）。

### 成交量统计类

| 因子 | 公式 | 含义 |
|------|------|------|
| VMA | `Mean($volume, d) / ($volume + ε)` | 均量 / 今日量 |
| VSTD | `Std($volume, d) / ($volume + ε)` | 量的标准差 / 今日量 |
| WVMA | `Std(|Δprice_ratio| × $volume, d) / (Mean(|Δprice_ratio| × $volume, d) + ε)` | 量价加权波动率 |
| VSUMP | `Sum(max(Δvol,0), d) / (Sum(|Δvol|, d) + ε)` | 放量天数占成交量变动比 |
| VSUMN | `Sum(max(-Δvol,0), d) / (Sum(|Δvol|, d) + ε)` | 缩量天数占成交量变动比 |
| VSUMD | `VSUMP - VSUMN` | 量的涨跌幅度差 |

> `Δclose = $close - Ref($close,1)`，`Δprice_ratio = $close/Ref($close,1) - 1`，`Δvol = $volume - Ref($volume,1)`。

---

## 因子数量汇总

| 分组 | 因子数 |
|------|--------|
| K 线形态（kbar） | 9 |
| 价格相对值（price） | 4 |
| 滚动统计（rolling，29 种 × 5 窗口） | 145 |
| **合计** | **158** |

---

## `$close` 依赖分析

`$close`（收盘价）是 Alpha158 中**使用最广泛的原始字段**，在三组因子中均有出现：

### K 线形态组（使用 $close）
KMID、KMID2、KUP、KUP2、KLOW、KLOW2、KSFT、KSFT2  
（`$close` 参与分子计算，反映实体与收盘位置）

### 价格相对值组（以 $close 为分母）
OPEN0、HIGH0、LOW0、VWAP0  
（所有价格字段均除以 `$close` 进行归一化）

### 滚动统计组（以 $close 为核心输入）

| 直接使用 `$close` 的因子 | 说明 |
|--------------------------|------|
| ROC | `Ref($close,d) / $close` |
| MA | `Mean($close,d) / $close` |
| STD | `Std($close,d) / $close` |
| BETA | `Slope($close,d) / $close` |
| RSQR | `Rsquare($close,d)` |
| RESI | `Resi($close,d) / $close` |
| QTLU | `Quantile($close,d,0.8) / $close` |
| QTLD | `Quantile($close,d,0.2) / $close` |
| RANK | `Rank($close,d)` |
| RSV | `($close - Min($low,d)) / (Max($high,d) - Min($low,d) + ε)` |
| CORR | `Corr($close, Log($volume+1), d)` |
| CORD | `Corr($close/Ref($close,1), ...)` |
| CNTP | `Mean($close > Ref($close,1), d)` |
| CNTN | `Mean($close < Ref($close,1), d)` |
| CNTD | 由 CNTP、CNTN 计算 |
| SUMP | `Sum(max($close-Ref($close,1),0), d) / ...` |
| SUMN | `Sum(max(Ref($close,1)-$close,0), d) / ...` |
| SUMD | 由 SUMP、SUMN 计算 |
| WVMA | `Std(|$close/Ref($close,1)-1|×$volume, d) / ...` |

不依赖 `$close` 的滚动因子：
- **IMAX / IMIN / IMXD**：仅使用 `$high` / `$low`
- **VMA / VSTD / VSUMP / VSUMN / VSUMD**：仅使用 `$volume`
- **MAX**：使用 `Max($high,d) / $close`（分母是 `$close`，分子是 `$high`）
- **MIN**：使用 `Min($low,d) / $close`（分母是 `$close`，分子是 `$low`）

**结论：158 个因子中，约 138 个（87%）在公式中直接引用了 `$close`。**

---

## `$factor` 依赖分析

**Alpha158 的因子公式本身不引用 `$factor`（复权因子）。**

### `$factor` 的作用

`$factor` 是 Qlib 数据管道中用于处理分红、配股、拆股等公司行为的复权系数。在 Tushare 等数据源的采集脚本（`scripts/data_collector/tushare/collector.py`）中，原始价格在写入 Qlib 数据库之前已经通过 `$factor` 完成了前复权处理：

```
前复权价 = 原始价 × $factor
```

因此，**`$close`、`$open`、`$high`、`$low`、`$vwap` 在存入 Qlib 后已是复权价格**，Alpha158 直接使用这些复权后的字段即可，无需在因子公式中显式引用 `$factor`。

### `$factor` 在代码库中的实际位置

| 位置 | 用途 |
|------|------|
| `scripts/data_collector/tushare/collector.py` | 采集时对原始价格乘以 `$factor` 完成前复权 |
| `qlib/contrib/data/highfreq_handler.py` | 高频数据处理器中使用（非 Alpha158） |

### 对 Alpha158 使用者的影响

- 若数据源已做**前复权**处理（默认情况），直接使用 Alpha158 因子即可，无需额外处理。
- 若数据源提供**未复权**原始价格，则 `$close` 等字段会在除权日发生跳跃，导致 ROC、MA 等跨日计算的因子出现异常值，**必须在数据预处理阶段完成复权**，或在 `inst_processors` 中添加复权处理步骤。

---

## 使用示例

```python
from qlib.contrib.data.handler import Alpha158

handler = Alpha158(
    instruments="csi300",
    start_time="2020-01-01",
    end_time="2023-12-31",
    fit_start_time="2020-01-01",
    fit_end_time="2022-12-31",
    infer_processors=[
        {"class": "ProcessInf"},
        {"class": "ZScoreNorm"},
        {"class": "Fillna"},
    ],
    learn_processors=[
        {"class": "DropnaLabel"},
        {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}},
    ],
)

# 获取特征数据
df = handler.fetch(col_set="feature")
print(df.shape)  # (样本数, 158)
```

自定义因子子集（仅使用部分滚动因子）：

```python
from qlib.contrib.data.loader import Alpha158DL

fields, names = Alpha158DL.get_feature_config(
    config={
        "kbar": {},
        "rolling": {
            "windows": [5, 20, 60],
            "include": ["MA", "STD", "ROC", "CORR", "RSV"],
        },
    }
)
```
