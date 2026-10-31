# 实验记录取消手动对比与详情精简

用户要求实验不再支持手动对比，详情主要呈现Baseline、变更内容与核心指标结果。Benchmark的版本勾选、完整模型介绍及分维度指标保留。

- [x] 移除模型、策略记录表对比列、选择状态、对比条、请求及详情“加入对比”；保留Benchmark自身版本选择。
- [x] 记录详情单独渲染三项主内容，核心指标沿用原数值与格式；完整原假设、登记和资源保留在折叠区。
- [x] 核对22个策略当时Baseline及变更，复用已有 `change_summary`、`comparison_baseline` 元数据协议；模型记录继续使用既有的核准字段。
- [x] 回归过滤、排序、CSV、结论编辑、Benchmark完整介绍和指标，检查33条原登记及原指标不变。
- [x] 更新维护说明与guide，保存审计并清理本次临时材料。

临时目录：`/private/tmp/qlib-experiment-details-20261031`。长期证据位于 `backtest/experiments/_catalog/audits/20261031-experiment-detail-summary/`。不训练、不回测、不晋升、不改实验原始指标、不提交推送。旧只读比较API暂留兼容，不再由页面调用。

验收：模型/策略记录均无手动对比控件、状态与页面请求；记录详情聚焦Baseline/变更/核心结果。Benchmark4代模型/5代BT版本选择、模型完整介绍及分维度指标保留。33条历史登记与指标不变，策略22份元数据只新增两展示字段；相关Python484项、前端95项通过。实机检查与审计见 `backtest/experiments/_catalog/audits/20261031-experiment-detail-summary/`，临时材料核验后清理。
