# 修复 close 字段复权问题：方案分析与执行计划

## 1. 问题背景

当前 Tushare 数据采集管道（`scripts/data_collector/tushare/collector.py`）在归一化阶段的处理如下：

```python
# 前复权：factor = 当日复权因子 / 最后一天的复权因子
df["factor"] = adj_series / adj_last
df["adjclose"] = df["close"] * df["factor"]        # adjclose = 前复权收盘价
for c in ["open", "high", "low"]:
    df[c] = df[c] * df["factor"]                    # open/high/low 做了前复权
# close 未做前复权！
```

随后所有价格字段（含 close 和 adjclose）统一除以首日 adjclose 进行标准化。

### 数据字段现状

| 字段 | 含义 | 是否前复权 |
|------|------|-----------|
| `$open` | 开盘价 × factor / first_adjclose | ✅ 已复权 |
| `$high` | 最高价 × factor / first_adjclose | ✅ 已复权 |
| `$low` | 最低价 × factor / first_adjclose | ✅ 已复权 |
| `$close` | **原始收盘价** / first_adjclose | ❌ **未复权** |
| `$adjclose` | 收盘价 × factor / first_adjclose | ✅ 已复权 |
| `$volume` | 原始成交量 / factor × first_adjclose | ✅ 已调整 |
| `$factor` | adj_factor / adj_factor_last | 复权系数 |

---

## 2. 已确认的问题

### 问题 1：Alpha158 特征不一致

Alpha158 共 158 个因子，其中约 138 个（87%）直接引用 `$close`。典型例子：

- **K 线形态因子**：`($close - $open) / $open`，其中 `$close` 未复权、`$open` 已复权，在除权日产生虚假跳变
- **价格相对值因子**：`$open / $close`，分子复权、分母未复权，除权日比值异常
- **滚动统计因子**：`Mean($close, 5) / $close`，$close 的时间序列在除权日不连续
- **价量相关因子**：`Corr($close, Log($volume+1), d)`，$close 跳变破坏相关性计算

**影响**：模型学到的是包含噪声的特征，在除权频繁的股票上尤为严重。

### 问题 2：Label 计算错误

```python
# qlib/contrib/data/handler.py:152
Label = Ref($close, -2) / Ref($close, -1) - 1   # T+1→T+2 收益率
```

`$close` 未复权，若 T+1 和 T+2 之间发生除权事件（如分红、送股），计算的收益率将**严重偏离真实收益**。

**举例**：股票 A 在 T+1 收盘价 20 元，T+2 日实施 10 送 10，T+2 收盘价 10.5 元（实际涨 5%）：
- 未复权 Label = 10.5 / 20 - 1 = **-47.5%** ❌
- 前复权 Label = adjclose_T+2 / adjclose_T+1 - 1 = **+5%** ✅

### 问题 3：回测价格不准确

回测引擎默认使用 `$close` 作为成交价（`deal_price="close"`），同时用 `$close` 做持仓估值（`account.py:245`）。

`$close` 未复权意味着：
- **持仓估值跳变**：在除权日，持仓价值突降（如送股后价格腰斩），但实际持有人并未亏损
- **收益率失真**：回测计算的 PnL 在除权日产生虚假亏损
- **策略评估偏差**：总收益、最大回撤、夏普比率等核心指标均受影响

**注**：`docs/alpha158_factors.md` 中声称"$close 在写入 Qlib 后已是复权价格"，**与代码实现不一致**，存在文档误导。

---

## 3. 用户原始方案评估

### 方案 1：对 close 字段也进行复权处理 → ✅ 可行且必要

**可行性分析**：

- **实现简单**：在 `TushareNormalize1d.normalize()` 中添加一行 `df["close"] = df["close"] * df["factor"]`
- **效果明确**：close 变为前复权价格，与 open/high/low 保持一致
- **自然解决**：
  - 特征一致性问题 → close 和其他价格字段统一为前复权 ✅
  - Label 准确性问题 → 前复权 close 的收益率反映真实总收益 ✅
  - 回测价格问题 → 前复权 close 连续无跳变，持仓估值正确 ✅

**潜在风险**：
- 需要**重新处理全部数据**并 dump
- 依赖旧 close 语义的下游代码需排查（已确认 `$adjclose` 在 qlib 核心代码中无引用）
- 已训练的模型需要**重新训练**

### 方案 2：新建 buy_price / sell_price 字段 → ⚠️ 不推荐，理由如下

用户原始设计：
- `buy_price` = 真实收盘价
- `sell_price` = 真实收盘价 × adj_factor_today / adj_factor_yesterday

**数学分析**：

```
sell_price / buy_price(前一天)
= (close_raw_T × adj_T / adj_{T-1}) / close_raw_{T-1}
= (close_raw_T × adj_T) / (close_raw_{T-1} × adj_{T-1})
= adjclose_T / adjclose_{T-1}
```

**这与直接使用前复权 close 计算的收益率完全一致**。因此：

1. **收益率等价**：单独的 buy/sell 字段不会带来任何收益率计算上的改进
2. **引入复杂性**：
   - 需要计算逐日的 adj_factor_today / adj_factor_yesterday，处理首个交易日等边界情况
   - 需要在回测配置中改为 `deal_price=("buy_price", "sell_price")` 的 tuple 形式
   - 所有 benchmark YAML 配置文件都需要更新
3. **估值不一致**：
   - qlib 用 `$close` 做日终持仓估值（`exchange.get_close()` → `position.update_stock_price()`）
   - 如果 `$close` 是前复权但 `buy_price` 是真实价格，成交时计算的股数与估值时使用的价格体系不一致
   - 会导致买入后第一个 bar 的持仓估值出现跳变
4. **首日标准化已使价格非真实**：所有价格已除以 first_adjclose，绝对价格本就不是真实市价

**结论**：方案 1 已完整解决所有问题，方案 2 是**过度设计**，增加复杂度但无额外收益。

### 方案 3：删除 adjclose → ✅ 合理的清理工作

在方案 1 实施后，close = adjclose（两者完全相同），adjclose 字段冗余。

**排查结果**：
- `$adjclose` 在 qlib 核心代码（`qlib/` 目录）中**无任何引用**
- 引用 `adjclose` 的文件仅限于：
  - `scripts/data_collector/tushare/collector.py`（生成和标准化逻辑）
  - `scripts/data_collector/tushare/README.md`（文档）
  - `scripts/data_collector/yahoo/collector.py`（Yahoo 数据源）
  - `scripts/data_collector/utils.py`（工具函数）
  - `docs/` 下的文档文件

**可安全删除**，但建议作为独立步骤，在方案 1 验证通过后再执行。

---

## 4. 最终方案

### 核心思路

> 将 close 字段统一为前复权价格，使所有价格字段（open/high/low/close）在数据管道中保持一致。同时清理冗余的 adjclose 字段。

### 修改范围

#### 4.1 数据采集归一化（核心修改）

**文件**：`scripts/data_collector/tushare/collector.py`

**修改内容**：

1. 在 `TushareNormalize1d.normalize()` 中，将 close 加入前复权处理：

   ```python
   # 修改前
   for c in ["open", "high", "low"]:
       if c in df.columns:
           df[c] = df[c] * df["factor"]

   # 修改后
   for c in ["open", "high", "low", "close"]:
       if c in df.columns:
           df[c] = df[c] * df["factor"]
   ```

2. 删除 `df["adjclose"] = df["close"] * df["factor"]` 这一行（因为 close 已经是复权后的值）

3. 从输出列中移除 `adjclose`：

   ```python
   # 修改前
   out_cols = [date_col, sym_col, "open", "high", "low", "close", "adjclose", "volume", "factor", "change"]

   # 修改后
   out_cols = [date_col, sym_col, "open", "high", "low", "close", "volume", "factor", "change"]
   ```

4. 更新 `_get_first_adjclose()` 方法：由于 close 本身已经是复权后的值，首日标准化应直接使用首日 close

5. 更新 `_manual_adj_data()` 的文档字符串

#### 4.2 文档更新

- `scripts/data_collector/tushare/README.md`：更新字段说明
- `docs/alpha158_factors.md`：更新数据说明，移除关于 close 未复权的误导描述
- `docs/vibe_coding/tushare_collector_plan.md`：更新设计文档

#### 4.3 不需要修改的部分

以下部分**无需修改**，因为它们通过 `$close` 引用数据，close 语义的修正会自动生效：

- `qlib/contrib/data/handler.py`（Label 计算）
- `qlib/contrib/data/loader.py`（Alpha158 因子定义）
- `qlib/backtest/exchange.py`（deal_price, get_close）
- `qlib/backtest/account.py`（持仓估值）
- 所有 benchmark YAML 配置文件
- `backtest/scripts/run_backtest.py`

---

## 5. 执行计划

### Step 1：修改归一化逻辑

1. 修改 `TushareNormalize1d.normalize()`：close 加入前复权、移除 adjclose 字段
2. 更新 `_get_first_adjclose()` 改为 `_get_first_close()`，使用复权后的 close
3. 更新 `_manual_adj_data()` 文档字符串

### Step 2：数据验证

1. 选取几只有除权事件的股票，对比修改前后的数据
2. 验证 close 和旧 adjclose 值是否一致
3. 验证在除权日前后，close 时间序列是否连续

### Step 3：全量数据重新处理

```bash
cd scripts/data_collector/tushare
python collector.py normalize_data --source_dir ./source --normalize_dir ./normalize
python ../../dump_bin.py dump_all \
  --data_path ./normalize \
  --qlib_dir ~/.qlib/qlib_data/cn_data \
  --freq day --date_field_name date --symbol_field_name symbol \
  --exclude_fields symbol,date --file_suffix .csv
```

### Step 4：验证下游功能

1. 跑一轮 Alpha158 特征提取，确认无报错
2. 跑一轮 LightGBM 训练 + 回测，对比修改前后结果
3. 重点关注含除权事件股票的 Label 和回测收益

### Step 5：文档更新

1. 更新 README.md 中的字段说明
2. 更新 alpha158_factors.md 中关于 $close 的描述
3. 更新 tushare_collector_plan.md

---

## 6. 首日标准化：已移除 ✅

已移除 `_manual_adj_data()` 首日标准化逻辑，价格保持**真实前复权人民币价格**。

**移除理由**：
- Alpha158 特征几乎全是比值或相对量，标准化因子会约掉，移除不影响特征值
- Label 是相邻日 close 的比值，标准化因子约掉，移除不影响 Label
- 回测价格恢复真实 CNY 尺度，使绝对金额、最小交易限制等更加准确
- 全面排查确认 qlib/backtest、qlib/contrib、测试用例均不依赖首日标准化约定

**修改内容**：删除 `_get_first_close()` 和 `_manual_adj_data()` 方法，在 `normalize()` 中移除对 `_manual_adj_data()` 的调用。

---

## 7. 风险提示

1. **数据重新处理**：修改后所有数据需要重新 normalize + dump，耗时取决于数据量
2. **模型重训练**：所有已训练模型的结果不再有效，需要重新训练
3. **结果不可比**：修改前后的回测结果不具可比性（特征分布变化）
4. **Yahoo 数据源**：`scripts/data_collector/yahoo/collector.py` 也有类似的 adjclose 逻辑，需同步检查（但本项目仅使用 Tushare，可后续处理）
