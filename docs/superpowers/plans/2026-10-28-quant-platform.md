# Quant Research Platform Implementation Plan

> For agentic workers: use subagent-driven development for independent components and an independent final review. User explicitly approved direct execution without further design confirmation.

**Goal:** Deliver a usable local three-module quantitative research platform without modifying the existing trading system.

**Architecture:** Independent FastAPI app in `quant_platform/`, static HTML/CSS/JS, loopback GET proxy to existing monitor, read-only research catalog, independent versioned analysis files.

**Tech Stack:** Existing Python environment `/opt/anaconda3/envs/qlib/bin/python`, FastAPI/httpx/uvicorn, vanilla JS; no training, Qlib init, pickle loading or new framework dependency.

**Spec:** `docs/superpowers/specs/2026-10-28-quant-platform-design.md` (approved 2026-10-28).

## Global Constraints

- Work only in the attached `quant-research-platform` checkout. Do not edit `live_trading/`, existing configs, schedules, database, shared drive, source registry/reports or current QMT changes.
- Existing monitor upstream `http://127.0.0.1:8082`; new app `127.0.0.1:8090`; do not stop/replace an existing service. Proxy only explicit GET routes, timeout, no redirects, no environment proxies.
- Research root `/Users/yuxianqi/Project/qlib_exp`, explicitly configurable, no silent alternate root. Model top3×h5 arithmetic annualization and strategy CAGR never share a comparison group.
- Analysis data lives in a separate configurable folder under the new platform by default, never production/research data. No uploaded code execution.
- Existing monitor files can be copied/read for reuse but never edited. Avoid runtime importing any live-trading application module.

## Review Focus

1. Single-seed, extended-window and paired-pool experiments cannot masquerade as official comparable results.
2. Report/file traversal and symlinks cannot expose arbitrary local files; HTML cannot execute same-origin code.
3. Monitor offline/slow responses cannot hang research pages or cause writes to production DB.
4. Analysis writes cannot lose updates, accept cross-origin mutations or overwrite historical source reports.
5. Page navigation and failed async requests cannot replace the current page with stale content.

## Shared contracts

`quant_platform/research.py`: `ResearchCatalog(root: Path)`; `snapshot() -> dict`; `compare(ids: list[str]) -> dict` raises ValueError for incompatible/non-comparable/unknown records; `resource(resource_id: str) -> Path | None` for registered safe source artifacts only.

Snapshot: `{records: [...], metric_definitions: {model: [{key,label,format}], strategy: [...]}, sources: {root,registry,modified_at}, warnings: [str]}`. Format is `pct`, `number` or `rankic`. Each record:
`{id,kind,name,version,current,is_baseline,status,date,direction,baseline_ref,seeds,official,comparable,comparison_key,scope,window:{start,end},pool,metrics:{key:number|null},years:{year:{key:number|null}},regimes:{D|F|T:{key:number|null}},protocol,hypothesis,note,warnings:[str],resources:[{id,name,available,kind}]}`. Use exp_id as id. Baseline version/current derive explicit source metadata and protocol; no metric-based promotion. Comparison response `{records:[...],comparison_key}`. JSON output must contain no NaN/Infinity.

Public routes supplied by root:
- `GET /api/research`: catalog snapshot. `GET /api/research/compare?id=...&id=...`: validated comparison.
- `GET /api/resources/{id}`: safe sandboxed source file, `?download=1` attachment.
- `GET /api/session`: `{csrf_token}` for same-origin analysis writes (header `X-CSRF-Token`).
- `GET /api/analyses`: `{records:[analysis summary/detail]}` (including historical source reports).
- `POST /api/analyses` and `PUT /api/analyses/{id}`: JSON analysis, response saved full record; update requires `version`. Fields `{title,background,method,conclusion,usage,tags:[str],related_experiments:[str],codes:[{name,content}],status:'active'|'archived'}`. All four sections + title and >=1 code required for new records. Returned additions `{id,version,created_at,updated_at,historical,report,attachments_missing}`. Historical entries read-only. Error HTTP 400/403/404/409/413 JSON `{detail}`.
- `GET /api/analyses/{id}/export`: zip with report Markdown and code files.
- Monitor routes remain `/api/overview`, `/api/nav`, `/api/positions`, `/api/batches`, `/api/batches/{id}` and existing known GET endpoints so reused JS works. No dynamic generic proxy.

## Task 1 — Research catalog (agent)

Files: create `quant_platform/research.py`, `tests/quant_platform/test_research.py`. Own only these files.

- [x] Write failing fixture tests for model fixed 3×5 extraction, ensemble completeness, baseline history/current, year/regime values, missing/invalid JSON, diagnostic isolation and resource traversal/symlinks.
- [x] Verify red using focused pytest, then implement shared contract. Registry indexes records; authoritative metrics from JSON. Identify actual source schemas by read-only inspection. No generator execution.
- [x] Test source refresh, compare rejection and finite serialization; inspect real source snapshot to reconcile all 4+4 baselines and status rows. Missing legacy sources must retain clear record status.
- [x] Provide test evidence and integration notes; root owns commits.

## Task 2 — Service, proxy and analysis archive (root)

Files: `quant_platform/{__init__,app,analyses,__main__}.py`, `quant_platform/README.md`, `quant_platform/.gitignore`, `tests/quant_platform/test_app.py`, `tests/quant_platform/test_analyses.py`.

- [x] Write failing tests proving analysis save/reload/update-conflict/archive/export, required fields and code limits, historical immutability, traversal/symlink rejection.
- [x] Implement `AnalysisStore(data_root, research_root)`, atomic JSON replace + process lock + optimistic version checks. Import diagnostic reports as readonly entries with original text and missing-attachment warnings.
- [x] Write failing service tests for allowed monitor GET / denied other route, upstream failure handling, hostile Host/Origin/CSRF and no live-module imports. Implement bounded loopback httpx proxy and independent app factory.
- [x] Add CLI default 8090, validation of source/storage boundaries, startup port conflict error, docs with launch/rollback commands. No background service installation.

## Task 3 — Unified UI (agent)

Files: `quant_platform/static/**`, `tests/quant_platform/test_frontend.cjs`. Own only these files.

- [x] Create clear Chinese navigation and refined light workspace/dark sidebar, responsive layout and typography. Reuse isolated copies of existing monitor JS/runtime and locally adapt styling; preserve existing monitor behaviors.
- [x] Implement research baseline/history, annual/regime metric views, filters/search/sort, safe comparison via API, CSV export, record details and sandboxed report view. Model/strategy and diagnostic scopes explicit.
- [x] Implement analysis search/status, detail/report/code view, create/edit/multiple-code uploads, save/archive/unarchive/export, CSRF and update conflicts. Escape all user/source text. Historical entries read-only; show original report and incomplete attachments honestly.
- [x] Test meaningful pure frontend query/format/comparison/export behavior and navigation stale-request guards with Node; syntax-check JS. Root handles browser QA against integrated app.

## Task 4 — Integration, browser QA and review (root + independent reviewer)

- [x] Run new tests and relevant existing monitor regression suite, verify real source metrics and isolated GET proxy.
- [x] Launch owned new service, check endpoints, use supported browser UI for all three modules and save/reload/export against temporary analysis data. Inspect the available 723 px browser viewport; failure states covered by service tests. No separate wide-screen browser run claimed.
- [x] Independent review of complete diff focusing on safety and correctness; fix findings, rerun only affected checks.
- [x] Commit scoped new platform/docs/tests, report exact URL/start command and verification limits. Preserve original live service and unrelated changes.

## Progress / rulings

- Approved independent platform and direct execution by user on 2026-10-28.
- Existing monitor baseline tests: 50 passed before changes.
- Independent file owners may work concurrently; no shared-file edits or worker commits.
- Integrated verification before final review fixes: 128 Python tests and 15 Node tests passed, including 83 existing monitor/boundary/wrapper regression tests.
- Browser QA: live overview, batch details/fills, prediction filtering; model/strategy baseline, annual/regime and annual comparison; sandboxed original HTML; historical Markdown; analysis create/edit/archive/reload. ZIP contents independently checked against the saved code and report.
- QA analysis writes were confined to `/private/tmp/qlib-quant-platform-qa-20261028`. Delivery storage is `/Users/yuxianqi/.qlib/research_platform/analyses`.
- Occupied-port check: starting on 8082 exited with code 2 before creating the requested analysis directory; original monitor PID 1026 continued listening.
- Scoped storage/proxy review passed after fixes for complete-record size limits, encoded dot segments and production/shared-drive storage boundaries.
- Final review fixes: explicit h5 label, head tradability and actual ST source now participate in comparison keys; stale archive/restore responses preserve newer drafts. Independent re-review passed with 10 Python and 3 JavaScript handler regressions.
- Final integrated verification: 138 Python tests passed (55 platform + 83 existing regressions), 18 Node tests passed. No training/backtest or full Qlib test suite run.
- Delivery service is running on 127.0.0.1:8090 with separate persistent storage, with no QA records. Original monitor remains the same PID 1026 on 127.0.0.1:8082.
