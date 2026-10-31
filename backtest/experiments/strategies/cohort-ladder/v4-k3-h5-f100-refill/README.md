# 阶梯3×5 F100与补位

固定每日新买3只、到期持5日，依次比较F100与强卖后补位。

模型：`regime-adapt/m0-h20-rankices-v1`。

固定口径：CohortLadder 每日新买3只、持有5交易日到期。五种子日截面zscore后等权融合信号，单次回测；全A、日频ST、日成交额≥1000万、上市≥60日、近60日持续成交；等权全A基准；2020-08-03～2026-07-31历史选型窗；100万、risk0.90、历史费率。旧运行config多数缺失，固定口径来自冻结登记协议和实验规范，不宣称逐项重新认证。

## 变体与对照

- Baseline · 不强制F100：[原始归档](variants/bt-v4/metadata.json)，登记ID `baseline/phase-m-v1-bt-v4`。
- 实验组 · F100 · 不补位：[原始归档](variants/v4-k3-h5-f100/metadata.json)，登记ID `regime-adapt/m0h20rankices-all-ladder-k3h5f100-bt`。
- 实验组 · F100 · 补新票：[原始归档](variants/v4-k3-h5-f100-replace/metadata.json)，登记ID `regime-adapt/m0h20rankices-all-ladder-k3h5f100r-bt`。

## 结论

原阶梯（s4）CAGR26.3%、夏普1.04；F100不补位降至18.2%，补位恢复至25.5%但回撤扩大。

完整数字及原始产物见[汇总报告](reports/summary.html)。平台结论编辑保存在本目录 `metadata.json` 的 `analysis.conclusion`。

## 维护方式

各变体的配置、报告、冻结指标和登记原件保存在 `variants/`。新增变体按实验规范登记，并在本组元数据中增加成员引用；新增一类实验可增加一个组目录和元数据，平台自动发现。

本目录是展示汇总，不生成合并分数，也不产生或晋升新的 Benchmark；原始版本、选型和历史对照关系仍由变体登记决定。

F100开启后再比较是否补位，属于两个阶段的规则消融；不能声称只有一个参数变化。；保留原始s3历史对照关系，组内受控对照是相同v4信号的s4。
