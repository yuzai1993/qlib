# Live 监控平台有效账号与批次状态同步设计

## 1. 背景

2026-07-15 的 live 信号因交易账号修正先后生成了三个批次：

- `20260715_csi300_topk10_001`：旧发布协议，已退出执行范围；
- `20260715_csi300_topk10_002`：账号 `8890116049`，已从 inbox 移入 archive；
- `20260715_csi300_topk10_003`：账号 `88813528`，是当前唯一有效批次。

监控平台目前把 SQLite 中所有批次都当作待对账批次，因此旧批次会显示缺失回执，概览也没有展示实际交易账号。共享目录位置不足以作为长期业务状态：完成批次和撤回批次都会进入 archive，无法可靠区分。

## 2. 目标与非目标

### 2.1 目标

1. 在账本中永久记录批次被哪个新批次替代，保留完整审计历史。
2. 盘后对账和 evening 检查只以未被替代的有效批次为准。
3. 监控概览明确展示当前有效账号和有效批次。
4. 批次列表展示账号、生命周期状态及替代关系；旧批次不再显示为待执行异常。
5. 将 2026-07-15 的 `001`、`002` 标记为由 `003` 替代，并重启 8081 服务使 API 代码生效。

### 2.2 非目标

- 不删除旧批次、订单、回执或本地数据库记录。
- 不修改 QMT 信号协议、当前 `003` 信号文件或 `LIVE_OK` 开关。
- 不允许 Web 页面修改批次状态；监控平台继续保持只读。
- 不从共享目录的 inbox/archive 位置自动推断替代关系。

## 3. 数据模型

在 `batches` 表增加两个可空字段：

- `superseded_by TEXT`：替代该批次的新 `batch_id`；
- `superseded_at TEXT`：本地时间戳。

`superseded_by IS NULL` 表示批次在生命周期层面有效；非空表示 `SUPERSEDED`。这里的“有效”只表示仍参与执行和对账，不代表已成交或已完成。

`LiveRecorder.supersede_batch(old_batch_id, new_batch_id)` 在单个事务中执行，并满足：

1. 新旧批次都必须存在；
2. 两者必须属于同一交易日和模式；若两者均有 `strategy_id`，还必须相同；旧协议批次缺少该字段时，要求 batch_id 中的策略段一致；
3. 不允许批次替代自己；
4. 重复写入相同替代关系幂等；
5. 已指向其他新批次时拒绝覆盖。

旧库由 `LiveRecorder` 初始化时自动 `ALTER TABLE` 补列。

## 4. 监控与 API 口径

### 4.1 有效批次查询

新增明确的查询入口，不改变历史审计接口的默认含义：

- `list_batches()`：仍返回全部批次；
- `get_active_batches_by_date(trade_date)`：只返回 `superseded_by IS NULL`；
- `get_latest_active_batch()`：按交易日和 batch_id 倒序取最新有效批次。

`run_postmarket` 使用 `get_active_batches_by_date`，因此旧批次不参与 missing、拒单率和成交核对。`run_evening` 只从有效批次中选择下一开市日批次。

### 4.2 REST API

`GET /api/overview` 新增：

- `account_id`：最新有效 LIVE 批次的账号；没有 LIVE 批次时为空；
- `active_batch_id`：最新有效批次 ID。

`GET /api/batches` 对每行新增：

- `lifecycle_status`: `ACTIVE` 或 `SUPERSEDED`；
- `superseded_by`；
- `raw_missing`：数据库按原始计划计算的缺失数；
- `missing`：SUPERSEDED 行固定为 0，避免页面误报；ACTIVE 行等于原始缺失数。

批次详情仍展示原始订单、回执和 reconcile 结果，不隐藏历史事实。

## 5. Web 展示

1. 顶部策略 badge 改为：`策略 · LIVE · 账号 88813528`。
2. 概览标题旁显示当前有效批次 `20260715_csi300_topk10_003`。
3. 批次表增加“账号”和“状态”列。
4. ACTIVE 显示绿色“有效”；SUPERSEDED 显示灰色“已废弃 → <新批次>”。
5. SUPERSEDED 行仍可展开查看原计划和历史信息，但缺失列显示 `—`，不使用红色异常样式。

账号和批次 ID 均做 HTML 转义；账号只在本机 `127.0.0.1` 只读页面展示。

## 6. 正式数据迁移

代码和测试通过后执行：

1. 初始化正式 `LiveRecorder`，触发新增列迁移；
2. 调用 `supersede_batch(001, 003)`；
3. 调用 `supersede_batch(002, 003)`；
4. 查询确认当天只剩 `003` 为 ACTIVE，账号为 `88813528`；
5. 保持 `002` 共享文件在 archive，`003` 保持在 inbox。

正式库修改前保留现有 `pre_hardening` 备份；本次仅增加列和更新两行，不删除数据。

## 7. 服务部署

当前 8081 uvicorn 进程不会热加载 Python API。部署时先确认进程命令确为 `live_trading/scripts/run_web.py --config csi300_topk10_live`，再停止旧进程并用相同 host/port 启动新进程。重启前后均只监听 `127.0.0.1:8081`。

部署后验证：

- `/api/overview` 返回账号 `88813528` 和批次 `003`；
- `/api/batches` 中 `001/002` 为 SUPERSEDED、`003` 为 ACTIVE；
- 页面顶部和批次表内容一致；
- `/api/positions`、`/api/nav` 等现有端点不受影响。

## 8. 测试

按 TDD 增加以下覆盖：

1. `supersede_batch` 成功、幂等和冲突校验；
2. active 查询排除被替代批次；
3. postmarket 不再对被替代批次计算 missing；
4. overview 选择最新有效 LIVE 账号和批次；
5. batches API 返回状态、替代关系和正确 missing 口径；
6. 静态页面包含账号、有效/已废弃状态展示字段；
7. 全量 `tests/live_trading` 与 signal generator 回归。

## 9. 回滚

若迁移或服务验证失败：

1. 停止新 Web 进程并恢复旧代码进程；
2. 将 `001/002` 的 `superseded_by/superseded_at` 置空即可恢复旧监控口径；
3. 新增可空列无需删除，不影响旧代码读取；
4. 不触碰 `003` 信号和 QMT 开关。
