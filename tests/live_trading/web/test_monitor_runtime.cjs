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
