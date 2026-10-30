# 分析目录

每个直系子目录对应一份分析。平台“分析档案”页每次刷新都会重新扫描这些目录；
模型实验、策略实验仍从实验登记表读取。

## 手工新增分析

创建一个名称稳定的子目录，将报告、代码和附件放在同一目录中：

```text
backtest/diagnostics/<analysis-name>/
  metadata.json
  REPORT.md
  code/
  assets/
```

最小元数据示例：

```json
{
  "schema_version": 1,
  "id": "my-analysis",
  "title": "分析标题",
  "historical": true,
  "status": "active",
  "version": 0,
  "created_at": "2026-10-31",
  "updated_at": "2026-10-31",
  "tags": ["诊断"],
  "related_experiments": [],
  "report_path": "REPORT.md",
  "code_files": [
    {"name": "analysis.py", "path": "code/analysis.py"}
  ],
  "attachments_missing": []
}
```

`id` 在整个分析根内唯一，允许字母、数字、下划线和连字符，最长 100 字符；
目录重命名后也应保留该 ID。`historical: true` 的手工分析仅供阅读与导出，
报告中缺失的历史资料应在 `attachments_missing` 中如实记录。
代码清单可以为空，平台不会执行代码。

所有文件引用均相对于当前分析目录，不能使用绝对路径、`..` 或符号链接。
`report_sha256` 及代码条目的 `sha256` 可选，填写后平台按文件实际字节校验。
单份代码不超过 256 KiB，完整加载记录不超过 2 MiB。
已有元数据如果损坏或与文件不一致，平台会明确报错，不退回推测信息。

## 平台编辑与版本

通过页面新建的分析使用 UUID 作为稳定 ID，每次保存都写入新的
`revisions/v<版本>-<唯一值>/`，其中包含 `REPORT.md`、`document.json` 和 `code/`。
`document.json` 保存四段正文的完整文本，元数据仅保存路径、哈希、版本、日期、标签等信息。
所有版本文件完成后才原子替换 `metadata.json`，读者始终读取一个完整版本。
旧版本属于可追溯档案，不作为临时文件删除。

页面保存使用分析根的 `.write.lock` 与版本号防止并发覆盖；运行时不能删除锁文件。
后台可以在不重启服务的情况下发现新目录。
导出的 ZIP 包可解压为本目录的一个子目录直接读取；同一个 ID 不能重复导入。

三份现有历史分析保留原报告及原 ID。恢复说明中的历史缺图和缺数据仍表示资料缺失，
本次目录整理没有重新运行研究、重建数据或修改结论。
