# 中证规模指数成分股历史重建（csindex_v2）

本文档描述如何从中证指数官网公告（及中证2000 的 Tushare 补充）重建沪深300 / 中证500 / 中证1000 / 中证2000 的历史成分区间，并输出 qlib 可用的 `instruments.txt`。

代码目录：`scripts/data_collector/csindex_v2/`  
缓存目录：`~/.cache/qlib/csindex_v2/`

---

## 1. 目标与产出


| 产出     | 路径                                | 说明                           |
| ------ | --------------------------------- | ---------------------------- |
| 成分区间   | `changes/{index}_instruments.txt` | qlib 格式：`SYMBOL\tstart\tend` |
| 区间 CSV | `changes/{index}_intervals.csv`   | 同上，便于分析                      |
| 起始日配置  | `changes/index_starts.json`       | 各指数覆盖起点与数据源策略                |
| 构建报告   | `changes/build_report.txt`        | 终局校验、警告                      |
| 全量变更   | `parsed/all_changes.csv`          | 去重后的 add/remove 事件流          |


**日期语义（当前默认）**：`all_changes.csv` 同时保留 `announce_date`（公告日）和
`effective_date`（生效日）用于追溯；区间起止统一使用**公告日**
（`DATE_MODE = "announce"`），缺公告日时才回退生效日。普通变更、人工修正和全量名单锚点
都通过同一日期解析逻辑，避免混用两种口径。

---

## 2. 数据源策略


| 指数          | 数据源          | 覆盖起始（公告日）      | 说明                               |
| ----------- | ------------ | -------------- | -------------------------------- |
| **csi300**  | 仅官方公告        | **2005-04-08** | 2005-07-01 生效的全量名单从公告日 2005-06-22 起正推；上市日初始名单由首次调样反推 |
| **csi500**  | 官方公告为主；2 条人工审核的 Tushare 月末快照补录 | **2015-11-30** | 定期调样使用官方公告；快速纳入缺失事件见 6.6 |
| **csi1000** | 仅官方公告        | **2015-11-30** | 同上                               |
| **csi2000** | Tushare 月末差分 | **2023-08-10** | 发布日初始 2000 只名单 + 月度快照差分          |


### 为何 csi500/1000 不从上市日开始？

2010-07 ~ 2015-06 期间，中证官网对中证500（及同期相关指数）的定期调样往往只有 **news 摘要**（「更换 50 只，举 1–2 个例子」），**无完整名单附件**，也无法从搜索 API 归档到当年栏目页。在「仅用公告」约束下，这段历史不可重建，故将覆盖起点定在 **2015-11-30**（首次可解析的完整 Excel 定期调样，公告 id=4272）。

csi1000 上市初期（2014-10 ~ 2015-06）同样缺少可解析的完整官方名单，起点与 csi500 对齐。

“官方公告为主”不等于忽略已经被本地快照证实、但无法检索到公告的事件。CSI500 的
`SH601598`、`SH601868` 快速纳入由 Tushare 首次月末快照与前后成员数共同验证，作为
`manual_fixes.csv` 的审核例外补入；日期只表示**首次可确认的月末快照日**，不声称是精确公告日。

### 为何 csi2000 用 Tushare？

中证2000 自发布后，定期调样名单多在指数页「拟生效样本」发布，公告侧经常无可用附件；Tushare `index_weight`（932000.CSI）可提供月末全量快照差分。

---

## 3. 端到端流水线

```
┌─────────────┐   ┌──────────────┐   ┌─────────────────────────────┐
│  crawler    │ → │ details/     │ → │ parser_content / excel / pdf │
│  搜索+详情  │   │ files/       │   │          ↓                  │
└─────────────┘   │ snapshots/   │   │ parsed/*_changes.csv        │
                  └──────────────┘   └─────────────┬───────────────┘
                                                   │
                  ┌──────────────┐                 ▼
                  │puller_tushare│ → parsed/tushare_gap_changes.csv
                  │ (仅 csi2000) │                 │
                  └──────────────┘                 ▼
                                         ┌─────────────────┐
                                         │   aggregator    │
                                         │ + manual_fixes  │
                                         └────────┬────────┘
                                                  ▼
                                         parsed/all_changes.csv
                                                  │
                                                  ▼
                                         ┌─────────────────┐
                                         │     builder     │
                                         │ → instruments   │
                                         │ → index_starts  │
                                         └─────────────────┘
```

### 3.1 爬虫（`crawler.py`）

1. **搜索 API** `csindex-home/search/search-content`
  关键词：`沪深300` / `中证500` / `中证1000` / `中证2000`  
   聚合为 `manifest.json`（id、日期、标题、命中关键词）。
2. **详情 API** `csindex-home/announcement/queryAnnouncementById`
  每条公告写入 `details/{id}.json`（正文 HTML + `enclosureList`）。
3. **附件下载**
  - 正式附件（`enclosureList`）→ `files/{YYYYMMDD}_{id}_{文件名}`  
  - 正文内嵌 `.xlsx/.xls/.pdf` 链接 → `files/{YYYYMMDD}_{id}_embed_...`
4. **当前成分快照**
  `snapshots/{code}cons.xls`（如 `000300cons.xls`），用于终局校验与反推锚点。

限速：请求间隔约 4s，翻页约 6s（见 `config.py`）。

### 3.1b 每日增量（`updater.py`）

```bash
python -m scripts.data_collector.csindex_v2.updater
# 或统一入口：
python -m scripts.data_collector.update_indices_daily
```

1. 增量搜索公告（整页无新增即停）→ 详情/附件  
2. 下载四指数官网成分快照（`000300/000905/000852/932000cons.xls`）  
3. 解析构建历史区间后安装到 `~/.qlib/qlib_data/cn_data/instruments/`  
4. **用官网快照差分对齐当前在册**（替代原聚宽日更；含 csi2000）  
5. 快照另存 `snapshots/daily/YYYYMMDD/` 便于回溯  

### 3.2 三路解析器


| 模块                  | 输入         | 典型年代      | 要点                                                  |
| ------------------- | ---------- | --------- | --------------------------------------------------- |
| `parser_content.py` | 正文 HTML 表格 | 2005–2014 | 形态 A/B/C；续行继承；「纳入/删除」表头                             |
| `parser_excel.py`   | xls/xlsx   | 2015+     | **X1**：sheet `调入`/`调出`（及同义 `换入`/`换出`）；**X2**：单表双层表头 |
| `parser_pdf.py`     | PDF 附件     | 2023+ 部分  | 分节识别沪深300/中证500/1000/2000                           |


只保留目标指数代码：`000300` / `000905` / `000852` / `932000`。

### 3.3 Tushare 补齐（`puller_tushare.py`）

- **仅拉取 csi2000**（`PULL_WINDOWS` 已收窄）。  
- 月末 `index_weight` 快照缓存于 `tushare_snapshots/csi2000.parquet`。  
- 相邻月末差分 → add/remove；若窗口内命中已知定期调样日则 `date_precision=exact`，否则 `month`。

### 3.4 聚合（`aggregator.py`）

1. 合并 content / excel / pdf / tushare_gap。
2. **非 csi2000 丢弃自动生成的全部 tushare 记录**；经人工审核的少数例外随后由
   `manual_fixes.csv` 显式补回，避免把月度粗化事件批量混入官方事件流。
3. 硬去重：同 `(指数, 股票, 方向, 生效日)`，来源优先级 excel > pdf > content > tushare。
4. 软去重：tushare 与官方同向且生效日相差 ≤45 天则丢 tushare（对 csi2000 仍有意义）。
5. 应用 `manual_fixes.csv`（add / drop / patch_date），同时传播人工记录的公告日和生效日。
6. 写出 `all_changes.csv`、`full_lists.csv`、`coverage.txt`。

### 3.5 构建（`builder.py` + `index_starts.py`）


| 指数               | 构建方式                                                                      |
| ---------------- | ------------------------------------------------------------------------- |
| csi300           | 锚点 = 2005-07-01 生效的全量名单（full_lists source_id=6773）；公告日模式从 2005-06-22 正推，再反推 2005-04-08 初始 300 只 |
| csi500 / csi1000 | 以当前官网快照为终点，**反推到 coverage_start**，再正序重建区间（保证终局与快照一致）                      |
| csi2000          | 锚点 = 发布公告 xlsx 2000 只；公告日模式从 2023-08-10 正推                              |


`index_starts.py` 自动检测 csi500/1000「无大缺口后的最早定期调样」作为起点，写入 `index_starts.json`。

---

## 4. 重建命令

在项目根目录、qlib conda 环境下：

```bash
# 若需更新公告/附件（慢，可跳过若缓存已齐）
# python -m scripts.data_collector.csindex_v2.crawler

# 解析（改解析器后必跑）
python -m scripts.data_collector.csindex_v2.parser_content
python -m scripts.data_collector.csindex_v2.parser_excel
python -m scripts.data_collector.csindex_v2.parser_pdf

# 仅当需要刷新 csi2000 差分
# python -m scripts.data_collector.csindex_v2.puller_tushare

# 聚合 + 构建
python -m scripts.data_collector.csindex_v2.aggregator
python -m scripts.data_collector.csindex_v2.builder
python -m scripts.data_collector.csindex_v2.validator
```

产物检查：

```bash
cat ~/.cache/qlib/csindex_v2/changes/build_report.txt
cat ~/.cache/qlib/csindex_v2/changes/index_starts.json
cat ~/.cache/qlib/csindex_v2/parsed/legacy_validation.txt
```

---

## 5. 人工修正（`manual_fixes.csv`）

无法或不宜自动解析的极少数事件，用 CSV 显式修正。字段为：

```text
action,index_name,symbol,type,effective_date,announce_date,note
```

`announce_date` 可为空；为空时 builder 按约定回退到 `effective_date`。对于同时知道公告日和
生效日的事件必须填写两列，不能把生效日覆盖为公告日。当前类别包括：


| 类型             | 例子                                                      |
| -------------- | ------------------------------------------------------- |
| **add**        | 美的集团上市首日纳入；上海航空退市调出；证券**换码**（300114→302132）；漏爬的临时成对调整（600090→600223）；月末快照证实的快速纳入（601598、601868） |
| **drop**       | 重复发布的调样行；未实际生效的调入（如红相股份）；H2 名单重复列出的调出                   |
| **patch_date** | 条件式/近似日期改为真实生效日                                         |


修改后只需重跑 `aggregator` + `builder`。

---

## 6. 终局偏差排查记录（2026-07）

官方-only 重建初期，csi500/1000 终局比官网快照各多出若干股票。根因与处理如下。

### 6.1 csi500：`SZ300114`（中航电测）


| 事实  | 说明                                                          |
| --- | ----------------------------------------------------------- |
| 官方  | 2023-12-08 调入中证500（id=15044，自中证1000 升入）                     |
| 换码  | 2025-02-17 代码变更为 **302132**（中航成飞），无单独「换码」公告                 |
| 后续  | 2025-06-13 以 **302132** 调出中证500并进入沪深300（id=15690）           |
| 症状  | 变更流只有 `300114 add` + `302132 remove`，中间未衔接 → 幽灵 `300114` 残留 |
| 修复  | manual：`2025-02-17` 对 csi500 `remove 300114` + `add 302132` |


### 6.2 csi1000：`SZ000546`（ST金圆）


| 事实  | 说明                                                               |
| --- | ---------------------------------------------------------------- |
| 官方  | 2023-07-07 临时调整公告 id=**14842**，附件 sheet 名为 **「换出/换入」**（非「调出/调入」） |
| 症状  | `parser_excel` 旧逻辑只认调入/调出 → 整份附件被跳过                              |
| 修复  | 解析器将 `换入/换出` 视为与 `调入/调出` 同义                                      |


### 6.3 csi1000：`SH600090`（*ST济堂）


| 事实   | 说明                                                                 |
| ---- | ------------------------------------------------------------------ |
| 官方调入 | 2016-12-12（id=4003）                                                |
| 调出   | 约 2020-07；对应 2020-07-03「**上证150等**指数」临时调整（*ST济堂），标题不含「中证1000」未进搜索池 |
| 修复   | manual：公告日 `2020-07-03`、生效日 `2020-07-13` remove，并与 `SH600223` add 成对处理 |


### 6.4 csi1000：`SZ002309`（ST中利）


| 事实   | 说明                                                                                 |
| ---- | ---------------------------------------------------------------------------------- |
| 官方调入 | 2018-12-17（id=11859）                                                               |
| ST   | 2022-05-31 起实施其他风险警示                                                               |
| 调出   | 按编制规则应为「次月第二个周五的下一交易日」≈ **2022-06-13**；官网临时调整公告未检索到；亦不在 2022-05-27 定期名单（id=14223）中 |
| 修复   | manual：`2022-06-13` remove                                                         |


### 6.5 csi1000：`SH600223`（鲁商发展）

- 官方：2016-12-12 调出中证1000（id=4003，xlsx 可核验）；2026-06-12 再次出现调出（id=3006137）。  
- Tushare 月末差分显示：2020-07 `SH600223` 调入与 `SH600090` 调出成对发生。  
- 对应临时调整公告日为 2020-07-03、生效日为 2020-07-13；官网标题未含「中证1000」，因此未进入搜索池。  
- manual 同时补入 `SH600223 add` 和 `SH600090 remove`，两者公告日/生效日完全一致。  
- 修复后 `SH600223` 的区间为 2020-07-03 ~ 2026-05-28，2026 年调出链闭合，不再出现幽灵调出警告。

### 6.6 csi500：`SH601598`、`SH601868` 快速纳入缺口

| 股票 | 可确认事实 | manual 记录日 | 日期精度 |
| --- | --- | --- | --- |
| `SH601598`（中国外运） | Tushare 在 2019-01 月末首次出现；后续 2019-06 调出链可闭合 | `2019-01-31` | 月，不是精确公告日 |
| `SH601868`（中国能建） | Tushare 在 2021-09 月末首次出现；后续 2021-12 调出链可闭合 | `2021-09-30` | 月，不是精确公告日 |

两条记录均未检索到可核验的中证调入公告，因此 `announce_date` 留空，builder 按通用规则
回退到上述记录日。这里不使用上市日代替指数公告日；备注保留本地证据和日期精度，后续如取得
官方公告，可直接补 `announce_date` 并保留当前 `effective_date` 供审计。

修复后四指数 **终局 roster 均与官网快照完全一致**。

---

## 7. 目录与关键文件

```
~/.cache/qlib/csindex_v2/
├── manifest.json              # 搜索结果元数据
├── details/{id}.json          # 公告详情
├── files/                     # PDF/Excel 附件
├── snapshots/{code}cons.xls   # 当前成分
├── tushare_snapshots/         # 月末权重缓存（csi2000 等）
├── parsed/
│   ├── content_changes.csv
│   ├── excel_changes.csv
│   ├── pdf_changes.csv
│   ├── tushare_gap_changes.csv
│   ├── all_changes.csv
│   ├── full_lists.csv
│   ├── coverage.txt
│   └── legacy_validation.txt   # 旧缓存交叉校验 + 构建产物结构校验
└── changes/
    ├── index_starts.json
    ├── build_report.txt
    ├── csi{300,500,1000,2000}_instruments.txt
    └── csi{300,500,1000,2000}_intervals.csv
```

模块内关键文件：


| 文件                 | 作用              |
| ------------------ | --------------- |
| `config.py`        | API、路径、指数元数据、限速 |
| `manual_fixes.csv` | 人工修正            |
| `index_starts.py`  | 覆盖起点检测与 JSON 输出 |
| `validator.py`     | 旧缓存匹配、区间结构、终局快照和关键历史链校验 |


---

## 8. 已知限制

1. **csi500：2010-07 ~ 2015-06** 十一期定期调样无完整官方名单（仅 news 摘要）。
2. **csi1000：2014-10 ~ 2015-06** 上市初期无完整官方变更链。
3. **搜索关键词盲区**：标题只写「上证150等」而不含「中证1000/500」的临时调整可能漏爬，需用有证据的成对 manual 修正（见 600090→600223）。
4. **证券换码**：官网很少发「旧码→新码」专用公告，需靠行情/重组公告 + manual 衔接（见 300114→302132）。
5. **csi2000**：完全依赖 Tushare 月度粒度，临时调整日可能被贴到月末或最近定期日。
6. **反推起点 roster 数量**可能略偏离额定（如 csi500 起始约 502）：来自覆盖起点之前未建模的临时调整残差，正推过程中会被后续调样消化；以终局快照一致为准。

---

## 9. 设计取舍摘要

- **宁可缩短覆盖区间，也不用 Tushare 填 csi300/500/1000**，保证这三段历史可追溯到官网公告（+ 少量有据可查的 manual）。  
- **csi500/1000 用「快照反推 + 截断起点」**，避免从残缺早期名单正推导致终局漂移。  
- **区间用公告日**：与「信息可知」时点对齐，便于实盘/研报对齐；`all_changes.csv` 仍保留真实生效日以便审计。若需成交日语义，改 `builder.DATE_MODE = "effective"` 后重建即可。
