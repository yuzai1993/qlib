const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('../../quant_platform/static/js/research.js');
const H = require('../../quant_platform/static/js/helpers.js');

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

test('experiment history sorts valid dates newest first with stable same-day and unknown dates last',()=>{
    const history=[
        {id:'old',date:'2026-08-01'}, {id:'missing'}, {id:'new-first',date:'2026-10-31'},
        {id:'invalid-day',date:'2026-02-30'}, {id:'new-second',date:'2026-10-31'},
        {id:'timed-first',date:'2026-10-31T00:01:00Z'}, {id:'timed-second',date:'2026-10-31T23:59:00Z'},
        {id:'empty',date:''}, {id:'number',date:20261031}, {id:'bad',date:'not-a-date'},
        {id:'leap',date:'2024-02-29'}
    ];
    assert.deepEqual(R.orderHistory(history).map(r=>r.id),
        ['new-first','new-second','timed-first','timed-second','old','leap','missing','invalid-day','empty','number','bad']);
    assert.equal(history[0].id,'old');
});

test('available research tracks follow each kind records without fabricating empty tracks',()=>{
    assert.deepEqual(R.availableTracks(records,'model'),['all_a']);
    assert.deepEqual(R.availableTracks(records,'unavailable'),[]);
    assert.deepEqual(R.availableTracks([...records,{kind:'strategy',track:'csi1000'},
        {kind:'model',track:'csi1000'},{kind:'model',track:'csi1000'}],'model'),['all_a','csi1000']);
    assert.deepEqual(R.availableTracks([{kind:'model',track:'csi1000'}],'model'),['csi1000']);
});

test('experiment types use current record values, legacy defaults and combined record filters',()=>{
    const samples=[
        {id:'m/target',kind:'model',experiment_type:'training_target',experiment_type_label:'训练目标',status:'completed',direction:'research'},
        {id:'m/structure',kind:'model',experiment_type:'model_structure',experiment_type_label:'模型结构',status:'failed',direction:'research'},
        {id:'m/old',kind:'model'}, {id:'s/old',kind:'strategy'}
    ];
    assert.deepEqual(R.experimentTypes(samples.slice(0,3)),[
        {key:'training_target',label:'训练目标'}, {key:'model_structure',label:'模型结构'}, {key:'uncategorized',label:'未分类'}
    ]);
    assert.equal(R.experimentType(samples[3]).label,'策略回测');
    assert.deepEqual(H.filterRecords(samples,{kind:'model',query:'target',status:'completed',direction:'research',experimentType:'training_target'}).map(r=>r.id),['m/target']);
    assert.equal(H.filterRecords(samples,{experimentType:'training_target',status:'failed'}).length,0);
    assert.deepEqual(H.filterRecords(samples,{experimentType:'uncategorized'}).map(r=>r.id),['m/old']);
});

test('manual analysis conclusion overrides registry text without changing research authority',()=>{
    const record={id:'m/manual',kind:'model',conclusion:'regress',comparable:false,official:false,
        analysis:{conclusion:'人工确认结论',evidence:['metrics/eval.json'],limitations:'限制说明',review_status:'reviewed'}};
    const summary=R.historySummary(record);
    assert.equal(summary.conclusion,'人工确认结论');
    assert.equal(summary.conclusionSource,'analysis');
    assert.equal(summary.registryConclusion,'退步');
    assert.deepEqual(summary.analysis.evidence,['metrics/eval.json']);
    assert.equal(summary.promotion,'');
    assert.equal(R.isComparable(record),false);
    assert.equal(record.conclusion,'regress');
    assert.equal(R.historySummary({...record,analysis:{conclusion:'  '}}).conclusion,'退步');
    assert.equal(R.historySummary({id:'m/empty'}).conclusion,'待填写分析结论');
});

test('model table identity shows its classified directory and escapes editorial labels',()=>{
    const record={id:'regime-adapt/old-slug',name:'可读模型名称',kind:'model',version:'v4',
        directory:'backtest/experiments/models/training-target/new-name',metrics:{net_ann:.4}};
    const html=R.renderMetricTables(R.metricTables([record],definitions,'full'));
    assert.match(html,/models\/training-target\/new-name/);
    assert.doesNotMatch(html,/regime-adapt\/old-slug/);
    assert.match(html,/可读模型名称/);
    assert.equal(R.recordIdentity({...record,directory:undefined}),record.id);
    const unsafe=R.renderMetricTables(R.metricTables([{...record,directory:'backtest/experiments/models/<img src=x>'}],definitions,'full'));
    assert.doesNotMatch(unsafe,/<img /);
    assert.match(unsafe,/&lt;img/);
});

test('strategy history uses its linked model version and short strategy summary without guessing from BT version',()=>{
    const record={id:'baseline/bt-v5',kind:'strategy',version:'v5',name:'旧实验名称',hypothesis:'完整研究假设',
        model_summary:{ref:'model/es',version:'v2',name:'M0 H20 ES'},
        strategy_summary:{name:'Top5',description:'每日换1，持有1日'}};
    const summary=R.strategyHistorySummary(record);
    assert.equal(summary.modelLabel,'v2 · M0 H20 ES');
    assert.equal(summary.modelRef,'model/es');
    assert.equal(summary.strategyName,'Top5');
    assert.equal(summary.strategyDescription,'每日换1，持有1日');
    const legacy=R.strategyHistorySummary({id:'strategy/old',kind:'strategy',version:'BT v99',name:'旧策略'});
    assert.equal(legacy.modelLabel,'未登记模型');
    assert.equal(legacy.modelVersion,'');
    assert.equal(legacy.strategyName,'旧策略');
    assert.equal(legacy.strategyDescription,'');
    assert.equal(R.strategyHistorySummary({...record,model_summary:{ref:'unknown/model',version:'',name:''}}).modelLabel,'未登记模型');
    assert.equal(R.strategyHistorySummary({...record,model_summary:{version:'v2',name:'未关联名称'}}).modelLabel,'未登记模型');
    assert.equal(record.version,'v5');
    const reference='模型v2，对照BT v4，晋升BT v5；原ID baseline/phase-m-v1-bt-v5';
    assert.equal(R.strategyReferenceText(record,reference),'模型v2，对照s4，晋升s5；原ID baseline/phase-m-v1-bt-v5');
    assert.equal(R.strategyReferenceText({...record,kind:'model'},reference),reference);
    assert.equal(R.strategyReferenceText({...record,track:'csi1000'},reference),reference);
    assert.equal(R.recordLabelText({...record,is_baseline:false,display_name:'Baseline · s5',name:'Baseline · BT v5'}),'Baseline · s5');
});

test('strategy presentation versions distinguish all-A strategy s from model v and CSI1000 names',()=>{
    const record={kind:'strategy',track:'all_a',version:'v5',display_version:'s5'};
    assert.equal(R.strategyVersionLabel(record),'s5');
    assert.equal(R.strategyVersionLabel({kind:'strategy',version:' BT v3 '}),'s3');
    assert.equal(R.strategyVersionLabel({kind:'strategy',version:'s4'}),'s4');
    assert.equal(R.strategyVersionLabel({kind:'strategy',version:'v1.1',display_version:'s1.1'}),'s1.1');
    assert.equal(R.strategyVersionLabel({kind:'strategy',version:'BT v1.2.3'}),'s1.2.3');
    assert.equal(R.strategyVersionLabel({kind:'model',version:'v4',display_version:'s4'}),'v4');
    assert.equal(R.strategyVersionLabel({kind:'strategy',track:'csi1000',version:'B4-S v1.0'}),'B4-S v1.0');
    assert.equal(R.strategyVersionLabel({kind:'strategy',track:'csi1000',version:'v2'}),'v2');
    assert.equal(R.strategyVersionLabel({kind:'strategy',version:'说明BT v2继续保留'}),'说明BT v2继续保留');
    assert.equal(record.version,'v5');
});

test('strategy benchmark labels and metric CSV show strategy s, linked model v and the concise rule',()=>{
    const record={id:'baseline/v5',kind:'strategy',version:'v5',display_version:'s5',is_baseline:true,
        name:'完整历史执行回测标题不会恢复到表格',model_summary:{ref:'model/v2',version:'v2',name:'Top5早停'},
        strategy_summary:{name:'Top5换1·持1·F100',description:'长策略说明'},metrics:{annualized_return:.35},
        years:{2026:{annualized_return:.1}},regimes:{D:{annualized_return:.2}}};
    for(const view of ['full','year','regime']){
        const table=R.metricTables([record],[{key:'annualized_return',label:'CAGR',format:'pct'}],view);
        const html=R.renderMetricTables(table,{compact:view==='full',interactive:true});
        assert.match(html,/>s5<\/span>/);
        assert.match(html,/>v2 · Top5早停<\/span>/);
        assert.match(html,/>Top5换1·持1·F100<\/span>/);
        assert.doesNotMatch(html,/>完整历史执行回测标题不会恢复到表格</);
        const csv=R.metricCsvRows(table),offset=view==='full'?0:1;
        assert.equal(csv[0][offset+2],'策略版本');
        assert.deepEqual(csv[1].slice(offset+2,offset+6),['s5','v2','Top5早停','Top5换1·持1·F100']);
    }
    assert.equal(R.historySummary({...record,promoted_version:'v5'}).promotion,'s5');
    assert.equal(R.historySummary({...record,kind:'model',promoted_version:'v5'}).promotion,'v5');
    assert.equal(record.model_summary.version,'v2');
});

test('strategy search covers linked model and strategy text while preserving other filters',()=>{
    const record={id:'bt/latest',kind:'strategy',experiment_type:'strategy_backtest',status:'completed',direction:'phase-m-v1-bt',
        model_summary:{ref:'model/es',version:'v2',name:'M0 H20 ES'},strategy_summary:{name:'Top5',description:'每日换1'}};
    for(const query of ['v2','H20 ES','Top5','每日换1'])assert.deepEqual(H.filterRecords([record],{query}).map(r=>r.id),['bt/latest']);
    assert.equal(H.filterRecords([record],{query:'Top5',status:'failed'}).length,0);
    assert.equal(H.filterRecords([record],{query:'Top5',experimentType:'model_structure'}).length,0);
    assert.deepEqual(H.filterRecords([record],{query:'Top5',status:'completed',direction:'phase-m-v1-bt',experimentType:'strategy_backtest'}),[record]);
});

test('model history uses only the registered change and historical baseline without inferring from self or current versions',()=>{
    const record={id:'model/new-v4',kind:'model',name:'短标题',version:'v4',current:true,baseline_ref:'self',
        hypothesis:'完整假设不能截断后当改动',change_summary:'  只把早停从净年化改为 RankIC  ',
        comparison_baseline:{ref:'model/previous-v3',version:' v3 ',initial:false}};
    const summary=R.modelHistorySummary(record);
    assert.equal(summary.changeDescription,'只把早停从净年化改为 RankIC');
    assert.equal(summary.baselineLabel,'v3');
    assert.equal(summary.baselineRef,'model/previous-v3');
    for(const missing of [{},{change_summary:' '},{change_summary:{text:'不能作为摘要'}}]){
        const legacy=R.modelHistorySummary({...record,change_summary:undefined,comparison_baseline:undefined,...missing});
        assert.equal(legacy.changeDescription,'待填写改动说明');
        assert.equal(legacy.baselineLabel,'未登记');
    }
    assert.equal(R.modelHistorySummary({...record,comparison_baseline:{ref:'self',version:''}}).baselineLabel,'未登记');
    assert.equal(R.modelHistorySummary({...record,comparison_baseline:{ref:'',version:'',initial:true}}).baselineLabel,'初始基线');
    assert.equal(R.modelHistorySummary({...record,comparison_baseline:{version:'v1',initial:true}}).baselineLabel,'v1');
    assert.equal(R.historySummary(record).hypothesis,record.hypothesis);
    assert.equal(record.baseline_ref,'self');
});

test('model history search includes the compact change and historical baseline while retaining status and category filters',()=>{
    const record={id:'model/trial',kind:'model',name:'早停试验',status:'completed',experiment_type:'early_stopping',
        change_summary:'只把 early stop 改为 daily_rank_ic',comparison_baseline:{ref:'model/previous',version:'v3'}};
    for(const query of ['daily_rank_ic','v3'])assert.deepEqual(H.filterRecords([record],{query}),[record]);
    assert.equal(H.filterRecords([record],{query:'daily_rank_ic',status:'failed'}).length,0);
    assert.equal(H.filterRecords([record],{query:'daily_rank_ic',experimentType:'features'}).length,0);
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

test('track filtering defaults legacy records to all A and isolates baseline selections by kind and track',()=>{
    const mixed=[...records,{id:'b6',kind:'model',track:'csi1000',is_baseline:true},
        {id:'b4s',kind:'strategy',track:'csi1000',is_baseline:true}];
    assert.deepEqual(R.recordsForTrack(mixed,'model').map(r=>r.id),['m1','m2','trial']);
    assert.deepEqual(R.recordsForTrack(mixed,'model','csi1000').map(r=>r.id),['b6']);
    const selection=R.createBenchmarkSelection();selection.sync(mixed);
    assert.deepEqual([...selection.ids('model')],['m1','m2']);
    assert.deepEqual([...selection.ids('model','csi1000')],['b6']);
    selection.all('model',false,'csi1000');
    selection.set('model','b6',true);
    assert.deepEqual([...selection.ids('model','csi1000')],[]);
    assert.deepEqual([...selection.ids('model')],['m1','m2']);
    assert.deepEqual([...selection.ids('strategy','csi1000')],['b4s']);
    selection.sync(mixed);
    assert.equal(selection.ids('model','csi1000').size,0);
});

test('track metric definitions use record overrides and never borrow all A definitions for CSI1000',()=>{
    const csi=[{key:'rank_ic',label:'CSI1000 RankIC',format:'rankic'}];
    const catalog={metric_definitions:{model:definitions},metric_definitions_by_track:{csi1000:{model:csi}}};
    assert.deepEqual(R.definitionsForTrack(catalog,'model'),definitions);
    assert.deepEqual(R.definitionsForTrack(catalog,'model','csi1000'),csi);
    assert.deepEqual(R.definitionsForTrack({metric_definitions:{model:definitions}},'model','csi1000'),[]);
    const override=[{key:'ic',label:'本实验 IC',format:'number'}];
    assert.deepEqual(R.definitionsForTrack(catalog,'model','all_a',{track:'csi1000',metric_definitions:override}),override);
    assert.deepEqual(R.definitionsForTrack(catalog,'model','all_a',{track:'csi1000'}),csi);
    assert.deepEqual(R.definitionsForTrack(catalog,'model','csi1000',{metric_definitions:[]}),[]);
});

test('comparison rejects cross-track records even with matching comparison keys and blocks drafts',()=>{
    const a={id:'a',kind:'model',comparable:true,comparison_key:'same'};
    assert.equal(R.comparisonIssue([a,{...a,id:'b',track:'all_a'}]),'');
    assert.match(R.comparisonIssue([a,{...a,id:'b',track:'csi1000'}]),/股票范围/);
    assert.match(R.comparisonIssue([a,{...a,id:'b',kind:'strategy'}]),/股票范围|类型/);
    for(const extra of [{official:false},{status:'draft'},{status:'unverified'},{comparable:false}]){
        assert.equal(R.isComparable({...a,...extra}),false);
        assert.match(R.comparisonIssue([a,{...a,id:'b',...extra}]),/正式/);
    }
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

test('verified benchmark output is distinct from a legacy promotion and deduplicated',()=>{
    const summary=R.historySummary({id:'study/targets',benchmark_outputs:[
        {ref:'model/v1',version:'v1',available:true},{ref:'model/v1',version:'v1',available:true},
        {ref:'model/v2',version:'v2',available:false},{ref:'',version:'v3',available:true}]});
    assert.deepEqual(summary.benchmarkOutputs,['v1']);
    assert.equal(summary.promotion,'v1');
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

test('model profiles retain explicit identity and seeds but do not infer missing research recipes',()=>{
    const record={kind:'model',id:'regime-adapt/old',version:'v4',directory:'backtest/experiments/models/early-stopping/rankic-v4',
        seeds:[42,1000],hypothesis:'完整假设不能猜成训练方法',change_summary:'原简短列表改动',
        resources:[{id:'source',name:'source.json',kind:'metrics',available:true},{id:'gone',name:'gone.json',kind:'registry_record',available:false}]};
    const before=JSON.stringify(record),profile=R.modelProfileSummary(record);
    assert.equal(profile.version,'v4');assert.equal(profile.experimentId,'models/early-stopping/rankic-v4');
    assert.deepEqual(profile.seeds,[42,1000]);
    for(const field of ['structure','features','trainingTarget','earlyStopping','predictionSignal','changes'])assert.equal(profile[field],'');
    assert.deepEqual(profile.trainingSample,{pool:'',time_range:'',filters:''});
    assert.deepEqual(profile.evaluationSample,{pool:'',time_range:'',filters:''});
    assert.equal(profile.artifacts[0].label,'评估结果');assert.equal(profile.artifacts[0].resourceId,'source');
    assert.equal(profile.artifacts[1].label,'历史登记');assert.equal(profile.artifacts[1].available,false);
    assert.equal(JSON.stringify(record),before);
});

test('model profiles treat malformed descriptive values as missing and keep model paths as text',()=>{
    const profile=R.modelProfileSummary({kind:'model',model_profile:{structure:[],features:{name:'不能推断'},early_stopping:[],prediction_signal:4,seeds:[42,'1000',null,NaN],
        training_sample:'not an object',evaluation_sample:{pool:['all']},
        model_paths:[null,{path:''},{path:'backtest/result/run/trained_model',label:'v2',seed:42,available:true}],
        artifacts:[null,{resource_id:'',name:'no-id.json',available:true},{id:'known',kind:'unknown',available:'true'}]}});
    assert.equal(profile.structure,'');assert.deepEqual(profile.seeds,[42]);
    assert.equal(profile.features,'');assert.equal(profile.earlyStopping,'');assert.equal(profile.predictionSignal,'');
    assert.equal(profile.trainingSample.pool,'');assert.equal(profile.evaluationSample.pool,'');
    assert.deepEqual(profile.modelPaths,[{path:'backtest/result/run/trained_model',label:'v2',seed:42,available:true}]);
    assert.equal(profile.artifacts[0].available,false);assert.equal(profile.artifacts[1].available,false);
    assert.equal(profile.artifacts[1].label,'研究附件');
});

test('model profile keeps explicit features and split methods without parsing deprecated combined text',()=>{
    const input={kind:'model',model_profile:{structure:'  单LightGBM  ',features:'  Alpha158 + range  ',
        early_stopping:'  499天次日RankIC  ',prediction_signal:'  五种子逐日z-score后等权融合  ',
        evaluation_method:'训练早停：不应解析；评估融合：不应沿用'}};
    const before=JSON.stringify(input),profile=R.modelProfileSummary(input);
    assert.equal(profile.structure,'单LightGBM');assert.equal(profile.features,'Alpha158 + range');
    assert.equal(profile.earlyStopping,'499天次日RankIC');
    assert.equal(profile.predictionSignal,'五种子逐日z-score后等权融合');
    assert.equal(Object.hasOwn(profile,'evaluationMethod'),false);
    const legacy=R.modelProfileSummary({model_profile:{structure:'旧描述包含Alpha158，不能从中切分',evaluation_method:input.model_profile.evaluation_method}});
    assert.equal(legacy.structure,'旧描述包含Alpha158，不能从中切分');
    assert.equal(legacy.features,'');assert.equal(legacy.earlyStopping,'');assert.equal(legacy.predictionSignal,'');
    assert.equal(JSON.stringify(input),before);
});
