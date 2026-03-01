# 修改计划：将 `download_index_data` 中东方财富指数行情接口替换为 Tushare API

## 1. 背景

`qlib/scripts/data_collector/yahoo/collector.py` 中 `YahooCollectorCN1d.download_index_data()` 方法使用东方财富（eastmoney）的 HTTP 接口获取 CSI300、CSI100、CSI500 三个指数的日线行情数据。该接口已启用反爬虫机制，导致请求失败，函数不可用。

需要将数据源替换为 [Tushare Pro](https://tushare.pro/) 的 `index_daily` 接口。

## 2. 当前实现分析

### 2.1 代码位置

**文件**：`qlib/scripts/data_collector/yahoo/collector.py`，第 226-256 行

```python
class YahooCollectorCN1d(YahooCollectorCN):
    def download_index_data(self):
        _format = "%Y%m%d"
        _begin = self.start_datetime.strftime(_format)
        _end = self.end_datetime.strftime(_format)
        for _index_name, _index_code in {"csi300": "000300", "csi100": "000903", "csi500": "000905"}.items():
            df = pd.DataFrame(
                map(
                    lambda x: x.split(","),
                    requests.get(
                        INDEX_BENCH_URL.format(index_code=_index_code, begin=_begin, end=_end)
                    ).json()["data"]["klines"],
                )
            )
            df.columns = ["date", "open", "close", "high", "low", "volume", "money", "change"]
            df["adjclose"] = df["close"]
            df["symbol"] = f"sh{_index_code}"
            # ... 保存到 CSV
```

### 2.2 东方财富接口返回字段

| 位置 | 字段 | 含义 |
| --- | --- | --- |
| 0 | date | 日期 |
| 1 | open | 开盘价 |
| 2 | close | 收盘价 |
| 3 | high | 最高价 |
| 4 | low | 最低价 |
| 5 | volume | 成交量 |
| 6 | money | 成交额 |
| 7 | change | 涨跌幅 |

### 2.3 下游依赖

保存的 CSV 文件（如 `sh000300.csv`）会进入后续的 normalize 和 dump_bin 流程，要求列名和格式一致：

- 必需列：`date`, `open`, `close`, `high`, `low`, `volume`, `adjclose`, `symbol`
- `adjclose` 设为与 `close` 相同（指数无复权概念）
- `symbol` 格式为 `sh{index_code}`（如 `sh000300`）

### 2.4 相关常量

`INDEX_BENCH_URL`（第 45 行）仅在此方法中使用，替换后可删除。

## 3. Tushare `index_daily` 接口说明

### 3.1 接口信息

- **接口名**：`index_daily`
- **文档**：https://tushare.pro/document/2?doc_id=95
- **权限要求**：2000 积分起
- **单次限制**：最多 8000 行

### 3.2 调用方式

```python
import tushare as ts

pro = ts.pro_api('YOUR_TOKEN')
df = pro.index_daily(ts_code='000300.SH', start_date='20260212', end_date='20260225')
```

### 3.3 返回字段

| Tushare 字段 | 含义 | 对应目标列 |
| --- | --- | --- |
| trade_date | 交易日期（YYYYMMDD） | date |
| open | 开盘价 | open |
| close | 收盘价 | close |
| high | 最高价 | high |
| low | 最低价 | low |
| vol | 成交量（手） | volume |
| amount | 成交额（千元） | money |
| pct_chg | 涨跌幅(%) | change |

### 3.4 指数代码映射

| 指数 | 东方财富代码 | Tushare ts_code |
| --- | --- | --- |
| CSI300 | 000300 | 000300.SH |
| CSI100 | 000903 | 000903.SH |
| CSI500 | 000905 | 000905.SH |

## 4. 详细修改方案

### 4.1 修改文件：`qlib/scripts/data_collector/yahoo/collector.py`

#### 步骤 1：替换 `download_index_data` 方法

将东方财富请求替换为 Tushare `index_daily` 调用：

```python
def download_index_data(self):
    import os
    import tushare as ts

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError(
            "TUSHARE_TOKEN environment variable is not set. "
            "Please set it to your Tushare Pro API token."
        )

    pro = ts.pro_api(token)
    _format = "%Y%m%d"
    _begin = self.start_datetime.strftime(_format)
    _end = self.end_datetime.strftime(_format)

    for _index_name, _index_code in {"csi300": "000300", "csi100": "000903", "csi500": "000905"}.items():
        logger.info(f"get bench data: {_index_name}({_index_code})......")
        try:
            ts_code = f"{_index_code}.SH"
            df = pro.index_daily(ts_code=ts_code, start_date=_begin, end_date=_end)

            if df is None or df.empty:
                logger.warning(f"{_index_name} returned empty data")
                continue

            # 字段映射和格式转换
            df = df.rename(columns={
                "trade_date": "date",
                "vol": "volume",
                "amount": "money",
                "pct_chg": "change",
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
            df["adjclose"] = df["close"]
            df["symbol"] = f"sh{_index_code}"
            df = df[["date", "open", "close", "high", "low", "volume", "money", "change", "adjclose", "symbol"]]

            # 保存（与原逻辑一致：追加到已有文件）
            _path = self.save_dir.joinpath(f"sh{_index_code}.csv")
            if _path.exists():
                _old_df = pd.read_csv(_path)
                df = pd.concat([_old_df, df], sort=False)
            df.to_csv(_path, index=False)
        except Exception as e:
            logger.warning(f"get {_index_name} error: {e}")
            continue
```

#### 步骤 2：删除 `INDEX_BENCH_URL` 常量

第 45 行的 `INDEX_BENCH_URL` 不再有引用，删除。

### 4.2 不需要修改的文件

- `base.py` — 不涉及
- `dump_bin.py` — 不涉及
- `fund/collector.py` — 有自己独立的 `INDEX_BENCH_URL`，不受影响

## 5. 字段对齐说明

### 5.1 成交量单位差异

- **东方财富**：volume 单位为**股**
- **Tushare**：vol 单位为**手**（1 手 = 100 股）

由于指数行情的 volume 在后续 normalize 流程中会经过 `adjusted_price` 复权调整，且指数的 `adjclose == close`（factor = 1），volume 值只做透传不做比例计算，因此单位差异不影响最终结果的正确性。不过需注意：如果有下游代码对 volume 的绝对值做了假设，可能需要关注此差异。

### 5.2 成交额单位差异

- **东方财富**：money 单位为**元**
- **Tushare**：amount 单位为**千元**

同理，`money` 字段在后续流程中被 `exclude_fields` 排除，不参与 bin 文件生成，因此不影响结果。

## 6. 使用方式

与之前替换 `get_hs_stock_symbols` 相同，使用前需设置环境变量：

```bash
export TUSHARE_TOKEN="your_tushare_pro_api_token"
```

## 7. 风险与注意事项

1. **单次 8000 行限制**：Tushare 单次最多返回 8000 行。对于日线数据，8000 个交易日约 32 年，完全覆盖增量更新场景
2. **Token 复用**：与 `get_hs_stock_symbols` 共用同一个 `TUSHARE_TOKEN` 环境变量
3. **Tushare 限频**：普通用户有调用频率限制，但三个指数只调用三次，不会触发限频
4. **向后兼容**：CSV 输出的列名和 symbol 格式完全兼容，下游 normalize 和 dump_bin 无需修改
