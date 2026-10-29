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

// A small DOM boundary for the real page scripts; selectors only find rendered controls.
function pageDocument() {
    class Element {
        constructor(tag = 'div', attributes = {}) {
            this.tagName = tag.toUpperCase(); this.attributes = attributes; this.children = [];
            this.dataset = {}; this.style = {}; this.listeners = {}; this.open = false;
            for (const declaration of (attributes.style || '').split(';')) {
                const [key, value] = declaration.split(':'); if (key) this.style[key.trim()] = value?.trim();
            }
            this.hidden = Object.hasOwn(attributes, 'hidden'); this.disabled = Object.hasOwn(attributes, 'disabled');
            for (const [key, value] of Object.entries(attributes)) {
                if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
            }
            this.classList = {
                contains: value => (this.attributes.class || '').split(/\s+/).includes(value),
                toggle: (value, on) => {
                    const classes = new Set((this.attributes.class || '').split(/\s+/).filter(Boolean));
                    if (on) classes.add(value); else classes.delete(value);
                    this.attributes.class = [...classes].join(' ');
                }
            };
        }
        set innerHTML(html) {
            this.children = [];
            const stack = [this];
            for (const token of String(html).match(/<[^>]*>|[^<]+/g) || []) {
                if (token.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
                if (token.startsWith('<!')) continue;
                if (!token.startsWith('<')) { if (token.trim()) stack.at(-1).children.push(token); continue; }
                const match = /^<([\w-]+)\b([^>]*)>/.exec(token);
                if (!match) continue;
                const attrs = {};
                for (const attr of match[2].matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
                    attrs[attr[1]] = decode(attr[2] ?? attr[3] ?? attr[4] ?? '');
                }
                const child = new Element(match[1], attrs);
                stack.at(-1).append(child);
                if (!/^(?:input|br|hr|img|meta|link)$/i.test(match[1])) stack.push(child);
            }
        }
        get innerHTML() { return this.children.map(child => typeof child === 'string' ? child : child.outerHTML).join(''); }
        get outerHTML() {
            const attrs = Object.entries(this.attributes).map(([key, value]) => ` ${key}="${H.escape(value)}"`).join('');
            return `<${this.tagName.toLowerCase()}${attrs}>${this.innerHTML}</${this.tagName.toLowerCase()}>`;
        }
        get textContent() { return decode(this.children.map(child => typeof child === 'string' ? child : child.textContent).join('')); }
        set textContent(value) { this.children = [H.escape(value)]; }
        get value() {
            if (this._value !== undefined) return this._value;
            if (this.tagName === 'SELECT') return this.querySelector('option')?.value || '';
            return this.attributes.value || '';
        }
        set value(value) { this._value = String(value); }
        append(...children) {
            for (const child of children) {
                if (child.parentElement) child.parentElement.children = child.parentElement.children.filter(row => row !== child);
                child.parentElement = this; this.children.push(child);
            }
        }
        before(child) {
            const parent = this.parentElement, index = parent.children.indexOf(this);
            child.parentElement = parent; parent.children.splice(index, 0, child);
        }
        getAttribute(key) { return this.attributes[key] ?? null; }
        addEventListener(name, handler) { this.listeners[name] = handler; }
        showModal() { this.open = true; }
        close() { this.open = false; this.listeners.close?.(); }
        click() { return this.onclick?.({target: this, preventDefault() {}}); }
        matches(selector) {
            if (selector.startsWith('#')) return this.attributes.id === selector.slice(1);
            const attr = /^\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]$/.exec(selector);
            if (attr) return Object.hasOwn(this.attributes, attr[1]) && (attr[2] === undefined || this.attributes[attr[1]] === attr[2]);
            const [tag, cls] = selector.split('.');
            return (!tag || this.tagName.toLowerCase() === tag.toLowerCase()) && (!cls || this.classList.contains(cls));
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
    root.innerHTML = fs.readFileSync(path.join(__dirname, '../../quant_platform/static/index.html'), 'utf8');
    return {
        getElementById: id => root.querySelector('#' + id), querySelectorAll: selector => root.querySelectorAll(selector),
        querySelector: selector => root.querySelector(selector), createElement: tag => new Element(tag)
    };
}

const catalog = {ledgers: [
    {id: 'ledger-a', label: '旧账本', strategy_id: 'strategy-a'},
    {id: 'ledger-b', label: '新账本 <观察>', strategy_id: 'strategy-b'}
], default_ledger: 'ledger-a'};

function defaultData(url) {
    const ledger = url.searchParams.get('ledger');
    if (url.pathname === '/api/ledgers') return catalog;
    if (url.pathname === '/api/overview') return {snapshot: null, total_fees: 0};
    if (url.pathname === '/api/positions') return {positions: [], cash: ledger === 'ledger-b' ? 300000 : 100000, cash_weight: 1};
    if (url.pathname === '/api/batches') return [{batch_id: 'same-batch', lifecycle_status: 'ACTIVE'}];
    if (url.pathname.startsWith('/api/batches/')) return {orders: [], fills: []};
    if (url.pathname === '/api/predictions/dates') return [ledger === 'ledger-b' ? '2026-10-30' : '2026-10-29'];
    if (url.pathname === '/api/predictions/summary') return {date: url.searchParams.get('date'), count: 0, top: [], bottom: []};
    if (url.pathname === '/api/predictions') return {data: [], total: 0};
    if (url.pathname === '/api/research') return {records: [], metric_definitions: {model: []}, sources: {}};
    return [];
}

async function monitorPage({search = '', remembered = null, hash = '#dashboard', respond = defaultData} = {}) {
    const document = pageDocument(), requests = [], storage = new Map(remembered ? [['qlib.monitor.ledger', remembered]] : []);
    const listeners = {}, location = new URL('http://localhost/' + search + hash);
    const history = {state: {workspace: 'preserve'}, replaceState(state, _title, url) {
        this.state = state; location.href = new URL(url, location.href).href;
    }};
    const flush = () => new Promise(resolve => setImmediate(resolve));
    const context = {
        document, location, URL, URLSearchParams, MonitorRuntime: Runtime,
        setTimeout() {}, clearTimeout() {}, Event: class {constructor(type) {this.type = type;}},
        localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
        fetch: async input => {
            const url = new URL(input, 'http://localhost'); requests.push(url);
            const data = await respond(url);
            return {ok: true, json: async () => data};
        },
        window: {
            PlatformHelpers: H, PlatformResearch: Research, PlatformNavigation: Runtime.createNavigationTracker(),
            location, history, addEventListener: (name, handler) => {listeners[name] = handler;},
            dispatchEvent: event => listeners[event.type]?.(event)
        }
    };
    context.window.localStorage = context.localStorage;
    Object.defineProperty(context, 'predSearch', {get: () => context.window.predSearch});
    for (const name of ['monitor.js', 'platform.js']) {
        vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../quant_platform/static/js', name), 'utf8'), context);
    }
    await flush(); await flush();
    const get = selector => {const el = document.querySelector(selector); assert.ok(el, `Missing rendered ${selector}`); return el;};
    const switchLedger = async ledger => {const select = get('#monitor-ledger'); select.value = ledger; await select.onchange({target: select}); await flush();};
    const navigate = async page => {location.hash = '#' + page; await listeners.hashchange(); await flush();};
    return {document, get, requests, storage, location, history, flush, switchLedger, navigate};
}

test('URL ledger outranks memory; monitor requests use that ledger and an empty snapshot retains cash', async () => {
    const page = await monitorPage({search: '?ledger=ledger-b', remembered: 'ledger-a'});
    assert.equal(page.get('#monitor-ledger').value, 'ledger-b');
    assert.equal(page.get('#monitor-ledger-control').hidden, false);
    assert.match(page.get('#monitor-ledger-control').textContent, /账本/);
    assert.match(page.get('#monitor-ledger').textContent, /新账本 <观察>/);
    assert.ok(page.requests.length >= 4);
    assert.ok(page.requests.filter(url => url.pathname !== '/api/ledgers').every(url => url.searchParams.get('ledger') === 'ledger-b'));
    assert.match(page.get('#content').textContent, /暂无快照/);
    assert.match(page.get('.cash-row').textContent, /现金[\s\S]*300,000\.00/);
    assert.doesNotMatch(page.get('#content').textContent, /run_monitor\.py|--stage/);
    const requests = page.requests.length; await page.flush(); assert.equal(page.requests.length, requests);
    await page.navigate('model-experiments');
    assert.equal(page.get('#monitor-ledger-control').hidden, true);
    assert.equal(page.requests.at(-1).searchParams.has('ledger'), false);
});

for (const [name, options, selected] of [
    ['valid remembered ledger', {remembered: 'ledger-b'}, 'ledger-b'],
    ['unknown URL with valid memory', {search: '?ledger=missing', remembered: 'ledger-b'}, 'ledger-b'],
    ['unknown URL and memory', {search: '?ledger=missing', remembered: 'retired'}, 'ledger-a']
]) test(`selection falls back to ${name}`, async () => {
    const page = await monitorPage(options);
    assert.equal(page.get('#monitor-ledger').value, selected);
});

test('switching ledger persists selection, closes detail, and ignores a delayed previous dashboard', async () => {
    let release;
    const page = await monitorPage({respond: url => url.pathname === '/api/overview' && url.searchParams.get('ledger') === 'ledger-a'
        ? new Promise(resolve => {release = resolve;}) : defaultData(url)});
    const dialog = page.get('#detail-dialog'); dialog.showModal(); page.get('#dialog-body').innerHTML = '旧账本详情';
    await page.switchLedger('ledger-b'); await page.flush();
    assert.equal(dialog.open, false);
    assert.equal(page.get('#dialog-body').textContent, '');
    assert.equal(page.storage.get('qlib.monitor.ledger'), 'ledger-b');
    assert.match(page.get('#content').textContent, /300,000\.00/);
    release({snapshot: {date: 'OLD'}, total_fees: 999}); await page.flush();
    assert.doesNotMatch(page.get('#content').textContent, /OLD|999\.00/);
    assert.match(page.get('#content').textContent, /300,000\.00/);
});

test('explicit ledger switch updates a URL selection so refresh keeps the new ledger and current route', async () => {
    const page = await monitorPage({search: '?ledger=ledger-a&workspace=research&tag=one&tag=two', hash: '#batches'});
    await page.switchLedger('ledger-b');
    assert.equal(page.location.searchParams.get('ledger'), 'ledger-b');
    assert.equal(page.location.searchParams.get('workspace'), 'research');
    assert.deepEqual(page.location.searchParams.getAll('tag'), ['one', 'two']);
    assert.equal(page.location.hash, '#batches');
    assert.deepEqual(page.history.state, {workspace: 'preserve'});
    const refreshed = await monitorPage({search: page.location.search, hash: page.location.hash,
        remembered: page.storage.get('qlib.monitor.ledger')});
    assert.equal(refreshed.get('#monitor-ledger').value, 'ledger-b');
    assert.equal(refreshed.requests.at(-1).searchParams.get('ledger'), 'ledger-b');
});

test('batch details remain ledger scoped and a delayed previous detail cannot expand the new ledger row', async () => {
    let release;
    const page = await monitorPage({hash: '#batches', respond: url => url.pathname === '/api/batches/same-batch' && url.searchParams.get('ledger') === 'ledger-a'
        ? new Promise(resolve => {release = resolve;}) : defaultData(url)});
    page.get('[data-batch="same-batch"]').click(); await page.flush();
    assert.equal(typeof release, 'function');
    await page.switchLedger('ledger-b');
    release({signal_date: 'OLD', orders: [], fills: []}); await page.flush();
    assert.doesNotMatch(page.get('#content').textContent, /OLD/);
    await page.get('[data-batch="same-batch"]').click(); await page.flush();
    assert.equal(page.requests.at(-1).pathname, '/api/batches/same-batch');
    assert.equal(page.requests.at(-1).searchParams.get('ledger'), 'ledger-b');
});

test('deferred Top5 buys explain execution-time sizing while legacy target amounts remain visible', async () => {
    const page = await monitorPage({hash: '#batches', respond: url => url.pathname === '/api/batches/same-batch'
        ? {signal_date: '2026-10-29', fills: [], orders: [
            {client_order_id: 'deferred', stock_code: '600000.SH', side: 'BUY', quantity: 0,
                target_value: 0, price_type: 'CLOSE_AUCTION_TOPK_CASH_V1'},
            {client_order_id: 'legacy', stock_code: '600001.SH', side: 'BUY', quantity: 0,
                target_value: 10000, price_type: 'CLOSE_AUCTION_LIMIT'}
        ]} : defaultData(url)});
    await page.get('[data-batch="same-batch"]').click(); await page.flush();
    assert.match(page.get('#content').textContent, /执行时计算/);
    assert.match(page.get('#content').textContent, /按可用现金分配/);
    assert.match(page.get('#content').textContent, /10,000\.00/);
});

test('a failed request from a previous ledger does not leak an error into the selected ledger', async () => {
    let reject;
    const page = await monitorPage({hash: '#batches', respond: url => url.pathname === '/api/batches/same-batch' && url.searchParams.get('ledger') === 'ledger-a'
        ? new Promise((_, fail) => {reject = fail;}) : defaultData(url)});
    const oldDetail = page.get('[data-batch="same-batch"]').click().then(() => null, error => error);
    await page.flush(); await page.switchLedger('ledger-b');
    reject(new Error('旧账本已断开'));
    assert.equal(await oldDetail, null);
    assert.match(page.get('#content').textContent, /批次与成交/);
});

test('prediction switch resets dates and filter and cannot reuse delayed instruments from the previous ledger', async () => {
    let release;
    const page = await monitorPage({hash: '#predictions', respond: url => url.pathname === '/api/predictions/instruments' && url.searchParams.get('ledger') === 'ledger-a'
        ? new Promise(resolve => {release = resolve;}) : defaultData(url)});
    assert.equal(typeof release, 'function');
    page.get('#pred-query').value = '旧筛选';
    await page.switchLedger('ledger-b'); await page.flush();
    assert.equal(page.get('#pred-date').value, '2026-10-30');
    assert.equal(page.get('#pred-query').value, '');
    assert.equal(page.requests.filter(url => url.pathname === '/api/predictions/instruments').length, 2);
    release([{instrument: 'SH600000', stock_code: '600000.SH', name: '过期标的'}]); await page.flush();
    page.get('#pred-query').value = '过期标的';
    await page.get('#pred-search-btn').click(); await page.flush();
    assert.equal(page.requests.some(url => url.pathname === '/api/predictions/daily-mean' && url.searchParams.get('instruments') === 'SH600000'), false);
    assert.ok(page.requests.filter(url => url.pathname.startsWith('/api/predictions') && url.searchParams.get('ledger') === 'ledger-b').length >= 6);
});
