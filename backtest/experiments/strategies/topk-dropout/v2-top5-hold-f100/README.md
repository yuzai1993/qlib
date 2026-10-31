# Top5持有期 × F100

固定Top5换1，对比最短持1/5日与F100开关，共4个组合。

模型：`regime-adapt/m0-h20-t5h5-es-v1`。

固定口径：TopkDropout topk5/drop1。五种子日截面zscore后等权融合信号，单次回测；全A、日频ST、日成交额≥1000万、上市≥60日、近60日持续成交；等权全A基准；2020-08-03～2026-07-31历史选型窗；100万、risk0.90、历史费率。旧运行config多数缺失，固定口径来自冻结登记协议和实验规范，不宣称逐项重新认证。

## 变体与对照

- Baseline · 持1 · 不强制F100：[原始归档](variants/bt-v2/metadata.json)，登记ID `baseline/phase-m-v1-bt-v2`。
- 实验组 · 持5 · 不强制F100：[原始归档](variants/v2-top5-drop1-hold5/metadata.json)，登记ID `regime-adapt/m0h20es-all-top5d1h5-bt`。
- 实验组 · 持1 · F100：[原始归档](variants/bt-v5/metadata.json)，登记ID `baseline/phase-m-v1-bt-v5`。
- 实验组 · 持5 · F100：[原始归档](variants/bt-v3/metadata.json)，登记ID `baseline/phase-m-v1-bt-v3`。

## 结论

持1+F100（s5）CAGR34.8%、夏普1.18，高于持1无F100的23.1%/0.86；持5时加F100未提高收益。

完整数字及原始产物见[汇总报告](reports/summary.html)。平台结论编辑保存在本目录 `metadata.json` 的 `analysis.conclusion`。

## 维护方式

各变体的配置、报告、冻结指标和登记原件保存在 `variants/`。新增变体按实验规范登记，并在本组元数据中增加成员引用；新增一类实验可增加一个组目录和元数据，平台自动发现。

本目录是展示汇总，不生成合并分数，也不产生或晋升新的 Benchmark；原始版本、选型和历史对照关系仍由变体登记决定。

hold_thresh为常规卖出的最短持有期，不是5日到期退出。；无F100为冻结协议未启用该条款的登记含义；s2的hold1及risk0.9由规范/历史规则说明确认，原运行config未留存。；展示四格即可沿单轴对比；禁止归纳为只改一个参数。
