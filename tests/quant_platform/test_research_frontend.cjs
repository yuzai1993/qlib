const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('../../quant_platform/static/js/research.js');

const definitions = [
    {key:'net_ann',label:'扣费净年化',format:'pct'},
    {key:'net_sharpe',label:'扣费夏普',format:'number'},
    {key:'rank_ic_mean',label:'全局 RankIC',format:'rankic'}
];
const records = [
    {id:'m1',kind:'model',version:'v1',is_baseline:true,metrics:{net_ann:.4,net_sharpe:1.2},window:{start:'2024-08-01',end:'2026-07-31'},years:{2024:{net_ann:.1},2026:{net_ann:.3}},regimes:{D:{net_ann:.2}}},
    {id:'m2',kind:'model',version:'v2',is_baseline:true,current:true,comparable:false,metrics:{net_ann:.5},years:{2025:{net_ann:.25}},regimes:{T:{net_ann:.4}}},
    {id:'trial',kind:'model',is_baseline:false,metrics:{net_ann:9}},
    {id:'s1',kind:'strategy',version:'v1',is_baseline:true}
];

test('baseline versions sort numerically newest first without mutating the input or reordering ties',()=>{
    const versions=[
        {id:'v2-first',version:'v2'}, {id:'unversioned'}, {id:'v10',version:'v10'},
        {id:'v2-second',version:'v2'}, {id:'v2.1',version:'v2.1'}, {id:'v1',version:'v1'}
    ];
    const sorted=R.orderVersions(versions);
    assert.deepEqual(sorted.map(r=>r.id),['v10','v2.1','v2-first','v2-second','v1','unversioned']);
    assert.deepEqual(versions.map(r=>r.id),['v2-first','unversioned','v10','v2-second','v2.1','v1']);
    assert.notEqual(sorted,versions);
});

test('benchmark defaults to all baselines including incomplete historical versions, separately per kind',()=>{
    const selection=R.createBenchmarkSelection();selection.sync(records);
    assert.deepEqual([...selection.ids('model')],['m1','m2']);
    assert.deepEqual([...selection.ids('strategy')],['s1']);
    selection.set('model','m1',false);
    assert.deepEqual([...selection.ids('model')],['m2']);
    assert.deepEqual([...selection.ids('strategy')],['s1']);
});

test('empty selection stays empty on refresh; newly promoted versions default selected and removed IDs disappear',()=>{
    const selection=R.createBenchmarkSelection();selection.sync(records);selection.all('model',false);
    selection.sync(records);
    assert.equal(selection.ids('model').size,0);
    selection.sync([...records.filter(r=>r.id!=='m1'),{id:'m3',kind:'model',is_baseline:true}]);
    assert.deepEqual([...selection.ids('model')],['m3']);
    selection.all('model',true);
    assert.deepEqual([...selection.ids('model')],['m2','m3']);
});

test('annual tables use one metric per table, versions as rows, union of years as columns, no full-period fallback',()=>{
    const tables=R.metricTables(records.slice(0,2),definitions,'year');
    assert.equal(tables.length,2);
    assert.deepEqual(tables[0].columns.map(c=>c.key),['2024','2025','2026']);
    assert.deepEqual(tables[0].rows.map(r=>r.record.version),['v1','v2']);
    assert.deepEqual(tables[0].rows.map(r=>r.values),[[.1,null,.3],[null,.25,null]]);
    assert.equal(R.metricTables([records[1]],definitions,'year',records.slice(0,2))[0].columns.length,3);
});

test('regime tables preserve D/F/T columns and shared renderer supports a single experiment',()=>{
    const tables=R.metricTables([records[0]],definitions,'regime');
    assert.deepEqual(tables[0].columns.map(c=>c.key),['D','F','T']);
    assert.deepEqual(tables[0].rows[0].values,[.2,null,null]);
    const html=R.renderMetricTables(tables);
    assert.equal((html.match(/<table>/g)||[]).length,2);
    assert.ok(html.includes('20.00%'));
    assert.ok(html.includes('—'));
    assert.equal(html.includes('40.00%'),false);
    assert.equal(html.includes('RankIC'),false);
});

test('core metrics retain global RankIC, raw export matches selected rows and dimensional tables',()=>{
    const full=R.metricTables([records[0]],definitions,'full');
    assert.equal(full.length,1);assert.equal(full[0].columns.length,3);
    assert.deepEqual(R.metricCsvRows(full)[1],['m1','m1','v1',.4,1.2,'']);
    const years=R.metricCsvRows(R.metricTables([records[1]],definitions,'year',records.slice(0,2)));
    assert.deepEqual(years[0],['指标','实验 ID','名称','版本','2024','2025','2026']);
    assert.deepEqual(years[1],['扣费净年化','m2','m2','v2','',.25,'']);
});

test('core selection puts accessible checkboxes first and retains unchecked version rows',()=>{
    const tables=R.metricTables(records.slice(0,2),definitions,'full');
    const html=R.renderMetricTables(tables,{selection:new Set(['m2']),interactive:true});
    assert.match(html,/<table class="benchmark-core">/);
    assert.match(html,/<tr><th[^>]*><input[^>]*id="benchmark-toggle-all"[^>]*data-benchmark-all[^>]*aria-label="全选展示版本"[^>]*><\/th><th[^>]*>版本 \/ 实验<\/th>/);
    assert.doesNotMatch(html,/<input[^>]*data-benchmark-all[^>]* checked/);
    assert.match(html,/<td[^>]*><input[^>]*data-benchmark="m1"[^>]*aria-label="展示 v1"[^>]*><\/td>/);
    assert.doesNotMatch(html,/<input[^>]*data-benchmark="m1"[^>]* checked/);
    assert.match(html,/<input[^>]*data-benchmark="m2"[^>]* checked/);
    assert.equal((html.match(/<tbody>[\s\S]*?<\/tbody>/)?.[0].match(/<tr\b/g)||[]).length,2);
    assert.match(html,/data-detail="m1"/);
    assert.match(html,/40\.00%/);
    const all=R.renderMetricTables(tables,{selection:new Set(['m1','m2'])});
    assert.match(all,/<input[^>]*data-benchmark-all[^>]* checked/);
    const none=R.renderMetricTables(tables,{selection:new Set()});
    assert.equal((none.match(/data-benchmark="/g)||[]).length,2);
    assert.doesNotMatch(none,/<input[^>]* checked/);
});

test('strategy arithmetic annual return is excluded in every view without changing source definitions',()=>{
    const strategyDefinitions=[
        {key:'annualized_return',label:'扣费累乘年化（CAGR）',format:'pct'},
        {key:'annualized_return_arith',label:'算术年化（审计）',format:'pct'},
        {key:'annualized_one_way_turnover',label:'年化单边换手（倍）',format:'number'}
    ];
    for(const view of ['full','year','regime']){
        assert.deepEqual(R.metricDefinitions(strategyDefinitions,'strategy',view).map(d=>d.key),['annualized_return','annualized_one_way_turnover']);
    }
    assert.equal(strategyDefinitions.length,3);
    assert.equal(R.metricDefinitions(strategyDefinitions,'model').length,3);
    const defs=R.metricDefinitions(strategyDefinitions,'strategy');
    assert.equal(defs[0].label,'扣费累乘年化（CAGR）');
    const tables=R.metricTables([{id:'strategy/source/very-long-id',name:'完整策略名称',version:'v12',metrics:{annualized_return:.345678}}],defs);
    const html=R.renderMetricTables(tables,{selection:new Set(),compact:true});
    assert.match(html,/<table class="benchmark-core benchmark-compact">/);
    assert.match(html,/title="扣费累乘年化（CAGR）"/);
    assert.match(html,/>扣费 CAGR<\/th>/);
    assert.match(html,/title="v12 · 完整策略名称"/);
    assert.match(html,/>v12<\/span>/);
    assert.doesNotMatch(html,/>完整策略名称<|class="record-id"|算术年化/);
    assert.match(html,/34\.57%/);
    assert.deepEqual(R.metricCsvRows(tables)[0],['实验 ID','名称','版本','扣费累乘年化（CAGR）','年化单边换手（倍）']);
});

test('empty benchmarks and empty source dimensions render clear empty states without fake numbers',()=>{
    assert.deepEqual(R.metricTables([],definitions,'full'),[]);
    assert.match(R.renderMetricTables([]),/请选择/);
    const table=R.metricTables([{id:'missing'}],definitions,'year');
    assert.match(R.renderMetricTables(table),/暂无分维度数据/);
});

test('records keep conclusion, note, reports and explicit promotion distinct from comparison baseline',()=>{
    const summary=R.historySummary({id:'trial',hypothesis:'改进标签',conclusion:'regress',note:'未通过',baseline_ref:'v4',version:'v99',is_baseline:false,resources:[]});
    assert.equal(summary.conclusion,'退步');assert.equal(summary.note,'未通过');
    assert.equal(summary.promotion,'');
    assert.equal(R.historySummary({id:'m',version:'v4',is_baseline:true}).promotion,'v4');
    assert.equal(R.historySummary({id:'trial',promoted_version:'v3'}).promotion,'v3');
});

test('reports use resource IDs, show missing reports and escape malicious source names',()=>{
    const html=R.reportLinks({resources:[{id:'a/b',kind:'report',name:'<img src=x>',available:true},{id:'gone',kind:'report',name:'gone.html',available:false},{id:'metrics',kind:'metrics',name:'metrics.json',available:true}]});
    assert.ok(html.includes('/api/resources/a%2Fb'));
    assert.ok(html.includes('&lt;img src=x&gt;'));
    assert.ok(html.includes('报告缺失'));assert.equal(html.includes('metrics.json'),false);
    assert.match(R.reportLinks({resources:[]}),/未登记报告/);
    const unsafe=R.renderMetricTables(R.metricTables([{...records[0],name:'<script>bad</script>'}],definitions,'full'));
    assert.equal(unsafe.includes('<script>'),false);
});
