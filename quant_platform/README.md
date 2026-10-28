# 量化研究平台

统一的本地服务，包含实盘监控、离线实验和分析档案，监听 `127.0.0.1:8090`。实盘查询直接使用 SQLite 只读连接，不再依赖旧 8082 Web 服务；交易执行、调度和每日快照/日报任务仍独立运行。

## 启动

在原 qlib 工作区手动运行（常驻服务已启动时无需重复运行）：

```bash
cd /Users/yuxianqi/Project/qlib
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/qlib/bin/python -m quant_platform
```

打开 <http://127.0.0.1:8090>。启动默认值：

- 研究源：`/Users/yuxianqi/Project/qlib_exp`
- 实盘配置：`live_trading/configs/alla_v4_ladder_k1h5_postclose_real.yaml`（只读加载）
- 分析存储：`/Users/yuxianqi/.qlib/research_platform/analyses/`
- 监听：`127.0.0.1:8090`

源目录、归档目录和端口可显式指定，例如：

```bash
/opt/anaconda3/envs/qlib/bin/python -m quant_platform \
  --research-root /Users/yuxianqi/Project/qlib_exp \
  --data-root /Users/yuxianqi/.qlib/research_platform/analyses \
  --project-root /Users/yuxianqi/Project/qlib \
  --live-config alla_v4_ladder_k1h5_postclose_real \
  --port 8090
```

端口被占用时启动会退出，不会关闭已有服务。平台不读取交易环境密钥、不初始化或迁移生产 SQLite。`mode=ro` 与 `query_only=ON` 禁止业务数据和 schema 写入；正常读取正在更新的 WAL，不使用可能漏数据的 immutable 模式。

## 常驻服务

复用原 Web 监控的 LaunchAgent `com.yuxianqi.qlib-live-monitor`，登录后启动、异常退出后重启。仓库模板位于 `live_trading/launchd/com.yuxianqi.qlib-live-monitor.plist`，安装路径为 `~/Library/LaunchAgents/`。原 8082 不再单独运行。

```bash
launchctl print gui/$(id -u)/com.yuxianqi.qlib-live-monitor
curl --noproxy '*' --fail http://127.0.0.1:8090/api/health
# 仅重启统一 Web 平台，不操作交易 scheduler
launchctl kickstart -k gui/$(id -u)/com.yuxianqi.qlib-live-monitor
```

日志沿用 `live_trading/logs/alla_v4_ladder_k1h5_postclose_real_web_service.{stdout,stderr}.log`。代码位于原 `/Users/yuxianqi/Project/qlib`，当前分支 `codex/qmt-clipboard-auto`，平台改动已迁回。分析档案位于工作区外，不随 Git 切换删除。

切换前的 plist 已保存在 `~/.qlib/research_platform/service_backups/20261028-single-service/com.yuxianqi.qlib-live-monitor.plist`。如需回退，先 bootout 上述 Web label，等待旧服务卸载完成，恢复备份 plist 再 bootstrap，可恢复原 8082；旧 `run_web_service.sh` / `scripts/run_web.py` 保留为回退入口。不要操作 `com.yuxianqi.qlib-live-scheduler`，也不要将 `run_monitor_cron.sh` 误当 Web 服务启动脚本。

## 使用

**实盘监控**保留概览、批次与成交、预测信号。浏览器刷新获取新数据；图表库不可用时使用本地曲线降级。账本缺失或 schema 查询异常时返回 503，研究与分析仍可使用。启动时账本缺失的实例需在数据源恢复后重启；平台不会尝试建表或修复实盘账本。

**离线实验**从研究根中的 `backtest/experiments/registry.jsonl` 索引记录，指标来自登记的 JSON，原 HTML 作为证据链接。模型和执行策略分别显示版本、年度、风格与实验记录。勾选 2–12 条合格同口径记录进行对比；可筛选、排序和导出 CSV。模型固定 top3×h5（算术净年化/HAC 波动/夏普），执行策略使用 CAGR，禁止跨轨道、池、窗口、费用与过滤口径混比。单种子和扩展窗口等诊断单独标识，不能加入正式比较。

历史源元数据不完整时保留来源说明；BT v3 的种子声明来自 registry。指标不从清理过的结果目录臆造补齐，缺失值显示为 `—`。研究源可持续更新，刷新页面即可重新读取；本平台不会运行报告生成器、训练、回测、pickle 反序列化或清理任务，也不修改实验 registry。

**分析档案**支持新建、编辑、归档与恢复、搜索及日期筛选。每篇包括背景、方法、结论、代码使用说明和至少一份代码；可粘贴或上传多份文本。单份代码限 256 KiB，最多 20 份，最终完整记录限 2 MiB。代码只保存、展示和下载，不执行。保存使用原子替换、跨进程锁及版本号，冲突时页面保留未提交内容。

现有 `backtest/diagnostics/*/REPORT.md` 以只读历史记录呈现。缺失图片或代码会如实显示。导出 ZIP 包含 `REPORT.md`、`metadata.json` 与 `code/`；可用这些导出包留存分析，归档或移除工作区前也应保存独立分析目录。

原始 HTML 在沙箱中展示，不执行脚本；依赖脚本的旧报告图形可能不显示，可下载原文件在本机自行查看。平台仅向注册资源发放读取入口，不提供任意本地路径读取或代码执行接口。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/quant_platform -q -p no:cacheprovider
node --test tests/quant_platform/test_frontend.cjs
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_monitor_web_api.py \
  tests/live_trading/test_monitor_web_frontend.py \
  tests/live_trading/test_repository_boundaries.py \
  tests/live_trading/test_operational_wrappers.py -q -p no:cacheprovider
```

Python 测试使用临时档案和临时 SQLite 账本，不启动或写入生产交易系统。只读验证另见 `tests/live_trading/test_monitor_read_only.py`。分析存储会拒绝研究根、`~/Project/qlib` 生产根、`/Volumes/qmt_bridge` 及其大小写别名或父目录。
