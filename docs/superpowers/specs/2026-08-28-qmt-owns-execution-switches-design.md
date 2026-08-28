# QMT 独占执行开关

日期：2026-08-28
状态：已批准
影响面：主策略发布与 QMT 认领；一手探针除外

## 1. 目标

Mac 只生成并发布可审计信号。是否交易、用模拟还是实盘，只由 Windows QMT 策略实例决定：

- 停策略 = 当天不交易
- 开策略 = 用源码里的 `ACCOUNT_ID` 下单

这是 2026-08-09 简化设计的收尾：主链路不再用 YAML 三件套、环境变量或批次头字段当执行闸。

## 2. 非目标

- 不改一手探针工具闸（`OPERATOR_PROBE` 仍要求 YAML triad 与 `LIVE_TRADING_CONFIRM`）
- 不删 `ACTIVE/PAUSED` 表；主发布本来就不因 `PAUSED` 停发
- 不改盘后通道时点与 `prType=49` 契约
- 旧批次若仍带 `mode` / `account_environment` / `account_id`，继续可读，不当闸

## 3. QMT

源码写死 `ACCOUNT_ID`。删除 `ACCOUNT_ENVIRONMENT` 与 `ALLOW_REAL_MONEY`。

认领仍失败关闭：

- `schema_version` 不是 2.0
- `account_type` 与本地不符
- `trade_date` 不是当天
- `ACCOUNT_ID` 为空

下单与快照只用源码 `ACCOUNT_ID`。批次头不再要求 `mode`。

## 4. Mac

主策略 YAML 删除：

- `broker_environment`
- `allow_real_money`
- `default_mode`

`live_config`：这两个字段不再是 STRATEGY 的必填项。若仍写着，继续校验旧配对，避免半改配置。缺省时**不得**跳过通道时点、开户现金、策略参数校验。

发布器：新批次头省略 `mode` / `account_environment` / `account_id`。不再有 `--mode`。

cron wrapper：

- 不再读 `LIVE_RUN_MODE`，不再传 `--mode`
- 仍 `unset LIVE_TRADING_CONFIRM`

`QMT_REAL_ACCOUNT_ID` 只给探针/快照工具；渲染器优先读源码里的 `ACCOUNT_ID`。

监控：`broker_environment != SIMULATION` 时做实盘侧快照/探针扫描（字段缺失视为非模拟）。券商对账把非 `SIMULATE` 批次（含空 mode）当实盘。

## 5. 有意接受的风险

Mac 发出去后，任何正在跑、指向该 inbox 的 QMT 实例都会吃单。账户和模拟/实盘完全由 QMT 操作员负责。

## 6. 验证

- QMT：无 mode/账号头字段时仍认领；空 `ACCOUNT_ID` 仍拒单
- 发布：新批次 JSON 不含 `mode` / `account_environment` / `account_id`
- 现网 YAML `alla_v4_ladder_k1h5_postclose_real` 加载仍校验盘后四个时点
- `pytest tests/live_trading -q` 相关子集
