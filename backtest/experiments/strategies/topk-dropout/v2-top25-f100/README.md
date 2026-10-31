# Top25 F100开关

固定Top25换5、最短持5日，对比F100关闭/开启。

模型：`regime-adapt/m0-h20-t5h5-es-v1`。

固定口径：TopkDropout topk25/drop5/最短持5日。五种子日截面zscore后等权融合信号，单次回测；全A、日频ST、日成交额≥1000万、上市≥60日、近60日持续成交；等权全A基准；2020-08-03～2026-07-31历史选型窗；100万、risk0.90、历史费率。旧运行config多数缺失，固定口径来自冻结登记协议和实验规范，不宣称逐项重新认证。

## 变体与对照

- Baseline · 不强制F100：[原始归档](variants/v2-top25-drop5-hold5/metadata.json)，登记ID `regime-adapt/m0h20es-all-top25d5h5-bt`。
- 实验组 · F100：[原始归档](variants/v2-top25-drop5-hold5-f100/metadata.json)，登记ID `regime-adapt/m0h20es-all-top25d5h5f100-bt`。

## 结论

开启F100后CAGR由24.6%降至22.9%，回撤由-34.1%收窄至-33.0%，换手增加。

完整数字及原始产物见[汇总报告](reports/summary.html)。平台结论编辑保存在本目录 `metadata.json` 的 `analysis.conclusion`。

## 维护方式

各变体的配置、报告、冻结指标和登记原件保存在 `variants/`。新增变体按实验规范登记，并在本组元数据中增加成员引用；新增一类实验可增加一个组目录和元数据，平台自动发现。

本目录是展示汇总，不生成合并分数，也不产生或晋升新的 Benchmark；原始版本、选型和历史对照关系仍由变体登记决定。

分组内对照为无F100；原始登记baseline_ref仍是s2，不改写历史比较对象。；Top25最短持5日轮换不等同于Top5×H5阶梯。
