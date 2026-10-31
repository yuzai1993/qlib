# 策略版本与 Benchmark 标识验收

用户要求：策略 Benchmark 标记模型版本与简称、策略简称；策略展示编号改为s；提交并推送仓库当前全部变更。

- 全A策略展示为s1–s5，依次关联模型v1、v2、v2、v4、v2；当前策略s5、当前模型v2。
- 核心、分年/分风格、详情、实验对照、晋升标记及CSV使用一致展示编号。
- 冻结登记中的v编号、原实验ID和生产配置路径保留。`display_version`为展示字段，不影响历史比较、晋升或清理资格。
- `validation.json`核对23个源实验、9个Benchmark的科学字段及104份源JSON未变。
- `report-validation.json`核对6份新报告、115个本地链接，42份冻结HTML和132份来源文件未变。
- `preexisting-test-failures.json`记录扩大检查发现、并在提交前HEAD隔离复现的23项既有失败；不调整CSI1000评测口径来掩盖失败。
- `winner-fixture-review.json`补充旧fixture缺少recorder/run信息的源码核对。
- `completion.json`记录最终测试、页面验收和临时工具清理。
- `staged-archive-validation.json`从Git暂存树重新导出实验归档，确认23源、14展示入口、9个Benchmark及5个参数组均完整可读。
- `whitespace-normalization.json`记录提交检查中清理的日志行尾空白和一个测试文件的末尾空行；保留前后哈希及原日志逐字节重建信息，不改写历史快照哈希。

一次性工具及HEAD验证副本位于 `/private/tmp/qlib-strategy-labels-20261031`，核验完成后删除；未复制到仓库。迁移CSV的Git例外同步至真实`variants/`路径，确保正式证据随本次提交保留。
