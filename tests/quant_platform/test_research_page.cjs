const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const H = require('../../quant_platform/static/js/helpers.js');
const Runtime = require('../../quant_platform/static/js/monitor_runtime.js');
const Research = require('../../quant_platform/static/js/research.js');

const decode = text => text.replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

// Parse only markup that the real page renders. Missing selectors stay missing,
// so wiring a handler to a nonexistent control cannot silently pass this harness.
function fakeDocument() {
    class Element {
        constructor(tag = 'div', attributes = {}) {
            this.tagName = tag.toUpperCase(); this.attributes = attributes;
            this.children = []; this.dataset = {}; this.style = {}; this.listeners = {};
            this.checked = Object.hasOwn(attributes, 'checked');
            this.disabled = Object.hasOwn(attributes, 'disabled'); this.open = false;
            this.value = attributes.value || '';
            for (const [key, value] of Object.entries(attributes)) {
                if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
            }
            this.classList = {toggle() {}, contains: value => (attributes.class || '').split(/\s+/).includes(value)};
        }
        set innerHTML(html) {
            this.children = [];
            const stack = [this], tokens = String(html).match(/<[^>]*>|[^<]+/g) || [];
            for (const token of tokens) {
                if (token.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
                if (token.startsWith('<!')) continue;
                if (!token.startsWith('<')) { stack.at(-1).children.push(token); continue; }
                const match = /^<([\w-]+)\b([^>]*)>/.exec(token);
                if (!match) continue;
                const attrs = {};
                for (const attr of match[2].matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
                    attrs[attr[1]] = decode(attr[2] ?? attr[3] ?? attr[4] ?? '');
                }
                const child = new Element(match[1], attrs), parent = stack.at(-1);
                child.parentElement = parent; parent.children.push(child);
                if (!/^(?:input|br|hr|img|meta|link)$/i.test(match[1]) && !token.endsWith('/>')) stack.push(child);
            }
        }
        get innerHTML() { return this.children.map(child => typeof child === 'string' ? child : child.outerHTML).join(''); }
        get outerHTML() {
            const attrs = Object.entries(this.attributes).map(([key, value]) => ` ${key}="${H.escape(value)}"`).join('');
            return `<${this.tagName.toLowerCase()}${attrs}>${this.innerHTML}</${this.tagName.toLowerCase()}>`;
        }
        get textContent() { return decode(this.children.map(child => typeof child === 'string' ? child : child.textContent).join('')); }
        set textContent(value) { this.children = [H.escape(value)]; }
        getAttribute(key) { return this.attributes[key] ?? null; }
        addEventListener(name, handler) { this.listeners[name] = handler; }
        showModal() { this.open = true; }
        close() { this.open = false; this.listeners.close?.(); }
        matches(selector) {
            if (selector.startsWith('#')) return this.attributes.id === selector.slice(1);
            const attr = /^\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]$/.exec(selector);
            if (attr) return Object.hasOwn(this.attributes, attr[1]) && (attr[2] === undefined || this.attributes[attr[1]] === attr[2]);
            return this.tagName.toLowerCase() === selector.toLowerCase();
        }
        querySelectorAll(selector) {
            const parts = selector.trim().split(/\s+/), found = [];
            const visit = parent => {
                for (const child of parent.children) {
                    if (typeof child === 'string') continue;
                    if (child.matches(parts.at(-1))) {
                        let ancestor = child.parentElement, index = parts.length - 2;
                        while (ancestor && index >= 0) {
                            if (ancestor.matches(parts[index])) index--;
                            ancestor = ancestor.parentElement;
                        }
                        if (index < 0) found.push(child);
                    }
                    visit(child);
                }
            };
            visit(this); return found;
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    }
    const root = new Element('body');
    root.innerHTML = '<button id="refresh"></button><span id="module-name"></span>' +
        '<nav><a data-page="model-experiments" href="#model-experiments">模型实验</a>' +
        '<a data-page="strategy-experiments" href="#strategy-experiments">策略实验</a></nav><nav id="monitor-nav"></nav>' +
        '<main id="content"></main><div id="toast"></div><dialog id="detail-dialog">' +
        '<h2 id="dialog-title"></h2><button id="dialog-close"></button><div id="dialog-body"></div></dialog>';
    return {
        getElementById: id => root.querySelector('#' + id),
        querySelectorAll: selector => root.querySelectorAll(selector),
        querySelector: selector => root.querySelector(selector),
        createElement: tag => new Element(tag)
    };
}

function fixtureCatalog() {
    const base = {
        status: 'completed', source_status: 'available', date: '2026-10-28', direction: 'label',
        official: true, comparable: true, scope: 'standard', pool: 'all', seeds: [42, 1000, 2000, 3000, 4000],
        window: {start: '2020-08-03', end: '2026-07-31'}, baseline_ref: 'model/v2',
        hypothesis: '修改训练标签', conclusion: '未通过', note: '独立备注', resources: []
    };
    return {
        records: [
            {...base, id: 'model/v1', name: '模型一', kind: 'model', version: 'v1', is_baseline: true,
                promoted_version: 'v1', comparison_key: 'model', metrics: {net_ann: .31, net_sharpe: .8, rank_ic_mean: .1},
                years: {'2026': {net_ann: .12}}, regimes: {D: {net_ann: .21}}},
            {...base, id: 'model/v2', name: '模型二', kind: 'model', version: 'v2', is_baseline: true, current: true,
                promoted_version: 'v2', comparison_key: 'model', metrics: {net_ann: .41, net_sharpe: 1.1},
                years: {'2025': {net_ann: .23}}, regimes: {T: {net_ann: .32}}},
            {...base, id: 'model/trial', name: '标签试验', kind: 'model', is_baseline: false, version: '',
                promoted_version: '', comparison_key: 'model', metrics: {net_ann: .89, net_sharpe: 1.9},
                years: {'2026': {net_ann: .13}}, regimes: {D: {net_ann: .17}},
                resources: [{id: 'report-trial', name: 'trial.html', kind: 'report', available: true}]},
            {...base, id: 'strategy/v1', name: '策略一', kind: 'strategy', version: 'v1', is_baseline: true,
                promoted_version: 'v1', comparison_key: 'strategy', metrics: {annualized_return: .27},
                years: {'2026': {annualized_return: .08}}, regimes: {F: {annualized_return: .16}}}
        ],
        metric_definitions: {
            model: [{key: 'net_ann', label: '扣费净年化', format: 'pct'}, {key: 'net_sharpe', label: '扣费夏普', format: 'number'},
                {key: 'rank_ic_mean', label: '全局 RankIC', format: 'rankic'}],
            strategy: [{key: 'annualized_return', label: '扣费累乘年化', format: 'pct'},
                {key: 'annualized_return_arith', label: '算术年化（审计）', format: 'pct'}]
        },
        sources: {root: '/research', registry: '/research/registry.jsonl'}, warnings: []
    };
}

async function researchPage(hash = '#model-experiments') {
    const document = fakeDocument(), catalog = fixtureCatalog();
    const flush = () => new Promise(resolve => setImmediate(resolve));
    const listeners = {}, location = {hash};
    const context = {
        document,
        window: {PlatformHelpers: H, PlatformResearch: Research, PlatformNavigation: Runtime.createNavigationTracker(),
            PlatformMonitor: {clear() {}}, addEventListener(name, handler) {listeners[name] = handler;}},
        location, setTimeout() {}, clearTimeout() {}, URLSearchParams,
        fetch: async url => {
            assert.equal(url, '/api/research');
            return {ok: true, json: async () => catalog};
        }
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../quant_platform/static/js/platform.js'), 'utf8'), context);
    await flush(); await flush();
    const get = selector => {
        const element = document.querySelector(selector);
        assert.ok(element, `Expected rendered control ${selector}; page: ${document.getElementById('content').textContent}`);
        return element;
    };
    const click = async selector => {
        const element = get(selector);
        assert.equal(typeof element.onclick, 'function', `Expected click handler ${selector}`);
        const before = location.hash;
        await element.onclick({target: element, preventDefault() {}});
        if (location.hash !== before) await listeners.hashchange();
        await flush();
    };
    const check = async (id, checked) => {
        const element = get(`[data-benchmark="${id}"]`);
        assert.equal(typeof element.onchange, 'function'); element.checked = checked;
        await element.onchange({target: element}); await flush();
    };
    return {document, get, click, check};
}

test('core table replaces cards and version picker, newest first; checkboxes filter only dimension rows', async () => {
    const page = await researchPage();
    assert.match(page.get('#content').textContent, /模型实验/);
    assert.equal(page.document.getElementById('benchmark-selector'), null);
    assert.doesNotMatch(page.get('#content').textContent, /当前 模型 baseline|已晋升版本/);
    const boxes = page.get('#research-core').querySelectorAll('[data-benchmark]');
    assert.deepEqual(boxes.map(box => box.dataset.benchmark), ['model/v2', 'model/v1']);
    assert.ok(boxes.every(box => box.checked));
    assert.equal(page.get('#research-core').querySelectorAll('tbody tr').length, 2);
    assert.equal(page.get('#research-table').querySelectorAll('tbody tr').length, 4);
    await page.check('model/v1', false);
    assert.equal(page.get('#research-core').querySelectorAll('tbody tr').length, 2);
    assert.equal(page.get('#research-table').querySelectorAll('tbody tr').length, 2);
    assert.doesNotMatch(page.get('#research-table').textContent, /模型一/);
    assert.match(page.get('#research-table').textContent, /模型二/);
    assert.match(page.get('#research-core').textContent, /模型一/);
    const html = page.get('#content').innerHTML;
    assert.ok(html.indexOf('id="research-core"') < html.indexOf('id="research-controls"'));
    assert.ok(html.indexOf('id="research-controls"') < html.indexOf('id="research-table"'));
    assert.equal(page.document.querySelector('[data-view="full"]'), null);
});

test('header checkbox clears only dimension selection and persists across dimension tabs and refresh', async () => {
    const page = await researchPage();
    let all = page.get('#benchmark-toggle-all'); all.checked = false; all.onchange({target: all});
    await page.click('[data-view="year"]');
    assert.equal(page.get('#research-table').querySelectorAll('tbody tr').length, 0);
    assert.equal(page.get('#research-core').querySelectorAll('tbody tr').length, 2);
    await page.click('#refresh');
    assert.ok(page.get('#research-core').querySelectorAll('[data-benchmark]').every(box => !box.checked));
    assert.equal(page.get('#research-table').querySelectorAll('tbody tr').length, 0);
    all = page.get('#benchmark-toggle-all'); all.checked = true; all.onchange({target: all});
    assert.ok(page.get('#research-core').querySelectorAll('[data-benchmark]').every(box => box.checked));
    assert.equal(page.get('#research-table').querySelectorAll('table').length, 2);
});

test('model and strategy version selections stay independent when switching kinds', async () => {
    const page = await researchPage();
    await page.check('model/v1', false);
    await page.click('[data-page="strategy-experiments"]');
    assert.equal(page.get('#module-name').textContent, '策略实验');
    assert.deepEqual(page.get('#research-core').querySelectorAll('[data-benchmark]').map(box => [box.dataset.benchmark, box.checked]), [['strategy/v1', true]]);
    assert.match(page.get('#research-table').textContent, /策略一/);
    assert.doesNotMatch(page.get('#research-core').textContent, /算术年化/);
    assert.doesNotMatch(page.get('#research-table').textContent, /算术年化/);
    await page.click('[data-page="model-experiments"]');
    assert.equal(page.get('#module-name').textContent, '模型实验');
    assert.equal(page.get('[data-benchmark="model/v1"]').checked, false);
    assert.equal(page.get('[data-benchmark="model/v2"]').checked, true);
    assert.doesNotMatch(page.get('#research-table').textContent, /模型一/);
});

test('sidebar exposes direct model and strategy routes while the old research link still opens models', async () => {
    const html = fs.readFileSync(path.join(__dirname, '../../quant_platform/static/index.html'), 'utf8');
    assert.match(html, /href="#model-experiments" data-page="model-experiments"/);
    assert.match(html, /href="#strategy-experiments" data-page="strategy-experiments"/);
    assert.doesNotMatch(html, /data-page="research"/);
    const strategy = await researchPage('#strategy-experiments');
    assert.equal(strategy.get('#module-name').textContent, '策略实验');
    assert.match(strategy.get('#research-core').querySelector('[data-detail="strategy/v1"]').getAttribute('title'), /策略一/);
    const legacy = await researchPage('#research');
    assert.equal(legacy.get('#module-name').textContent, '模型实验');
    assert.equal(legacy.get('#research-core').querySelectorAll('tbody tr').length, 2);
});

test('experiment records expose content conclusion report links and promoted version without metric columns', async () => {
    const page = await researchPage();
    await page.click('[data-module="records"]');
    const table = page.get('#research-table');
    const headers = table.querySelectorAll('th').map(header => header.textContent);
    for (const label of ['日期', '实验内容', '结论', '报告链接', '晋升版本']) assert.ok(headers.includes(label), label);
    assert.match(table.textContent, /修改训练标签/); assert.match(table.textContent, /未通过/);
    assert.doesNotMatch(table.textContent, /扣费净年化/);
    const link = table.querySelectorAll('a').find(anchor => anchor.getAttribute('href') === '/api/resources/report-trial');
    assert.ok(link, 'The trial report has a direct resource link');
    assert.match(link.textContent, /trial\.html/);
});

test('single experiment detail switches to yearly metric matrices with slices rather than full values', async () => {
    const page = await researchPage();
    await page.click('[data-module="records"]');
    await page.click('[data-detail="model/trial"]');
    assert.equal(page.get('#detail-dialog').open, true);
    assert.match(page.get('#detail-metrics').textContent, /89\.00%/);
    await page.click('[data-detail-view="year"]');
    const matrices = page.get('#detail-metrics');
    assert.equal(matrices.querySelectorAll('table').length, 2);
    assert.match(matrices.textContent, /2020/); assert.match(matrices.textContent, /2026/);
    assert.match(matrices.textContent, /13\.00%/);
    assert.doesNotMatch(matrices.textContent, /89\.00%|全局 RankIC/);
    assert.ok(matrices.querySelectorAll('tbody tr').every(row => row.textContent.includes('标签试验')));
});
