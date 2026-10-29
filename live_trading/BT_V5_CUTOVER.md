# BT v5 交付与实盘切换记录

2026-10-30更新：用户确认10月30日为目标交易日，实际可用现金244,607元、持仓为0、无活动委托，Windows旧QMT已停用。00:48按用户要求**重新生成10月29日信号并一次性发布10月30日新版批次** `20261030_alla_v2_top5d1f100_postclose_real_002`，5买0卖，原子写入共享 `inbox` 并逐字节校验。审计为 `live_trading/logs/bt_v5_published_20261030.json`。**后续已按用户要求启用Mac工作日20:00生产调度，Windows执行器与任务仍由操作者配置启动。**

01:12调度及监控验证记录为 `live_trading/logs/bt_v5_services_enabled_20261030.json`。LaunchAgent显式使用 `_active` 配置，`RunAtLoad=false`、`KeepAlive=false`，注册后运行次数0；第一场定时触发为10月30日20:00，生成11月2日信号。日报与告警沿用Server酱通道。监控默认读取新版账本，右上角可切换历史账本；已验证新002批次5笔计划、现金244607元、切换隔离及数据库/WAL未被监控改写。

同日修复执行计划缺少股票名称：一次性发布时新账本尚未刷新名称缓存，现已通过现有Tushare刷新脚本补齐5919条名称，并在浏览器确认5笔计划的代码与名称。刷新前后其余18张表的数据哈希完全一致，未重发批次或修改现金及委托。审计为 `live_trading/logs/top5_names_repair_20261030.json`，页面截图为同名PNG；日常调度已有名称刷新步骤。

用户选择继续剪贴板通信，已补齐 `topk-cash-v1` 能力、卖单终态门禁、卖后现金与持仓名额冻结、重启去重和Mac回执导入。部署包位于共享 `strategy/bt_v5_clipboard_20261030`（Windows `D:\qmt_bridge\strategy\bt_v5_clipboard_20261030`）；完整安装、只读验收与任务配置见 [新版Top5剪贴板部署](qmt_strategy/README_TOPK_CLIPBOARD.md)。最终本地实盘回归1424通过；15项旧v4原始模型导出测试因本机缺少训练产物单独排除，完整首轮的失败原因已保存。真实Windows往返与券商字段仍须现场验收。

准备分支 `codex/bt-v5-top5-cutover` 的12个提交已快进合入 `/Users/yuxianqi/Project/qlib` 的 `main`（集成提交为 `5e3fe6ad`）。原工作区 `/Users/yuxianqi/.codex/worktrees/bt-v5-top5-cutover/qlib` 暂保留审计资料。原准备配置仍指向本地暂存桥；新增操作配置 `configs/alla_v2_top5d1f100_postclose_real_active.yaml` 指向 `/Volumes/qmt_bridge`，与准备配置共享新策略ID及独立账本。新账本保留原始资本30万元参考，通过明确记录的交接现金校正-55393元得到244607元，未伪造成交或改动旧账本；审计为 `live_trading/logs/bt_v5_1030_handoff.json`。收益基点按新策略交接余额设置，旧收益历史保留在旧库。

Mac旧策略LaunchAgent `com.yuxianqi.qlib-live-scheduler` 已禁用并卸载。旧10月30日批次 `20261030_alla_v4_ladder_k1h5_postclose_real_001` 的 `.done` 与 `.jsonl` 已从共享 `inbox` 移入 `/Volumes/qmt_bridge/quarantine/20261030T002153+0800_old_1030_disabled/`，逐文件SHA校验一致；本地记录为 `live_trading/logs/old_1030_withdrawal_20261030.json`。这只证明共享入口已撤下，不撤销Windows本地journal、已有剪贴板请求或券商委托；旧QMT继续保持停止，Windows任务与手动中转进程还须停用。

此前的离线核对文件 `backtest/result/bt_v5_operator_preview_20261030/offline_review.json` 保留为历史预览，仍为 `publishable=false`。本次正式批次使用新跑的五模型评分，5216只、过滤后4934只，5个买入候选与原预览一致；执行价格与股数由新版relay在窗口内查询真实账户后确定。原始资金300,000元与当前可用现金244,607元是不同口径。

## 停止Windows旧中转

`QlibClipboardRelay` 是Windows任务计划程序中的启动规则，安装脚本默认在工作日14:54启动 `clipboard_relay.py auto`。中转程序是实际运行的Python进程：读取共享信号，通过剪贴板与QMT通信并回传成交和账户快照。定时任务可以启动它，手动终端也可以启动它。

在Windows PowerShell查看任务动作，确认它指向本项目的 `clipboard_relay.py`：

```powershell
$relayTask = Get-ScheduledTask -TaskPath '\' -TaskName 'QlibClipboardRelay'
$relayTask.Actions | Format-List Execute,Arguments,WorkingDirectory
```

然后禁用未来启动，并结束当前任务实例：

```powershell
Disable-ScheduledTask -InputObject $relayTask
Stop-ScheduledTask -InputObject $relayTask
Get-ScheduledTask -TaskPath '\' -TaskName 'QlibClipboardRelay' |
    Select-Object TaskName,State
```

预期State为 `Disabled`。若不存在该任务，仍需检查手动进程。手动终端优先在原窗口按 `Ctrl+C`。找不到窗口时精确列出本项目中转进程，不要结束所有Python：

```powershell
$relayProcesses = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(python|pythonw|py)[0-9.]*\.exe$' -and
    $_.CommandLine -match '(?i)(^|[\\/\s"])clipboard_relay\.py(?=["\s]|$)'
})
$relayProcesses | Select-Object ProcessId,Name,CommandLine | Format-List
```

确认列出的路径均为旧策略中转程序后，停止这些PID：

```powershell
$relayProcesses | ForEach-Object {
    Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
}
```

再次执行查询，结果应为空。禁用任务防止下一次启动，结束任务停止该任务当前实例；手动启动的进程须另行停止。旧QMT保持停用，不删除 `output/state`、`processed_batches.txt` 或 `active_*.json` 来尝试重置；这些状态可能包含旧批次恢复和去重证据。

## 冻结方案

- 历史执行层回测基线：`baseline/phase-m-v1-bt-v5`，原risk=0.90数字保留；拟实盘及配对回测按用户本次要求改为risk=1.0，结果独立登记。
- 模型：历史 **v2 · M0 H20 ES**，`regime-adapt/m0-h20-t5h5-es-v1`；五种子 `[42,1000,2000,3000,4000]`，早停 `top5_h5_net_ann`，最佳轮次 `[36,63,42,57,48]`。五个导出模型的164维输入及精简前后预测已核对；来源和导出 SHA 在 `models/v2_t5h5es/manifest.json`。
- 拟实盘策略：`TopkDropoutStrategy(topk=5,n_drop=1,hold_thresh=1,force_sell_rank=100,risk_degree=1.0,initial_buy_count=None)`。常规换出1只；掉出前100的持仓额外退出，受实际可卖数量和交易限制约束。hold=1 是最低持仓约束，不是每天清空所有股票。首日人工清理旧持仓是一次性切换步骤，不加入每日策略。
- 官方信号：每个种子先按日截面 z-score，再等权平均。模型研究基线继续是 **v4 RankIC ES**，CSI1000 B6-M/B4-S 不变。
- 30万元为账户原始资金口径，**切换时继承实际现金与持仓，不重新注入30万元或重置净值**。risk=1.0用于卖出后可用现金的名义买入预算；整手、最低佣金及限价预留可能留下现金，不保证实际满仓。买入还受真实现金和费用可负担上限约束。

## 回测证据与口径

官方历史基线仍是risk=0.90、100万元、原费率、2020-08-03～2026-07-31。CAGR 34.84%、夏普1.177、最大回撤−33.02%。历史窗参与过早停和策略选择，不是独立样本外。

原risk=0.90的30万元回测保留：买费0.0000954、卖费0.0005954、最低费5元，日收盘撮合。相同历史窗 CAGR 35.73%、夏普1.204、最大回撤−32.73%。这些不是risk=1.0的成绩。当前费率采用固定比例/最低费近似，不含真实竞价滑点、逐交易所精确过户费或其他实盘差异。

原risk=0.90在2026-08-03～2026-10-28的57日诊断为累计+15.59%、等权全A基准−0.80%、最大回撤−12.82%。它承接连续回测7月底的持仓和现金；不是实际实盘成绩，也不是8月重新投入30万元。未据此调参，不将短窗折年收益视为长期预期。risk=1.0使用相同冻结信号、窗口和费用另行复算，单独登记在`diag/bt-v5-300k-risk1-current-cost-20261029`。

risk=1.0复算结果：历史1454日CAGR37.23%、夏普1.209、最大回撤−33.68%；后续57日累计+16.25%、最大回撤−13.36%。后续区间同样承接模拟持仓，未模拟本次实际账户清仓，也不含本次清仓成本。完整数值以新诊断JSON为准。

数据和图表可从以下仓库相对路径查看：

- `backtest/experiments/phase_m_v1_bt_report.html`：当前基线与历史版本。
- `backtest/experiments/phase_m_v1_bt_v5.html`：BT v5与BT v4同口径比较。
- `backtest/experiments/phase_m_v1_bt_v5_cutover_diagnostic.html`：30万元/当前费率与新增区间。
- `backtest/experiments/bt_v5_cutover_20261029/`：逐日收益、有效配置、完整指标和来源哈希。
- `backtest/experiments/phase_m_v1_bt_v5_cutover_risk1_diagnostic.html`：用户指定risk=1.0的新诊断；与历史risk=0.90分开。

## 新执行协议

新配置为 `configs/alla_v2_top5d1f100_postclose_real.yaml`，配对回测为 `backtest/configs/alla_v2_top5d1f100_parity.yaml`。当前桥目录是本地 `live_trading/data/bt_v5_offline_bridge`，通知关闭，账本路径独立；这些是离线准备配置，尚未安装进生产调度。

`strategy.execution_policy=post_sell_cash_v1`、`max_positions=5` 开启新协议。订单使用 `CLOSE_AUCTION_TOPK_CASH_V1`，每单带相同的现金比例与持仓上限；旧执行器会拒绝未知价型，禁止只更新Mac端而继续用旧QMT执行器。原阶梯策略和不含该协议的旧批次维持原行为。

新协议等全部卖单达到终态后，读取有效的券商持仓、可用现金和委托快照；按真实剩余持仓计算空位，冻结买入名单和现金预算。部分卖出仍占一个持仓名额；未完成卖出不会靠时间截止强行释放名额。持仓/现金/委托信息缺失或有其他未标记活动委托时不继续买入。预算和已占名额持久化，重启不能重新放大预算。整仓卖出保留送股或部分成交造成的零股。

预算分母取空位裁切后的候选数，再做报价、可交易和整手检查。候选买不够一手或报价不可用时，原分配份额留作现金，不重分配给其他股票、不补新候选；顺序与研究回测一致。

QMT源版本须为 `2026-10-29-topk-cash-v1` 或经验证的后续版。模板保留 `MAX_ORDER_QUANTITY=100` 的保守缺省，不能直接当正式部署件使用，否则整仓卖出和买入会被截为100股。部署渲染应使用现有 `render_main_source` 的 `max_order_quantity=0`（移除固定股数上限，仍受新现金预算与5只持仓上限约束）、`execution_profile=CLOSE_AUCTION`，并关闭阶梯净额抵销。账户绑定在操作者本地完成；本次没有读取账户密钥或生成已绑定真实账户的运行件。

上段版本与渲染参数只适用于文件桥。本次用户选定的剪贴板执行器使用 `qmt_clipboard.py`，版本为 `2026-10-30-topk-cash-clipboard-v1`，配合新版relay/automation；无需运行上述文件桥渲染命令。共享包的 `ACCOUNT_ID` 保持为空，由操作者在QMT本地填写。

## 清仓前账本审计（10月29日历史）

本节保留清仓前证据，不代表用户10月30日确认的当前账户状态。最新空仓/现金假设见开头的离线核对文件；原账本没有据口头余额回填或伪造成交。审计目录已按逐文件哈希复制到生产根，原工作区副本保留。

只读来源：生产 `live_trading/data/alla_v4_ladder_k1h5_postclose_real.db`。最新隔离快照位于 `backtest/result/bt_v5_cutover_audit/20261029T154840002290Z_risk1_final/source_snapshot.db`，同目录`source_audit.json`保留审计，`source_file_evidence.json`记录读取前后文件哈希。

快照生成于上海时间2026-10-29 23:48:40，SHA-256：`2fd8d1b07c95d904a46b7bf2e1396c86a6084677d4017b891f963abb116da1eb`。23张表、现金51,839.06711849991、4只持仓合计13,400股和历史记录均一致；读取前后生产主数据库、WAL和SHM字节哈希一致。这是账本观察，不等于已核对实时券商状态。原有分层记录完整保留为历史；新策略不应继续运行阶梯到期逻辑。

清仓前检查记录的事项：

1. 快照中，2026-10-30旧策略有两笔计划订单，尚无对应成交回报。共享inbox原件现已隔离（见开头）；Windows本地日志与实际券商状态仍需保留核对，不能把共享入口撤下视为撤单回报。
2. 2026-09-02、09-24有历史部分成交回报，另有5笔历史卖出失败回报。它们不代表当前一定有活动委托，但切换前应确认剩余持仓已反映到账本，不能自动重放。
3. 新旧策略的client order ID不包含策略名。10月30日序号001已经使用；本快照中下一个未用序号是002。启用当时必须重新核对，不从新策略的001重新开始。

上次`hypothetical_20261029_intents.json`的1卖2买预览已经过时，不用于本次切换。新预览使用`preview_bt_v5_cutover.py --mode manual_liquidation`，列出全部正持仓的账面股数，包括零股；券商可卖数量保持未知，卖出所得不提前计入可用现金。输出始终`publishable=false`、`ready_for_activation=false`，没有可提交订单或批次身份。清仓后候选允许包含原持仓中的股票，实际启用前必须按最新信号重算。

清仓前清单在`backtest/result/bt_v5_cutover_audit/20261029T154840002290Z_risk1_final/manual_liquidation_preview.json`：4只原持仓全部列入人工核对清单。采用2026-10-29当天v2五种子评分（5216只，过滤后4934只），没有使用过期日期替代；评分文件及来源哈希位于同目录`v2_scores_20261029.pkl/.json`。这份历史清单不再作为当前持仓状态；用户已确认的空仓状态用于新的离线预览。

## 人工启用与回退顺序

以下为最初交付的切换检查顺序；当前一次性发布状态以本文开头记录为准。Windows真实交易启用和后续自动调度切换由操作者完成：

1. 停止旧策略继续生成和执行订单，核对旧策略已发布批次与券商活动委托。停止Mac调度不等于撤销已经发布或提交的订单；须确认旧批次不会随后被执行。
2. 在券商端确认实际持仓与可卖数量，再按本次清仓选择处理全部旧持仓。停牌、不可卖或部分成交留下的任何正持仓都阻断“清仓后重建”路径，不能在账本中先记为已卖出。
3. 依据真实成交、费用和券商资金记录完成对账，保留全部历史；手工成交若不能匹配系统订单，应按实际证据人工核对，不能伪造系统回执。确认实际持仓为零、无活动委托，并核对账本现金与券商可用现金，再生成新的只读审计。不能直接启用本次清仓前副本。
4. 复核最新v2五种子信号与risk=1.0配对配置。验收新版QMT执行器、账户绑定、真实桥目录和去重序号后，再由操作者切换调度。正常Topk策略从已确认的空持仓和真实可用现金开始建仓；不会再次自动执行首日清仓。

不要运行两个策略向同一账户同时发单。部署时检查QMT实际加载脚本的版本，而非只确认仓库模板已经更新。现金不足一手、费用或涨跌停造成的未建满仓位正常保留为现金，不重置账户净值，不把清仓日成本混入历史回测成绩。

启用前的只读一致性检查命令：

```sh
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/check_backtest_parity.py --config alla_v2_top5d1f100_postclose_real
```

`prepare_topk_cutover.py --risk-degree 1.0`只给审计目标记录本次risk，不修改源账本。退出码2表示成功生成离线审计但仍有启用阻断项；它从不自动启用交易。`run_publish_signals --dry-run`会构造可写账本，不应用它验证生产数据库；本次预览使用独立只读工具。

若需回退，先停止新策略继续产生批次，核对已经提交/成交的订单，再用**最新实际状态**恢复旧配置。不得用启用前的旧数据库覆盖新成交记录，也不得清空QMT去重状态来重放旧订单。旧配置和旧模型完整保留。
