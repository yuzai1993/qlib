# Tushare Pro 日线数据采集

基于 [Tushare Pro](https://tushare.pro/document/2?doc_id=27) 的 A 股日线采集与 qlib bin 入库。

## 约定

- **close**：真实收盘价（未复权）
- **adjclose**：前复权收盘价 = close / adj_factor，复权因子来自 [复权因子接口](https://tushare.pro/document/2?doc_id=28)
- **涨跌幅**：使用 Tushare 接口返回的 pct_chg（不再用复权价计算）
- 支持**天级增量更新**

## 环境

- 设置 `TUSHARE_TOKEN`（在 [Tushare 用户中心](https://tushare.pro/user/token) 获取）
- 安装依赖：`pip install -r requirements.txt`

## 使用

需在 `scripts/data_collector/tushare` 目录下执行，或保证可 `import collector` 到本目录的 `collector` 模块。

### 1. 全量采集

```bash
cd scripts/data_collector/tushare
python collector.py download_data --source_dir ./source --start 2020-01-01 --end 2025-02-28
```

### 2. 归一化

```bash
python collector.py normalize_data --source_dir ./source --normalize_dir ./normalize
```

### 3. 写入 qlib bin（全量）

`dump_bin` 不会执行 Tushare collector，只读取**上面第 2 步的归一化结果目录**（`--normalize_dir`）。因此必须先完成 1、2 步，再把同一目录作为 `--data_path`：

```bash
# 在 qlib 仓库根目录执行（或 data_path/qlib_dir 用绝对路径）
python scripts/dump_bin.py dump_all \
  --data_path scripts/data_collector/tushare/normalize \
  --qlib_dir ~/.qlib/qlib_data/cn_data \
  --freq day --date_field_name date --symbol_field_name symbol \
  --exclude_fields symbol,date --file_suffix .csv
```

### 4. 天级增量更新（采集 + 归一化 + 增量 dump）

**推荐**：一条命令完成采集、归一化、增量写入 bin（无需单独执行 `dump_bin dump_update`）：

```bash
cd scripts/data_collector/tushare
python collector.py update_data_to_bin --qlib_dir ~/.qlib/qlib_data/cn_data --start_date 2025-02-27
```

不传 `start_date` 时，取**在 calendars/day_future.txt 中但不在 calendars/day.txt 中的最早日期**；若 day_future.txt 不存在或差集为空，则回退为 day.txt 最后一日的下一日或昨日。

若需**手动分步**做增量：先在本目录执行 `download_data` 和 `normalize_data`，再在仓库根目录执行 `python scripts/dump_bin.py dump_update --data_path scripts/data_collector/tushare/normalize --qlib_dir <qlib_dir> ...`（参数同上面 dump_all，仅把 `dump_all` 改为 `dump_update`）。

### 5. 定时任务（每周一至五 18:00，日志按日期写入 logs/data）

已提供脚本 `run_update_to_bin.sh`，会执行 `update_data_to_bin` 并将 stdout/stderr 追加到 **qlib 根目录** 下 `logs/data/YYYY-MM-DD.log`。

**一次性设置 crontab**（在终端执行）：

```bash
crontab -e
```

在打开的编辑器中加入一行（路径按你本机 qlib 根目录修改）：

```
0 18 * * 1-5 /home/yuzai/qlib/scripts/data_collector/tushare/run_update_to_bin.sh
```

保存退出即可。含义：每周一至周五 18:00 执行该脚本，日志自动写入 `logs/data/` 下以当天日期命名的文件。

**手动执行脚本**（不依赖 cron）：

```bash
/home/yuzai/qlib/scripts/data_collector/tushare/run_update_to_bin.sh
```

## 修改计划

详见 [docs/vibe_coding/tushare_collector_plan.md](../../../docs/vibe_coding/tushare_collector_plan.md)。
