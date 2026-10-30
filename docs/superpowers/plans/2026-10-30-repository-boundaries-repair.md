# 仓库边界与清理器修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修正生产状态说明与清理器的过时假设，完成删除 qlib_exp 前的差异和保留清单，并交付普通 HTML 报告。

**Architecture:** 保留生产入口和数据库原位置；清理器只处理显式研究根，路径与保留协议失败时阻止删除。两个工作区先做只读比较，研究资料不自动覆盖或迁移。

**Tech Stack:** Python、pytest、Git、静态 HTML。

**Spec:** 用户 2026-10-30 本轮修复与双工作区比较请求；backtest/EXPERIMENT_STANDARD.md；live_trading/BT_V5_CUTOVER.md。

## Global Constraints

- 初次审查阶段不删除 qlib_exp；后续用户已明确授权舍弃diff、迁移平台研究源并移除旧worktree，执行结果见下方后续阶段。不执行 cleanup --apply，不训练、回测或取数。
- 当前实盘配置为 alla_v2_top5d1f100_postclose_real_active；研究模型 v4 与历史执行层 BT v5 基线口径不变。
- 保留基线完整五种子、实盘和审计资料；不通过放松边界或隐式扫描 sibling 消除错误。
- 不覆盖 qlib_exp 的未提交代码、配置、registry、报告和分析。
- 用户无法读取 Canvas，改用普通 HTML 和聊天摘要。

## Review Focus

- 生产默认配置与显式历史配置均可正确解析。
- full-period 策略保留规则与历史 valid/test 规则不得混用。
- 绝对路径、越界、软链、缺失种子或 recorder 必须阻止删除。
- Phase M v1 模型与 BT v5 历史模型依赖不能被旧 CSI1000 清理逻辑删掉。
- registry 同 ID 的多条记录和两端差异不能用简单覆盖或按 ID 去重处理。

## Tasks

- [x] Task 1: 默认配置回归 RED→GREEN，修正 AGENTS、运行手册和平台说明中的现行状态；历史修订记录保持其日期语境。
- [x] Task 1b: 平台指标读取与报告生成器一致，优先 portable_eval_output，保留原始来源并验证边界/资源链接，不切换实际研究源。
- [x] Task 2: 清理器先写失败回归，再修复显式根与保留协议；运行完整清理器单测和两根 dry-run，不 apply。
- [x] Task 3: 比较提交关系、tracked 工作树、非忽略未跟踪文件、registry 和主要产物；生成必留/建议归档/可重建清单。
- [x] Task 4: 交付普通 HTML 报告；对代码改动做独立审查并运行相关平台/实盘回归。

## Rulings

- 本轮直接修复现有生产根中的已授权文件，保留运行配置、服务和数据库；不创建或重启生产服务。
- qlib_exp 的实际资料来源先保留；迁移须依赖本轮差异清单，不能提前把运行平台切到不完整资料源。
- 来源迁移前的代码修复同时让平台读取 portable_eval_output；明确登记但损坏的持久化路径（含空值）不能悄悄回读历史结果掩盖问题。回归已先失败再修复。
- 扩展检查中的 9 个旧 Phase S 基线/指标测试独立复现失败（不包含本轮清理器测试）；本轮不改历史测试口径，交付时明确记录。
- 独立审查发现 BT recorder 和既有 CSI1000 train recorder 链只检查实验根，未验证真实 recorder。两处均补充先失败回归后修复；共享边界/状态绑定校验，训练模型须非空且非软链，多 fold 同样验证。

## Verification

- 清理器与预测覆盖相关验证：78 passed（清理器 70 例）；平台 Python：104 passed；实盘运维/边界/调度：75 passed；平台前端：45 passed。
- 最终联合 Python 定向回归：257 passed in 27.50s；git diff --check 通过。HTML、纯文本及本地下载链接核验通过。
- 使用主根代码和主根权威 registry：生产根 dry-run 因 37 个缺失/保留校验错误阻止全部删除；显式 qlib_exp 根 dry-run 0 错误，保留 result 40 / MLflow 7。候选删除清单仅供检查，未 apply。
- 只读差异：main 领先 dev 25 提交，dev 无独有提交；旧工作区仍有 5 个 tracked 修改、312 个非忽略未跟踪文件与独有研究原件，尚未迁移。

## 后续授权与执行：统一研究源并移除旧工作区

- [x] 用户决定舍弃旧工作区未提交diff；已恢复5个tracked文件、删除307个新增文件，留用5份本地忽略规则，Git工作区清理后无差异。
- [x] 迁入539个必要文件，含B6/v1/v2/v3/v4各5模型；25模型SHA保持，7个MLflow实验及正式session可用，未复制17.6GiB派生缓存。
- [x] 为平台和模型报告增加持久化源路径；旧31条可用记录与2,623个既有指标值逐项保持。主根36条研究记录、35条可用，仅1条既知缺失。
- [x] CLI与LaunchAgent显式使用主根；重载平台并由运行API确认研究根与实际指标，再以git worktree remove移除旧目录，保留dev分支。
- [x] 删除后重新运行catalog校验与主根cleanup dry-run，零退化、零保留校验错误；268项Python相关回归与45项前端测试通过。

服务初次自动重载中，bootout返回后旧服务尚在SIGTERMed阶段，紧接的bootstrap返回I/O错误。直接注册恢复服务后，使用分步卸载/注册完成切源；最终接口正常，交易scheduler的plist哈希未改变。稳定证据为 `backtest/experiments/_catalog/audits/repository_migration_20261030/service_cutover.json`。
