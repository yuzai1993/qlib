# BT v2重复策略归并

2026-10-31，按用户要求，归并模型v2 Top5早停 × Top5换1、最短持1日的重复记录。

原`regime-adapt/m0h20es-all-top5d1-bt`是候选实验，`baseline/phase-m-v1-bt-v2`是同一实验晋升后的基线。两者读取同一JSON，模型、五种子、窗口、100万元账户、risk=0.90、费用及全部主指标/分年/分风格结果一致。

保留BT v2为唯一活动条目，将候选的2份JSON、2份HTML及1份冻结记录移入`strategies/topk-dropout/bt-v2/`。候选独立目录及活动索引已删除；原候选元数据和索引保存在本审计目录。BT v2原冻结`promoted_from`仍保留候选ID，活动展示不再重复列出已归并候选。

旧文件路径由明确别名继续解析。必要旧资源引用保留在canonical目录清单中，旧报告、指标、登记记录URL继续可读；平台按资料用途与实际文件去重，缺失的非报告审计证据继续保留。目录清单中的兼容别名不是额外文件副本。

当前27个活动实验（6模型、21策略），9个Benchmark。当前模型仍为v2，当前执行基线仍为BT v5；未训练、回测或删除模型。策略总报告仅重建hub，其它原始HTML字节未变。

`dependency-audit.json`记录重复性和引用检查，`archive-consolidation.json`记录归并、冻结字节与别名，`report-validation.json`记录hub及链接检查，`final-review.json`记录独立复审，`validation.json`记录最终测试和清理核验。
