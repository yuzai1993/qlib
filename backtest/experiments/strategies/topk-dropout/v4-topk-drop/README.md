# 持仓与换仓档位

固定持1日、F100，对比Top3换1、Top5换1与Top15换3。

模型：`regime-adapt/m0-h20-rankices-v1`。

固定口径：TopkDropout 最短持1日、F100。五种子日截面zscore后等权融合信号，单次回测；全A、日频ST、日成交额≥1000万、上市≥60日、近60日持续成交；等权全A基准；2020-08-03～2026-07-31历史选型窗；100万、risk0.90、历史费率。旧运行config多数缺失，固定口径来自冻结登记协议和实验规范，不宣称逐项重新认证。

## 变体与对照

- Baseline · Top3 · 换1：[原始归档](variants/v4-top3-drop1-f100/metadata.json)，登记ID `regime-adapt/m0h20rankices-all-top3d1f100-bt`。
- 实验组 · Top5 · 换1：[原始归档](variants/v4-top5-drop1-f100/metadata.json)，登记ID `regime-adapt/m0h20rankices-all-top5d1f100-bt`。
- 实验组 · Top15 · 换3：[原始归档](variants/v4-top15-drop3-f100/metadata.json)，登记ID `regime-adapt/m0h20rankices-all-top15d3f100-bt`。

## 结论

Top5换1的CAGR23.6%、夏普0.87，高于Top3换1的14.8%/0.60；Top15换3为19.4%/0.82。

完整数字及原始产物见[汇总报告](reports/summary.html)。平台结论编辑保存在本目录 `metadata.json` 的 `analysis.conclusion`。

## 维护方式

各变体的配置、报告、冻结指标和登记原件保存在 `variants/`。新增变体按实验规范登记，并在本组元数据中增加成员引用；新增一类实验可增加一个组目录和元数据，平台自动发现。

本目录是展示汇总，不生成合并分数，也不产生或晋升新的 Benchmark；原始版本、选型和历史对照关系仍由变体登记决定。

Top15同时将n_drop从1改为3，应展示两个参数列，不得把差异归因于topk单轴。；严格单变量替代方案：仅Top3/Top5归一组，Top15单列。；组内控制选择Top3只是展示约定，原始登记baseline_ref=s4保留。
