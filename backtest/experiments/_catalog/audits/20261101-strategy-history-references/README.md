# 策略实验晋升版本与 Baseline

按用户要求在策略实验中标记晋升版本和对照Baseline。

- 列表策略说明下显示Baseline；参数组明确标注“组内Baseline”。
- 晋升列展示已验证的源成员历史版本，标记“对照”和“当前”，无晋升明确说明。组本身不获得版本、晋升或基线身份。
- Top5组关联s2（组内对照）、s3、s5（当前），阶梯组关联s4（组内对照）；30万诊断不会因历史对照s5而被标为晋升s5。
- 详情单列成员原历史Baseline，CSV同步。Top5四个源成员分别对照s1、s3、s4、s2，不改写为组内统一对照s2。
- `validation.json`保存8条策略展示关系，并确认23条原始记录及9个Benchmark对象完整不变。
- `completion.json`记录回归与页面验收；所有成员缺档、跨类型、跨股票范围、坏来源或组重叠均停止有效晋升展示，保留源记录。

一次性材料目录 `/private/tmp/qlib-strategy-history-labels-20261101` 在验收完成后删除。正式逻辑在 `quant_platform/strategy_history.py`；元数据和原实验指标无变更。
