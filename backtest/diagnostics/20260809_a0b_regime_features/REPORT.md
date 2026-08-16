# A0b 阶段报告：Tier-1 regime 信号构造与领先性事件研究

> **恢复说明（2026-08-12）**：本文档在 2026-08-11 误清理后从会话转录逐字恢复。原目录下的中间产物（`tier1_signals_daily.csv`、`tier2_signals_daily.csv`、`event_study_*.csv`、`f_subtype_check.csv`、`a0b_event_study.png`、`tier2/_poc_summary.csv`）已丢失、未能恢复；最终特征文件 `backtest/configs/regime-adapt/regime_features_v1.csv` 为 gitignore 幸存文件，不受影响。

- 日期：2026-08-09（stage-1：本地价量信号；Tier-2 外部数据链路另行推进）
- 脚本：`backtest/scripts/_tmp_a0b_build_signals.py`（信号构造）、`_tmp_a0b_event_study.py`（事件研究）
- 产物：`tier1_signals_daily.csv`（2003-2026 日频，5729 天）、`event_study_paths.csv`、`event_study_triggers.csv`、`f_subtype_check.csv`、`a0b_event_study.png`

## 方法

- 信号统一做**因果 z 标准化**（250 日滚动均值/标准差，shift(1) 防前视）——与未来入模型的形态一致。
- 事件锚 = **行情实际底部**（episode 首月前 10 ~ 后 25 个交易日内全A等权净值最低点），修正了月度标签起点与行情启动日错位近一个月的问题（如 924 行情 9 月 24 日启动）。
- 触发定义：窗口 [-20,+10] 内 z≥1 连续 3 日；领先日为负 = 行情底部前触发。
- 本地无 `$amount` 字段，成交额用 `$volume×$vwap` 构造。

## 触发统计（13 个 F、10 个 T episode）

| 信号 | F 触发 | F 中位领先日 | T 触发 | T 中位领先日 | 定级 |
|---|---|---|---|---|---|
| **amount_surge**（量能 5/60 日比） | **9/13** | **−13** | 4/10 | −15 | F 先导，主力 |
| **mom_cum20**（动量利差 20 日累计） | 7/13 | −19 | 5/10 | −9 | F/T 双先导，主力 |
| **idx_uplow60**（距 60 日低点涨幅） | 7/13 | −21 | 4/10 | −9 | 先导 + 亚型判别 |
| lowvol_highvol_cum20（低波利差累计） | 6/13 | −12 | **5/10** | −15 | T 先导 |
| limitup_share / limitup2_share（涨停/连板占比） | 6/13 | 0 / −4 | ≤2/10 | — | F **同步确认**（非先验） |
| disp20（截面离散度） | 5/13 | −3 | 1/10 | — | F 同步 |
| newhigh_share（120 日新高占比） | 4/13 | −2 | 2/10 | — | T 确认 |
| small_big_cum20（小-大利差累计） | 4/13 | 0 | 2/10 | — | 辅助 |
| eqw300_mom20（等权-300 比价动量） | 3/13 | +8 | 2/10 | — | 弱，降级 |

注：中位领先日以行情底部为 0 点；延续型 episode（起点非底部）会拉负领先日，V 反型的触发多在底部后 0~+5 日内——即对 V 反急涨，多数信号是"确认型"而非"预测型"，**先验性主要来自量能与利差动量的前置爬升**。

## F 态亚型判别（A0a 硬性要求的验证，关键结论）

以起点（行情底部）当天的**指数距 250 日高点回撤（idx_dd250）**判别：

| 亚型 | 判别 | episode（A0a Q0 尾部超额 %/yr） |
|---|---|---|
| 延续主升（防御尾部**赢**） | dd250 ≥ −10% | 2015-02（**+153**）、2006-05（+34）、2020-02（+13）、2007-01（+11） |
| 底部 V 反（防御尾部**输**） | dd250 ≤ −15% | 2015-09（**−107**）、2012-01（−36）、2008-09（−27）、2019-02（−26）、**2024-09/924（−25**）、2024-02（−2）、2016-02（+2 中性）、2009-08（0 中性） |

- **非中性案例（|Q0|>10）分离度 100%（8/8）**；两个中性案例落在 V 反区，方向预测保守但不反向。
- `idx_uplow60` 同样分离（赢家 +15%~+42%，输家 ≤ +11%）。
- 结论：**idx_dd250 与 idx_uplow60 必须进入 M3 特征集**——它们让模型能条件化学习两类 F 态相反的尾部映射，这正是 A0a 发现的 F 态内部异质性的解法。

## 入选建议（stage-1，冻结前还需 FS 存活检查）

- **核心 7 个**：amount_surge、mom_cum20、idx_uplow60、idx_dd250、lowvol_highvol_cum20、limitup_share、disp20。
- 备选：limitup2_share（与 limitup_share 相关性高，二选一）、newhigh_share、small_big_cum20。
- 剔除：eqw300_mom20（触发差、且与 small_big_cum20 信息重叠）。
- 全部信号覆盖 2004-01 起的完整训练时代（z 标准化需 120 日 warmup，2003 年数据可覆盖）。

## Tier-2 外部数据（stage-2，2026-08-09 完成）

**渠道决策：全部用 tushare**（与行情数据同源，token 走 `~/.qlib_live_env` 的 `TUSHARE_TOKEN`），AkShare 不再需要。PoC 探测 7 个接口全部有权限（`tier2/_poc_summary.csv`）：

| 序列 | tushare 接口 | 实际覆盖 | 处置 |
|---|---|---|---|
| 两融余额（沪+深） | `margin` | 2010-03~今 | 已落盘；**注意最新一日常缺交易所披露，聚合时须剔除不全日期** |
| Shibor 1周（DR007 代理） | `shibor` | 2006-10~今 | 已落盘 |
| 股指期货主力连续 | `fut_daily(ts_code="IF.CFX"等)` | IF 2010+ / IC 2015+ / IM 2022+ | 已落盘，基差=主力收盘/指数−1（优先 IC） |
| 期权日线 | `opt_daily` | 2015+ | 通路正常；IV 需自算，**暂缓** |
| ETF 份额 | `fund_share` | 通路正常 | 暂缓（分页拉取成本高、先验性存疑） |
| 新增开户数 | `stk_account` | **仅 2015-05~2019-02**（披露停更） | **剔除** |
| 北向资金 | — | 2024-08 后停高频披露 | 剔除（维持 stage-1 结论） |

构造信号（`tier2_signals_daily.csv`，因果 z 同框架）：`margin_bal_chg20`（两融余额 20 日变化）、`basis_pct`（主力基差率）、`shibor1w_neg_chg20`（利率 20 日变化取负，宽松=正）。

**Tier-2 事件研究结果**（episode 数受历史深度限制）：

| 信号 | F 触发 | F 中位领先 | T 触发 | 定级 |
|---|---|---|---|---|
| basis_pct | 4/8 | −4 | 3/6（−15） | **入选**：风险偏好拐点的同步信号，924 当天基差由负转正、3 日内冲至 +4.4% 罕见升水 |
| shibor1w_neg_chg20 | 5/11 | −1 | 1/7 | **入选**：同步偏弱但历史深度最长（2006+），924 触发即货币宽松 |
| margin_bal_chg20 | 2/8 | −7（前锋 z −0.80） | 1/6 | **降级为归因备选**：底部前余额仍在下降，转正滞后行情数日，属确认型 |

结论：Tier-2 以 `basis_pct` + `shibor1w_neg_chg20` 两列进 M3（NaN 哨兵，覆盖不到 2007-09 F 段已在计划中声明）；先验性主力仍是 Tier-1 的量能与利差动量。

## 入模型形态（2026-08-09 定稿）

- 特征文件：`backtest/configs/regime-adapt/regime_features_v1.csv`（生成脚本 `backtest/scripts/build_regime_features.py`）。
- 11 列：7 个 Tier-1 因果 z（250 日滚动、shift(1)、clip±5）+ 2 个 Tier-2 z（NaN 哨兵）+ `idx_dd250`/`idx_uplow60` 原始值。Tier-1 z 自 2003-09-30 全有效，覆盖 2004-01 训练起点。
- 注入方式：`Alpha158RegimeTechnical` handler（`backtest/features/regime.py`）以 day 级广播列并入个股面板。
- FS 存活检查（csi300 迷你训练，B6-M 超参）：REGIME 列存活率与总体特征相当（5/11、6/11 vs 44%/47%），无系统性被筛；`idx_dd250_z`、`idx_uplow60`、`lowvol_highvol_cum20_z` 在全部子模型稳定存活。

## 待办

1. Tier-2 日更链路并入 postclose cron（晋升后再做，实验期手动快照即可）。
