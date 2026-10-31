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
            for(const select of this.querySelectorAll('select')){
                const options=select.querySelectorAll('option');
                const chosen=options.find(option=>Object.hasOwn(option.attributes,'selected'))||options[0];
                select.value=chosen?.getAttribute('value')||'';
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
        click() { this.onclick?.({target:this,preventDefault(){}}); }
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

async function researchPage(hash = '#model-experiments', options = {}) {
    const document = fakeDocument(), catalog = fixtureCatalog();
    if(options.catalog)options.catalog(catalog);
    const requests=[],downloads=[];
    const flush = () => new Promise(resolve => setImmediate(resolve));
    const listeners = {}, location = {hash};
    const context = {
        document,
        window: {PlatformHelpers: H, PlatformResearch: Research, PlatformNavigation: Runtime.createNavigationTracker(),
            PlatformMonitor: {clear() {}}, addEventListener(name, handler) {listeners[name] = handler;}},
        location, setTimeout() {}, clearTimeout() {}, URLSearchParams, Blob,
        URL:{createObjectURL(blob){downloads.push(blob);return 'blob:download';},revokeObjectURL(){}},
        fetch: async (url,init) => {
            requests.push({url,method:init?.method||'GET',...(init?.body?{payload:JSON.parse(init.body)}:{})});
            if(url==='/api/session')return {ok:true,json:async()=>({csrf_token:'test-csrf'})};
            if(url==='/api/research/conclusion')return options.saveConclusion(JSON.parse(init.body),init);
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
    const change = async (selector,value) => {
        const element=get(selector);element.value=value;
        assert.equal(typeof element.onchange,'function');
        await element.onchange({target:element});await flush();
    };
    const input=async(selector,value)=>{
        const element=get(selector);element.value=value;
        assert.equal(typeof element.oninput,'function');
        await element.oninput({target:element});await flush();
    };
    return {document, get, click, check,change,input,requests,downloads,flush,catalog};
}

function addExperimentTypes(catalog){
    catalog.records[0].experiment_type='training_target';catalog.records[0].experiment_type_label='训练目标';
    catalog.records[1].experiment_type='model_structure';catalog.records[1].experiment_type_label='模型结构';
    catalog.records[2].experiment_type='training_target';catalog.records[2].experiment_type_label='训练目标';
}

function hiddenV1Benchmark(catalog){
    const v1=catalog.records.find(record=>record.id==='model/v1');
    catalog.records=catalog.records.filter(record=>record.id!==v1.id);
    v1.benchmark_only=true;
    v1.model_profile={early_stopping:'历史499天次日RankIC',features:'Alpha158＋range',
        structure:'单LightGBM',training_target:'H20累计收益',version:'v1'};
    catalog.benchmarks=[...catalog.records.filter(record=>record.is_baseline),v1];
    catalog.records.find(record=>record.id==='model/trial').benchmark_outputs=[
        {ref:v1.id,version:'v1',available:true}];
}

test('dedicated benchmarks restore hidden v1 without restoring its experiment row',async()=>{
    const page=await researchPage('#model-experiments',{catalog:hiddenV1Benchmark});
    assert.deepEqual(page.get('#research-core').querySelectorAll('[data-benchmark]').map(box=>box.dataset.benchmark),
        ['model/v2','model/v1']);
    await page.click('[data-module="records"]');
    assert.equal(historyIds(page).includes('model/v1'),false);
    const trial=page.get('[data-detail="model/trial"]').parentElement.parentElement;
    assert.match(trial.textContent,/产出 v1/);
    assert.doesNotMatch(trial.textContent,/晋升 v1/);
    assert.ok(page.get('#research-table').querySelectorAll('th').some(header=>header.textContent==='Benchmark版本'));
    await page.click('#export-csv');
    const csv=await page.downloads[0].text();
    assert.match(csv.split('\r\n')[0],/"Benchmark版本"/);
    assert.doesNotMatch(csv,/model\/v1/);
    assert.match(csv,/model\/trial.*"v1"/);
});

test('hidden v1 benchmark opens its own profile and keeps yearly detail and dimension exports',async()=>{
    const page=await researchPage('#model-experiments',{catalog:hiddenV1Benchmark});
    await page.click('[data-detail="model/v1"]');
    assert.equal(page.get('#detail-dialog').open,true);
    assert.match(page.get('[data-profile-field="early_stopping"]').textContent,/历史499天次日RankIC/);
    assert.match(page.get('#detail-metrics').textContent,/31\.00%/);
    await page.click('[data-detail-view="year"]');
    assert.match(page.get('#detail-metrics').textContent,/12\.00%/);
    assert.doesNotMatch(page.get('#detail-metrics').textContent,/31\.00%/);
    await page.click('#dialog-close');
    await page.check('model/v2',false);
    await page.click('#export-csv');
    const csv=await page.downloads[0].text();
    assert.match(csv,/模型一/);assert.match(csv,/0\.21/);assert.doesNotMatch(csv,/模型二/);
    await page.click('#refresh');
    assert.equal(page.get('[data-benchmark="model/v1"]').checked,true);
    assert.equal(page.get('[data-benchmark="model/v2"]').checked,false);
});

test('explicit empty benchmark list is authoritative while history remains available',async()=>{
    const page=await researchPage('#model-experiments',{catalog:catalog=>{catalog.benchmarks=[];}});
    assert.equal(page.document.querySelector('[data-benchmark]'),null);
    await page.click('[data-module="records"]');
    assert.deepEqual(historyIds(page),['model/v1','model/v2','model/trial']);
});

test('benchmark-only stock scopes are selectable without leaking hidden rows into history',async()=>{
    const page=await researchPage('#model-experiments',{catalog:catalog=>{
        hiddenV1Benchmark(catalog);
        catalog.benchmarks.push({...catalog.benchmarks.at(-1),id:'csi/hidden-v1',track:'csi1000',name:'CSI历史基线'});
        catalog.metric_definitions_by_track={csi1000:{model:catalog.metric_definitions.model}};
    }});
    await page.change('#research-track','csi1000');
    assert.deepEqual(page.get('#research-core').querySelectorAll('[data-benchmark]').map(box=>box.dataset.benchmark),['csi/hidden-v1']);
    assert.doesNotMatch(page.get('#research-core').textContent,/模型一|模型二/);
    await page.click('[data-module="records"]');
    assert.deepEqual(historyIds(page),[]);
    await page.click('[data-module="benchmark"]');
    assert.ok(page.get('[data-benchmark="csi/hidden-v1"]').checked);
});

test('unverified benchmark outputs do not acquire a produced-version badge',async()=>{
    const page=await researchPage('#model-experiments',{catalog:catalog=>{
        catalog.records.find(record=>record.id==='model/trial').benchmark_outputs=[
            {ref:'model/v1',version:'v1',available:false},
            {ref:'model/v2',version:'v2',available:'true'},
            {ref:'',version:'v3',available:true}];
    }});
    await page.click('[data-module="records"]');
    const trial=page.get('[data-detail="model/trial"]').parentElement.parentElement;
    assert.doesNotMatch(trial.textContent,/产出|晋升 v[123]/);
});

const historyIds=page=>page.get('#research-table').querySelectorAll('tbody tr').map(row=>row.querySelector('[data-detail]').dataset.detail);

for(const kind of ['model','strategy']){
    test(`${kind} record histories do not expose manual comparison or make comparison requests`,async()=>{
        const page=await researchPage(`#${kind}-experiments`);
        await page.click('[data-module="records"]');
        assert.equal(page.get('#research-table').querySelectorAll('input').length,0);
        assert.equal(page.get('#research-table').querySelectorAll('th')[0].textContent,'日期');
        assert.equal(page.document.querySelector('#compare-bar'),null);
        assert.equal(page.document.querySelector('[data-select]'),null);
        await page.click('#refresh');
        await page.click('#export-csv');
        assert.equal(page.requests.some(r=>r.url.startsWith('/api/research/compare')),false);
        assert.equal(page.get('#research-table').querySelectorAll('input').length,0);
    });
}

test('benchmark selection remains independent after visiting comparison-free experiment histories',async()=>{
    const page=await researchPage('#model-experiments');
    await page.check('model/v2',false);
    await page.click('[data-module="records"]');
    assert.equal(page.document.querySelector('[data-select]'),null);
    assert.deepEqual(historyIds(page),['model/v1','model/v2','model/trial']);
    await page.click('[data-module="benchmark"]');
    assert.equal(page.get('[data-benchmark="model/v2"]').checked,false);
    assert.equal(page.get('[data-benchmark="model/v1"]').checked,true);
    assert.match(page.get('#research-table').textContent,/分维度已选 1 \/ 2 个版本/);
    await page.click('#export-csv');
    assert.match(await page.downloads[0].text(),/model\/v1/);
    assert.doesNotMatch(await page.downloads[0].text(),/model\/v2/);
    assert.equal(page.requests.some(r=>r.url.startsWith('/api/research/compare')),false);
});

for(const [kind,id,version,metric] of [['model','model/trial','v1','89.00%'],['strategy','strategy/v1','BT v3','27.00%']]){
    test(`${kind} experiment detail focuses on historical baseline changes and core metrics`,async()=>{
        const page=await researchPage(`#${kind}-experiments`,{catalog(catalog){
            const record=catalog.records.find(r=>r.id===id);
            record.comparison_baseline={ref:'historical/baseline',version,initial:false};
            record.change_summary='从甲规则改为乙规则，仅记录本次变更。';
            record.hypothesis='完整原假设保留在历史登记中。';
            record.analysis={conclusion:'已有人工精简结论',limitations:'已有局限'};
            record.model_profile={structure:'完整配方不属于简版实验介绍'};
        }});
        await page.click('[data-module="records"]');
        await page.click(`[data-detail="${id}"]`);
        const body=page.get('#dialog-body');
        assert.match(body.querySelector('[data-experiment-field="baseline"]').textContent,new RegExp(kind==='strategy'?'s3':version));
        assert.match(body.querySelector('[data-experiment-field="changes"]').textContent,/从甲规则改为乙规则/);
        assert.match(body.textContent,/核心指标结果/);
        assert.match(page.get('#detail-metrics').textContent,new RegExp(metric.replace('.','\\.')));
        assert.equal(body.querySelector('[data-detail-view]'),null);
        assert.equal(body.querySelector('[data-profile-field]'),null);
        assert.equal(body.querySelector('#detail-compare'),null);
        const audit=body.querySelector('details');
        assert.ok(audit,'Original information remains accessible in a collapsed audit block');
        assert.match(audit.textContent,/完整原假设保留在历史登记中/);
        assert.match(audit.textContent,new RegExp(id));
        assert.match(body.textContent,/已有人工精简结论/);
        assert.equal(page.requests.some(r=>r.url.startsWith('/api/research/compare')),false);
    });
}

test('experiment core results include the exact historical baseline instead of the current version',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        catalog.records.find(r=>r.id==='model/trial').comparison_baseline={ref:'model/v1',version:'v1',initial:false};
    }});
    await page.click('[data-module="records"]');
    await page.click('[data-detail="model/trial"]');
    const metrics=page.get('#detail-metrics'),rows=metrics.querySelectorAll('tbody tr');
    assert.equal(rows.length,2);
    assert.match(rows[0].textContent,/Baseline.*v1.*模型一/);
    assert.match(rows[0].textContent,/31\.00%/);
    assert.match(rows[1].textContent,/实验组.*标签试验/);
    assert.match(rows[1].textContent,/89\.00%/);
    assert.doesNotMatch(metrics.textContent,/41\.00%/);
    assert.equal(metrics.querySelector('input'),null);
    assert.equal(page.requests.some(r=>r.url.startsWith('/api/research/compare')),false);
});

for(const failure of ['initial','missing','wrong-kind','wrong-track','wrong-version','seed-mismatch']){
    test(`experiment core retains a null baseline row when historical control is ${failure}`,async()=>{
        const page=await researchPage('#model-experiments',{catalog(catalog){
            const record=catalog.records.find(r=>r.id==='model/trial');
            record.comparison_baseline={ref:'model/v1',version:'v1',initial:false};
            if(failure==='initial')record.comparison_baseline={ref:'',version:'',initial:true};
            if(failure==='missing')record.comparison_baseline.ref='model/removed';
            const control=catalog.records.find(r=>r.id==='model/v1');
            if(failure==='wrong-kind')control.kind='strategy';
            if(failure==='wrong-track')control.track='csi1000';
            if(failure==='wrong-version')record.comparison_baseline.version='v9';
            if(failure==='seed-mismatch')record.seeds=[42];
        }});
        await page.click('[data-module="records"]');
        await page.click('[data-detail="model/trial"]');
        const metrics=page.get('#detail-metrics'),rows=metrics.querySelectorAll('tbody tr');
        assert.equal(rows.length,2);
        assert.match(rows[0].textContent,/Baseline/);
        assert.deepEqual(rows[0].querySelectorAll('td').slice(1).map(cell=>cell.textContent),['—','—','—']);
        assert.match(rows[1].textContent,/89\.00%/);
        assert.doesNotMatch(metrics.textContent,/31\.00%|41\.00%/);
        if(failure==='initial')assert.match(metrics.textContent,/初始基线.*无前版/);
        if(failure==='seed-mismatch')assert.match(metrics.textContent,/种子口径/);
    });
}

test('backend pair results preserve baseline when the experiment failed',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        const record=catalog.records.find(r=>r.id==='model/trial');
        record.status='failed';record.metrics={};
        record.core_results={kind:'baseline_comparison',status:'available',title:'实验组与Baseline',
            note:'Baseline与实验组窗口相同。',warnings:[],records:[
                {id:'model/v1',kind:'model',name:'Baseline · v1',version:'',metrics:{net_ann:.31}},
                {id:record.id,kind:'model',name:'实验组 · 失败尝试',version:'',metrics:{}}
            ]};
    }});
    await page.click('[data-module="records"]');
    await page.click('[data-detail="model/trial"]');
    const rows=page.get('#detail-metrics').querySelectorAll('tbody tr');
    assert.equal(rows.length,2);
    assert.match(rows[0].textContent,/Baseline.*31\.00%/);
    assert.deepEqual(rows[1].querySelectorAll('td').slice(1).map(cell=>cell.textContent),['—','—','—']);
    assert.match(page.get('#dialog-body').textContent,/本次实验无有效核心指标/);
});

test('legacy failed experiment keeps baseline and never displays stale trial metrics',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        const record=catalog.records.find(r=>r.id==='model/trial');
        record.status='failed';record.comparison_baseline={ref:'model/v1',version:'v1',initial:false};
    }});
    await page.click('[data-module="records"]');
    await page.click('[data-detail="model/trial"]');
    const rows=page.get('#detail-metrics').querySelectorAll('tbody tr');
    assert.equal(rows.length,2);
    assert.match(rows[0].textContent,/31\.00%/);
    assert.deepEqual(rows[1].querySelectorAll('td').slice(1).map(cell=>cell.textContent),['—','—','—']);
});

test('a broken backend pair cannot display only an experiment row',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        const record=catalog.records.find(r=>r.id==='model/trial');
        record.core_results={kind:'baseline_comparison',status:'available',title:'实验组与Baseline',
            records:[{id:record.id,kind:'model',name:'实验组',metrics:record.metrics}]};
    }});
    await page.click('[data-module="records"]');
    await page.click('[data-detail="model/trial"]');
    const metrics=page.get('#detail-metrics');
    assert.match(metrics.textContent,/核心结果对照暂不可用/);
    assert.equal(metrics.querySelector('table'),null);
    assert.doesNotMatch(metrics.textContent,/89\.00%/);
});

test('Benchmark version detail ignores experiment pairs and retains version dimension tabs',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        const record=catalog.records.find(r=>r.id==='model/v2');
        record.core_results={kind:'baseline_comparison',status:'available',title:'实验组与Baseline',
            records:[{id:'model/v1',kind:'model',name:'Baseline',metrics:{net_ann:.31}},
                {id:record.id,kind:'model',name:'实验组',metrics:record.metrics}]};
    }});
    await page.click('[data-detail="model/v2"]');
    const metrics=page.get('#detail-metrics');
    assert.equal(metrics.querySelectorAll('tbody tr').length,1);
    assert.match(metrics.textContent,/41\.00%/);
    assert.doesNotMatch(metrics.textContent,/31\.00%|实验组与Baseline/);
    assert.ok(metrics.querySelector('[data-detail-view="year"]'));
    await page.click('[data-detail-view="year"]');
    assert.match(page.get('#detail-metrics').textContent,/23\.00%/);
});

function addBenchmarkProfile(catalog){
    const record=catalog.records.find(r=>r.id==='model/v2');
    Object.assign(record,{id:'regime-adapt/m0-h20-rankices-v1',name:'RankIC早停（v4）',version:'v4',promoted_version:'v4',
        directory:'backtest/experiments/models/early-stopping/rankic-v4',
        resources:[{id:'profile-report',name:'rankic.html',kind:'report',available:true},
            {id:'profile-metrics',name:'evaluation.json',kind:'metrics',available:true},
            {id:'profile-missing',name:'old-eval.json',kind:'original_metrics',available:false}],
        model_profile:{version:'v4',experiment_id:'models/early-stopping/rankic-v4',structure:'单LightGBM（冻结超参）',features:'Alpha158 + range，无regime特征',
            seeds:[42,1000,2000,3000,4000],training_sample:{pool:'全A',time_range:'2004-01-02 ~ 2020-07-31',filters:'上市满60日，移除无标签样本'},
            training_target:'未来20日累计收益，日截面排名标准化',evaluation_sample:{pool:'全A',time_range:'2020-08-03 ~ 2026-07-31',filters:'日频ST、成交额及次日可成交性过滤'},
            early_stopping:'评估窗daily_rank_ic，使用H5收益标签。',prediction_signal:'五种子信号先逐日z-score，再等权融合后评估。',
            changes:'早停从Top3×H5净年化改为评估窗日RankIC。',
            model_paths:[{path:'backtest/result/rankic_s42/run_01/artifacts_root/artifacts/trained_model',seed:42,available:true},
                {path:'backtest/result/older_s42/trained_model',label:'v1',seed:42,available:false}],
            artifacts:[{resource_id:'profile-report',name:'rankic.html',kind:'report',label:'实验报告',description:'该实验的原始HTML报告。',available:true},
                {resource_id:'profile-metrics',name:'evaluation.json',kind:'metrics',label:'评估结果',description:'完整的模型评估指标JSON。',available:true},
                {resource_id:'profile-missing',name:'old-eval.json',kind:'original_metrics',label:'原运行评估结果',description:'历史运行目录中的评估文件。',available:false}]}});
    return record;
}

function addTrainingTargetResults(catalog){
    const record=catalog.records.find(r=>r.id==='model/trial');
    record.core_results={status:'available',title:'Top3×H5 · 训练目标对比',
        note:'只改变训练标签H；评估持有期h固定为5，每组五种子合成后评估。',warnings:[],
        records:[
            {id:'labels/h1',kind:'model',name:'H1',version:'',directory:'models/training-target/h1',
                metrics:{net_ann:-.3979450067859733,net_sharpe:-.5926332166019191,rank_ic_mean:null}},
            {id:'labels/h5',kind:'model',name:'H5',version:'',directory:'models/training-target/h5',
                metrics:{net_ann:.24768385502744275,net_sharpe:.37316668102198236,rank_ic_mean:null}},
            {id:'labels/h10',kind:'model',name:'H10',version:'',directory:'models/training-target/h10',
                metrics:{net_ann:.24370402110020561,net_sharpe:.35188838319720855,rank_ic_mean:null}},
            {id:'labels/h20',kind:'model',name:'H20（对照）',version:'',directory:'models/early-stopping/top3-v3',
                metrics:{net_ann:.2969937650578606,net_sharpe:.5153214282581099,rank_ic_mean:.09837344126849204}}
        ]};
    return record;
}

test('training-target overview shows the fixed Top3 h5 child results and preserves missing RankIC',async()=>{
    const page=await researchPage('#model-experiments',{catalog:addTrainingTargetResults});
    await page.click('[data-module="records"]');
    await page.click('[data-detail="model/trial"]');
    const metrics=page.get('#detail-metrics');
    const title=metrics.querySelector('[data-core-results-title]');
    assert.ok(title,'The overview heading must be outside the metric section whose h3 is hidden');
    assert.equal(title.parentElement,metrics);
    assert.equal(title.textContent,'Top3×H5 · 训练目标对比');
    assert.match(metrics.textContent,/Top3×H5 · 训练目标对比/);
    assert.match(metrics.textContent,/训练标签H.*持有期h固定为5/);
    const rows=metrics.querySelectorAll('tbody tr');
    assert.equal(rows.length,4);
    assert.deepEqual(rows.map(row=>row.querySelectorAll('td').slice(1).map(cell=>cell.textContent)),[
        ['-39.79%','-0.59','—'],['24.77%','0.37','—'],['24.37%','0.35','—'],['29.70%','0.52','0.0984']
    ]);
    assert.match(rows[0].textContent,/H1/);
    assert.match(rows[3].textContent,/H20（对照）/);
    assert.doesNotMatch(metrics.textContent,/89\.00%/,'The old grid parent metric is never a child result');
    assert.equal(metrics.querySelector('input'),null);
    assert.equal(page.document.querySelector('[data-detail-view]'),null);
    assert.equal(page.requests.some(r=>r.url.startsWith('/api/research/compare')),false);
});

for(const status of ['unavailable','empty']){
    test(`training-target overview ${status} does not fall back to the old grid result`,async()=>{
        const page=await researchPage('#model-experiments',{catalog(catalog){
            const record=addTrainingTargetResults(catalog);
            if(status==='unavailable')record.core_results.status='unavailable';
            else record.core_results.records=[];
            record.core_results.warnings=['H5来源未通过校验 <source>'];
        }});
        await page.click('[data-module="records"]');
        await page.click('[data-detail="model/trial"]');
        const metrics=page.get('#detail-metrics');
        assert.match(metrics.textContent,/训练目标对比.*暂不可用/);
        assert.match(metrics.textContent,/H5来源未通过校验 <source>/);
        assert.doesNotMatch(metrics.textContent,/89\.00%|-39\.79%|29\.70%/);
        assert.equal(metrics.querySelector('table'),null);
        assert.equal(metrics.querySelector('source'),null);
    });
}

test('Benchmark detail also uses declared training-target results without old grid dimension tabs',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        const record=addTrainingTargetResults(catalog);
        record.is_baseline=true;record.version='v0';
        record.core_results.title='<b>Top3×H5</b>';
        record.core_results.note='<script>训练标签H</script>';
    }});
    await page.click('[data-detail="model/trial"]');
    const metrics=page.get('#detail-metrics');
    assert.match(metrics.textContent,/<b>Top3×H5<\/b>/);
    assert.match(metrics.textContent,/<script>训练标签H<\/script>/);
    assert.equal(metrics.querySelector('b'),null);
    assert.equal(metrics.querySelector('script'),null);
    assert.equal(metrics.querySelectorAll('tbody tr').length,4);
    assert.doesNotMatch(metrics.textContent,/89\.00%/);
    assert.equal(metrics.querySelector('[data-detail-view]'),null);
});

test('benchmark model detail separates features early stopping and prediction signal before unchanged metrics',async()=>{
    const page=await researchPage('#model-experiments',{catalog:addBenchmarkProfile});
    await page.click('[data-detail="regime-adapt/m0-h20-rankices-v1"]');
    assert.equal(page.get('#dialog-title').textContent,'RankIC早停（v4）');
    const body=page.get('#dialog-body'),fields=body.querySelectorAll('[data-profile-field]');
    assert.deepEqual(fields.map(e=>e.querySelector('small').textContent),
        ['模型版本','实验 ID','模型结构','使用特征','模型种子','训练样本','训练目标','评估样本','训练早停','预测信号','变更内容','模型地址']);
    const field=key=>body.querySelector(`[data-profile-field="${key}"]`);
    assert.equal(fields[0].textContent,'模型版本v4');
    assert.equal(fields[1].textContent,'实验 IDmodels/early-stopping/rankic-v4');
    assert.doesNotMatch(fields[1].textContent,/regime-adapt/);
    assert.equal(field('structure').textContent,'模型结构单LightGBM（冻结超参）');
    assert.doesNotMatch(field('structure').textContent,/Alpha158|range/);
    assert.equal(field('features').textContent,'使用特征Alpha158 + range，无regime特征');
    assert.match(field('training_sample').textContent,/股票池.*全A.*时间范围.*2004-01-02.*2020-07-31.*过滤逻辑.*上市满60日/);
    assert.match(field('evaluation_sample').textContent,/股票池.*全A.*时间范围.*2020-08-03.*过滤逻辑.*日频ST/);
    assert.match(field('early_stopping').textContent,/daily_rank_ic.*H5收益标签/);
    assert.doesNotMatch(field('early_stopping').textContent,/z-score/);
    assert.match(field('prediction_signal').textContent,/五种子信号先逐日z-score/);
    assert.doesNotMatch(field('prediction_signal').textContent,/daily_rank_ic/);
    assert.equal(field('evaluation_method'),null);
    assert.match(field('changes').textContent,/从Top3×H5净年化改为评估窗日RankIC/);
    assert.match(field('model_paths').textContent,/种子42.*trained_model/);
    assert.match(field('model_paths').textContent,/v1.*种子42.*缺失/);
    assert.equal(field('model_paths').querySelectorAll('a').length,0,'Model binary addresses remain plain text');
    assert.match(page.get('#detail-metrics').textContent,/41\.00%/);
    assert.match(page.get('#detail-metrics').textContent,/1\.10/);
    const artifacts=body.querySelectorAll('[data-profile-artifact]');
    assert.equal(artifacts.length,3);
    assert.match(artifacts[0].textContent,/实验报告.*该实验的原始HTML报告。.*rankic\.html.*预览.*下载/);
    assert.match(artifacts[1].textContent,/评估结果.*完整的模型评估指标JSON/);
    assert.match(artifacts[2].textContent,/原运行评估结果.*缺失/);
    assert.equal(artifacts[0].querySelector('a').getAttribute('href'),'/api/resources/profile-report?download=1');
    assert.equal(artifacts[2].querySelector('a'),null);
    assert.ok(body.textContent.indexOf('模型地址')<body.textContent.indexOf('评估指标'));
    assert.ok(body.textContent.indexOf('评估指标')<body.textContent.indexOf('原始报告与产物'));
    await page.click('[data-detail-view="year"]');
    assert.match(page.get('#detail-metrics').textContent,/23\.00%/);
    assert.doesNotMatch(page.get('#detail-metrics').textContent,/41\.00%/);
});

test('model profile never fabricates missing recipe fields and standardizes legacy resources',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        const r=catalog.records.find(r=>r.id==='model/trial');
        r.directory='backtest/experiments/models/training-target/trial';
        r.hypothesis='冻结假设保留在审计中，不能猜成训练方法';
        r.is_baseline=true;r.version='v0';r.promoted_version='v0';
    }});
    await page.click('[data-detail="model/trial"]');
    const fields=page.get('#dialog-body').querySelectorAll('[data-profile-field]');
    assert.equal(fields[1].textContent,'实验 IDmodels/training-target/trial');
    for(const key of ['structure','features','training_sample','training_target','evaluation_sample','early_stopping','prediction_signal','changes','model_paths']){
        const field=page.get('#dialog-body').querySelector(`[data-profile-field="${key}"]`);
        assert.ok(field,`Expected a separately rendered ${key} field`);
        assert.match(field.textContent,/未登记/);
    }
    assert.doesNotMatch(page.get('#dialog-body').querySelector('[data-profile-field="early_stopping"]').textContent,/冻结假设|当前|v4/);
    assert.match(page.get('#dialog-body').textContent,/冻结假设保留在审计中/);
    assert.match(page.get('#dialog-body').querySelector('[data-profile-artifact]').textContent,/实验报告.*trial\.html/);
    assert.match(page.get('#detail-metrics').textContent,/89\.00%/);
});

test('model profile fields model addresses and artifact annotations escape untrusted text',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        const r=addBenchmarkProfile(catalog);
        r.model_profile.structure='<script>unsafe()</script>';
        r.model_profile.features='<img src=x onerror=unsafe()> 特征';
        r.model_profile.early_stopping='<svg onload=unsafe()> 早停';
        r.model_profile.prediction_signal='<script>signal()</script>';
        r.model_profile.training_sample.pool='<img src=x onerror=unsafe()>';
        r.model_profile.changes='<svg onload=unsafe()>从甲改为乙';
        r.model_profile.model_paths=[{path:'backtest/result/<img src=x>/trained_model',seed:42,available:true}];
        r.model_profile.artifacts[0]={resource_id:'a/b',name:'<img src=x>.html',kind:'report',label:'<b>实验报告</b>',description:'<script>说明</script>',available:true};
    }});
    await page.click('[data-detail="regime-adapt/m0-h20-rankices-v1"]');
    const body=page.get('#dialog-body');
    assert.equal(body.querySelector('script'),null);assert.equal(body.querySelector('img'),null);assert.equal(body.querySelector('svg'),null);
    assert.match(body.textContent,/<script>unsafe\(\)<\/script>/);
    for(const key of ['features','early_stopping','prediction_signal'])assert.ok(body.querySelector(`[data-profile-field="${key}"]`));
    assert.match(body.querySelector('[data-profile-field="features"]').textContent,/<img src=x onerror=unsafe\(\)> 特征/);
    assert.match(body.querySelector('[data-profile-field="early_stopping"]').textContent,/<svg onload=unsafe\(\)> 早停/);
    assert.match(body.querySelector('[data-profile-field="prediction_signal"]').textContent,/<script>signal\(\)<\/script>/);
    assert.match(body.textContent,/<b>实验报告<\/b>/);
    assert.equal(body.querySelector('[data-profile-artifact]').querySelector('a').getAttribute('href'),'/api/resources/a%2Fb?download=1');
    assert.equal(body.querySelector('[data-profile-field="model_paths"]').querySelector('a'),null);
});

const firstConclusionRevision='a'.repeat(64),nextConclusionRevision='b'.repeat(64);
function editableConclusions(catalog){
    catalog.records.forEach(r=>Object.assign(r,{analysis_revision:firstConclusionRevision,
        analysis:{conclusion:'预填简短结论',evidence:['metrics/evidence.json'],review_status:'draft'}}));
}
function savedConclusion(payload){return {ok:true,json:async()=>({id:payload.id,
    analysis:{conclusion:payload.conclusion,evidence:['metrics/evidence.json'],review_status:'draft',updated_at:'2026-10-31'},
    analysis_revision:nextConclusionRevision})};}

for(const [kind,id] of [['model','model/trial'],['strategy','strategy/v1']]){
    test(`${kind} history conclusions edit inline, save with revision, and update CSV details and search`,async()=>{
        const page=await researchPage(`#${kind}-experiments`,{catalog:editableConclusions,saveConclusion:async(payload,init)=>{
            assert.equal(init.headers['X-CSRF-Token'],'test-csrf');
            return savedConclusion(payload);
        }});
        await page.click('[data-module="records"]');
        await page.click(`[data-edit-conclusion="${id}"]`);
        const input=page.get(`[data-conclusion-input="${id}"]`);
        assert.equal(input.value,'预填简短结论');
        assert.equal(input.getAttribute('maxlength'),'500');
        assert.equal(page.get('#detail-dialog').open,false);
        await page.input(`[data-conclusion-input="${id}"]`,'  人工精简结论 <b>保留文字</b>  ');
        await page.click(`[data-save-conclusion="${id}"]`);
        const request=page.requests.find(r=>r.method==='PUT');
        assert.deepEqual(request.payload,{id,conclusion:'人工精简结论 <b>保留文字</b>',revision:firstConclusionRevision});
        assert.equal(page.document.querySelector(`[data-conclusion-input="${id}"]`),null);
        const record=page.catalog.records.find(r=>r.id===id);
        assert.equal(record.analysis_revision,nextConclusionRevision);
        assert.equal(record.conclusion,'未通过');
        assert.match(page.get('#research-table').textContent,/人工精简结论/);
        assert.equal(page.get('#research-table').querySelector('b'),null);
        await page.input('#research-search','人工精简结论');
        assert.deepEqual(historyIds(page),[id]);
        await page.click('#export-csv');
        assert.match(await page.downloads[0].text(),/人工精简结论/);
        await page.click(`[data-detail="${id}"]`);
        assert.match(page.get('#dialog-body').textContent,/人工精简结论/);
        assert.match(page.get('#dialog-body').textContent,/原登记结论.*未通过/);
    });
}

test('conclusion cancel sends no write, and drafts survive filtering and module switches',async()=>{
    const page=await researchPage('#model-experiments',{catalog:editableConclusions});
    await page.click('[data-module="records"]');await page.click('[data-edit-conclusion="model/trial"]');
    await page.input('[data-conclusion-input="model/trial"]','尚未保存的结论');
    await page.input('#research-search','不存在的实验');
    await page.input('#research-search','');
    assert.equal(page.get('[data-conclusion-input="model/trial"]').value,'尚未保存的结论');
    await page.click('[data-module="benchmark"]');await page.click('[data-module="records"]');
    assert.equal(page.get('[data-conclusion-input="model/trial"]').value,'尚未保存的结论');
    await page.click('[data-cancel-conclusion="model/trial"]');
    assert.equal(page.document.querySelector('[data-conclusion-input="model/trial"]'),null);
    assert.match(page.get('#research-table').textContent,/预填简短结论/);
    assert.ok(page.requests.every(r=>r.method==='GET'));
});

test('conclusion validation rejects blank or overlong text before sending a request',async()=>{
    const page=await researchPage('#model-experiments',{catalog:editableConclusions});
    await page.click('[data-module="records"]');await page.click('[data-edit-conclusion="model/trial"]');
    for(const invalid of ['  ','字'.repeat(501)]){
        await page.input('[data-conclusion-input="model/trial"]',invalid);
        await page.click('[data-save-conclusion="model/trial"]');
        assert.match(page.get('[data-conclusion-error="model/trial"]').textContent,/填写|500/);
        assert.equal(page.get('[data-conclusion-input="model/trial"]').value,invalid);
    }
    assert.ok(page.requests.every(r=>r.method==='GET'));
});

for(const status of [409,500])test(`conclusion HTTP ${status} preserves draft and allows a deliberate retry`,async()=>{
    let attempts=0;
    const page=await researchPage('#model-experiments',{catalog:editableConclusions,saveConclusion:async payload=>{
        attempts++;return attempts===1?{ok:false,status,json:async()=>({detail:'服务暂时不可用'})}:savedConclusion(payload);
    }});
    await page.click('[data-module="records"]');await page.click('[data-edit-conclusion="model/trial"]');
    await page.input('[data-conclusion-input="model/trial"]','应当保留的草稿');
    await page.click('[data-save-conclusion="model/trial"]');
    assert.equal(page.get('[data-conclusion-input="model/trial"]').value,'应当保留的草稿');
    assert.match(page.get('[data-conclusion-error="model/trial"]').textContent,status===409?/刷新/:/服务暂时不可用/);
    assert.equal(page.get('[data-save-conclusion="model/trial"]').disabled,false);
    assert.equal(page.catalog.records.find(r=>r.id==='model/trial').analysis.conclusion,'预填简短结论');
    await page.click('[data-save-conclusion="model/trial"]');
    assert.equal(attempts,2);
    assert.equal(page.catalog.records.find(r=>r.id==='model/trial').analysis.conclusion,'应当保留的草稿');
});

test('a pending conclusion save cannot be duplicated or render into another research page',async()=>{
    let release;
    const page=await researchPage('#model-experiments',{catalog:editableConclusions,
        saveConclusion:payload=>new Promise(resolve=>{release=()=>resolve(savedConclusion(payload));})});
    await page.click('[data-module="records"]');await page.click('[data-edit-conclusion="model/trial"]');
    await page.input('[data-conclusion-input="model/trial"]','迟到的模型结论');
    const oldSave=page.get('[data-save-conclusion="model/trial"]');
    await page.click('[data-save-conclusion="model/trial"]');
    assert.equal(page.get('[data-save-conclusion="model/trial"]').disabled,true);
    assert.equal(page.get('[data-cancel-conclusion="model/trial"]').disabled,true);
    assert.match(page.get('[data-save-conclusion="model/trial"]').textContent,/保存中/);
    oldSave.onclick();await page.flush();
    assert.equal(page.requests.filter(r=>r.method==='PUT').length,1);
    await page.click('[data-page="strategy-experiments"]');
    const strategyHTML=page.get('#research-table').innerHTML;
    release();await page.flush();await page.flush();
    assert.equal(page.get('#research-table').innerHTML,strategyHTML);
    assert.doesNotMatch(page.get('#research-table').textContent,/迟到的模型结论/);
    await page.click('[data-page="model-experiments"]');
    assert.match(page.get('#research-table').textContent,/迟到的模型结论/);
});

test('legacy records without an editable revision do not expose a write control',async()=>{
    const page=await researchPage();await page.click('[data-module="records"]');
    assert.equal(page.document.querySelector('[data-edit-conclusion]'),null);
    assert.ok(page.requests.every(r=>r.method==='GET'));
});

test('returning to a pending conclusion draft settles the visible editor without requiring another refresh',async()=>{
    let release;
    const page=await researchPage('#model-experiments',{catalog:editableConclusions,
        saveConclusion:payload=>new Promise(resolve=>{release=()=>resolve(savedConclusion(payload));})});
    await page.click('[data-module="records"]');await page.click('[data-edit-conclusion="model/trial"]');
    await page.input('[data-conclusion-input="model/trial"]','切页后保存完成');
    await page.click('[data-save-conclusion="model/trial"]');
    await page.click('[data-page="strategy-experiments"]');await page.click('[data-page="model-experiments"]');
    assert.match(page.get('[data-save-conclusion="model/trial"]').textContent,/保存中/);
    release();await page.flush();await page.flush();
    assert.equal(page.document.querySelector('[data-conclusion-input="model/trial"]'),null);
    assert.match(page.get('#research-table').textContent,/切页后保存完成/);
});

test('a delayed save acknowledgement does not replace a newer refreshed metadata revision',async()=>{
    let release;
    const page=await researchPage('#model-experiments',{catalog:editableConclusions,
        saveConclusion:payload=>new Promise(resolve=>{release=()=>resolve(savedConclusion(payload));})});
    await page.click('[data-module="records"]');await page.click('[data-edit-conclusion="model/trial"]');
    await page.input('[data-conclusion-input="model/trial"]','较早保存的结论');
    await page.click('[data-save-conclusion="model/trial"]');
    const record=page.catalog.records.find(r=>r.id==='model/trial');
    record.analysis={conclusion:'另一个窗口后来保存的结论'};record.analysis_revision='c'.repeat(64);
    await page.click('#refresh');release();await page.flush();await page.flush();
    assert.equal(record.analysis_revision,'c'.repeat(64));
    assert.equal(record.analysis.conclusion,'另一个窗口后来保存的结论');
    assert.equal(page.document.querySelector('[data-conclusion-input="model/trial"]'),null);
    assert.match(page.get('#research-table').textContent,/另一个窗口后来保存的结论/);
    assert.doesNotMatch(page.get('#research-table').textContent,/较早保存的结论/);
});

test('model record types retain scope filtering while strategy records omit type controls and column',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){addExperimentTypes(catalog);addCsiCatalog(catalog);}});
    await page.click('[data-module="records"]');
    assert.ok(page.get('#research-table').querySelectorAll('th').some(th=>th.textContent==='实验类型'));
    assert.equal(page.get('#research-type').getAttribute('aria-label'),'实验类型');
    assert.deepEqual(page.get('#research-type').querySelectorAll('option').map(o=>o.getAttribute('value')),['','training_target','model_structure']);
    assert.equal(page.document.querySelector('#research-direction'),null);
    await page.change('#research-type','training_target');
    assert.deepEqual(historyIds(page),['model/v1','model/trial']);
    await page.input('#research-search','标签试验');
    assert.deepEqual(historyIds(page),['model/trial']);
    await page.change('#research-status','failed');
    assert.equal(historyIds(page).length,0);
    await page.change('#research-track','csi1000');
    assert.deepEqual(page.get('#research-type').querySelectorAll('option').map(o=>o.getAttribute('value')),['','evaluation_method']);
    assert.equal(page.get('#research-type').value,'');
    await page.click('[data-page="strategy-experiments"]');
    assert.equal(page.document.querySelector('#research-type'),null);
    assert.equal(page.get('#research-table').querySelectorAll('th').some(th=>th.textContent==='实验类型'),false);
    assert.equal(page.document.querySelector('#research-direction'),null);
    assert.deepEqual(historyIds(page),['csi/b4s']);
    await page.click('[data-page="model-experiments"]');
    assert.equal(page.get('#research-type').value,'');
});

test('strategy records omit direction filters across navigation and refresh while retaining search and status',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        addExperimentTypes(catalog);strategyColumnsCatalog(catalog);
        const current=catalog.records.find(r=>r.id==='bt/v5'),older=catalog.records.find(r=>r.id==='bt/older');
        Object.assign(current,{experiment_type:'strategy_backtest',direction:'phase-m-v1-bt'});
        Object.assign(older,{experiment_type:'training_target',status:'failed',direction:'diagnostic'});
    }});
    await page.click('[data-module="records"]');await page.change('#research-type','training_target');
    await page.click('[data-page="strategy-experiments"]');
    assert.equal(page.document.querySelector('#research-type'),null);
    assert.equal(page.document.querySelector('#research-direction'),null);
    assert.deepEqual(historyIds(page),['bt/v5','bt/older']);
    await page.click('#refresh');
    assert.equal(page.document.querySelector('#research-type'),null);
    assert.equal(page.document.querySelector('#research-direction'),null);
    assert.equal(historyIds(page).length,2);
    await page.input('#research-search','Top5');await page.change('#research-status','failed');
    assert.equal(historyIds(page).length,0);
    await page.input('#research-search','');
    assert.deepEqual(historyIds(page),['bt/older']);
    await page.change('#research-status','');
    assert.deepEqual(historyIds(page),['bt/v5','bt/older']);
    await page.click('#export-csv');
    const csv=await page.downloads[0].text();
    assert.match(csv,/bt\/v5/);assert.match(csv,/bt\/older/);
    await page.click('[data-page="model-experiments"]');
    assert.equal(page.get('#research-type').value,'');
    assert.ok(page.get('#research-table').querySelectorAll('th').some(th=>th.textContent==='实验类型'));
});

test('refresh preserves an available type filter and clears it when that type disappears',async()=>{
    const page=await researchPage('#model-experiments',{catalog:addExperimentTypes});
    await page.click('[data-module="records"]');
    await page.change('#research-type','model_structure');
    await page.click('#refresh');
    assert.equal(page.get('#research-type').value,'model_structure');
    page.catalog.records=page.catalog.records.filter(r=>r.experiment_type!=='model_structure');
    await page.click('#refresh');
    assert.equal(page.get('#research-type').value,'');
    assert.equal(historyIds(page).length,2);
});

test('legacy manual analysis stays read only and escaped while compact details retain source paths',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        addExperimentTypes(catalog);
        Object.assign(catalog.records[2],{name:'新的训练目标',directory:'backtest/experiments/models/training-target/new-name',
            experiment_type_label:'训练目标 <svg onload=unsafe()>',
            comparable:false,official:false,conclusion:'regress',analysis:{conclusion:'人工结论 <script>unsafe()</script>',
                evidence:['metrics/<img src=x>.json'],limitations:'限制 <b>text</b>',review_status:'reviewed',updated_at:'2026-10-31'}});
    }});
    await page.click('[data-module="records"]');
    const row=page.get('[data-detail="model/trial"]').parentElement.parentElement;
    assert.match(row.textContent,/训练目标/);assert.match(row.textContent,/人工分析/);
    assert.doesNotMatch(row.textContent,/models\/training-target\/new-name/);
    assert.equal(page.document.querySelector('[data-select]'),null);
    assert.equal(row.querySelector('script'),null);
    await page.click('[data-detail="model/trial"]');
    const body=page.get('#dialog-body');
    assert.match(body.textContent,/内部登记 ID.*model\/trial/);
    assert.match(body.textContent,/实验目录.*models\/training-target\/new-name/);
    assert.match(body.textContent,/实验结论.*人工结论/);
    assert.match(body.textContent,/原登记结论.*退步/);
    assert.match(body.textContent,/证据.*metrics\/<img src=x>\.json/);
    assert.match(body.textContent,/限制.*限制 <b>text<\/b>/);
    assert.match(body.querySelector('details').textContent,/原始报告与登记/);
    assert.equal(body.querySelector('script'),null);assert.equal(body.querySelector('img'),null);assert.equal(body.querySelector('svg'),null);
    assert.equal(page.document.querySelector('#detail-compare'),null);
    assert.ok(page.requests.every(r=>r.method==='GET'));
});

test('record CSV includes type and manual conclusion in filtered date order',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        addExperimentTypes(catalog);catalog.records[0].date='2026-10-30';catalog.records[2].date='2026-10-31';
        catalog.records[2].analysis={conclusion:'人工汇总结论'};catalog.records[2].conclusion='';
    }});
    await page.click('[data-module="records"]');
    await page.change('#research-type','training_target');
    await page.click('#export-csv');
    const csv=await page.downloads[0].text();
    assert.match(csv.split('\r\n')[0],/实验类型/);
    assert.match(csv,/训练目标/);assert.match(csv,/人工汇总结论/);
    assert.ok(csv.indexOf('model/trial')<csv.indexOf('model/v1'));
    assert.doesNotMatch(csv,/model\/v2/);
});

function strategyColumnsCatalog(catalog){
    const current=catalog.records.find(r=>r.kind==='strategy');
    Object.assign(current,{id:'bt/v5',version:'v5',promoted_version:'v5',name:'旧执行回测标题',date:'2026-10-31',
        hypothesis:'完整长假设仅留详情',baseline_ref:'长对照引用',
        model_summary:{ref:'model/es-v2',version:'v2',name:'M0 H20 ES'},
        strategy_summary:{name:'Top5',description:'每日换1，持有1日'}});
    catalog.records.push({...current,id:'bt/older',version:'v4',promoted_version:'v4',date:'2026-10-30',
        model_summary:{ref:'model/old',version:'v1',name:'历史模型'},strategy_summary:{name:'Top25',description:'每日换5'}});
}

function strategyModelFilterCatalog(catalog){
    strategyColumnsCatalog(catalog);
    const current=catalog.records.find(r=>r.id==='bt/v5');
    current.model_summary.name='Top5早停';
    catalog.records.find(r=>r.id==='bt/older').model_summary.name='M0 H20';
    catalog.records.push({...current,id:'bt/rankic',version:'',promoted_version:'',is_baseline:false,
        model_summary:{ref:'model/rank-v4',version:' v4 ',name:' RankIC早停 '}});
}

test('strategy Benchmark shows compact model and rule identity with consistent s versions in details and CSV',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:catalog=>{
        strategyColumnsCatalog(catalog);
        const current=catalog.records.find(r=>r.id==='bt/v5');
        current.display_version='s5';
        current.comparison_baseline={ref:'bt/older',version:'v4',display_version:'s4'};
        current.core_results={kind:'baseline_comparison',status:'available',records:[
            {id:'bt/older',kind:'strategy',version:'v4',display_version:'s4',name:'Baseline · 原对照',metrics:{annualized_return:.2}},
            {id:'bt/v5',kind:'strategy',version:'v5',display_version:'s5',name:'实验组 · Top5',metrics:{annualized_return:.27}}]};
    }});
    const label=page.get('#research-core').querySelector('[data-detail="bt/v5"]');
    assert.deepEqual(label.querySelectorAll('span').map(span=>span.textContent),['s5','v2 · M0 H20 ES','Top5']);
    assert.equal(page.get('[data-benchmark="bt/v5"]').getAttribute('aria-label'),'展示 s5');
    assert.doesNotMatch(page.get('#research-core').textContent,/旧执行回测标题/);
    assert.match(page.get('#research-table').textContent,/s5.*v2 · M0 H20 ES.*Top5/);
    await page.click('[data-detail="bt/v5"]');
    assert.equal(page.get('#dialog-title').textContent,'s5 · v2 · M0 H20 ES · Top5');
    assert.match(page.get('#dialog-body').textContent,/晋升版本s5/);
    await page.click('#dialog-close');
    await page.click('#export-csv');
    const benchmarkCsv=await page.downloads[0].text();
    assert.match(benchmarkCsv,/"s5","v2","M0 H20 ES","Top5"/);
    await page.click('[data-module="records"]');
    assert.match(page.get('#research-table').textContent,/晋升 s5/);
    await page.click('[data-detail="bt/v5"]');
    assert.match(page.get('[data-experiment-field="baseline"]').textContent,/s4/);
    assert.doesNotMatch(page.get('[data-experiment-field="baseline"]').textContent,/BT v4/);
    assert.match(page.get('#detail-metrics').textContent,/s4.*Baseline · 原对照.*s5.*实验组 · Top5/);
    await page.click('#dialog-close');
    await page.click('#export-csv');
    assert.match(await page.downloads[1].text(),/"s5"/);
    assert.equal(page.catalog.records.find(r=>r.id==='bt/v5').version,'v5');
});

test('strategy Benchmark identity escapes model and strategy names without changing model versions',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:catalog=>{
        strategyColumnsCatalog(catalog);
        const current=catalog.records.find(r=>r.id==='bt/v5');
        current.model_summary.name='<img src=x onerror=unsafe()>';
        current.strategy_summary.name='<svg onload=unsafe()>策略';
    }});
    const core=page.get('#research-core');
    assert.match(core.textContent,/s5v2 · <img src=x onerror=unsafe\(\)><svg onload=unsafe\(\)>策略/);
    assert.equal(core.querySelector('img'),null);assert.equal(core.querySelector('svg'),null);
});

test('strategy conclusion presentation uses s while its editor and saved source retain explicit BT text',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:catalog=>{
        strategyColumnsCatalog(catalog);editableConclusions(catalog);
        catalog.records.find(r=>r.id==='bt/v5').analysis.conclusion='BT v5使用模型v2，比较BT v4。';
    },saveConclusion:savedConclusion});
    await page.click('[data-module="records"]');
    assert.match(page.get('#research-table').textContent,/s5使用模型v2，比较s4/);
    await page.input('#research-search','s5使用');
    assert.deepEqual(historyIds(page),['bt/v5']);
    await page.click('[data-edit-conclusion="bt/v5"]');
    assert.equal(page.get('[data-conclusion-input="bt/v5"]').value,'BT v5使用模型v2，比较BT v4。');
    await page.input('[data-conclusion-input="bt/v5"]','模型v2，BT v5维持当前。');
    await page.click('[data-save-conclusion="bt/v5"]');
    assert.equal(page.requests.find(r=>r.method==='PUT').payload.conclusion,'模型v2，BT v5维持当前。');
    await page.input('#research-search','');
    assert.match(page.get('#research-table').textContent,/模型v2，s5维持当前/);
    assert.equal(page.catalog.records.find(r=>r.id==='bt/v5').analysis.conclusion,'模型v2，BT v5维持当前。');
});

test('strategy detail descriptions relabel explicit BT references while preserving model versions and source fields',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:catalog=>{
        strategyColumnsCatalog(catalog);
        Object.assign(catalog.records.find(r=>r.id==='bt/v5'),{
            hypothesis:'假设：模型v2，对照BT v4，形成BT v5。',
            note:'说明：BT v5对照模型v4。',
            analysis:{conclusion:'结论：BT v5使用模型v2。',limitations:'局限：BT v4和模型v4的区别。'},
            strategy_summary:{name:'Top5',description:'策略简介：BT v5使用模型v2。'},
            resources:[{id:'raw-report',name:'BT v5-source.html',kind:'report',available:true}]});
    }});
    await page.click('[data-detail="bt/v5"]');
    const body=page.get('#dialog-body').textContent;
    for(const text of ['假设：模型v2，对照s4，形成s5。','说明：s5对照模型v4。',
        '局限：s4和模型v4的区别。','策略简介：s5使用模型v2。'])assert.ok(body.includes(text),text);
    assert.ok(body.includes('BT v5-source.html'),'Original resource name remains unchanged');
    const source=page.catalog.records.find(r=>r.id==='bt/v5');
    assert.equal(source.hypothesis,'假设：模型v2，对照BT v4，形成BT v5。');
    assert.equal(source.analysis.limitations,'局限：BT v4和模型v4的区别。');
    await page.click('#dialog-close');await page.click('[data-module="records"]');
    await page.click('[data-detail="bt/v5"]');
    assert.ok(page.get('#dialog-body').textContent.includes('局限：s4和模型v4的区别。'));
});

test('strategy history model choices use refs and compose search, status and CSV export',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:catalog=>{
        strategyModelFilterCatalog(catalog);
        const current=catalog.records.find(r=>r.id==='bt/v5');
        catalog.records.push({...current,id:'bt/other-ref',is_baseline:false,
            model_summary:{...current.model_summary,ref:'model/other-v2'}},
            {...current,id:'bt/failed',is_baseline:false,status:'failed'},
            {...current,id:'bt/other-strategy',is_baseline:false,strategy_summary:{name:'Top10',description:'每日换2'}},
            {...current,id:'bt/unregistered',is_baseline:false,model_summary:{version:'v2',name:'无关联名称'}});
    }});
    assert.equal(page.document.querySelector('#research-model'),null);
    await page.click('[data-module="records"]');
    const options=page.get('#research-model').querySelectorAll('option');
    assert.equal(page.get('#research-model').getAttribute('aria-label'),'模型');
    for(const label of ['v1 · M0 H20','v2 · Top5早停','v4 · RankIC早停','未登记模型'])
        assert.ok(options.some(o=>o.textContent===label),label);
    assert.ok(options.some(o=>o.getAttribute('value')==='model/es-v2'));
    assert.ok(options.some(o=>o.getAttribute('value')==='model/other-v2'));
    await page.change('#research-model','model/es-v2');
    await page.change('#research-status','completed');
    await page.input('#research-search','每日换1');
    assert.deepEqual(historyIds(page),['bt/v5']);
    await page.click('#export-csv');
    const csv=await page.downloads[0].text();
    assert.match(csv,/bt\/v5/);assert.doesNotMatch(csv,/bt\/other-ref|bt\/older|bt\/rankic|bt\/failed|bt\/other-strategy/);
    await page.click('[data-module="benchmark"]');
    assert.equal(page.document.querySelector('#research-model'),null);
    assert.deepEqual(page.get('#research-core').querySelectorAll('[data-benchmark]').map(box=>box.dataset.benchmark),['bt/v5','bt/older']);
    await page.click('[data-page="model-experiments"]');
    await page.click('[data-module="records"]');
    assert.equal(page.document.querySelector('#research-model'),null);
    await page.click('[data-page="strategy-experiments"]');
    assert.equal(page.get('#research-model').value,'');
});

test('strategy model filter clears across stock scopes and when its ref disappears on refresh',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:catalog=>{
        strategyModelFilterCatalog(catalog);addCsiCatalog(catalog);
        catalog.records.find(r=>r.id==='csi/b4s').model_summary={ref:'csi/model-b6',version:'B6-M',name:'CSI基线'};
    }});
    await page.click('[data-module="records"]');
    await page.change('#research-model','model/es-v2');
    await page.click('#refresh');
    assert.equal(page.get('#research-model').value,'model/es-v2');
    assert.deepEqual(historyIds(page),['bt/v5']);
    await page.change('#research-track','csi1000');
    assert.equal(page.get('#research-model').value,'');
    assert.deepEqual(historyIds(page),['csi/b4s']);
    await page.change('#research-track','all_a');
    await page.change('#research-model','model/es-v2');
    page.catalog.records=page.catalog.records.filter(r=>r.id!=='bt/v5');
    await page.click('#refresh');
    assert.equal(page.get('#research-model').value,'');
    assert.deepEqual(historyIds(page),['bt/rankic','bt/older']);
});

test('strategy model option text and refs are trimmed and escaped before rendering',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:catalog=>{
        const record=catalog.records.find(r=>r.kind==='strategy');
        record.model_summary={ref:' model/<unsafe> ',version:' v2 ',name:' <img src=x onerror=bad()> '};
    }});
    await page.click('[data-module="records"]');
    const select=page.get('#research-model'),option=select.querySelectorAll('option')[1];
    assert.equal(option.textContent,'v2 · <img src=x onerror=bad()>');
    assert.equal(option.getAttribute('value'),'model/<unsafe>');
    assert.equal(select.querySelector('img'),null);
    await page.change('#research-model','model/<unsafe>');
    assert.deepEqual(historyIds(page),['strategy/v1']);
});

test('experiment record view hides source variants, resolves grouped details and preserves benchmarks',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:catalog=>{
        strategyModelFilterCatalog(catalog);
        const current=catalog.records.find(r=>r.id==='bt/v5');
        catalog.experiment_records=[{...current,id:'strategy-groups/turnover',name:'换仓参数对比',version:'',promoted_version:'',
            comparison_baseline:{ref:current.id,version:''},group_summary:{control_label:'Top5换1·持1'},
            core_results:{kind:'strategy_parameter_comparison',status:'available',title:'换仓参数核心结果',records:[
                {id:'control',kind:'strategy',name:'Baseline · 换1',version:'',metrics:{annualized_return:.27}},
                {id:'variant',kind:'strategy',name:'实验组 · 换2',version:'',metrics:{annualized_return:.33}}]}}];
    }});
    await page.click('[data-module="records"]');
    assert.deepEqual(historyIds(page),['strategy-groups/turnover']);
    await page.click('[data-detail="strategy-groups/turnover"]');
    assert.equal(page.get('#detail-dialog').open,true);
    assert.match(page.get('[data-experiment-field="baseline"]').textContent,/Top5换1·持1/);
    assert.match(page.get('#detail-metrics').textContent,/换仓参数核心结果.*Baseline · 换1.*27\.00%.*实验组 · 换2.*33\.00%/);
    await page.click('#dialog-close');
    await page.click('#export-csv');
    const csv=await page.downloads[0].text();
    assert.match(csv,/strategy-groups\/turnover/);assert.doesNotMatch(csv,/bt\/v5|bt\/older|bt\/rankic/);
    await page.click('[data-module="benchmark"]');
    assert.deepEqual(page.get('#research-core').querySelectorAll('[data-benchmark]').map(box=>box.dataset.benchmark),['bt/v5','bt/older']);
    await page.click('[data-detail="bt/v5"]');
    assert.equal(page.get('#dialog-title').textContent,'s5 · v2 · Top5早停 · Top5');
});

test('an explicit empty experiment record view does not restore hidden source records',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:catalog=>{catalog.experiment_records=[];}});
    await page.click('[data-module="records"]');
    assert.deepEqual(historyIds(page),[]);
    await page.click('#export-csv');
    assert.equal((await page.downloads[0].text()).split('\r\n').length,1);
    await page.click('[data-module="benchmark"]');
    assert.ok(page.get('[data-benchmark="strategy/v1"]'));
});

function editableGroupedHistory(catalog){
    editableConclusions(catalog);
    const source=catalog.records.find(r=>r.kind==='strategy');
    catalog.experiment_records=[{...source,id:'strategy-groups/turnover',name:'换仓参数对比',analysis:{...source.analysis}}];
}

test('group conclusions save back to the displayed record without changing source variants',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:editableGroupedHistory,saveConclusion:savedConclusion});
    await page.click('[data-module="records"]');
    await page.click('[data-edit-conclusion="strategy-groups/turnover"]');
    await page.input('[data-conclusion-input="strategy-groups/turnover"]','换仓参数汇总结论');
    await page.click('[data-save-conclusion="strategy-groups/turnover"]');
    assert.equal(page.catalog.experiment_records[0].analysis.conclusion,'换仓参数汇总结论');
    assert.equal(page.catalog.experiment_records[0].analysis_revision,nextConclusionRevision);
    assert.equal(page.catalog.records.find(r=>r.kind==='strategy').analysis.conclusion,'预填简短结论');
    assert.match(page.get('#research-table').textContent,/换仓参数汇总结论/);
    await page.click('#export-csv');
    assert.match(await page.downloads[0].text(),/换仓参数汇总结论/);
});

test('a delayed group conclusion save keeps a newer refreshed group revision',async()=>{
    let release;
    const page=await researchPage('#strategy-experiments',{catalog:editableGroupedHistory,
        saveConclusion:payload=>new Promise(resolve=>{release=()=>resolve(savedConclusion(payload));})});
    await page.click('[data-module="records"]');
    await page.click('[data-edit-conclusion="strategy-groups/turnover"]');
    await page.input('[data-conclusion-input="strategy-groups/turnover"]','较早的汇总结论');
    await page.click('[data-save-conclusion="strategy-groups/turnover"]');
    const record=page.catalog.experiment_records[0];
    record.analysis={conclusion:'更新的汇总结论'};record.analysis_revision='c'.repeat(64);
    await page.click('#refresh');release();await page.flush();await page.flush();
    assert.equal(record.analysis_revision,'c'.repeat(64));
    assert.equal(record.analysis.conclusion,'更新的汇总结论');
    assert.match(page.get('#research-table').textContent,/更新的汇总结论/);
    assert.doesNotMatch(page.get('#research-table').textContent,/较早的汇总结论/);
});

test('strategy records split model and strategy and retain original sources behind the compact detail',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:strategyColumnsCatalog});
    const benchmark=page.get('#research-core').textContent;
    assert.ok(benchmark.indexOf('s5')<benchmark.indexOf('s4'));
    await page.click('[data-module="records"]');
    const table=page.get('#research-table'),headers=table.querySelectorAll('th').map(th=>th.textContent);
    assert.ok(headers.includes('模型'));assert.ok(headers.includes('策略'));assert.equal(headers.includes('实验内容'),false);
    assert.equal(headers.includes('实验类型'),false);
    const row=page.get('[data-detail="bt/v5"]').parentElement.parentElement;
    const cells=row.querySelectorAll('td');
    assert.equal(cells[1].textContent,'v2 · M0 H20 ES');
    assert.equal(cells[2].textContent,'Top5每日换1，持有1日');
    assert.doesNotMatch(cells[1].textContent+cells[2].textContent,/完整长假设|长对照引用|bt\/v5|旧执行回测标题/);
    await page.input('#research-search','H20 ES');
    assert.deepEqual(historyIds(page),['bt/v5']);
    await page.input('#research-search','每日换1');
    assert.deepEqual(historyIds(page),['bt/v5']);
    await page.click('[data-detail="bt/v5"]');
    assert.match(page.get('#dialog-body').textContent,/bt\/v5/);
    assert.match(page.get('#dialog-body').textContent,/完整长假设仅留详情/);
    assert.match(page.get('#dialog-body').textContent,/Baseline.*变更内容.*核心指标结果/);
    assert.match(page.get('#detail-metrics').textContent,/27\.00%/);
    assert.match(page.get('#dialog-body').querySelector('details').textContent,/长对照引用/);
    assert.equal(page.catalog.records.find(r=>r.id==='bt/v5').model_summary.version,'v2');
    assert.ok(page.requests.every(r=>r.method==='GET'));
});

test('strategy history CSV keeps separate model and strategy fields while model CSV exports change and baseline',async()=>{
    const page=await researchPage('#strategy-experiments',{catalog:strategyColumnsCatalog});
    await page.click('[data-module="records"]');
    await page.click('#export-csv');
    const csv=await page.downloads[0].text(),header=csv.split('\r\n')[0];
    for(const label of ['模型版本','模型简称','策略简称','策略描述'])assert.match(header,new RegExp(label));
    assert.doesNotMatch(header,/实验内容|实验假设|实验类型/);
    assert.match(csv,/"v2","M0 H20 ES","Top5","每日换1，持有1日"/);
    assert.ok(csv.indexOf('bt/v5')<csv.indexOf('bt/older'));
    assert.doesNotMatch(csv,/完整长假设仅留详情/);
    await page.click('[data-page="model-experiments"]');await page.click('#export-csv');
    const modelHeader=(await page.downloads[1].text()).split('\r\n')[0];
    assert.equal(modelHeader,'"日期","实验 ID","实验类型","实验内容","Baseline","结论","结论来源","备注","报告链接","Benchmark版本","状态"');
});

test('model history content shows only its change and historical baseline while full source stays in details',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        Object.assign(catalog.records.find(r=>r.id==='model/trial'),{
            hypothesis:'完整假设：保留所有冻结训练参数及限制',scope:'seed42_diagnostic',baseline_ref:'self',
            directory:'backtest/experiments/models/early-stopping/trial',
            change_summary:'早停改为 daily_rank_ic',comparison_baseline:{ref:'model/v1',version:'v1',initial:false}
        });
    }});
    const benchmark=page.get('#research-core').textContent;
    await page.click('[data-module="records"]');
    const cell=page.get('[data-detail="model/trial"]').parentElement;
    assert.equal(cell.textContent,'标签试验早停改为 daily_rank_icBaseline：v1');
    assert.equal(cell.querySelectorAll('.record-id').length,0);
    assert.doesNotMatch(cell.textContent,/完整假设|仅种子|self|models\/early-stopping|model\/trial|v2/);
    await page.click('#export-csv');
    const csv=await page.downloads[0].text();
    assert.match(csv,/"model\/trial","未分类","早停改为 daily_rank_ic","v1"/);
    assert.doesNotMatch(csv,/完整假设|冻结训练参数/);
    await page.input('#research-search','daily_rank_ic');
    assert.deepEqual(historyIds(page),['model/trial']);
    await page.click('[data-detail="model/trial"]');
    assert.match(page.get('#dialog-body').textContent,/完整假设：保留所有冻结训练参数及限制/);
    assert.match(page.get('#dialog-body').textContent,/model\/trial/);
    await page.click('#dialog-close');
    await page.click('[data-module="benchmark"]');
    assert.equal(page.get('#research-core').textContent,benchmark);
});

test('model history missing editorial fields stay explicit and an initial baseline is not treated as a self comparison',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        Object.assign(catalog.records.find(r=>r.id==='model/v1'),{
            baseline_ref:'model/v2',comparison_baseline:{ref:'',version:'',initial:true},change_summary:'建立初始模型'
        });
        Object.assign(catalog.records.find(r=>r.id==='model/v2'),{baseline_ref:'self',comparison_baseline:{ref:'self',version:''}});
    }});
    await page.click('[data-module="records"]');
    const initial=page.get('[data-detail="model/v1"]').parentElement;
    assert.equal(initial.textContent,'模型一建立初始模型Baseline：初始基线');
    const unknown=page.get('[data-detail="model/v2"]').parentElement;
    assert.equal(unknown.textContent,'模型二待填写改动说明Baseline：未登记');
    assert.doesNotMatch(unknown.textContent,/self|v2/);
    await page.click('#export-csv');
    const csv=await page.downloads[0].text();
    assert.match(csv,/"建立初始模型","初始基线"/);
    assert.match(csv,/"待填写改动说明","未登记"/);
});

test('model history safely escapes compact changes and historical baseline labels',async()=>{
    const page=await researchPage('#model-experiments',{catalog(catalog){
        Object.assign(catalog.records.find(r=>r.id==='model/trial'),{
            change_summary:'<img src=x onerror=alert(1)> 只改标签',
            comparison_baseline:{ref:'<script>ref</script>',version:'<svg onload=alert(2)>'}
        });
    }});
    await page.click('[data-module="records"]');
    const cell=page.get('[data-detail="model/trial"]').parentElement;
    assert.equal(cell.querySelectorAll('img').length,0);
    assert.equal(cell.querySelectorAll('svg').length,0);
    assert.match(cell.innerHTML,/&lt;img/);
    assert.match(cell.innerHTML,/&lt;svg/);
    assert.match(cell.textContent,/只改标签/);
});

test('strategy cells escape new summary text and legacy records explicitly show missing model identity',async()=>{
    const legacy=await researchPage('#strategy-experiments');await legacy.click('[data-module="records"]');
    const oldRow=legacy.get('[data-detail="strategy/v1"]').parentElement.parentElement;
    assert.equal(oldRow.querySelectorAll('td')[1].textContent,'未登记模型');
    assert.equal(oldRow.querySelectorAll('td')[2].textContent,'策略一');
    const page=await researchPage('#strategy-experiments',{catalog(catalog){
        const record=catalog.records.find(r=>r.kind==='strategy');
        record.model_summary={ref:'model/safe',version:'v2',name:'<img src=x onerror=bad()>'};
        record.strategy_summary={name:'<svg onload=bad()>',description:'<script>bad()</script>'};
        record.comparable=false;record.official=false;
    }});
    await page.click('[data-module="records"]');
    const table=page.get('#research-table');
    assert.match(table.textContent,/<img src=x onerror=bad\(\)>/);
    assert.equal(table.querySelector('img'),null);assert.equal(table.querySelector('svg'),null);assert.equal(table.querySelector('script'),null);
    assert.equal(page.document.querySelector('[data-select]'),null);
});

function addCsiCatalog(catalog){
    const base={...catalog.records[0],track:'csi1000',comparison_key:'model',direction:'csi-only',
        experiment_type:'evaluation_method',experiment_type_label:'评估方法'};
    catalog.records.push(
        {...base,id:'csi/b6',name:'CSI1000 模型基线',version:'B6-M',current:true,metrics:{rank_ic:.1234},years:{},regimes:{}},
        {...base,id:'csi/trial',name:'CSI1000 模型实验',version:'',is_baseline:false,current:false,metrics:{ic:.3456},
            metric_definitions:[{key:'ic',label:'实验原生 IC',format:'number'}]},
        {...base,id:'csi/draft',name:'未核验档案',status:'draft',official:false,is_baseline:false,current:false,metrics:{}},
        {...base,id:'csi/b4s',name:'CSI1000 策略基线',kind:'strategy',version:'B4-S',comparison_key:'strategy',
            experiment_type:'strategy_backtest',experiment_type_label:'策略回测',metrics:{csi_ann:.56},years:{},regimes:{}}
    );
    catalog.metric_definitions_by_track={csi1000:{
        model:[{key:'rank_ic',label:'CSI1000 RankIC',format:'rankic'}],
        strategy:[{key:'csi_ann',label:'CSI1000 扣费年化',format:'pct'}]
    }};
}

test('track selector offers only available tracks for the current experiment kind',async()=>{
    const allA=await researchPage();
    assert.equal(allA.get('#research-track').getAttribute('aria-label'),'股票范围');
    assert.equal(allA.get('[for="research-track"]').textContent,'股票范围');
    assert.deepEqual(allA.get('#research-track').querySelectorAll('option').map(o=>o.getAttribute('value')),['all_a']);
    const mixed=await researchPage('#model-experiments',{catalog:catalog=>{
        addCsiCatalog(catalog);catalog.records=catalog.records.filter(r=>r.kind!=='strategy'||r.track==='csi1000');
    }});
    await mixed.click('[data-page="strategy-experiments"]');
    assert.deepEqual(mixed.get('#research-track').querySelectorAll('option').map(o=>o.getAttribute('value')),['csi1000']);
    assert.deepEqual(mixed.get('#research-core').querySelectorAll('[data-benchmark]').map(b=>b.dataset.benchmark),['csi/b4s']);
    const empty=await researchPage('#strategy-experiments',{catalog:catalog=>{
        catalog.records=catalog.records.filter(r=>r.kind!=='strategy');
    }});
    assert.deepEqual(empty.get('#research-track').querySelectorAll('option'),[]);
    assert.equal(empty.get('#research-track').disabled,true);
    assert.match(empty.get('#content').textContent,/暂无股票范围/);
});

test('refresh removes empty tracks, resets incompatible filters and discovers later added tracks',async()=>{
    const page=await researchPage('#model-experiments',{catalog:addCsiCatalog});
    await page.change('#research-track','csi1000');
    await page.click('[data-module="records"]');
    await page.change('#research-type','evaluation_method');
    page.catalog.records=page.catalog.records.filter(r=>r.track!=='csi1000');

    await page.click('#refresh');

    assert.deepEqual(page.get('#research-track').querySelectorAll('option').map(o=>o.getAttribute('value')),['all_a']);
    assert.match(page.get('#research-track').innerHTML,/value="all_a" selected/);
    assert.equal(page.document.querySelector('#compare-bar'),null);
    assert.match(page.get('#research-table').textContent,/标签试验/);
    assert.doesNotMatch(page.get('#research-table').textContent,/CSI1000/);
    assert.doesNotMatch(page.get('#research-type').innerHTML,/evaluation_method/);
    addCsiCatalog(page.catalog);
    await page.click('#refresh');
    assert.deepEqual(page.get('#research-track').querySelectorAll('option').map(o=>o.getAttribute('value')),['all_a','csi1000']);
    assert.match(page.get('#research-track').innerHTML,/value="all_a" selected/);
});

test('experiment record rows use date descending while benchmarks still use version descending',async()=>{
    const page=await researchPage('#model-experiments',{catalog:catalog=>{
        catalog.records[0].date='2026-10-30';catalog.records[1].date='2026-08-01';
        catalog.records[2].date='2026-10-31';
        catalog.records.push({...catalog.records[2],id:'model/same-day'},
            {...catalog.records[2],id:'model/bad-date',date:'2026-02-30'},
            {...catalog.records[2],id:'model/no-date',date:undefined});
    }});
    assert.deepEqual(page.get('#research-core').querySelectorAll('[data-benchmark]').map(b=>b.dataset.benchmark),['model/v2','model/v1']);
    await page.click('[data-module="records"]');
    assert.deepEqual(historyIds(page),
        ['model/trial','model/same-day','model/v1','model/v2','model/bad-date','model/no-date']);
});

test('research track selector defaults to all A and isolates benchmarks, definitions and history options',async()=>{
    const page=await researchPage('#model-experiments',{catalog:addCsiCatalog});
    assert.match(page.get('#research-track').innerHTML,/value="all_a" selected/);
    assert.deepEqual(page.get('#research-core').querySelectorAll('[data-benchmark]').map(b=>b.dataset.benchmark),['model/v2','model/v1']);
    assert.doesNotMatch(page.get('#content').textContent,/CSI1000 模型基线/);
    await page.change('#research-track','csi1000');
    assert.deepEqual(page.get('#research-core').querySelectorAll('[data-benchmark]').map(b=>b.dataset.benchmark),['csi/b6']);
    assert.match(page.get('#research-core').textContent,/CSI1000 RankIC.*0\.1234/);
    assert.doesNotMatch(page.get('#content').textContent,/top3|模型一|模型二|扣费净年化/);
    await page.click('[data-module="records"]');
    assert.match(page.get('#research-table').textContent,/CSI1000 模型实验|未核验档案/);
    assert.doesNotMatch(page.get('#research-table').textContent,/标签试验|策略基线/);
    assert.deepEqual(page.get('#research-type').querySelectorAll('option').map(o=>o.getAttribute('value')),['','evaluation_method']);
    assert.ok(page.get('#research-status').querySelectorAll('option').some(o=>o.getAttribute('value')==='draft'));
    assert.equal(page.document.querySelector('[data-select]'),null);
    assert.equal(page.document.querySelector('#compare-bar'),null);
    await page.change('#research-type','evaluation_method');
    await page.change('#research-track','all_a');
    assert.equal(page.document.querySelector('#compare-bar'),null);
    assert.match(page.get('#research-table').textContent,/标签试验/);
    assert.deepEqual(page.get('#research-type').querySelectorAll('option').map(o=>o.getAttribute('value')),['','uncategorized']);
    assert.equal(page.get('#research-status').querySelectorAll('option').some(o=>o.getAttribute('value')==='draft'),false);
    assert.ok(page.requests.every(r=>r.method==='GET'));
});

test('track-aware experiment detail uses the record metric definitions and strategy track survives kind navigation',async()=>{
    const page=await researchPage('#model-experiments',{catalog:addCsiCatalog});
    await page.change('#research-track','csi1000');
    await page.click('[data-module="records"]');
    await page.click('[data-detail="csi/trial"]');
    assert.match(page.get('#dialog-body').textContent,/股票范围.*CSI1000/);
    assert.doesNotMatch(page.get('#dialog-body').textContent,/研究轨道/);
    assert.match(page.get('#detail-metrics').textContent,/实验原生 IC.*0\.35/);
    assert.doesNotMatch(page.get('#detail-metrics').textContent,/扣费净年化|CSI1000 RankIC/);
    await page.click('[data-page="strategy-experiments"]');
    await page.click('[data-module="benchmark"]');
    assert.deepEqual(page.get('#research-core').querySelectorAll('[data-benchmark]').map(b=>b.dataset.benchmark),['csi/b4s']);
    assert.match(page.get('#research-core').textContent,/CSI1000 扣费年化.*56\.00%/);
    assert.doesNotMatch(page.get('#research-core').textContent,/策略一/);
});

test('experiment comparison controls cannot reappear after stock scope changes or refresh',async()=>{
    const page=await researchPage('#model-experiments',{catalog:addCsiCatalog});
    await page.click('[data-module="records"]');
    assert.equal(page.document.querySelector('[data-select]'),null);
    await page.change('#research-track','csi1000');
    await page.click('#refresh');
    assert.deepEqual(historyIds(page),['csi/b6','csi/trial','csi/draft']);
    assert.equal(page.get('#detail-dialog').open,false);
    assert.equal(page.document.querySelector('#compare-bar'),null);
    assert.equal(page.document.querySelector('#compare-now'),null);
    await page.change('#research-track','all_a');
    assert.deepEqual(historyIds(page),['model/v1','model/v2','model/trial']);
    assert.equal(page.requests.some(r=>r.url.startsWith('/api/research/compare')),false);
});

test('model and strategy experiment details do not offer an add-to-comparison action',async()=>{
    for(const [kind,id] of [['model','model/v1'],['strategy','strategy/v1']]){
        const page=await researchPage(`#${kind}-experiments`);
        await page.click('[data-module="records"]');
        await page.click(`[data-detail="${id}"]`);
        assert.equal(page.get('#detail-dialog').open,true);
        assert.equal(page.document.querySelector('#detail-compare'),null);
        assert.doesNotMatch(page.get('#dialog-body').textContent,/加入同口径对比/);
        assert.equal(page.requests.some(r=>r.url.startsWith('/api/research/compare')),false);
    }
});

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
    const page = await researchPage('#model-experiments',{catalog(catalog){
        catalog.records.find(r=>r.id==='model/trial').change_summary='修改训练标签';
    }});
    await page.click('[data-module="records"]');
    const table = page.get('#research-table');
    const headers = table.querySelectorAll('th').map(header => header.textContent);
    for (const label of ['日期', '实验内容', '结论', '报告链接', 'Benchmark版本']) assert.ok(headers.includes(label), label);
    assert.match(table.textContent, /修改训练标签/); assert.match(table.textContent, /未通过/);
    assert.doesNotMatch(table.textContent, /扣费净年化/);
    const link = table.querySelectorAll('a').find(anchor => anchor.getAttribute('href') === '/api/resources/report-trial');
    assert.ok(link, 'The trial report has a direct resource link');
    assert.match(link.textContent, /trial\.html/);
});

test('benchmark detail retains yearly metric matrices with slices rather than full values', async () => {
    const page = await researchPage();
    await page.click('[data-detail="model/v2"]');
    assert.equal(page.get('#detail-dialog').open, true);
    assert.match(page.get('#detail-metrics').textContent, /41\.00%/);
    await page.click('[data-detail-view="year"]');
    const matrices = page.get('#detail-metrics');
    assert.equal(matrices.querySelectorAll('table').length, 2);
    assert.match(matrices.textContent, /2020/); assert.match(matrices.textContent, /2025/);
    assert.match(matrices.textContent, /23\.00%/);
    assert.doesNotMatch(matrices.textContent, /41\.00%|全局 RankIC/);
    assert.ok(matrices.querySelectorAll('tbody tr').every(row => row.textContent.includes('模型二')));
});
