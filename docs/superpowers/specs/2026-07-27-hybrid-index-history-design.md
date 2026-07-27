# CSI500/CSI1000 训练专用 Hybrid 历史设计

日期：2026-07-27  
状态：已批准  
适用模块：`scripts/data_collector/csindex_v2/`

## 1. 背景

当前 `csindex_v2` 对 CSI500 和 CSI1000 只使用可审计的中证官网公告，
两者的连续覆盖均从 2015-11-30 开始。研究模型因此从 2016 年开始训练。

本次增加两个训练专用指数池：

- `csi500_hybrid`
- `csi1000_hybrid`

它们从 2010 年开始提供近似历史，但在 2015-11-30 及以后必须与现有
`csi500`、`csi1000` 公告口径逐日完全一致。原官方指数池、当前研究基线、
评估池和实盘配置均不自动切换。

## 2. 目标与非目标

### 2.1 目标

1. 从 2010 年开始提供可用于训练样本实验的 CSI500/CSI1000 近似成分。
2. 保持 `csindex_v2` 的官方公告事件流和四个现有指数产物不变。
3. 2015-11-30 起，hybrid 的成分区间逐行继承官方产物。
4. 将 hybrid 接入现有每日指数调度：历史前缀冻结，官方后缀每日重建。
5. 保存来源、参数、覆盖日期、成员数和输入哈希，保证近似历史可审计和复现。
6. 在写入 Qlib 前强制验证公告后缀完全一致；失败时拒绝覆盖旧 hybrid。

### 2.2 非目标

1. 不声称 2015-11-30 以前的 hybrid 是官方指数历史。
2. 不修改 `backtest/EXPERIMENT_STANDARD.md` 的固定训练区间或提升基线。
3. 不自动修改现有训练、评估或实盘配置。
4. 不将 Tushare 的近似事件混入 `parsed/all_changes.csv`。
5. 不在每日任务中重新下载或重新计算 2010–2015 的历史前缀。

## 3. 时间线与数据源

统一公告切换点为 `2015-11-30`；前一个交易日为 `2015-11-27`。

### 3.1 CSI500 Hybrid

| 区间 | 来源 | 日期语义 |
|---|---|---|
| 2010-01-29～2015-11-27 | Tushare `index_weight` 月末全量快照 | 快照日在册，持有到下一快照前一交易日 |
| 2015-11-30～未来 | `csindex_v2` 官方 `csi500_instruments.txt` | 原公告日口径，逐行原样继承 |

### 3.2 CSI1000 Hybrid

| 区间 | 来源 | 日期语义 |
|---|---|---|
| 2010-01-29～2015-04 月末 | Tushare `daily_basic.total_mv` 代理池 | 当月月末截面，持有到下一截面前一交易日 |
| 2015-05-29～2015-11-27 | Tushare `index_weight` 月末全量快照 | 快照日在册，持有到下一快照前一交易日 |
| 2015-11-30～未来 | `csindex_v2` 官方 `csi1000_instruments.txt` | 原公告日口径，逐行原样继承 |

CSI1000 代理池在每个月末：

1. 读取所有沪深 A 股 `total_mv`；
2. 删除 `total_mv` 缺失或非正数的记录；
3. 剔除当日 CSI300 官方成分；
4. 剔除当日 CSI500 Tushare 成分；
5. 按 `total_mv` 降序、证券代码升序稳定排序；
6. 取前 `min(1000, 可用数量)` 只。

不额外模拟 ST、流动性、上市时长及精确调样缓冲区。该取舍符合“时间不要求特别准确”的
训练扩样目的，并在清单中明确标记为 proxy。

## 4. 组件与数据流

新增独立模块 `scripts/data_collector/csindex_v2/hybrid_history.py`，包含三个边界清晰的阶段。

### 4.1 一次性回填

命令：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  -m scripts.data_collector.csindex_v2.hybrid_history backfill
```

职责：

- 从 `TUSHARE_TOKEN` 创建 Tushare Pro 客户端；
- 补齐 CSI500、CSI1000 的 `index_weight` 月末缓存；
- 按 Qlib 日历逐月补齐 2010-01～2015-04 的 `daily_basic` 总市值截面；
- 每成功取得一个月即落盘，支持中断续跑；
- 不删除或覆盖已有有效月份。

### 4.2 冻结历史前缀

命令：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  -m scripts.data_collector.csindex_v2.hybrid_history freeze-prefix
```

职责：

- 标准化快照证券代码；
- 构造月度 roster；
- 将 roster 转成 Qlib 的闭区间 `[start, end]`；
- 把所有区间裁剪到 `2015-11-27`；
- 输出两个不可变 prefix CSV；
- 输出 manifest，记录算法版本、日期、来源、月度成员数和输入 SHA-256。

### 4.3 每日拼接

命令：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  -m scripts.data_collector.csindex_v2.hybrid_history build
```

职责：

- 读取冻结 prefix；
- 读取当天刚构建的官方 `csi500/csi1000` instruments；
- 验证 prefix 没有越过切换点；
- 将 prefix 与官方行直接拼接，不跨切换点合并区间；
- 验证 hybrid 中 `start >= 2015-11-30` 的行与官方文件完全相同；
- 输出 `changes/csi500_hybrid_instruments.txt` 和
  `changes/csi1000_hybrid_instruments.txt`。

缓存布局：

```text
~/.cache/qlib/csindex_v2/hybrid/
├── snapshots/
│   ├── csi500_index_weight.parquet
│   ├── csi1000_index_weight.parquet
│   └── total_mv_monthly.parquet
├── prefixes/
│   ├── csi500_hybrid_prefix.csv
│   └── csi1000_hybrid_prefix.csv
└── manifest.json
```

现有 `tushare_snapshots/csi500.parquet` 和 `csi1000.parquet` 可作为迁移输入；
新模块先复用有效缓存，不重复请求。

## 5. 日调度接入

现有顺序扩展为：

1. 增量抓取官方公告和快照；
2. 重建四个官方指数；
3. 安装四个官方指数；
4. 使用冻结 prefix 构建两个 hybrid；
5. 验证公告后缀；
6. 原子安装两个 hybrid；
7. 对四个官方指数执行现有官网快照只读校验；
8. 记录六个已安装指数的当前成员数。

hybrid 不加入官网快照校验循环，因为当前截面已经由“公告后缀逐行相同”保证；
官网快照仍只验证四个官方指数。

若 prefix 缺失、损坏或公告后缀不一致：

- 四个官方指数照常安装和校验；
- 不覆盖上一次成功安装的 hybrid；
- 日调度返回失败并记录清晰错误，触发现有告警。

## 6. 安全与一致性

1. `OFFICIAL_INDICES` 与 `HYBRID_INDICES` 分开定义，避免把 hybrid 当成官网指数处理。
2. 所有构建先写临时文件，验证通过后再 `os.replace` 到目标路径。
3. 官方文件是后缀的唯一来源，不通过 Tushare 覆盖或修补公告段。
4. 切换点校验同时检查：
   - prefix 最大结束日不晚于 2015-11-27；
   - hybrid 的官方行与官方输入 DataFrame 完全相同；
   - 切换点及之后的逐日成员集合相同；
   - 区间无倒置、无重叠。
5. Tushare token 只从运行环境读取，不写入源码、缓存或日志。

## 7. 测试

### 7.1 单元测试

- Tushare 代码到 Qlib 代码的转换；
- 月末截面稳定排序和 `total_mv` 代理池选择；
- CSI300/CSI500 剔除；
- roster 到闭区间的转换；
- 切换日前缀裁剪；
- 同一证券多段成员区间不重叠；
- 缓存去重与断点续跑；
- manifest 哈希和参数完整。

### 7.2 拼接回归测试

- 官方输入的每一行均原样出现在 hybrid 后缀；
- 任一官方行被修改、遗漏或增加时校验失败；
- 2015-11-30 起逐日成员集合相同；
- hybrid 文件不改变原 `csi500.txt`、`csi1000.txt`。

### 7.3 调度测试

- 日更先安装官方池，再构建和安装 hybrid；
- hybrid 构建失败时官方安装仍完成；
- hybrid 构建失败时旧 hybrid 不被覆盖；
- 官网快照校验只遍历官方池；
- 日更结果包含六个池的当前成员数。

## 8. 实验使用

本次只构建数据能力，不自动跑模型实验。后续若比较更早训练区间，属于
`train-data` 方向的 Phase M 实验：

- `baseline_ref` 使用当时生效的研究基线；
- 固定五种子；
- valid/test 时间不变；
- 只改变训练池或训练起点；
- 按 `EXPERIMENT_STANDARD.md` 登记 registry、更新报告和清理产物。

## 9. 验收标准

1. 两个 hybrid 的最早日期均不晚于 2010-01-29。
2. CSI500 前缀来自完整的 500 只 Tushare 月末快照。
3. CSI1000 代理期按已批准的 `total_mv` 规则构造。
4. 2015-11-30 起两个 hybrid 与对应官方池逐日完全一致。
5. 每日调度可自动刷新 hybrid 官方后缀。
6. 原有四个指数文件、公告事件流、基线配置和实盘配置不被修改。
7. 相关测试通过，文档包含一次性回填和日更行为说明。
