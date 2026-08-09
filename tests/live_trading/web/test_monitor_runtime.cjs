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
