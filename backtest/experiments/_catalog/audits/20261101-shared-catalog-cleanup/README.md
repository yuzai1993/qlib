# 共享资料与审计目录整理

按用户明确批准执行：保留路径映射及实盘parity，迁移有归属的资料与文档，精简重复审计，删除无引用旧指标/报告和CSI1000冻结预测。

- `audit-compaction.json`：27份旧审计文件压缩为摘要，保留原路径、SHA、大小、关键结果及Git来源；旧历史JSON保持原事实，退役引用单列。
- `shared_assets.json`：7份资料的归属、迁移及原SHA，10份无活动引用旧指标和1份修复前报告的删除证据。
- `prediction-deletion.json`：删除19,052,371字节预测文件的SHA、保留模型校验与退役行为；小manifest/协议保留。
- `reference-updates.json`：7个新文件别名、16个仅指向已删资产的旧别名退役；61个目录别名保留。活动清单列当前及兼容路径，v1介绍的完整性SHA同步。
- `hub-link-updates.json`：模型总报告4个链接更新，288个表格单元格原样。三份迁移HTML仅修改href/src，正文及指标不变。
- `validation.json`：23源记录、24登记事件、9个Benchmark、14展示入口的科学字段及登记SHA不变；23指标源和32份冻结记录原字节，3份parity原字节，166个旧可用资源入口仍有效。
- `runtime-documents.json`：实际平台的已登记研究计划新/旧URL可读且内容一致；未登记文档继续只能作为本地资料，不开放任意资源读取。
- `staged-validation.json`：从Git暂存内容导出实验归档与研究文档后，23源、9 Benchmark、14展示入口及五个参数组均可加载，无目录告警。
- `preexisting-csi-test-failures.json`：CSI退役相关扩展检查中的14项既有失败已在HEAD隔离复现；未修改旧评测口径强行通过。
- `completion.json`：最终回归、尺寸、引用与临时工具清理结果。

研究文档在`docs/research/`，历史v1指标和模型报告已归所属实验，旧s1修复对照报告位于其`reports/`。旧s1报告中的13个图形iframe原result文件在本轮前已缺失；链接仅重定相对层级，未恢复旧大产物，也未改变报告正文数字。

一次性工具和验证材料仅位于`/private/tmp/qlib-shared-catalog-cleanup-20261101`，核验后删除。正式测试保留，生产调度、模型、资金、持仓与实盘对照未修改。
