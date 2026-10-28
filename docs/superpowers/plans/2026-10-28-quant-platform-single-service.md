# Quant Platform Single Service Implementation Plan

**Goal:** One managed local service in the original qlib checkout, preserving trading behavior.

**Architecture:** Existing monitor GET router with read-only SQLite readers inside the platform; retain the existing monitor LaunchAgent label and move its listener to 8090.

**Spec:** `docs/superpowers/specs/2026-10-28-quant-platform-single-service-design.md`

## Constraints / rulings

- Work in `/Users/yuxianqi/Project/qlib`, branch `codex/qmt-clipboard-auto`; user explicitly requested the original checkout. No new worktree.
- Preserve unrelated QMT changes. Fingerprints and original draft saved in `/private/tmp/quant-platform-original-worktree-20261028`.
- Do not change trading configs, scheduler, snapshot/report jobs, bridge contents or production database schema/data.
- Use `/opt/anaconda3/envs/qlib/bin/python`; tests use temporary DB fixtures and `PYTHONDONTWRITEBYTECODE=1`.
- Database errors fail closed for monitor requests; analysis files remain separately stored. No writable fallback.

## Task 1 — Read-only monitor readers (agent)

Files: `live_trading/modules/monitor_store.py`, read-only branch only of `live_trading/modules/fill_importer.py`, `live_trading/web/api.py`, new `tests/live_trading/test_monitor_read_only.py`.

- [x] Write and observe failing tests: read-only readers refuse writes, no init/migration or missing-path creation, escaped URI paths work, all GET routes match legacy fixture output, WAL new commits remain visible.
- [x] Add `MonitorStore(db_path, read_only=False)`; read-only path checks file, skips mkdir/init, uses `mode=ro` and `query_only=ON`; keep writable path unchanged.
- [x] Harden existing LiveRecorder read-only connection URI and query-only enforcement without changing write mode.
- [x] Add `create_router(config, project_root, *, read_only=False)` and pass the flag to readers; preserve old caller behavior. No shared-directory scan/write.
- [x] Run new tests plus monitor API/store and recorder regressions; report evidence, no commit/service action by agent.

## Task 2 — Unified platform integration (root)

Files: `quant_platform/app.py`, `__main__.py`, README, live monitor LaunchAgent template and README section, platform tests, relevant LaunchAgent assertion in existing wrapper tests.

- [x] Write failing integration tests for direct GET without network, missing/schema-error DB with independent research/archive access, forbidden write methods, and default CLI/config/data paths.
- [x] Replace proxy with read-only router. Preserve local Host/CSRF/resource controls; expose monitor mode/status in health. Add `--project-root`/`--live-config`; default persistent analysis storage outside production root.
- [x] Update LaunchAgent template to explicit unified module, config, root, persistent storage and 8090. Keep label, logging and KeepAlive; do not load trading credentials.
- [x] Update startup, rollback and service documentation; make old design/README statements explicitly historical.
- [x] Run platform/monitor/boundary/wrapper regressions and frontend tests; independent review before service cutover.

## Task 3 — Cutover and delivery (root)

- [x] Preflight at a temporary port and compare representative real GET responses with old 8082, without displaying account data in logs.
- [x] Back up installed plist; stop only owned 8090 and the existing Web LaunchAgent. Install/bootstrap unified service with rollback on failure.
- [x] Verify 8082 no longer listens, unified 8090 endpoints healthy, original project cwd, unchanged scheduler/other job configuration and unchanged unrelated file fingerprints.
- [x] Browser smoke, scoped commit on original branch, retain persistent analyses and document final status.

## Verification evidence — 2026-10-28

- Read-only reader tests: observed 14 failures before implementation, then all 20 new tests passed; 155 reader/API/recorder checks passed.
- Integration tests observed failures before direct-router wiring. Follow-up regressions covered actual health probing, query/response/concurrency limits, and batch-path validation; the five batch-path cases initially produced four failures and now all pass.
- Final suite: 284 Python tests passed across platform, monitor, recorder, repository boundaries and operational wrappers; 18 Node frontend tests passed. Black checks, diff whitespace checks and plist lint passed.
- Independent code review completed with no remaining blocking findings.
- Temporary 8091 preflight matched all 16 live GET response types against old 8082. After final review fixes, six representative live endpoints matched again and invalid batch paths returned 404.
- Unified service installed under existing Web label com.yuxianqi.qlib-live-monitor. Listener 8090 is managed by launchd, running from /Users/yuxianqi/Project/qlib; health reports direct-read-only / ready. No listener remains on 8082 or 8091.
- macOS initially rejected immediate bootstrap while the previous service was unloading. The old service was restored, then cutover succeeded after waiting for label removal and bounded retries. Trading scheduler was never stopped or reloaded.
- Original installed Web plist retained at /Users/yuxianqi/.qlib/research_platform/service_backups/20261028-single-service/com.yuxianqi.qlib-live-monitor.plist.
- Trading scheduler plist fingerprint and runtime state remained unchanged. All 14 unrelated working-file fingerprints remained unchanged.
- Browser smoke confirmed live data and navigation on the unified 8090 service. Platform code is on the original codex/qmt-clipboard-auto branch; the previous implementation commit 7f1487d2 was fast-forwarded before this consolidation.
