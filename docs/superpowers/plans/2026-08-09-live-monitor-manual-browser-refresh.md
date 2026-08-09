# Live Monitor Manual Browser Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all automatic live-monitor refreshes and keep the SPA responsive by owning chart lifecycles, rejecting stale navigation results, and deferring the prediction instrument index.

**Architecture:** Add one dependency-free classic-script runtime, `monitor_runtime.js`, that is also loadable through CommonJS for Node tests. `app.js` delegates chart ownership, navigation generations, and lazy prediction loading to this runtime while retaining all existing REST endpoints and HTML rendering.

**Tech Stack:** Vanilla JavaScript, ECharts 5, Node.js 22 built-in `node:test`/`vm`, Python 3.11 pytest 9, FastAPI static-file integration.

## Global Constraints

- The browser's native page reload is the only refresh mechanism; do not add a refresh button, timer, visibility-triggered request, or “last refreshed” UI.
- Keep all current REST paths and response schemas unchanged; the monitor remains GET/HEAD-only.
- At most one ECharts instance may be owned by the SPA at a time, with one window `resize` listener.
- A stale page request must never write success or error content after a newer navigation begins.
- The prediction instrument index must not block dates, summary, chart, or the first result table and must load at most once per browser page lifetime.
- Do not add npm packages, a `package.json`, WebSocket, server-sent events, or a frontend framework.
- Use `/opt/anaconda3/envs/qlib/bin/python` for pytest. Do not use heredoc/stdin to run Python that imports Qlib.
- Do not touch or stage unrelated `backtest/experiments/diagnostics/20260809_b4s_alpha_decay/` artifacts.

---

## File Structure

- Create `live_trading/web/static/js/monitor_runtime.js`: browser/resource lifecycle primitives shared by production and Node tests.
- Modify `live_trading/web/static/index.html`: load the runtime before `app.js`; add no controls.
- Modify `live_trading/web/static/js/app.js`: remove polling and use the runtime.
- Create `tests/live_trading/web/test_monitor_runtime.cjs`: Node unit and VM integration tests against the production scripts.
- Create `tests/live_trading/test_monitor_web_frontend.py`: pytest wrapper for the Node suite.
- Modify `tests/live_trading/test_monitor_web_api.py`: include the new runtime in its existing read-only asset scan; change no API assertions or fixtures.

### Task 1: Frontend test harness and chart ownership primitive

**Files:**
- Create: `tests/live_trading/web/test_monitor_runtime.cjs`
- Create: `tests/live_trading/test_monitor_web_frontend.py`
- Create: `live_trading/web/static/js/monitor_runtime.js`

**Interfaces:**
- Consumes: an event target with `addEventListener('resize', fn)` and chart objects with `resize()`/`dispose()`.
- Produces: `MonitorRuntime.createChartManager(resizeTarget)` with `replace(chart)`, `clear()`, and `current()`; exports the same API through CommonJS.

- [ ] **Step 1: Write the failing chart-manager Node test**

Create `tests/live_trading/web/test_monitor_runtime.cjs`:

```javascript
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '../../..');
const runtimePath = path.join(repoRoot,
    'live_trading/web/static/js/monitor_runtime.js');
const appPath = path.join(repoRoot, 'live_trading/web/static/js/app.js');

test('chart manager disposes the old chart and resizes only the current chart', () => {
    const resizeHandlers = [];
    const target = { addEventListener(name, handler) {
        assert.equal(name, 'resize');
        resizeHandlers.push(handler);
    }};
    const { createChartManager } = require(runtimePath);
    const manager = createChartManager(target);
    const chart = () => ({
        disposed: 0, resized: 0,
        dispose() { this.disposed += 1; },
        resize() { this.resized += 1; },
    });
    const first = chart();
    const second = chart();
    manager.replace(first);
    manager.replace(second);
    resizeHandlers[0]();
    assert.equal(resizeHandlers.length, 1);
    assert.equal(first.disposed, 1);
    assert.equal(first.resized, 0);
    assert.equal(second.resized, 1);
    assert.equal(manager.current(), second);
    manager.clear();
    assert.equal(second.disposed, 1);
    assert.equal(manager.current(), null);
});
```

- [ ] **Step 2: Add the pytest-to-Node bridge**

Create `tests/live_trading/test_monitor_web_frontend.py`:

```python
"""Run dependency-free monitor frontend behavior tests with Node."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
NODE_TEST = REPO_ROOT / "tests/live_trading/web/test_monitor_runtime.cjs"

@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_monitor_frontend_runtime():
    result = subprocess.run(
        [NODE, "--test", str(NODE_TEST)], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 3: Verify RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_monitor_web_frontend.py -q
```

Expected: FAIL because `monitor_runtime.js` is absent.

- [ ] **Step 4: Implement the minimal chart manager**

Create `live_trading/web/static/js/monitor_runtime.js`:

```javascript
/* Dependency-free runtime primitives for the live-monitor SPA. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.MonitorRuntime = api;
}(typeof globalThis === 'undefined' ? null : globalThis, function () {
    'use strict';
    function createChartManager(resizeTarget) {
        let activeChart = null;
        resizeTarget.addEventListener('resize', () => {
            if (activeChart) activeChart.resize();
        });
        return {
            replace(chart) {
                if (activeChart && activeChart !== chart) activeChart.dispose();
                activeChart = chart || null;
                return activeChart;
            },
            clear() {
                if (activeChart) activeChart.dispose();
                activeChart = null;
            },
            current() { return activeChart; },
        };
    }
    return { createChartManager };
}));
```

- [ ] **Step 5: Verify GREEN and commit**

```bash
node --test tests/live_trading/web/test_monitor_runtime.cjs
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_monitor_web_frontend.py -q
git add live_trading/web/static/js/monitor_runtime.js \
  tests/live_trading/web/test_monitor_runtime.cjs \
  tests/live_trading/test_monitor_web_frontend.py
git commit -m "test(live): add monitor frontend behavior harness"
```

Expected: one Node subtest and the pytest wrapper PASS before committing.

### Task 2: Remove automatic refresh and wire chart cleanup

**Files:**
- Modify: `tests/live_trading/web/test_monitor_runtime.cjs`
- Modify: `tests/live_trading/test_monitor_web_api.py:327-333`
- Modify: `live_trading/web/static/index.html:30`
- Modify: `live_trading/web/static/js/app.js:3-5,113-136,468-527,691-715`

**Interfaces:**
- Consumes: `window.MonitorRuntime.createChartManager(window)` from Task 1.
- Produces: one initial dashboard navigation, zero scheduled refresh intervals, and one active chart across dashboard/prediction navigation.

- [ ] **Step 1: Add a VM harness that executes production JavaScript**

Append this harness to the Node test. It mocks only the external DOM/fetch/ECharts boundaries:

```javascript
function createDeferred() {
    let resolve;
    let reject;
    const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
    return { promise, resolve, reject };
}

function createAppHarness(fetchImpl) {
    const content = { innerHTML: '' };
    const elements = {
        content,
        'nav-chart': {},
        'strategy-badge': { innerHTML: '' },
        'execution-status': { innerHTML: '' },
    };
    const intervals = [];
    const resizeHandlers = [];
    const charts = [];
    const context = vm.createContext({
        console, URLSearchParams, fetch: fetchImpl,
        document: {
            getElementById(id) { return elements[id] || null; },
            querySelectorAll() { return []; },
        },
        echarts: {
            init() {
                const chart = {
                    disposed: 0, resized: 0,
                    dispose() { this.disposed += 1; },
                    resize() { this.resized += 1; },
                    setOption() {},
                };
                charts.push(chart);
                return chart;
            },
            getInstanceByDom() { return null; },
        },
        setInterval(handler, milliseconds) {
            intervals.push({ handler, milliseconds });
            return intervals.length;
        },
        clearInterval() {},
    });
    context.window = context;
    context.globalThis = context;
    context.addEventListener = (name, handler) => {
        assert.equal(name, 'resize');
        resizeHandlers.push(handler);
    };
    vm.runInContext(fs.readFileSync(runtimePath, 'utf8'), context);
    vm.runInContext(fs.readFileSync(appPath, 'utf8'), context);
    return { charts, content, context, elements, intervals, resizeHandlers };
}
```

- [ ] **Step 2: Add failing startup and chart integration tests**

```javascript
test('app starts once without scheduling automatic refresh', async () => {
    const paths = [];
    const never = new Promise(() => {});
    const harness = createAppHarness(pathname => {
        paths.push(pathname);
        return never;
    });
    await Promise.resolve();
    assert.deepEqual(paths.sort(), ['/api/nav', '/api/overview']);
    assert.equal(harness.intervals.length, 0);
});

test('dashboard redraw disposes its previous ECharts instance', () => {
    const harness = createAppHarness(() => new Promise(() => {}));
    const rows = [{ date: '2026-08-07', cumulative_return: 0.01,
        benchmark_cumulative_return: 0.02 }];
    harness.context.drawNavChart(rows, '中证1000');
    harness.context.drawNavChart(rows, '中证1000');
    harness.resizeHandlers[0]();
    assert.equal(harness.resizeHandlers.length, 1);
    assert.equal(harness.charts.length, 2);
    assert.equal(harness.charts[0].disposed, 1);
    assert.equal(harness.charts[0].resized, 0);
    assert.equal(harness.charts[1].resized, 1);
});
```

- [ ] **Step 3: Verify RED**

```bash
node --test tests/live_trading/web/test_monitor_runtime.cjs
```

Expected: FAIL because current `app.js` schedules a 60-second interval, registers resize listeners per chart, and retains the old chart.

- [ ] **Step 4: Load the runtime and remove polling**

Change the bottom of `index.html` without adding controls:

```html
<script src="/js/monitor_runtime.js"></script>
<script src="/js/app.js"></script>
```

Add `live_trading/web/static/js/monitor_runtime.js` to the asset tuple in
`test_web_monitor_exposes_no_marker_or_publish_controls`, so the new production
script is covered by the existing read-only boundary check.

At the top of `app.js`, remove `refreshTimer` and add:

```javascript
const chartManager = MonitorRuntime.createChartManager(window);
```

Delete `scheduleRefresh()`, its `setInterval`, and the final `scheduleRefresh()` call. Keep exactly:

```javascript
navigate('dashboard');
```

- [ ] **Step 5: Centralize chart ownership**

At the beginning of both `drawNavChart` and `loadPredMeanChart`, call `chartManager.clear()` before empty-data/initialization branches. After `echarts.init(...).setOption(...)`, register the instance with:

```javascript
chartManager.replace(chart);
```

Delete both anonymous `window.addEventListener('resize', ...)` calls and the local `echarts.getInstanceByDom(...).dispose()` branch. At the start of `navigate`, call `chartManager.clear()` before replacing `content.innerHTML`.

- [ ] **Step 6: Verify GREEN and commit**

```bash
node --check live_trading/web/static/js/monitor_runtime.js
node --check live_trading/web/static/js/app.js
node --test tests/live_trading/web/test_monitor_runtime.cjs
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_monitor_web_frontend.py \
  tests/live_trading/test_monitor_web_api.py -q
git add live_trading/web/static/index.html \
  live_trading/web/static/js/app.js \
  tests/live_trading/test_monitor_web_api.py \
  tests/live_trading/web/test_monitor_runtime.cjs
git commit -m "fix(live): stop automatic monitor redraws"
```

Expected: syntax checks, Node tests, and pytest pass before committing.

### Task 3: Reject stale page responses

**Files:**
- Modify: `tests/live_trading/web/test_monitor_runtime.cjs`
- Modify: `live_trading/web/static/js/monitor_runtime.js`
- Modify: `live_trading/web/static/js/app.js:38-676,681-701`

**Interfaces:**
- Consumes: the existing sidebar page names.
- Produces: `MonitorRuntime.createNavigationTracker()` with `begin(page)`, `isCurrent(token)`, and `currentPage()`; each async renderer accepts a token.

- [ ] **Step 1: Write failing unit and VM integration tests**

```javascript
test('navigation tracker invalidates every older page token', () => {
    const { createNavigationTracker } = require(runtimePath);
    const tracker = createNavigationTracker();
    const dashboard = tracker.begin('dashboard');
    const alerts = tracker.begin('alerts');
    assert.equal(tracker.isCurrent(dashboard), false);
    assert.equal(tracker.isCurrent(alerts), true);
    assert.equal(tracker.currentPage(), 'alerts');
});

test('an older dashboard response cannot overwrite a newer alerts page', async () => {
    const overview = createDeferred();
    const nav = createDeferred();
    const responses = {
        '/api/overview': overview.promise,
        '/api/nav': nav.promise,
        '/api/alerts?limit=100': Promise.resolve([]),
    };
    const harness = createAppHarness(async pathname => ({
        ok: true, json: async () => responses[pathname],
    }));
    await harness.context.navigate('alerts');
    const alertsHtml = harness.content.innerHTML;
    overview.resolve({ snapshot: null, strategy_id: 'main', mode: 'LIVE',
        account_id: '', active_batch_id: '', strategy_statuses: [] });
    nav.resolve([]);
    await Promise.all([overview.promise, nav.promise]);
    await new Promise(resolve => setImmediate(resolve));
    assert.match(alertsHtml, /告警历史/);
    assert.equal(harness.content.innerHTML, alertsHtml);
});
```

- [ ] **Step 2: Verify RED**

```bash
node --test tests/live_trading/web/test_monitor_runtime.cjs
```

Expected: FAIL because the tracker is absent and the old dashboard can still write after the alerts navigation.

- [ ] **Step 3: Implement and export the tracker**

Add inside the runtime factory:

```javascript
function createNavigationTracker() {
    let generation = 0;
    let page = null;
    return {
        begin(nextPage) {
            page = nextPage;
            generation += 1;
            return Object.freeze({ page, generation });
        },
        isCurrent(token) {
            return Boolean(token) && token.page === page
                && token.generation === generation;
        },
        currentPage() { return page; },
    };
}
return { createChartManager, createNavigationTracker };
```

- [ ] **Step 4: Guard every asynchronous DOM write**

Create `const navigationTracker = MonitorRuntime.createNavigationTracker();`. Change `navigate(page)` to use a token and guard its error:

```javascript
async function navigate(page) {
    const token = navigationTracker.begin(page);
    currentPage = page;
    chartManager.clear();
    document.querySelectorAll('.sidebar nav a').forEach(a =>
        a.classList.toggle('active', a.dataset.page === page));
    content.innerHTML = '<div class="loading">加载中...</div>';
    try {
        await PAGES[page](token);
    } catch (e) {
        if (navigationTracker.isCurrent(token)) {
            content.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
        }
    }
}
```

Add `token` to `renderDashboard`, `renderPositions`, `renderBatches`, `renderPredictions`, `renderCashflows`, `renderPipeline`, and `renderAlerts`. After each awaited API group and before DOM access, use:

```javascript
if (!navigationTracker.isCurrent(token)) return;
```

Repeat the check after the positions-history request. Capture and pass the prediction token through `loadPredSummary(token)`, `loadPredMeanChart(token)`, and `predSearch(page, token)`; each checks before accessing prediction DOM nodes.

- [ ] **Step 5: Verify GREEN and commit**

```bash
node --test tests/live_trading/web/test_monitor_runtime.cjs
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_monitor_web_frontend.py \
  tests/live_trading/test_monitor_web_api.py -q
git add live_trading/web/static/js/monitor_runtime.js \
  live_trading/web/static/js/app.js \
  tests/live_trading/web/test_monitor_runtime.cjs
git commit -m "fix(live): ignore stale monitor page responses"
```

Expected: all tests pass, including the deferred dashboard integration case.

### Task 4: Defer and cache the prediction instrument index

**Files:**
- Modify: `tests/live_trading/web/test_monitor_runtime.cjs`
- Modify: `live_trading/web/static/js/monitor_runtime.js`
- Modify: `live_trading/web/static/js/app.js:338-527`

**Interfaces:**
- Consumes: date, primary-data, and instrument-index promise loaders.
- Produces: `MonitorRuntime.createLazyResource(loader).load()` and `MonitorRuntime.loadPredictionPage(options)`; primary prediction rendering resolves independently from instruments.

- [ ] **Step 1: Write failing lazy-load tests**

```javascript
test('lazy resource invokes its loader once per page lifetime', async () => {
    const { createLazyResource } = require(runtimePath);
    let calls = 0;
    const resource = createLazyResource(async () => {
        calls += 1;
        return ['SH600000'];
    });
    const [first, second] = await Promise.all([resource.load(), resource.load()]);
    assert.deepEqual(first, ['SH600000']);
    assert.deepEqual(second, ['SH600000']);
    assert.equal(calls, 1);
});

test('prediction primary rendering does not wait for instruments', async () => {
    const { createLazyResource, loadPredictionPage } = require(runtimePath);
    const instruments = createDeferred();
    const events = [];
    const resource = createLazyResource(() => instruments.promise);
    await loadPredictionPage({
        loadDates: async () => ['2026-08-07'],
        renderShell: dates => events.push(`shell:${dates[0]}`),
        loadPrimary: async () => events.push('primary'),
        instrumentResource: resource,
        acceptInstruments: rows => events.push(`instruments:${rows.length}`),
        rejectInstruments: () => events.push('instrument-error'),
        isCurrent: () => true,
    });
    assert.deepEqual(events, ['shell:2026-08-07', 'primary']);
    instruments.resolve(['SH600000']);
    await instruments.promise;
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(events,
        ['shell:2026-08-07', 'primary', 'instruments:1']);
});
```

- [ ] **Step 2: Verify RED**

```bash
node --test tests/live_trading/web/test_monitor_runtime.cjs
```

Expected: FAIL because `createLazyResource` and `loadPredictionPage` are absent.

- [ ] **Step 3: Implement and export the lazy primitives**

```javascript
function createLazyResource(loader) {
    let promise = null;
    return {
        load() {
            if (!promise) promise = Promise.resolve().then(loader);
            return promise;
        },
    };
}

async function loadPredictionPage(options) {
    const dates = await options.loadDates();
    if (!options.isCurrent()) return;
    if (options.renderShell(dates) === false) return;
    options.instrumentResource.load().then(
        rows => {
            if (options.isCurrent()) options.acceptInstruments(rows);
        },
        error => {
            if (options.isCurrent()) options.rejectInstruments(error);
        },
    );
    await options.loadPrimary();
}

return {
    createChartManager,
    createLazyResource,
    createNavigationTracker,
    loadPredictionPage,
};
```

Keep rejected promises cached so only a browser page reload retries, matching the approved refresh semantics.

- [ ] **Step 4: Reorder prediction loading without changing the UI**

Define one page-lifetime resource next to `predInstruments`:

```javascript
const predInstrumentResource = MonitorRuntime.createLazyResource(
    () => api('/predictions/instruments'));
```

Extract existing HTML construction and event binding into `renderPredictionShell(dates, token)`. Preserve all copy, selectors, handlers, and pagination. Return `false` after rendering the existing empty state when `dates.length === 0`; return `true` after binding the populated shell. Implement `renderPredictions(token)` as:

```javascript
return MonitorRuntime.loadPredictionPage({
    loadDates: () => api('/predictions/dates'),
    isCurrent: () => navigationTracker.isCurrent(token),
    renderShell: dates => renderPredictionShell(dates, token),
    loadPrimary: () => Promise.all([
        loadPredSummary(token),
        loadPredMeanChart(token),
        predSearch(0, token),
    ]),
    instrumentResource: predInstrumentResource,
    acceptInstruments: rows => { predInstruments = rows || []; },
    rejectInstruments: () => { predInstruments = []; },
});
```

Do not await `predInstrumentResource` from `loadPrimary`. Pass `token` through the existing search, date, reset, autocomplete, and pagination handlers.

- [ ] **Step 5: Verify GREEN and commit**

```bash
node --check live_trading/web/static/js/monitor_runtime.js
node --check live_trading/web/static/js/app.js
node --test tests/live_trading/web/test_monitor_runtime.cjs
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_monitor_web_frontend.py \
  tests/live_trading/test_monitor_web_api.py -q
git add live_trading/web/static/js/monitor_runtime.js \
  live_trading/web/static/js/app.js \
  tests/live_trading/web/test_monitor_runtime.cjs
git commit -m "perf(live): defer prediction instrument index"
```

Expected: syntax checks and all tests pass; Node output includes both lazy-loading cases.

### Task 5: Full regression and browser acceptance

**Files:**
- Verify: `live_trading/web/static/index.html`
- Verify: `live_trading/web/static/js/monitor_runtime.js`
- Verify: `live_trading/web/static/js/app.js`
- Verify: `tests/live_trading/web/test_monitor_runtime.cjs`
- Verify: `tests/live_trading/test_monitor_web_frontend.py`

**Interfaces:**
- Consumes: the running read-only monitor at `http://127.0.0.1:8081/`.
- Produces: fresh automated and browser evidence for every design criterion.

- [ ] **Step 1: Run complete focused verification**

```bash
node --check live_trading/web/static/js/monitor_runtime.js
node --check live_trading/web/static/js/app.js
node --test tests/live_trading/web/test_monitor_runtime.cjs
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_monitor_web_frontend.py \
  tests/live_trading/test_monitor_web_api.py -q
git diff --check
```

Expected: all commands pass and `git diff --check` emits no output.

- [ ] **Step 2: Verify browser-native reload and no custom refresh UI**

Reload `http://127.0.0.1:8081/` in the in-app browser. Confirm current snapshot/account/batch data render, no refresh button exists, and the console has no warnings or errors.

- [ ] **Step 3: Prove the former interval is inactive**

Record the `#nav-chart` `_echarts_instance_`, wait 65 seconds using waits shorter than 60 seconds, and read it again. Expected: the ID is unchanged, “加载中” never appears, and the curve does not replay.

- [ ] **Step 4: Exercise navigation and prediction responsiveness**

Navigate repeatedly through 概览 → 预测信号 → 持仓 → 概览. Confirm the content always matches the selected sidebar item, stale pages do not replace it, chart pages hold one canvas, non-chart pages hold zero canvases, and the prediction result table becomes visible independently of the instrument-index request.

- [ ] **Step 5: Audit the approved scope**

Compare the diff with `docs/superpowers/specs/2026-08-09-live-monitor-manual-browser-refresh-design.md`. Confirm no auto refresh or custom refresh UI, charts are disposed centrally, stale writes are rejected, prediction instruments are lazy/cached, API files are unchanged, and unrelated Alpha-decay artifacts are unstaged.

- [ ] **Step 6: Commit only if verification required a correction**

For a correction, repeat its RED/GREEN test and commit only affected monitor files:

```bash
git add live_trading/web/static/index.html \
  live_trading/web/static/js/monitor_runtime.js \
  live_trading/web/static/js/app.js \
  tests/live_trading/web/test_monitor_runtime.cjs \
  tests/live_trading/test_monitor_web_frontend.py
git commit -m "fix(live): close monitor refresh verification gap"
```

If no correction is necessary, do not create an empty commit.
