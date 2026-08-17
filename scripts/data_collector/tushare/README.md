# Tushare Pro 日线数据采集

基于 [Tushare Pro](https://tushare.pro/document/2?doc_id=27) 的 A 股日线采集与 qlib bin 入库。

## 约定

- **close**：前复权收盘价（与 open/high/low 保持一致，均为前复权价格，单位为真实人民币）
- **vwap**：前复权日均价 = amount×1000 / (vol×100) × factor（元/股；无 amount 或停牌为空）
- **涨跌幅**：使用 Tushare 接口返回的 pct_chg（不再用复权价计算）
- 所有价格字段（open/high/low/close）统一前复权，**不做首日标准化**，保持真实价格
- 支持**天级增量更新**
- 正常的单交易日更新按 `trade_date` 各调用一次全市场 `daily` 和
  `adj_factor`，不再为每只股票分别请求；Tushare 官方日线单次上限为
  6000 行，当前 A 股股票池可在一次请求内返回
- 批量结果会在写文件前校验日期、字段、重复行、6000 行截断风险和股票池覆盖率；
  校验或接口失败时整批回退到原有逐股票采集，避免发布半批数据
- 多日历史回补及传入 `limit_nums` 的调试任务仍使用逐股票路径；停牌股票在单日
  批量结果中没有新行，其已有历史 CSV 不会被清空或覆盖

## 环境

- 设置 `TUSHARE_TOKEN`（在 [Tushare 用户中心](https://tushare.pro/user/token) 获取），例如
  `export TUSHARE_TOKEN="你的 token"`。日度 cron 会加载 Git 忽略的
  `~/.qlib_live_env`，也可在该文件中设置同名变量；不要把 token 写入代码或配置。
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

日志出现 `full-market daily batch saved` 表示使用快速路径；出现
`falling back to per-symbol collection` 表示批量接口或完整性校验失败，任务已自动切换到
较慢但兼容的逐股票路径，应结合后续 `all steps OK` 判断当日入库是否最终成功。

若需**手动分步**做增量：先在本目录执行 `download_data` 和 `normalize_data`，再在仓库根目录执行 `python scripts/dump_bin.py dump_update --data_path scripts/data_collector/tushare/normalize --qlib_dir <qlib_dir> ...`（参数同上面 dump_all，仅把 `dump_all` 改为 `dump_update`）。

### 5. 定时任务（工作日，日志按日期写入 logs/data）

已提供脚本 `run_update_to_bin.sh`，顺序执行：

1. `update_data_to_bin`（日线采集 + 归一化 + dump）
2. 指数成分日更
3. vwap 巡检
4. 前复权回溯完整性巡检（CSI300，近 90 天）
5. ST 日频名单增量更新（`st_calendar.py update`）

**原则：** 每一步都会跑完（单步失败不阻断后续）；任一步失败通过 Server酱推微信（环境变量 `SERVERCHAN_SENDKEY`，建议放在 `~/.qlib_live_env`）；任一步失败则脚本最终以非 0 退出。stdout/stderr 追加到 **qlib 根目录** 下 `logs/data/YYYY-MM-DD.log`。

**一次性设置 crontab**（在终端执行）：

```bash
crontab -e
```

在打开的编辑器中加入一行（路径按你本机 qlib 根目录修改；与实盘约定一致时用 16:30）：

```
30 16 * * 1-5 /Users/yuxianqi/Project/qlib/scripts/data_collector/tushare/run_update_to_bin.sh
```

保存退出即可。

**手动执行脚本**（不依赖 cron）：

```bash
/Users/yuxianqi/Project/qlib/scripts/data_collector/tushare/run_update_to_bin.sh
```

## ST 日频名单

回测 Phase M（默认 `--st-daily`）与实盘 `run_publish_signals.py` 共用同一份缓存
`scripts/data_collector/tushare/st_daily.csv`（gitignore，不进 git）。查询只做
`st_symbols_on(daily, as_of)`，禁止用「当前名字含 ST」的静态快照。

两路来源与优先级：

- `stock_st(trade_date=YYYYMMDD)`：交易所日频权威，**只按交易日拉**。禁止
  `start_date/end_date` 范围查询（实测会被截成 1000 行）。单日 `len>=1000` 拒收。
- `namechange`：区间展开到 qlib 交易日历。必须按 `ann_date` **年度分片**
  （`YYYY0101`–`YYYY1231`）；任一年 `len>=10000` 拒收。另补一次无日期全量只做差集兜底。
- 同日两路都有时以 `stock_st` 为准。
- 退市整理期：`name` 含「退」（沪市前缀 `退市创兴`、深/北市后缀 `天龙退`）由
  `namechange` + `stock_basic.delist_date` 覆盖；`stock_st` 不含整理期。

首次部署必须手工 backfill（数分钟、约 2340 次 `stock_st` 调用）：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  scripts/data_collector/tushare/st_calendar.py update \
  --qlib-dir ~/.qlib/qlib_data/cn_data --backfill
```

缓存缺失时增量 `update` 以非 0 退出，提示先 `--backfill`。日更只追加新交易日，
并刷新当年与上一年 `namechange`。审计区间 `st_calendar.csv` 仅供人读，不参与过滤。

产物（均 gitignore）：

- `st_daily.csv`：过滤唯一事实来源
- `st_namechange_raw.csv`：namechange 原始区间
- `st_calendar.csv`：由日频压出的区间，仅审计

盘后 `run_postclose_cron.sh` 已先跑 `run_update_to_bin.sh` 再发布；ST 名单会在
publish 之前更新。发布侧缓存 `max_date < signal_date` 会直接失败，不得静默跳过。

## 修改计划

详见 [docs/vibe_coding/tushare_collector_plan.md](../../../docs/vibe_coding/tushare_collector_plan.md)。
