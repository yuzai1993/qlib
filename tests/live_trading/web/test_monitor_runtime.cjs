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

test('navigation tracker invalidates every older page token', () => {
    const { createNavigationTracker } = require(runtimePath);
    const tracker = createNavigationTracker();
    const dashboard = tracker.begin('dashboard');
    const alerts = tracker.begin('alerts');
    assert.equal(tracker.isCurrent(dashboard), false);
    assert.equal(tracker.isCurrent(alerts), true);
    assert.equal(tracker.currentPage(), 'alerts');
});

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
    overview.resolve({
        snapshot: null,
        strategy_id: 'main',
        mode: 'LIVE',
        account_id: '',
        active_batch_id: '',
        strategy_statuses: [],
        stages: {},
        recent_alerts: [],
    });
    nav.resolve([]);
    await Promise.all([overview.promise, nav.promise]);
    await new Promise(resolve => setImmediate(resolve));
    assert.match(alertsHtml, /告警历史/);
    assert.equal(harness.content.innerHTML, alertsHtml);
});
