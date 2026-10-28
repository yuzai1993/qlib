# BT v5 promotion and offline live adaptation

Approved by the user on 2026-10-29 following the feasibility audit.

Promote M0 H20 ES (historical model v2, five fixed seeds 42/1000/2000/3000/4000) with TopkDropout topk=5, n_drop=1, hold_thresh=1, force_sell_rank=100, risk_degree=0.90 to execution backtest baseline BT v5. The model research baseline remains v4 RankIC ES. Preserve historical records and the official one-million-account historical cost basis; evaluate the 300,000-account/current-fee variant separately. Never present the reused historical evaluation window as out-of-sample. Post-August frozen-parameter validation is diagnostic and cannot be used to tune this choice.

Implementation is isolated in the managed bt-v5-top5-cutover worktree. Prepare model export with source SHA, config and parity checks, force-rank exits, full odd-lot exits, post-sale-cash allocation and a broker-side position ceiling under failed/partial sells. Legacy ladder behavior must remain unchanged. Protocol extensions must fail closed on executors that cannot honor them; preserve ordinary legacy serialization/checksums.

Allocation parity clarification: divide post-sale cash times risk by the candidate count after actual remaining positions restrict free slots, before quote/board-lot/tradability checks. An unaffordable or unavailable candidate leaves its share in cash; do not redistribute its budget or substitute another candidate. This follows the existing research strategy's order of operations.

Offline verification uses temporary databases and bridges. Production cash and holdings must be preserved when preparing a ledger copy. Outstanding production batches are a cutover blocker, never automatically superseded. No production database/config/scheduler changes, bridge writes, account connections, order publication, transactions, or QMT activation are authorized in this implementation. The user performs real-account activation only after reviewing the completed package. Keep opening capital at 300,000 and target risk at 0.90.

Deliver an audited baseline/report, tests for the changed execution behavior, immutable model provenance, current-cost and forward-period diagnostics when local data permits, isolated ledger-transition audit, and an activation/rollback runbook that distinguishes prepared artifacts from active production.
