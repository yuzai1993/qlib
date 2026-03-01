# 修改计划：将 `get_hs_stock_symbols` 中东方财富接口替换为 Tushare API

## 1. 背景

`qlib/scripts/data_collector/utils.py` 中的 `get_hs_stock_symbols()` 函数用于获取沪深市场全部 A 股标的列表。其内部嵌套方法 `_get_symbol()` 当前使用东方财富（eastmoney）的 HTTP 接口分页爬取股票列表，但该接口已启用反爬虫机制，导致请求失败，函数不可用。

需要将数据源替换为 [Tushare Pro](https://tushare.pro/) 的 `stock_basic` 接口。

## 2. 影响范围分析

### 2.1 当前实现

**文件**：`qlib/scripts/data_collector/utils.py`，第 167-275 行

`_get_symbol()` 方法的核心逻辑：

1. 向东方财富 `http://99.push2.eastmoney.com/api/qt/clist/get` 发起分页请求
2. 从返回的 JSON 中解析出股票代码（纯数字，如 `600519`、`000001`）
3. 根据代码前缀添加 yahooquery 格式后缀：
   - `6` 开头 → `.ss`（上交所）
   - `0` 或 `3` 开头 → `.sz`（深交所）
4. 返回 `set`，如 `{"600519.ss", "000001.sz", ...}`

外层 `get_hs_stock_symbols()` 的逻辑：
- 多次调用 `_get_symbol()` 直到收集到 ≥ `MINIMUM_SYMBOLS_NUM`（3900）个标的
- 与本地缓存文件 `~/.cache/hs_symbols_cache.pkl` 合并后排序返回

### 2.2 调用方

| 文件 | 用法 |
| --- | --- |
| `scripts/data_collector/yahoo/collector.py` | `YahooCollectorCN.get_instrument_list()` 调用，获取符号列表后用于 yahoo 数据采集 |
| `scripts/data_collector/pit/collector.py` | `PITCollector.get_instrument_list()` 调用，获取符号列表后用于 PIT 数据采集 |

两个调用方都期望返回带 `.ss` / `.sz` 后缀的符号列表（yahooquery 格式），且依赖后续的 `normalize_symbol()` 方法将 `.ss` 转为 `sh`、`.sz` 保持为 `sz`。

### 2.3 接口不变性

**`get_hs_stock_symbols()` 的返回值格式不变**：返回排序后的 `list`，元素为 `"{code}.ss"` 或 `"{code}.sz"` 格式字符串。这样所有调用方无需任何修改。

## 3. Tushare API 说明

### 3.1 接口信息

- **接口名**：`stock_basic`
- **文档**：https://tushare.pro/document/2?doc_id=25
- **权限要求**：2000 积分起
- **调用方式**：

```python
import tushare as ts

pro = ts.pro_api('YOUR_TOKEN')
df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')
```

### 3.2 返回数据示例

| ts_code | symbol | name |
| --- | --- | --- |
| 000001.SZ | 000001 | 平安银行 |
| 600519.SH | 600519 | 贵州茅台 |
| 300750.SZ | 300750 | 宁德时代 |

### 3.3 代码映射关系

Tushare 返回的 `ts_code` 后缀为 `.SH`（上交所）和 `.SZ`（深交所），需要转换为 yahooquery 格式：

| Tushare 后缀 | yahooquery 后缀 | 说明 |
| --- | --- | --- |
| `.SH` | `.ss` | 上交所 |
| `.SZ` | `.sz` | 深交所 |

## 4. 详细修改方案

### 4.1 修改文件：`qlib/scripts/data_collector/utils.py`

#### 步骤 1：新增 import

在文件头部添加：

```python
import os
```

（`tushare` 在函数内部延迟导入，避免未安装时影响其他功能）

#### 步骤 2：替换 `_get_symbol()` 方法

将当前基于东方财富分页爬取的实现替换为 Tushare 调用：

```python
def _get_symbol():
    import tushare as ts

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError(
            "TUSHARE_TOKEN environment variable is not set. "
            "Please set it to your Tushare Pro API token. "
            "Get your token at https://tushare.pro/user/token"
        )

    pro = ts.pro_api(token)
    # 获取所有正常上市的股票
    df = pro.stock_basic(exchange='', list_status='L', fields='ts_code')

    if df is None or df.empty:
        raise ValueError("Failed to fetch stock list from Tushare.")

    _symbols = []
    for ts_code in df['ts_code']:
        code, exchange = ts_code.split('.')
        if exchange == 'SH':
            _symbols.append(f"{code}.ss")
        elif exchange == 'SZ':
            _symbols.append(f"{code}.sz")
        elif exchange == 'BJ':
            # 北交所标的暂不纳入，与原逻辑保持一致
            continue

    if len(_symbols) < MINIMUM_SYMBOLS_NUM:
        raise ValueError(
            f"Incomplete stock list from Tushare: got {len(_symbols)}, "
            f"expected >= {MINIMUM_SYMBOLS_NUM}"
        )

    return set(_symbols)
```

#### 步骤 3：简化外层重试逻辑

由于 Tushare 接口稳定性远高于爬虫，不再需要合并多次调用结果的循环。但为保持兼容性和鲁棒性，保留 while 循环和缓存合并机制不变。

#### 步骤 4：移除不再需要的常量

`HS_SYMBOLS_URL` 常量（第 24 行）已无引用，可以删除。

### 4.2 不需要修改的文件

- `scripts/data_collector/yahoo/collector.py` — 仅调用 `get_hs_stock_symbols()`，接口不变
- `scripts/data_collector/pit/collector.py` — 同上

## 5. 使用方式变更

用户在使用前需要设置 Tushare Token 环境变量：

```bash
export TUSHARE_TOKEN="your_tushare_pro_api_token"
```

或在 Python 中设置：

```python
import os
os.environ["TUSHARE_TOKEN"] = "your_tushare_pro_api_token"
```

## 6. 风险与注意事项

1. **Token 安全**：Tushare Token 通过环境变量传入，不硬编码在代码中
2. **积分要求**：`stock_basic` 接口需要 2000 积分，用户需确保账户积分充足
3. **北交所标的**：原东方财富接口不包含北交所（`8` 开头），Tushare 可能返回北交所（`.BJ`）标的，本方案中跳过北交所以保持一致
4. **网络依赖**：Tushare 接口仍需网络访问，但无反爬虫问题
5. **向后兼容**：返回格式完全兼容，所有下游调用方无需修改
