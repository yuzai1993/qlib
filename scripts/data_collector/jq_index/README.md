# jq_index：基于聚宽重建宽基指数成分股历史

利用聚宽（JoinQuant）`jqdatasdk` 的 `get_index_stocks(code, date)` 接口，
逐交易日拉取沪深300、中证500、中证1000、中证2000的每日成分股快照，
推导成分变动事件，构建 Qlib `instruments.txt`。

## 覆盖指数与起始时间

| 指数 | 聚宽代码 | 成立日 | 标称样本数 |
|------|---------|--------|-----------|
| 沪深300 | `000300.XSHG` | 2005-04-08 | 300 |
| 中证500 | `000905.XSHG` | 2007-01-15 | 500 |
| 中证1000 | `000852.XSHG` | 2014-10-17 | 1000 |
| 中证2000 | `932000.XSHG` | 2023-02-17 | 2000 |

## 安装

```bash
pip install -r scripts/data_collector/jq_index/requirements.txt
```

## 使用

### 一、查看进度

```bash
python -m scripts.data_collector.jq_index.cli status
```

### 二、首次历史拉取（单日可跑完）

总调用量约 14,474 次，付费版日限 100 万次，约 2 小时一次跑完。

**推荐：用环境变量传账号密码**（避免明文留在 shell history）：

```bash
export JQ_USER=18657117125
export JQ_PWD=your_password   # 改成你改过的新密码
```

**一次性拉取全量历史：**

```bash
python -m scripts.data_collector.jq_index.cli pull
```

程序会按 CSI2000 → CSI1000 → CSI500 → CSI300 的顺序拉取。
若中途因网络等原因中断，再跑一次 `pull` 会自动从断点续拉。

### 三、构建 instruments

```bash
# 输出 csi300_jq.txt, csi500_jq.txt 等（不覆盖原文件）
python -m scripts.data_collector.jq_index.cli build

# 确认数据无误后，替换原文件
python -m scripts.data_collector.jq_index.cli build --suffix ""
```

### 四、与旧缓存交叉校验

旧缓存位于 `~/.cache/qlib/index/CSI300/` 和 `CSI100/`，
包含 2008-2021 年每次定期调样的官方附件。

```bash
python -m scripts.data_collector.jq_index.cli validate
```

输出：每次调样日期的一致率，以及差异明细。

### 每日维护

> **已停用**：定时任务不再调用聚宽。四指数日更改由中证官网公告 + 成分快照
> （`python -m scripts.data_collector.update_indices_daily`）。

本模块仍可用于历史重建 / 手工对比：

```bash
export JQ_USER=...
export JQ_PWD=...
python -m scripts.data_collector.jq_index.cli update
```

## 文件说明

| 路径 | 内容 |
|------|------|
| `~/.cache/qlib/jq_index/{index}/snapshots.parquet` | 每日成分快照（date, symbol 两列）|
| `~/.cache/qlib/jq_index/{index}/progress.json` | 拉取进度（断点续传用）|
| `~/.qlib/qlib_data/cn_data/instruments/{index}_jq.txt` | 构建好的 instruments（默认含 `_jq` 后缀）|

## API 调用量说明

| 指数 | 约需调用次数 |
|------|------------|
| CSI300 (2005→今) | ~5,520 |
| CSI500 (2007→今) | ~5,059 |
| CSI1000 (2014→今) | ~3,035 |
| CSI2000 (2023→今) | ~860 |
| **合计** | **~14,474** |

分 2 天完成，后续每日维护仅需 4 次调用。

> 若使用付费版（日限 100 万次），单日即可跑完全部 14,474 次。

## 旧缓存复用说明

`~/.cache/qlib/index/CSI300/` 和 `CSI100/` 下有 2008-2021 年的
每次定期调样附件（共 27 个事件），来自中证官方 Excel/CSV。

`validate` 命令会将 JQ 数据与这批旧缓存对比，
预期一致率 ≥ 95%（少量差异来自临时调整的时间精度）。
