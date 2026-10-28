# 量化研究平台

独立的本地研究与档案入口，包含实盘监控、离线实验和分析档案。现有实盘监控和交易程序保持运行；平台仅对 `127.0.0.1:8082` 发出白名单 GET 请求。

## 启动

从本平台的独立 checkout 运行：

```bash
/opt/anaconda3/envs/qlib/bin/python -m quant_platform
```

打开 <http://127.0.0.1:8090>。启动默认值：

- 研究源：`/Users/yuxianqi/Project/qlib_exp`
- 实盘监控：`http://127.0.0.1:8082`
- 分析存储：本 checkout 的 `quant_platform/data/analyses/`（忽略 Git）
- 监听：`127.0.0.1:8090`

源目录、归档目录和端口可显式指定，例如：

```bash
/opt/anaconda3/envs/qlib/bin/python -m quant_platform \
  --research-root /Users/yuxianqi/Project/qlib_exp \
  --data-root /Users/yuxianqi/.qlib/research_platform/analyses \
  --monitor-url http://127.0.0.1:8082 \
  --port 8090
```

端口被占用时启动会退出，不会关闭已有服务。停止本平台终端中的进程即可退出；原 8082 监控、交易调度和 QMT 都不需要重启。此模块不安装常驻调度，不读取交易环境密钥，不初始化生产 SQLite。

本次交付保留在分支 `codex/quant-research-platform` 的独立工作区；正在运行的实例使用工作区外的持久分析目录。关闭平台后，可使用以下命令重新启动：

```bash
cd /Users/yuxianqi/.codex/worktrees/quant-research-platform/qlib
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/qlib/bin/python -m quant_platform \
  --data-root /Users/yuxianqi/.qlib/research_platform/analyses
```

临时验收档案与上述持久目录分开，正式实例不会带入测试分析。此工作区不合并到当前生产 checkout。

## 使用

**实盘监控**保留概览、批次与成交、预测信号。浏览器刷新获取新数据；图表库不可用时使用本地曲线降级。上游离线时仅实盘模块显示连接状态，研究与分析仍可使用。

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

Python 测试使用临时档案和模拟监控响应，不启动或写入生产交易系统。分析存储会拒绝研究根、`~/Project/qlib` 生产根、`/Volumes/qmt_bridge` 及其大小写别名或父目录。
