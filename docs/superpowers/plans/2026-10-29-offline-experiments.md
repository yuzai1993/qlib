# Offline Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 模型、策略各以 Benchmark 和实验记录组织，统一指标展示。

**Architecture:** 保留 ResearchCatalog 与现有路由；增加共享纯展示模块供 Benchmark、单次详情及正式对比调用。页面选择与实验对比选择独立。

**Tech Stack:** Python / FastAPI、原生 JavaScript / CSS、Node test runner、pytest。

**Spec:** docs/superpowers/specs/2026-10-29-offline-experiments-design.md

## Global Constraints

- 研究数据只读，沿用实验规范。主格模型 top3×h5、策略 CAGR，不跨口径计算差值。
- 延续原工作区 main 集成约定；本次不创建额外服务或交易进程。
- 文本转义、资源 ID 受控、CSV 防公式注入。

## Review Focus

- 全部取消后不得自动恢复全选；模块切换及刷新保留选择。
- 年份缺口不可回填全期值；维度列不随取消某一版本而错位。
- 历史 baseline 缺指标仍可展示，但不得变成正式可比较记录。
- 结论与备注分别保留；对照版本不得被误报为晋升版本。
- 资源缺失、恶意文本及空数据不破坏页面与导出。

## Task 1: Metadata and shared presentation

Files: quant_platform/research.py, quant_platform/static/js/research.js, tests/quant_platform/test_research.py, tests/quant_platform/test_research_frontend.cjs.

- [x] 添加结论字段及独立回归测试；先失败后实现。
- [x] 新建共享版本选择、指标矩阵、HTML 表格和 CSV 结构，测试覆盖 Review Focus。
- [x] 运行对应 Node / Python 测试。

## Task 2: Page integration

Files: quant_platform/static/js/platform.js, quant_platform/static/index.html, quant_platform/static/css/platform.css, quant_platform/README.md, tests/quant_platform/test_frontend.cjs.

- [x] 页面按两个一级和两个二级模块组织，Benchmark 内切换核心/分年/分风格；默认选中所有 baseline。
- [x] 实验记录主要列改为用户要求的五项；共享 renderer 接入详情与比较，CSV 对应所见。
- [x] 运行平台 Node 与 Python 全部回归，浏览器核对真实数据、交互与窄屏。
- [x] 独立审查变更并处理实质问题。

## Progress

- 初始验证：Node 18 passed；Python 65 passed。
- 实施决策：原文规格已明确交互与数据结构，直接实施；沿用既有 main 原目录约定。

- 完成验证：Node 31 passed；Python 平台 71 passed；独立审查无实质问题。
- 浏览器真实数据：模型/策略各 4 个 baseline 默认全选；取消模型 v1 后分年表剩 3 行、策略仍为 4 行；年度列为 2020–2026；策略实验记录标出 v2/v3/v4 晋升，报告隔离预览可打开，单次详情 D/F/T 矩阵正常。窄屏采用横向滚动表格。
- 本地 8090 Web 服务已重载后端字段并恢复 health=ok、monitor_status=ready；未触及交易调度、研究 registry、训练或回测。重载时既有端口预检短暂报占用，LaunchAgent 自动重试后恢复；未为此扩大代码修改范围。

## 2026-10-30 页面修订

- [x] 左侧模型实验、策略实验替换离线实验入口；旧 `#research` 保持兼容。
- [x] 移除统计卡和版本选择框，核心表默认版本倒序、首列勾选；取消勾选保留核心行，只更新下方维度表。
- [x] 核心表下面保留分风格、分年两个页签；模型与策略选择独立，表头支持全选与中间状态。
- [x] 策略移除算术年化，压紧核心表列宽；完整名称和口径保留在提示及导出中。
- 验证：Node 35 passed，Python 71 passed；独立代码/CSS 审查无实质问题。浏览器在 687px 宽窗口中同屏显示全部策略核心指标；实际取消 v3 后核心保留四行、维度变为三行，分年覆盖 2020–2026，表头恢复全选正常。
