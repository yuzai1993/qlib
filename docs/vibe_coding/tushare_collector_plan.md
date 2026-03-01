# Tushare 日线 Collector 修改计划

## 1. 目标与参考

- **目标**：在 `scripts/data_collector` 下新增基于 Tushare Pro 的 A 股日线采集与入库流程，产出 qlib bin 格式数据。
- **参考实现**：`scripts/data_collector/yahoo/collector.py`（YahooCollector / YahooNormalize / Run）。
- **数据与接口**：
  - 日线行情：[A股日线行情](https://tushare.pro/document/2?doc_id=27) `pro.daily(ts_code, start_date, end_date)`，字段含 open/high/low/close/pre_close/change/pct_chg/vol/amount 等。
  - 复权因子：[复权因子](https://tushare.pro/document/2?doc_id=28) `pro.adj_factor(ts_code, trade_date|start_date|end_date)`，字段 ts_code, trade_date, adj_factor。

## 2. 数据语义与存储约定

### 2.1 价格与复权

- **close**：仅存放**真实收盘价**（Tushare daily 的 `close`），不做复权。
- **adjclose（前复权收盘价）**：`adjclose = close / adj_factor`，复权因子来自 `adj_factor` 接口。
- **涨跌幅**：使用 **Tushare 接口返回的 pct_chg**（日线接口的涨跌幅字段），不再用前复权价格计算。Normalize 阶段将 pct_chg（%）转为小数作为 change。

### 2.2 归一化后写入 qlib 的字段（与 dump_bin 一致）

- 与现有日线一致：`date, symbol, open, high, low, close, volume, factor, change`。
- **close**：真实收盘价（见上）。
- **open / high / low**：前复权价格，即 `open/adj_factor`、`high/adj_factor`、`low/adj_factor`。
- **volume**：前复权成交量，与价格口径一致，即 `vol * adj_factor`（使前复权下 price×volume 口径一致）。
- **factor**：`factor = 1 / adj_factor`，满足 `adjclose = close * factor`，便于与现有 qlib 因子逻辑兼容。
- **change**：Tushare 接口返回的 pct_chg（%），转为小数写入，即 `change = pct_chg / 100`。

### 2.3 存储格式

- 原始采集结果先落盘为 CSV（按标的），再经 Normalize 生成带 factor/change 的 CSV，最后通过现有 `dump_bin` 流程写入 **qlib bin 格式**（day 频率），不新增 bin 结构。

## 3. 目录与文件结构

- 在 `scripts/data_collector/` 下新增子目录 **`tushare`**。
- 计划新增/修改文件：
  - `scripts/data_collector/tushare/collector.py`：Tushare 采集与归一化、Run 入口。
  - `scripts/data_collector/tushare/requirements.txt`：依赖（至少 `tushare`）。
  - 可选：`scripts/data_collector/tushare/README.md`：使用说明与示例命令。

## 4. Collector 设计（参考 YahooCollector）

### 4.1 标的与符号

- **标的列表**：使用 Tushare `stock_basic(exchange="", list_status="L")` 获取 A 股列表，或复用 `data_collector.utils.get_hs_stock_symbols()` 的标的集合；若复用需做一次 ts_code ↔ 内部 symbol 的映射（见下）。
- **内部 symbol**：与 qlib 及现有 dump 一致，采用前缀格式：`shXXXXXX` / `szXXXXXX`（与 Yahoo CN 一致）。
- **Tushare ts_code**：`XXXXXX.SH` / `XXXXXX.SZ`。采集时用 ts_code 调接口，落盘与 normalize 时用 sh/sz 前缀的 symbol。

### 4.2 采集流程（全量 / 增量统一）

- 按标的拉取：
  - **日线**：`pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)`，日期格式 `YYYYMMDD`。
  - **复权因子**：同一标的、同一日期区间 `pro.adj_factor(ts_code=ts_code, start_date=start_date, end_date=end_date)`。
- 单标的落盘 CSV 列建议：`date, open, high, low, close, volume, adj_factor, pct_chg, symbol`（或等价），其中：
  - `close` 为真实收盘价；
  - `pct_chg` 为 Tushare 日线接口返回的涨跌幅（%），供 Normalize 转为 change；
  - `adjclose = close / adj_factor` 在 Normalize 阶段计算。
- 采集逻辑复用基类 `BaseCollector` 的 `save_instrument`、`_simple_collector`、`collector_data`，不再在 TushareCollectorCN 中重写。

### 4.3 复权因子与涨跌幅

- 复权因子：每次拉取日线时同时拉取同区间的 `adj_factor`，落盘到 CSV；归一化时用 `adjclose = close / adj_factor`，对 open/high/low/volume 做前复权，**close 保持真实价**。
- 涨跌幅：**直接使用 Tushare 日线接口返回的 pct_chg**，在 Normalize 中转为 `change = pct_chg / 100`，不再用前复权价格计算。

## 5. Normalize 设计（参考 YahooNormalize1d）

- **输入**：Collector 产出的 CSV（按标的），含 date, open, high, low, close, volume, adj_factor, pct_chg（及 symbol）。
- **_get_calendar_list**：父类 `BaseNormalize` 中为抽象方法，子类必须实现；TushareNormalize1d 保留实现，调用 `get_calendar_list("ALL")`。
- **步骤**：
  1. 按 date 排序，与交易日历对齐（可复用 `get_calendar_list("ALL")` 或 Tushare 交易日历）。
  2. 计算 `adjclose = close / adj_factor`，`factor = 1 / adj_factor`。
  3. 前复权：`open = open/adj_factor`，`high = high/adj_factor`，`low = low/adj_factor`，`volume = vol*adj_factor`；**close 原样**。
  4. 涨跌幅：使用接口返回的 pct_chg，`change = pct_chg / 100`（pct_chg 为 %）。
  5. 可选：与 Yahoo 一致做「首日 close 标准化」；若不做，需在文档说明与 Yahoo 的差异。
  6. 输出列：`date, symbol, open, high, low, close, volume, factor, change`，供 dump_bin 使用。

## 6. 天级增量更新与 bin 更新

- **天级增量**：通过 `update_data_to_bin(qlib_dir, start_date=..., end_date=...)` 拉取指定区间的日线并归一化、dump_update。参数 **start_date** 未传时，取 **在 calendars/day_future.txt 中但不在 calendars/day.txt 中的最早日期**；若 day_future.txt 不存在或差集为空，则回退为 day.txt 最后一日的下一日或昨日。

- **与 Tushare collector 的关系**：`dump_bin` 的 **两条命令不会去执行** Tushare collector，而是**读取** Tushare 归一化后的 CSV 目录（即 collector 的 `normalize_dir`）。因此必须先跑 Tushare 的 `download_data` 和 `normalize_data`，得到 `normalize_dir`，再把该目录作为 `dump_bin` 的 `--data_path`。

- **端到端流程**：
  1. **Tushare 采集**（在 `scripts/data_collector/tushare` 下）：  
     `python collector.py download_data --source_dir ./source --start ... --end ...`  
     `python collector.py normalize_data --source_dir ./source --normalize_dir ./normalize`  
     得到目录 `./normalize`（即后面的 `<normalize_dir>`）。
  2. **写入 qlib bin**（在 **qlib 仓库根目录** 执行，或对路径使用绝对路径）：  
     - **全量**（首次或重建）：  
       `python scripts/dump_bin.py dump_all --data_path <normalize_dir> --qlib_dir <qlib_dir> --freq day --date_field_name date --symbol_field_name symbol --exclude_fields symbol,date --file_suffix .csv`  
       其中 `<normalize_dir>` 填第 1 步的归一化输出目录，例如 `scripts/data_collector/tushare/normalize` 或绝对路径。
     - **增量**（在已有 qlib 数据上追加）：  
       方式 A（推荐）：直接跑 Tushare 的 `update_data_to_bin`，内部会拉数 + 归一化 + 调用 dump_update：  
       `python collector.py update_data_to_bin --qlib_dir <qlib_dir> [--start_date ...]`（在 tushare 目录下执行）。  
       方式 B（手动分步）：先 `collector.py download_data` + `collector.py normalize_data`，再执行：  
       `python scripts/dump_bin.py dump_update --data_path <normalize_dir> --qlib_dir <qlib_dir> --freq day --date_field_name date --symbol_field_name symbol --exclude_fields symbol,date --file_suffix .csv`。  
      `qlib_dir` 下须已存在 `calendars/day.txt` 与 `instruments/all.txt`（即已做过一次 dump_all 或 GetData 初始化）。
- Run 类提供 `download_data`、`normalize_data`、`update_data_to_bin`（内部调 dump_update）等入口。`update_data_to_bin` 参数为 **start_date**；未传时由日历逻辑取默认起始日（见上）。

## 7. 依赖与配置

- **环境变量**：`TUSHARE_TOKEN`（必选），与现有 utils 中 Tushare 使用方式一致。
- **requirements**：`tushare`，版本按 tushare 官方推荐；若项目已有统一 requirements，可在 tushare 子目录的 requirements.txt 中仅列 tushare。

## 8. 实现顺序建议

1. **目录与占位**：新建 `tushare/`，`collector.py` 中实现 `TushareCollectorCN`（仅日线）、`TushareNormalize1d`、`Run`，先保证能跑通全量采集 + 归一化 + dump_all。
2. **采集**：实现按 ts_code 拉 daily + adj_factor，合并为带 adj_factor 的 CSV，close 存真实价，adjclose 或延到 Normalize 算。
3. **归一化**：实现前复权、factor、change（前复权涨跌幅），输出列与 dump_bin 约定一致。
4. **Run 与 CLI**：`download_data`、`normalize_data`、`update_data_to_bin(qlib_dir, start_date=..., end_date=...)`（调用 dump_update）；start_date 默认由 day_future/day 日历差集取最早日。

## 9. 与 Yahoo 的差异小结

| 项目         | Yahoo 日线           | Tushare 日线（本方案）        |
|--------------|----------------------|-------------------------------|
| close        | 复权后（adjclose 等） | **真实收盘价**                |
| adjclose     | 来自 Yahoo           | **close / adj_factor**        |
| 涨跌幅       | 基于 close           | **Tushare 接口 pct_chg**      |
| 复权因子来源 | Yahoo 接口           | **Tushare adj_factor 接口**  |
| 增量回溯     | 无复权因子回溯       | **按 adj_factor 回溯更新价格与成交量，close 不变** |

按此计划可实现：adjclose 为前复权、close 为真实价、qlib bin 存储、天级增量、涨跌幅使用 Tushare 接口 pct_chg；Collector 复用基类 save_instrument/_simple_collector/collector_data；update_data_to_bin 使用 start_date，默认由 day_future/day 日历取。
