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
