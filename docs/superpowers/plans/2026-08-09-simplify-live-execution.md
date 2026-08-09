# 简化实盘发布与 prType=49 调试实现计划

## 目标

实现已确认规格：主发布端只生成/发布信号并保留审计链路，不再依赖 LIVE_OK、PR49_LIVE_OK、LIVE_TRADING_CONFIRM 或 SIMULATION/REAL 判断；新增主策略人工卖出覆盖；prType=49 独立为不接主监控的 QMT 调试策略。

## 实施步骤

### 1. 建立行为契约与测试基线

- 在 `tests/live_trading/test_run_publish_signals.py` 增加无模式环境变量、无 marker、执行状态为 PAUSED 时仍能发布的测试。
- 增加覆盖工具的参数校验、幂等、保留原始预测和最终 SELL 的测试。
- 增加独立 prType=49 请求/回执协议测试，确保不读取主 marker、不写主账本。
- 先运行现有 live_trading 测试，记录旧基线，避免把与新设计无关的历史测试失败混入变更。

### 2. 解耦主发布端

- 修改 `live_trading/scripts/run_publish_signals.py`：移除发布前的账户解析、LIVE_TRADING_CONFIRM、REAL 强制 LIVE 和 execution-state 阻断；批次仍保留审计元数据并写入账本。
- 修改 `live_trading/modules/live_config.py` 与相关调用，使发布配置允许缺少 broker environment/account id；账户信息仅供 QMT/快照采集端使用。
- 修改 `live_trading/scripts/run_publish_cron.sh`、`run_publish_catchup_cron.sh` 以及调度说明，删除 marker/确认变量的导出和检查。
- 保留 `fill_importer`、账本、原子发布和监控的读取能力；监控将 marker 缺失从阻断/故障改成不再检查的普通状态。

### 3. 实现人工卖出覆盖

- 新增 `live_trading/scripts/override_main_signal.py`，基于已发布批次生成独立 override 批次。
- 在 `live_trading/modules` 增加覆盖数据结构/校验，保存 source batch、原订单、最终订单、原因、操作者和时间。
- 覆盖仅允许主策略、已有持仓标的、正整数股数；同一 override id 重跑必须返回同一结果，不允许静默改写原批次。
- 更新 README 和操作示例，明确“先正常发布买入，再人工覆盖 SELL”流程。

### 4. 拆出 prType=49 独立 QMT 策略

- 新增独立 QMT 入口/配置（建议 `live_trading/qmt_strategy/qmt_pr49_debug.py` 与 `configs/csi1000_pr49_debug.yaml`），使用专属 `pr49_debug` 目录。
- 请求协议固定记录 code、side、quantity、price、prType=49、请求时间、回报/异常；不读取主 inbox、marker 或主账本。
- 更新 Windows 复制/编译说明，提供一手 BUY 后次日 SELL 的手工启动步骤；不接 `run_monitor.py`。

### 5. 清理旧调度与文档

- 删除 cron 中 LIVE_OK/PR49_LIVE_OK 相关步骤和旧授权脚本的主流程引用；旧脚本若保留，必须明确标记为兼容/废弃且不能被默认调度调用。
- 更新 `live_trading/README.md`、`live_trading/qmt_strategy/README_QMT.md`、pr49 checklist，删除与新流程冲突的授权说明。
- 保留迁移回滚说明和主链路风险提示：QMT 只要运行并指向 inbox，就可能消费信号。

### 6. 验证与交付

- 运行 `live_trading` 全量测试及新增覆盖/pr49 测试。
- 做一次本地共享目录原子发布回归，确认 `.done`、账本和监控仍可读取。
- 运行静态检查、`git diff --check`，检查仓库内不再有默认调度依赖 marker。
- 提交并推送 `main`，按现有流程同步 `exp/workspace`；交付时明确 Windows QMT 需重新复制、编译并人工复核账户绑定后才可启用。

## 完成标准

- 没有 marker 或 `LIVE_TRADING_CONFIRM` 时，主发布仍能生成合法批次。
- 主发布不因 SIMULATION/REAL 配置或 PAUSED 状态拒绝写入。
- 人工 SELL 覆盖可审计、幂等，并可被主 QMT 消费。
- prType=49 BUY→SELL 测试不触碰主监控/账本。
- 全量测试通过，且没有默认 cron 继续创建或要求旧 marker。

