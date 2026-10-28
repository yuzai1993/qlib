# BT v5 offline cutover implementation plan

Spec: docs/superpowers/specs/2026-10-29-bt-v5-top5-cutover-design.md

## Global Constraints

- Work only in /Users/yuxianqi/.codex/worktrees/bt-v5-top5-cutover/qlib.
- Follow backtest/EXPERIMENT_STANDARD.md. Approval covers BT v5 promotion, not changing model research v4 or tuning parameters.
- Target v2 five seeds [42,1000,2000,3000,4000], TopkDropout 5/1/hold1/f100/risk0.90; offline live capital 300000.
- Never modify active production config/database/scheduler or publish to real bridge/QMT. Historical qlib_exp artifacts are read-only.
- Use /opt/anaconda3/envs/qlib/bin/python. No stdin/heredoc execution that launches Qlib multiprocessing.
- Tests use isolated fixtures. Preserve legacy ladder behavior. Record data gaps and pending activation honestly.

### Task 1: Baseline promotion and portable reporting

Own backtest/scripts/promote_bt_v5_top5d1f100.py, promotion tests, registry.jsonl, BT report HTML, durable baseline metric summaries, EXPERIMENT_STANDARD.md and root AGENTS.md. Promote baseline/phase-m-v1-bt-v5 from regime-adapt/m0h20es-all-top5d1f100-bt; mark previous current BT false, preserve history, model baseline v4 unchanged. Bind v2 model explicitly. Compute missing regimes from original returns and canonical monthly labels. Use original artifacts in /Users/yuxianqi/Project/qlib_exp read-only; include source paths and hashes. Produce repeatable idempotent promotion and portable reports; no hand-entered metrics. Record historical selection limitation. Verify exact registered metrics and current flags. Do not edit production activation docs or live code.

### Task 2: Live execution parity with guarded protocol

Own live_trading/modules/order_manager.py, live_config.py, backtest_parity.py, signal_schema.py, order_planner.py, signal_publisher.py if required, live_trading/qmt_strategy/, and their tests. Audit callers before selecting minimal optional execution contract. Implement force_sell_rank=100 selection, permit initial_buy_count=None, preserve full odd-lot sells, allocate new buys from cash available after confirmed sells times risk_degree divided by executable buy count, enforce max_positions=5 after failed/partial sells. Handle retries/resume without recomputing budgets dangerously or oversubscribing slots. Validate protocol fields and reject unsupported/older executor paths explicitly. Preserve existing serialization and legacy strategy behavior when extension absent. Root will build target configs after you document required settings. First add focused failing tests for critical behaviors, then implement, run relevant live tests. No production imports or financial API calls. Report files touched, contract fields/config requirements, tests and remaining gaps.

### Task 3: Model/config package and offline diagnostics

Root owns new target live/parity/diagnostic configs and model export manifest, offline experiment driver and records separate from Task 1, new ledger-transition audit utility/tests, and cutover runbook. Export frozen v2 models using existing slim exporter; no retraining. Verify prediction/export parity. Run 300k current-cost replay and frozen post-Aug validation using available local data; separately retain official one-million historical result. A readonly backup audit preserves source cash/positions/history, records pending batches/cohorts, and refuses to declare ready while outstanding batches exist. Use temp bridges and destination databases. Explain no real-account switch has occurred.

### Task 4: Integration verification and independent review

Validate target live/backtest parity, protocol handling including partial/failed sells and restart, model SHA provenance, baseline history/report consistency and isolated ledger behavior. Run the affected tests, inspect final diff with independent review, address material defects, and deliver exact artifact paths plus activation blockers. Keep worktree for review; do not merge it into scheduled production automatically.
