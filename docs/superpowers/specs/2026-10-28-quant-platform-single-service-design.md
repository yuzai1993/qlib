# 量化平台单服务整合

用户要求将实盘监控与量化平台合并为一个服务，所有后续改动回到原 qlib 工作区。此前用户已授权自行决定实现细节并直接完成验证。

## 决定与约束

- 实施开始时实际检出分支是 `codex/qmt-clipboard-auto`，当时与 `main` 同为 `defa538d`；平台提交 `7f1487d2` 先快进合入该分支。用户后续明确以 `main` 为准，全部平台与 QMT 剪贴板提交已快进合入 `main`，原工作目录也已切回 `main`。
- 统一入口保留 `http://127.0.0.1:8090`，从 `/Users/yuxianqi/Project/qlib` 运行。原 `8082` Web 服务在验证后停用。
- 复用现有 `com.yuxianqi.qlib-live-monitor` LaunchAgent 身份，改为启动统一平台。保持登录启动/异常重启，不新增第二个常驻服务。
- 分析持久目录保持 `/Users/yuxianqi/.qlib/research_platform/analyses`；研究源仍为 `/Users/yuxianqi/Project/qlib_exp`。
- 交易 YAML、交易执行、共享盘、定时交易、快照/日报任务、scheduler LaunchAgent 不变。不运行训练、回测、交易或数据库迁移。

## 实现选择

选择在平台内挂载现有监控 GET 路由，并启用 SQLite 只读模式。继续 HTTP 代理不能消除第二个服务；复制全部查询会造成两份业务逻辑，因此不采用。

`LiveRecorder` 已支持 `read_only=True`；补全 `MonitorStore` 的同类模式，路由增加显式 `read_only` 参数。写入模式默认行为保持原样。只读连接跳过目录创建、建表、迁移、种子数据、WAL 模式修改；使用转义的 SQLite URI `mode=ro` 和 `query_only=ON`。不使用 `immutable=1`，保证能读取正在更新的 WAL。

统一平台删除 HTTP 代理与上游地址参数，增加实盘配置和项目根参数。CLI 从原项目配置只读加载当前 `alla_v4_ladder_k1h5_postclose_real`。账本缺失或 schema 查询错误时明确显示不可用，不补建数据库；研究和分析保持可访问。恢复缺失的启动账本后可重启平台。

## 验证与切换

临时 fixture 验证全部监控 GET 返回与旧接口一致、无 schema/业务数据改写、INSERT/UPDATE/DELETE 被拒绝、缺失库不创建、WAL 新提交可见。运行平台与相关实盘回归、Node 前端测试，并独立审查。

使用临时端口从原目录预检统一平台，GET 与现有 8082 对照。备份安装 plist，检查新 plist 语法；停止会话中的 8090 平台，再 bootout 旧 Web label、安装并 bootstrap 新配置。验证仅 8090 监听、实盘接口正常、LaunchAgent 工作目录正确。失败时恢复备份 plist 和旧 8082。交易 scheduler 始终不操作。
