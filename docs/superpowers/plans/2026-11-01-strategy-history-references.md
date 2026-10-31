# 策略实验的晋升版本与 Baseline 标注

用户要求：策略实验标明晋升的策略版本与对比 Baseline。

1. 从已验证的源实验、Benchmark与对照关系生成历史展示信息；组只汇总成员的既有晋升，不获得新的基线或当前版本身份。
2. 列表在策略说明下标出Baseline，组内对照明确标注；晋升列展示关联的s版本及当前/对照身份，未晋升的实验明确说明。详情列出各成员的原历史Baseline，CSV同步。
3. 核对八个策略入口：Top5组关联s2/s3/s5、阶梯组关联s4，其余参数组不推断晋升；独立s1为初始基线，K5和延窗分别对照s2/s4。
4. 运行相关Python与前端回归、核验实际页面和原科学字段不变，更新维护说明并删除本任务临时工具。

一次性材料：`/private/tmp/qlib-strategy-history-labels-20261101`。长期证据：`backtest/experiments/_catalog/audits/20261101-strategy-history-references/`。
