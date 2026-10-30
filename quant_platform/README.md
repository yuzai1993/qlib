# 量化研究平台

统一的本地服务，包含实盘监控、离线实验和分析档案，监听 `127.0.0.1:8090`。实盘查询直接使用 SQLite 只读连接，不再依赖旧 8082 Web 服务；交易执行、调度和每日快照/日报任务仍独立运行。

## 启动

在原 qlib 工作区手动运行（常驻服务已启动时无需重复运行）：

```bash
cd /Users/yuxianqi/Project/qlib
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/qlib/bin/python -m quant_platform \
  --live-config alla_v2_top5d1f100_postclose_real_active
```

打开 <http://127.0.0.1:8090>。默认值与仓库常驻服务模板配置：

- 项目根和研究源：`/Users/yuxianqi/Project/qlib`
- 实盘配置：`live_trading/configs/alla_v2_top5d1f100_postclose_real_active.yaml`（命令行默认值与当前生产一致，仍建议显式指定；只读加载）
- 分析存储：`/Users/yuxianqi/Project/qlib/backtest/diagnostics/`，每个子目录是一份分析
- 监听：`127.0.0.1:8090`

源目录、归档目录和端口可显式指定，例如：

```bash
/opt/anaconda3/envs/qlib/bin/python -m quant_platform \
  --research-root /Users/yuxianqi/Project/qlib \
  --data-root /Users/yuxianqi/Project/qlib/backtest/diagnostics \
  --project-root /Users/yuxianqi/Project/qlib \
  --live-config alla_v2_top5d1f100_postclose_real_active \
  --port 8090
```

省略 `--research-root` 时使用 `--project-root`；省略 `--data-root` 时使用研究源中的 `backtest/diagnostics`。显式指定的 `--data-root` 表示一套完整的分析目录集合，适合仓库外的测试或独立档案，不再混入另一处历史目录。活动配置从 `qlib` 读取研究资料；修改仓库模板不会自动切换正在运行的服务，实际研究根和分析目录可由 `/api/health` 的 `research_root`、`analysis_storage` 核验。

2026-10-30 已把必要基线训练原件、正式回测记录和持久化评估 JSON 迁入 `qlib`，更新已安装的 LaunchAgent 并重载平台；运行接口已确认研究根为 `/Users/yuxianqi/Project/qlib`，随后移除了旧 `qlib_exp` 工作区。旧源31条可用记录与2,623个既有指标值均保留，主根现有36条记录、35条指标源可用，1条历史缺失保持标注。迁移清单及切换验收见 `backtest/experiments/_catalog/audits/repository_migration_20261030/`；实盘交易调度器未改动。

端口被占用时启动会退出，不会关闭已有服务。平台不读取交易环境密钥、不初始化或迁移生产 SQLite。`mode=ro` 与 `query_only=ON` 禁止业务数据和 schema 写入；正常读取正在更新的 WAL，不使用可能漏数据的 immutable 模式。

## 常驻服务

复用原 Web 监控的 LaunchAgent `com.yuxianqi.qlib-live-monitor`，登录后启动、异常退出后重启。仓库模板位于 `live_trading/launchd/com.yuxianqi.qlib-live-monitor.plist`，显式设置项目根和研究根为 `/Users/yuxianqi/Project/qlib`，安装路径为 `~/Library/LaunchAgents/`。原 8082 不再单独运行。

```bash
launchctl print gui/$(id -u)/com.yuxianqi.qlib-live-monitor
curl --noproxy '*' --fail http://127.0.0.1:8090/api/health
# 仅重启统一 Web 平台，不操作交易 scheduler
launchctl kickstart -k gui/$(id -u)/com.yuxianqi.qlib-live-monitor
```

日志沿用 `live_trading/logs/alla_v4_ladder_k1h5_postclose_real_web_service.{stdout,stderr}.log`。代码位于原 `/Users/yuxianqi/Project/qlib`，平台与 QMT 剪贴板改动已合入 `main`，工作目录也使用 `main`。分析档案位于工作区外，不随 Git 切换删除。

切换前的 plist 已保存在 `~/.qlib/research_platform/service_backups/20261028-single-service/com.yuxianqi.qlib-live-monitor.plist`。如需回退，先 bootout 上述 Web label，等待旧服务卸载完成，恢复备份 plist 再 bootstrap，可恢复原 8082；旧 `run_web_service.sh` / `scripts/run_web.py` 保留为回退入口。不要操作 `com.yuxianqi.qlib-live-scheduler`，也不要将 `run_monitor_cron.sh` 误当 Web 服务启动脚本。

## 使用

**实盘监控**保留概览、批次与成交、预测信号。浏览器刷新获取新数据；图表库不可用时使用本地曲线降级。账本缺失或 schema 查询异常时返回 503，研究与分析仍可使用。启动时账本缺失的实例需在数据源恢复后重启；平台不会尝试建表或修复实盘账本。

左侧的**模型实验、策略实验**各含「Benchmark」「实验记录」。数据从研究根中的 `backtest/experiments/registry.jsonl` 及登记的 JSON 读取，原 HTML 作为证据链接。旧 `#research` 链接仍可打开模型实验。

- **Benchmark** 核心表始终展示全部已晋升 baseline，按版本从新到旧排列；首列勾选控制下方分维度表，表头可全选或清空。模型与策略分别记住当前会话的选择。核心表下方可切换分风格、分年；每个指标一张表，行是版本，列是年度或风格。单次实验详情使用同一套展示方式。CSV 导出当前已选版本的分维度指标。
- **实验记录**展示日期、实验内容、结论、报告链接和晋升版本。晋升仅来自明确的版本登记或 `promoted_to` / `promoted_from` 关系，不从指标表现推测。可搜索筛选、查看详情、导出，并勾选 2–12 条合格同口径记录正式对比。

模型固定 top3×h5（算术净年化/HAC 波动/夏普），策略使用 CAGR，包含基准涨幅，页面不再展示策略算术年化；全局 RankIC 仅在模型核心指标展示。禁止跨轨道、池、窗口、费用与过滤口径混比。单种子和扩展窗口等诊断单独标识，不能加入正式比较。

历史源元数据不完整时保留来源说明；BT v3 的种子声明来自 registry。指标不从清理过的结果目录臆造补齐，缺失值显示为 `—`。研究源可持续更新，刷新页面即可重新读取；本平台不会运行报告生成器、训练、回测、pickle 反序列化或清理任务，也不修改实验 registry。

登记 `portable_eval_output` 的实验优先读取这份持久化 JSON，指标资源链接与来源显示同步指向它；原 `eval_output` 作为来源证据保留。明确登记的持久化源缺失、无效或越界时如实报告，不自动改读旧结果掩盖问题。这使正式摘要能够独立于可清理的原始运行目录保留。

**分析档案**直接扫描 `backtest/diagnostics` 的各个子目录，元数据保存在各目录的 `metadata.json`，正文、报告与代码分别保存。新建一个符合[目录协议](../backtest/diagnostics/README.md)的分析目录，刷新页面即可加载，无需重启平台。元数据损坏、路径越界或内容校验失败会明确报错，不根据报告标题猜测替代记录。

页面支持新建、编辑、归档与恢复、搜索及日期筛选。每篇可编辑分析包括背景、方法、结论、代码使用说明和至少一份代码；单份代码限 256 KiB，最多 20 份，完整记录限 2 MiB。代码仅按文本保存和下载。编辑使用不可变版本目录，最后原子更新元数据指针；跨进程锁与版本号防止旧编辑覆盖新版本，冲突时页面保留未提交内容。

现有三份历史报告保留原始字节、稳定 ID、只读标识与缺失附件说明，现存的两份永久代码已归档到对应分析目录。导出 ZIP 包含 `REPORT.md`、`metadata.json`、`code/`，可编辑记录另含 `document.json`；解压到分析根的一个子目录后可直接重新加载。没有元数据的旧 `REPORT.md` 仍可兼容读取为只读历史资料，新分析应提供独立元数据。

原始 HTML 在沙箱中展示，不执行脚本；依赖脚本的旧报告图形可能不显示，可下载原文件在本机自行查看。平台仅向注册资源发放读取入口，不提供任意本地路径读取或代码执行接口。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/quant_platform -q -p no:cacheprovider
node --test tests/quant_platform/*.cjs
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_monitor_web_api.py \
  tests/live_trading/test_monitor_web_frontend.py \
  tests/live_trading/test_repository_boundaries.py \
  tests/live_trading/test_operational_wrappers.py -q -p no:cacheprovider
```

Python 测试使用临时档案和临时 SQLite 账本，不启动或写入生产交易系统。研究源内仅允许写入其 `backtest/diagnostics` 专属子树，其它研究、生产和实盘路径仍被拒绝；交易共享目录及其父目录始终禁止作为分析根。只读验证另见 `tests/live_trading/test_monitor_read_only.py`。
