# Result保留与研究脚本分类 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** result只保存当前平台实验所需产物，研究脚本按准备、训练、评估、报告、登记、维护六类归位。

**Architecture:** 以现存typed实验元数据、冻结登记和历史Benchmark来源追踪产物，保留现有完整五种子与当前回测的严格检查；只清理result，不顺带清理mlruns。脚本实际迁入六个Python子包，统一使用明确的包导入并同步修正命令和文件定位；不保留重复实现。

**Tech Stack:** Python 3.12、pytest、现有experiment_archive/registry/cleanup模块、shell。

**Spec:** 用户本轮两项要求；保留约束见`AGENTS.md`与`backtest/EXPERIMENT_STANDARD.md`第6.3节。本次整理不重训、不改模型/策略选择和指标。

## Global Constraints

- 当前平台23源实验、24登记事件、14展示入口、9个Benchmark及所有历史指标保持不变。
- 当前生产配置、实盘模型、账本、调度保持不变；不执行训练或正式回测。
- 当前/历史模型基线完整五种子、当前策略依赖模型和融合预测必须保留。
- 普通历史产物本轮前已缺失只记录，不补造；现存必要模型、路径边界或symlink检查失败则拒绝删除。
- 一次性迁移/核验脚本放`/private/tmp/qlib-result-scripts-20261101/`，完成后删除；仓库只留正式工具、可复用测试和精简审计。

## Review Focus

- metadata/历史Benchmark及嵌套冻结记录中的依赖不能遗漏。
- 分类后的直接CLI执行、包导入和跨类别调用均应可用，不能依赖工作目录。
- `__file__`根路径、shell相对路径、动态导入、测试读取源码路径须跟随迁移。
- 实盘共用的宇宙过滤/模型早停和平台共用的登记/路径解析保持同一实现。
- 审计来源中的旧路径不冒充活动入口；删除固定补丁须核对仍可复用的逻辑与调用者。

## Task 1: 记录基点与依赖

- [x] 保存平台快照、实验/实盘原件指纹，运行相关测试并压缩记录既有失败。
- [x] 列出result各目录归属、现存必要依赖及可清理旧产物。
- [x] 列出156个脚本/模块的六类归属、重复入口及一次性候选。

## Task 2: 正式保留规则

**Files:** `backtest/scripts/cleanup_experiment_artifacts.py`（迁入maintenance）、`tests/backtest/test_cleanup_experiment_artifacts.py`。

- [x] 先测平台非基线产物保护、未登记session可清理、越界拒绝和只清result。
- [x] 添加显式platform保留模式与only-result执行选项，复用已有路径和模型保护。
- [x] dry-run确认并保存小审计后删除旧CSI产物；有保留价值的散装审计归正式审计位置。

## Task 3: 脚本分类与引用

**Files:** `backtest/scripts/{prepare,train,evaluate,report,register,maintenance}/`及实际调用方/测试/活动文档。

- [x] 将准备配置/样本、训练、预测回测与指标、报告、登记、维护模块分别归位。
- [x] 修正包导入、直接CLI启动、文件根路径、shell与调用命令；补代表性跨目录CLI/导入回归。
- [x] 核实重复与一次性入口；可复用逻辑先归正式模块，确认无活动调用后删除入口。

## Task 4: 验证与收尾

- [x] 对比迁移前后的平台快照、归档指标与实盘原件；检查必要模型完整性。
- [x] 运行相关Python和前端回归，仅分析相对基点新增的失败；检查脚本导入/语法与shell语法。
- [x] 更新scripts README、guide整理表、规范与审计，记录实际保留和删除数量。
- [x] 删除本任务临时工具及工作副本，检查git status与diff --check；统一提交推送。
