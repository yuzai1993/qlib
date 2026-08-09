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
                    disposed: 0, resized: 0, option: null,
                    dispose() { this.disposed += 1; },
                    resize() { this.resized += 1; },
                    setOption(option) { this.option = option; },
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

function jsonResponse(data) {
    return { ok: true, json: async () => data };
}

function installPredictionElements(harness, query = '') {
    const input = {
        value: query,
        style: {},
        addEventListener() {},
    };
    const elements = {
        'pred-date': { value: '2026-08-07' },
        'pred-query': input,
        'pred-query-ac': {
            innerHTML: '', style: {}, querySelectorAll() { return []; },
        },
        'pred-search-btn': {},
        'pred-reset-btn': {},
        'pred-summary': { innerHTML: '' },
        'pred-mean-sub': { textContent: '' },
        'pred-mean-chart': { innerHTML: '' },
        'pred-info': { textContent: '' },
        'pred-table': { innerHTML: '' },
        'pred-pagination': { innerHTML: '' },
    };
    Object.assign(harness.elements, elements);
    return elements;
}

function createPredictionHarness(route, query = '') {
    const never = new Promise(() => {});
    const harness = createAppHarness(pathname => {
        if (pathname === '/api/overview' || pathname === '/api/nav') return never;
        const custom = route(pathname);
        if (custom !== undefined) return jsonResponse(custom);
        if (pathname === '/api/predictions/dates') {
            return jsonResponse(['2026-08-07']);
        }
        if (pathname === '/api/predictions/instruments') return jsonResponse([]);
        if (pathname.startsWith('/api/predictions/summary?')) {
            return jsonResponse({
                date: '2026-08-07', count: 1, mean_score: 0.1,
                top: [], bottom: [],
            });
        }
        if (pathname.startsWith('/api/predictions/daily-mean')) {
            return jsonResponse([{ date: '2026-08-07', mean_score: 0.1 }]);
        }
        if (pathname.startsWith('/api/predictions?')) {
            return jsonResponse({ data: [], total: 0 });
        }
        if (pathname === '/api/alerts?limit=100') return jsonResponse([]);
        throw new Error(`unexpected request: ${pathname}`);
    });
    const elements = installPredictionElements(harness, query);
    return { ...harness, elements };
}

function currentPredictionToken(harness) {
    return vm.runInContext('predToken', harness.context);
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

test('an older dashboard rejection cannot alter the alerts page or chart', async () => {
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
    harness.context.drawNavChart([{
        date: '2026-08-07', cumulative_return: 0.01,
        benchmark_cumulative_return: 0.02,
    }], 'new page chart');
    const activeChart = vm.runInContext('chartManager.current()', harness.context);

    overview.reject(new Error('late dashboard failure'));
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(harness.content.innerHTML, alertsHtml);
    assert.equal(activeChart.disposed, 0);
    assert.equal(vm.runInContext('chartManager.current()', harness.context), activeChart);
});

test('current prediction load failure disposes its completed chart', async () => {
    const summary = createDeferred();
    const harness = createPredictionHarness(pathname => {
        if (pathname.startsWith('/api/predictions/summary?')) {
            return summary.promise;
        }
        return undefined;
    });
    const navigation = harness.context.navigate('predictions');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.charts.length, 1);
    assert.equal(vm.runInContext('chartManager.current()', harness.context),
        harness.charts[0]);

    summary.reject(new Error('summary unavailable'));
    await navigation;

    assert.match(harness.content.innerHTML, /加载失败：summary unavailable/);
    assert.equal(harness.charts[0].disposed, 1);
    assert.equal(vm.runInContext('chartManager.current()', harness.context), null);
});

test('newer prediction summary wins when responses arrive out of order', async () => {
    const oldSummary = createDeferred();
    const newSummary = createDeferred();
    const harness = createPredictionHarness(pathname => {
        if (pathname.includes('date=2026-08-06')) return oldSummary.promise;
        if (pathname.includes('date=2026-08-05')) return newSummary.promise;
        return undefined;
    });
    await harness.context.navigate('predictions');
    const token = currentPredictionToken(harness);

    harness.elements['pred-date'].value = '2026-08-06';
    const oldRequest = harness.context.loadPredSummary(token);
    harness.elements['pred-date'].value = '2026-08-05';
    const newRequest = harness.context.loadPredSummary(token);

    newSummary.resolve({
        date: '2026-08-05', count: 5, mean_score: 0.5,
        top: [], bottom: [],
    });
    await newRequest;
    oldSummary.resolve({
        date: '2026-08-06', count: 6, mean_score: 0.6,
        top: [], bottom: [],
    });
    await oldRequest;

    assert.match(harness.elements['pred-summary'].innerHTML, /2026-08-05/);
    assert.doesNotMatch(harness.elements['pred-summary'].innerHTML, /2026-08-06/);
});

test('newer prediction search wins when responses arrive out of order', async () => {
    const oldSearch = createDeferred();
    const newSearch = createDeferred();
    const harness = createPredictionHarness(pathname => {
        const parsed = new URL(pathname, 'http://monitor.test');
        if (parsed.searchParams.get('instrument') === 'OLD') return oldSearch.promise;
        if (parsed.searchParams.get('instrument') === 'NEW') return newSearch.promise;
        return undefined;
    });
    await harness.context.navigate('predictions');

    harness.elements['pred-query'].value = 'OLD';
    const oldRequest = harness.context.predSearch(0);
    harness.elements['pred-query'].value = 'NEW';
    const newRequest = harness.context.predSearch(0);

    newSearch.resolve({
        data: [{ rank: 1, instrument: 'SZ000001', stock_code: 'NEW',
            name: 'new row', date: '2026-08-07', score: 0.2 }],
        total: 1,
    });
    await newRequest;
    oldSearch.resolve({
        data: [{ rank: 1, instrument: 'SH600000', stock_code: 'OLD',
            name: 'old row', date: '2026-08-07', score: 0.1 }],
        total: 1,
    });
    await oldRequest;

    assert.match(harness.elements['pred-table'].innerHTML, /NEW/);
    assert.doesNotMatch(harness.elements['pred-table'].innerHTML, /OLD/);
});

test('stale daily-mean response cannot replace the newer chart', async () => {
    const oldMean = createDeferred();
    const oldStock = createDeferred();
    const newMean = createDeferred();
    const newStock = createDeferred();
    let meanCalls = 0;
    const harness = createPredictionHarness(pathname => {
        if (pathname === '/api/predictions/instruments') {
            return [
                { instrument: 'SH600000', stock_code: '600000.SH', name: 'old stock' },
                { instrument: 'SZ000001', stock_code: '000001.SZ', name: 'new stock' },
            ];
        }
        if (pathname === '/api/predictions/daily-mean') {
            meanCalls += 1;
            if (meanCalls === 2) return oldMean.promise;
            if (meanCalls === 3) return newMean.promise;
        }
        if (pathname.endsWith('instruments=SH600000')) return oldStock.promise;
        if (pathname.endsWith('instruments=SZ000001')) return newStock.promise;
        return undefined;
    });
    await harness.context.navigate('predictions');
    await new Promise(resolve => setImmediate(resolve));
    const token = currentPredictionToken(harness);

    harness.elements['pred-query'].value = '600000.SH';
    const oldRequest = harness.context.loadPredMeanChart(token);
    harness.elements['pred-query'].value = '000001.SZ';
    const newRequest = harness.context.loadPredMeanChart(token);

    newMean.resolve([{ date: '2026-08-07', mean_score: 0.2 }]);
    newStock.resolve([{ date: '2026-08-07', mean_score: 0.3 }]);
    await newRequest;
    oldMean.resolve([{ date: '2026-08-07', mean_score: 0.1 }]);
    oldStock.resolve([{ date: '2026-08-07', mean_score: 0.15 }]);
    await oldRequest;

    assert.equal(harness.charts.length, 2);
    assert.equal(harness.charts[1].option.series[1].name,
        '000001.SZ new stock');
});

test('complete instrument codes load stock curves without the index', async () => {
    const instruments = createDeferred();
    const paths = [];
    const harness = createPredictionHarness(pathname => {
        paths.push(pathname);
        if (pathname === '/api/predictions/instruments') return instruments.promise;
        return undefined;
    }, '600000.SH');
    await harness.context.navigate('predictions');
    assert.equal(paths.filter(pathname =>
        pathname.endsWith('daily-mean?instruments=600000.SH')).length, 1);

    instruments.reject(new Error('instrument index unavailable'));
    await new Promise(resolve => setImmediate(resolve));
    harness.elements['pred-query'].value = 'SH600000';
    await harness.context.loadPredMeanChart(currentPredictionToken(harness));

    assert.equal(paths.filter(pathname =>
        pathname.endsWith('daily-mean?instruments=SH600000')).length, 1);
});

test('stale instrument rejection is consumed and remains cached', async () => {
    const instruments = createDeferred();
    let instrumentCalls = 0;
    const harness = createPredictionHarness(pathname => {
        if (pathname === '/api/predictions/instruments') {
            instrumentCalls += 1;
            return instruments.promise;
        }
        return undefined;
    });
    await harness.context.navigate('predictions');
    await harness.context.navigate('alerts');
    const alertsHtml = harness.content.innerHTML;

    instruments.reject(new Error('instrument index unavailable'));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.content.innerHTML, alertsHtml);

    await harness.context.navigate('predictions');
    assert.equal(instrumentCalls, 1);
});
