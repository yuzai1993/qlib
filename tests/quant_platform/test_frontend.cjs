const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const H = require('../../quant_platform/static/js/helpers.js');
const Research = require('../../quant_platform/static/js/research.js');
const R = require('../../quant_platform/static/js/monitor_runtime.js');

test('missing and non-finite metrics remain missing, percentages are explicit', () => {
    assert.equal(H.formatMetric(null, 'pct'), '—');
    assert.equal(H.formatMetric(Infinity, 'number'), '—');
    assert.equal(H.formatMetric(.1234, 'pct'), '12.34%');
    assert.equal(H.formatMetric(.123456, 'rankic'), '0.1235');
});
test('filters search source IDs and status without mixing model and strategy', () => {
    const rows = [{id:'m-v4',name:'模型',kind:'model',status:'complete',direction:'signal',metrics:{x:null}}, {id:'s-v4',kind:'strategy',metrics:{x:2}}, {id:'m-v3',kind:'model',status:'complete',metrics:{x:1}}];
    assert.deepEqual(H.filterRecords(rows,{kind:'model',query:'v4'}).map(x=>x.id),['m-v4']);
    assert.deepEqual(H.sortRecords(rows,'x','desc').map(x=>x.id),['s-v4','m-v3','m-v4']);
});
test('strategy model filters use the full model ref and combine with search and status', () => {
    const trial={id:'trial',kind:'strategy',status:'completed',model_summary:{ref:' model/es-v2 ',version:'v2',name:'Top5早停'},
        strategy_summary:{name:'Top5',description:'强制卖出100'}};
    const rows=[trial,{...trial,id:'same-label',model_summary:{...trial.model_summary,ref:'model/another-v2'}},
        {...trial,id:'failed',status:'failed'}, {...trial,id:'top10',strategy_summary:{name:'Top10'}},
        {id:'model',kind:'model',status:'completed'}, {id:'unknown',kind:'strategy',status:'completed'}];
    assert.deepEqual(H.filterRecords(rows,{kind:'strategy',modelRef:'model/es-v2',query:'强制卖出',status:'completed'}).map(r=>r.id),['trial']);
    assert.deepEqual(H.filterRecords(rows,{kind:'strategy',modelRef:H.UNKNOWN_STRATEGY_MODEL}).map(r=>r.id),['unknown']);
    assert.deepEqual(H.filterRecords(rows,{kind:'model',modelRef:'model/es-v2'}).map(r=>r.id),['model']);
});
test('comparison denies diagnostic, incomplete and mismatched protocols', () => {
    const a = {id:'a',comparable:true,comparison_key:'official-model'};
    assert.equal(H.comparisonIssue([a,{...a,id:'b'}]),'');
    assert.match(H.comparisonIssue([a,{...a,comparable:false}]),/正式/);
    assert.match(H.comparisonIssue([a,{...a,comparison_key:'strategy'}]),/口径/);
});
test('CSV escapes formulas, quotes and newlines; text never injects markup', () => {
    const csv=H.csv([['id','name'],['=SUM(1)','a"b\nc']]);
    assert.ok(csv.includes('"\'=SUM(1)"'));
    assert.ok(csv.includes('"a""b\nc"'));
    assert.equal(H.escape('<img onerror="x">'),'&lt;img onerror=&quot;x&quot;&gt;');
});
test('navigation generations reject stale completions and dispose old charts', () => {
    const n=R.createNavigationTracker();
    const a=n.begin('research'); const b=n.begin('analyses');
    assert.equal(n.isCurrent(a),false); assert.equal(n.isCurrent(b),true);
    let disposed=0;const c=R.createChartManager({addEventListener(){}});
    c.replace({dispose(){disposed++}});c.clear();assert.equal(disposed,1);
});
test('annual/regime metrics do not fallback to full-period values', () => {
    const r={metrics:{x:1},years:{2024:{x:2}},regimes:{D:{x:3}}};
    assert.deepEqual(H.metricsFor(r,'year','2024'),{x:2});
    assert.deepEqual(H.metricsFor(r,'year','2025'),{});
    assert.deepEqual(H.metricsFor(r,'regime','D'),{x:3});
});
test('switching to slices drops global RankIC and resets unavailable metric focus', () => {
    const defs=[{key:'net_ann',format:'pct'},{key:'rank_ic_mean',format:'rankic'}];
    assert.deepEqual(H.metricColumns(defs,'year','rank_ic_mean'),[defs[0]]);
    assert.deepEqual(H.metricColumns(defs,'full','rank_ic_mean'),[defs[1]]);
});
test('timestamps use Asia/Shanghai for both display and date filters', () => {
    assert.equal(H.formatDate('2026-10-28T01:20:00+00:00',true),'2026-10-28 09:20:00 北京时间');
    assert.equal(H.formatDate('2026-10-27T18:00:00Z'),'2026-10-28');
    assert.equal(H.formatDate('2026-10-28'),'2026-10-28');
    assert.equal(H.formatDate('not-a-date',true),'—');
});
test('diagnostic scope and registry status use accurate Chinese labels', () => {
    assert.equal(H.scopeLabel('single_seed_diagnostic'),'单种子诊断');
    assert.equal(H.scopeLabel('extended_window_diagnostic'),'延长窗诊断');
    assert.equal(H.scopeLabel('pool_diagnostic'),'指定池诊断');
    assert.equal(H.scopeLabel('standard'),'标准评估');
    assert.equal(H.statusLabel('preregistered'),'已预登记');
    assert.equal(H.statusLabel('superseded'),'已取代');
});
test('experiment history summary contains metadata and no mixed comparison metrics', () => {
    const row=H.experimentSummary({id:'a',name:'实验',date:'2026-10-28',hypothesis:'假设',baseline_ref:'v4',status:'running',scope:'single_seed_diagnostic',source_status:'available',metrics:{net_ann:.42},resources:[{id:'safe-id',kind:'report',available:true}]});
    assert.equal(row.hypothesis,'假设');assert.equal(row.baseline,'v4');
    assert.equal(row.scope,'单种子诊断');assert.equal(row.status,'运行中');
    assert.equal(row.source,'指标源可读 · 1 份可用报告');
    assert.equal(Object.hasOwn(row,'metrics'),false);
});
test('historical Markdown safely renders headings, tables, lists, quotes and code', () => {
    const rendered=H.markdown('# 研究报告\n\n| 指标 | 结果 |\n| --- | ---: |\n| 夏普 | 1.2 |\n\n- 第一项\n- 第二项\n\n> 原始结论\n\n```python\nprint("<unsafe>")\n```');
    assert.ok(rendered.includes('<h1>研究报告</h1>'));
    assert.ok(rendered.includes('<table>'));
    assert.ok(rendered.includes('<th>指标</th>'));
    assert.ok(rendered.includes('<td>1.2</td>'));
    assert.ok(rendered.includes('<ul><li>第一项</li><li>第二项</li></ul>'));
    assert.ok(rendered.includes('<blockquote>'));
    assert.ok(rendered.includes('print(&quot;&lt;unsafe&gt;&quot;)'));
});
test('historical Markdown never activates HTML, scripts, event handlers or remote images', () => {
    const rendered=H.markdown('# <img src=x onerror=alert(1)>\n\n<script>alert(1)</script>\n\n![remote](https://example.test/pixel.png)\n\n[click](javascript:alert(1))');
    assert.equal(/<(?:script|img|iframe)\b/i.test(rendered),false);
    assert.equal(/<[^>]+\s(?:onerror|src|href)=/i.test(rendered),false);
    assert.ok(rendered.includes('&lt;script&gt;'));
    assert.ok(rendered.includes('![remote](https://example.test/pixel.png)'));
});
test('all browser entrypoints parse before deployment', () => {
    for (const name of ['helpers.js','research.js','monitor_runtime.js','monitor.js','platform.js']) {
        new vm.Script(fs.readFileSync(path.join(__dirname,'../../quant_platform/static/js',name),'utf8'),{filename:name});
    }
});
test('prediction navigation cannot render shell after leaving page', async () => {
    const tracker=R.createNavigationTracker();const token=tracker.begin('predictions');
    let release,rendered=false,loaded=false;
    const pending=R.loadPredictionPage({loadDates:()=>new Promise(resolve=>{release=resolve;}),isCurrent:()=>tracker.isCurrent(token),renderShell:()=>{rendered=true;},instrumentResource:R.createLazyResource(()=>[]),acceptInstruments(){},rejectInstruments(){},loadPrimary:()=>{loaded=true;}});
    tracker.begin('research');release(['2026-10-28']);await pending;
    assert.equal(rendered,false);assert.equal(loaded,false);
});
test('slow autocomplete resource does not delay primary prediction query', async () => {
    let resolveInstruments,primary=false,instruments=false;
    const resource=R.createLazyResource(()=>new Promise(resolve=>{resolveInstruments=resolve;}));
    await R.loadPredictionPage({loadDates:async()=>['2026-10-28'],isCurrent:()=>true,renderShell:()=>true,instrumentResource:resource,acceptInstruments:()=>{instruments=true;},rejectInstruments(){},loadPrimary:async()=>{primary=true;}});
    assert.equal(primary,true);assert.equal(instruments,false);
    resolveInstruments([]);await new Promise(resolve=>setImmediate(resolve));assert.equal(instruments,true);
});

async function analysisPageWithDelayedArchive(status) {
    const elements=new Map();
    function element(id) {
        if (!elements.has(id)) elements.set(id,{
            innerHTML:'',textContent:'',value:'',style:{},dataset:{},classList:{toggle(){}},listeners:{},open:false,
            addEventListener(name,handler){this.listeners[name]=handler;},
            showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},
            querySelectorAll(selector){return id==='content'&&selector==='[data-analysis]'?[element('record-button')]:[];}
        });
        return elements.get(id);
    }
    element('record-button').dataset.analysis='record-a';
    const record={id:'record-a',title:'已有分析',background:'背景',method:'方法',conclusion:'结论',usage:'说明',tags:[],related_experiments:[],codes:[{name:'a.py',content:'print(1)'}],status,version:1};
    let release;
    const context={
        document:{getElementById:element,querySelectorAll:()=>[]},
        window:{PlatformHelpers:H,PlatformResearch:Research,PlatformNavigation:R.createNavigationTracker(),PlatformMonitor:{clear(){}},addEventListener(){}},
        location:{hash:'#analyses'},setTimeout(){},clearTimeout(){},
        fetch:async (url,options)=>{
            if(url==='/api/session')return {ok:true,json:async()=>({csrf_token:'token'})};
            if(url==='/api/analyses'&&!options.method)return {ok:true,json:async()=>({records:[record]})};
            assert.equal(url,'/api/analyses/record-a');assert.equal(options.method,'PUT');
            return new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({...record,status:status==='archived'?'active':'archived',version:2})});});
        }
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../quant_platform/static/js/platform.js'),'utf8'),context);
    const flush=()=>new Promise(resolve=>setImmediate(resolve));
    await flush();element('record-button').onclick();element('analysis-archive').onclick();await flush();
    assert.equal(typeof release,'function');
    return {element,flush,release};
}

for(const [status,nextDialog] of [['active','new'],['archived','edit']]) {
    test(`slow ${status==='active'?'archive':'restore'} keeps a newer ${nextDialog} draft open`,async()=>{
        const {element,flush,release}=await analysisPageWithDelayedArchive(status);
        if(nextDialog==='new'){element('detail-dialog').close();element('new-analysis').onclick();}
        else element('analysis-edit').onclick();
        element('analysis-title').value='尚未保存的新草稿';
        const form=element('dialog-body').innerHTML, list=element('content').innerHTML;
        release();await flush();
        assert.equal(element('detail-dialog').open,true);
        assert.equal(element('analysis-title').value,'尚未保存的新草稿');
        assert.equal(element('dialog-body').innerHTML,form);
        assert.equal(element('content').innerHTML,list);
        element('detail-dialog').close();element('record-button').onclick();
        assert.ok(element('dialog-body').innerHTML.includes('版本 2'));
        assert.ok(element('dialog-body').innerHTML.includes(status==='active'?'恢复记录':'归档记录'));
    });
}

test('archive response closes its own unchanged detail and refreshes the analysis list',async()=>{
    const {element,flush,release}=await analysisPageWithDelayedArchive('active');
    release();await flush();
    assert.equal(element('detail-dialog').open,false);
    assert.ok(element('analysis-list').innerHTML.includes('暂无匹配的分析'));
});
