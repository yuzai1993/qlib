(function(root,factory){
    const api=factory(typeof module==='object'&&module.exports?require('./helpers.js'):root.PlatformHelpers);
    if(typeof module==='object'&&module.exports)module.exports=api;
    if(root)root.PlatformResearch=api;
})(typeof globalThis==='undefined'?null:globalThis,function(H){
    'use strict';
    const esc=H.escape;
    const regimeNames={D:'D · 防御/震荡',F:'F · 高波博弈',T:'T · 大盘趋势'};
    const compactStrategyLabels={annualized_return:'扣费 CAGR',sharpe_ratio:'夏普',alpha:'Alpha',beta:'Beta',
        benchmark_cumulative_return:'基准涨幅',max_drawdown:'最大回撤',calmar_ratio:'Calmar',
        annualized_volatility:'年化波动',annualized_one_way_turnover:'年换手（倍）'};

    const recordTrack=record=>record?.track||'all_a';
    const typeLabels={training_target:'训练目标',early_stopping:'早停方法',model_structure:'模型结构',
        features:'特征',evaluation_method:'评估方法',strategy_backtest:'策略回测',uncategorized:'未分类'};
    function experimentType(record){
        const key=H.experimentTypeKey(record),label=record.experiment_type_label;
        return {key,label:typeof label==='string'&&label.trim()?label.trim():typeLabels[key]||key};
    }
    const experimentTypes=records=>[...new Map(records.map(record=>{
        const type=experimentType(record);return [type.key,type];
    })).values()];
    const strategyModels=records=>[...new Map(records.filter(record=>record.kind==='strategy').map(record=>{
        const model=H.strategyHistorySummary(record),ref=model.modelRef||H.UNKNOWN_STRATEGY_MODEL;
        return [ref,{ref,label:model.modelLabel}];
    })).values()].sort((a,b)=>a.label.localeCompare(b.label,'zh-CN',{numeric:true}));
    function recordIdentity(record){
        const directory=record.directory;
        if(record.kind==='model'&&typeof directory==='string'){
            if(directory.startsWith('backtest/experiments/models/'))return directory.slice('backtest/experiments/'.length);
            if(directory.startsWith('models/'))return directory;
        }
        return record.id;
    }
    const recordsForTrack=(records,kind,track='all_a')=>records.filter(r=>r.kind===kind&&recordTrack(r)===track);
    const availableTracks=(records,kind)=>[...new Set(records.filter(r=>r.kind===kind).map(recordTrack))]
        .sort((a,b)=>a===b?0:a==='all_a'?-1:b==='all_a'?1:a.localeCompare(b));
    function definitionsForTrack(catalog,kind,track='all_a',record){
        if(record){
            if(Array.isArray(record.metric_definitions))return record.metric_definitions;
            track=recordTrack(record);
        }
        return catalog?.metric_definitions_by_track?.[track]?.[kind]||
            (track==='all_a'?catalog?.metric_definitions?.[kind]:null)||[];
    }
    const isComparable=record=>record.comparable===true&&record.official!==false&&
        !['draft','unverified','unregistered'].includes(record.status);
    function comparisonIssue(records){
        if(records.some(r=>r.kind!==records[0]?.kind||recordTrack(r)!==recordTrack(records[0])))return '所选记录的股票范围或实验类型不同';
        if(records.some(r=>!isComparable(r)))return '所选记录包含非正式或不完整结果，不能参与正式比较';
        return H.comparisonIssue(records);
    }

    function orderVersions(records){
        return records.map((record,index)=>({record,index,parts:(String(record.version||'').match(/\d+/g)||[]).map(Number)}))
            .sort((a,b)=>{
                for(let i=0;i<Math.max(a.parts.length,b.parts.length);i++){
                    const difference=(b.parts[i]??-1)-(a.parts[i]??-1);
                    if(difference)return difference;
                }
                return a.index-b.index;
            }).map(item=>item.record);
    }

    function orderHistory(records){
        const timestamp=record=>{
            const value=record.date;
            if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(value))return null;
            const day=value.slice(0,10),calendar=Date.parse(day),time=Date.parse(value);
            if(!Number.isFinite(calendar)||new Date(calendar).toISOString().slice(0,10)!==day||!Number.isFinite(time))return null;
            return calendar;
        };
        return records.map((record,index)=>({record,index,time:timestamp(record)}))
            .sort((a,b)=>a.time===null?(b.time===null?a.index-b.index:1):
                b.time===null?-1:b.time-a.time||a.index-b.index).map(item=>item.record);
    }

    function metricDefinitions(definitions,kind,view='full'){
        const visible=kind==='strategy'?definitions.filter(d=>d.key!=='annualized_return_arith'):definitions;
        return H.metricColumns(visible,view).map(d=>kind==='strategy'&&view==='full'?
            {...d,displayLabel:compactStrategyLabels[d.key]||d.label}:{...d});
    }

    function createBenchmarkSelection(){
        const selected={},known={},group=(kind,track='all_a')=>kind+':'+track;
        return {
            sync(records){
                for(const kind of ['model','strategy']){
                    const tracks=new Set([...availableTracks(records,kind),
                        ...Object.keys(known).filter(key=>key.startsWith(kind+':')).map(key=>key.slice(kind.length+1))]);
                    for(const track of tracks){
                        const key=group(kind,track),ids=new Set(recordsForTrack(records,kind,track).filter(r=>r.is_baseline).map(r=>r.id));
                        selected[key]??=new Set();known[key]??=new Set();
                        for(const id of ids)if(!known[key].has(id))selected[key].add(id);
                        for(const id of selected[key])if(!ids.has(id))selected[key].delete(id);
                        known[key]=ids;
                    }
                }
            },
            ids(kind,track='all_a'){return new Set(selected[group(kind,track)]);},
            set(kind,id,checked,track='all_a'){const key=group(kind,track);if(known[key]?.has(id)){if(checked)selected[key].add(id);else selected[key].delete(id);}},
            all(kind,checked,track='all_a'){const key=group(kind,track);selected[key]=checked?new Set(known[key]):new Set();}
        };
    }

    function dimensions(records,view){
        const keys=new Set(view==='regime'?['D','F','T']:[]);
        for(const record of records){
            Object.keys(view==='year'?record.years||{}:record.regimes||{}).forEach(key=>keys.add(key));
            if(view==='year'){
                const start=Number(record.window?.start?.slice(0,4)),end=Number(record.window?.end?.slice(0,4));
                if(start>=1900&&end>=start&&end-start<100)for(let year=start;year<=end;year++)keys.add(String(year));
            }
        }
        return view==='year'?[...keys].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true})):[...keys];
    }

    // Both benchmark and single-experiment views consume these same table models.
    function metricTables(records,definitions,view='full',dimensionRecords=records){
        if(!records.length)return [];
        const defs=H.metricColumns(definitions,view);
        if(view==='full')return [{key:'full',title:'核心指标',view,columns:defs,
            rows:records.map(record=>({record,values:defs.map(d=>record.metrics?.[d.key]??null)}))}];
        const slices=dimensions(dimensionRecords,view);
        return defs.map(d=>({key:d.key,title:d.label,view,
            columns:slices.map(key=>({key,label:view==='regime'?(regimeNames[key]||key):key,format:d.format})),
            rows:records.map(record=>({record,values:slices.map(key=>H.metricsFor(record,view,key)[d.key]??null)}))}));
    }

    function recordLabelText(record){
        const name=record.display_name||H.strategyReferenceText(record,record.name)||record.id,version=H.strategyVersionLabel(record);
        const hasVersion=record.kind==='strategy'&&version&&new RegExp('(^|[^A-Za-z0-9])'+version.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'(?=$|[^A-Za-z0-9])').test(name);
        return (version&&!hasVersion?version+' · ':'')+name;
    }
    function recordLabel(record,interactive,compact=false){
        const name=record.display_name||H.strategyReferenceText(record,record.name)||record.id,version=H.strategyVersionLabel(record),label=recordLabelText(record);
        const identity=recordIdentity(record);
        if(record.kind==='strategy'&&record.is_baseline){
            const summary=strategyBenchmarkSummary(record),tag=interactive?'button':'span';
            return `<${tag} class="record-name strategy-benchmark-name"${interactive?` data-detail="${esc(record.id)}"`:''} title="${esc(summary.title)}"><span class="benchmark-version">${esc(summary.version||'未登记版本')}</span><span class="benchmark-model">${esc(summary.modelLabel)}</span><span class="benchmark-strategy">${esc(summary.strategyName)}</span></${tag}>`+
                (record.current?' <span class="badge badge-current">当前</span>':'');
        }
        const shown=compact?(version||name):label,title=compact?` title="${esc(label)}"`:'';
        return (interactive?`<button class="record-name" data-detail="${esc(record.id)}"${title}>${esc(shown)}</button>`:`<span class="record-name"${title}>${esc(shown)}</span>`)+
            (record.current?' <span class="badge badge-current">当前</span>':'')+
            (compact?'':`<div class="record-id" title="${esc(identity)}">${esc(identity)}</div>`);
    }

    function renderMetricTables(tables,{interactive=false,sortable=false,sort='',order='desc',selection,compact=false}={}){
        if(!tables.length)return '<div class="empty">请选择至少一个版本查看指标。</div>';
        const selectable=selection!=null&&typeof selection.has==='function';
        return tables.map((table,tableIndex)=>{
            const tableClasses=[...(selectable?['benchmark-core']:[]),...(compact?['benchmark-compact']:[])];
            const allSelected=table.rows.length>0&&table.rows.every(({record})=>selection?.has(record.id));
            const selectHeader=selectable?`<th scope="col" class="benchmark-selection"><input type="checkbox"${tableIndex===0?' id="benchmark-toggle-all"':''} data-benchmark-all aria-label="全选展示版本"${allSelected?' checked':''}></th>`:'';
            const headers=table.columns.map(column=>{
                const label=compact?(column.displayLabel||column.label):column.label;
                return `<th scope="col"${label!==column.label?` title="${esc(column.label)}"`:''}>${sortable&&table.view==='full'?`<button class="text-button" data-sort="${esc(column.key)}">${esc(label)}${sort===column.key?(order==='desc'?' ↓':' ↑'):' ↕'}</button>`:esc(label)}</th>`;
            }).join('');
            const rows=table.rows.map(({record,values})=>`<tr class="${record.current?'current-row':''}">`+
                (selectable?`<td class="benchmark-selection"><input type="checkbox" data-benchmark="${esc(record.id)}" aria-label="展示 ${esc(H.strategyVersionLabel(record)||record.display_name||record.name||record.id)}"${selection.has(record.id)?' checked':''}></td>`:'')+
                `<td class="record-column">${recordLabel(record,interactive,compact)}</td>${values.map((value,index)=>`<td>${H.formatMetric(value,table.columns[index].format)}</td>`).join('')}</tr>`).join('');
            return `<section class="metric-section" data-metric-table="${esc(table.key)}"><h3>${esc(table.title)}</h3>`+
                (!table.columns.length?'<div class="empty">暂无分维度数据。</div>':
                    `<div class="table-wrap"><table${tableClasses.length?` class="${tableClasses.join(' ')}"`:''}><thead><tr>${selectHeader}<th scope="col" class="record-column">版本 / 实验</th>${headers}</tr></thead><tbody>${rows}</tbody></table></div>`)+
                '</section>';
        }).join('');
    }

    function metricCsvRows(tables){
        if(!tables.length)return [['实验 ID','名称','版本']];
        const sliced=tables[0].view!=='full',strategy=tables[0].rows.every(({record})=>record.kind==='strategy');
        return [[...(sliced?['指标']:[]),'实验 ID','名称',strategy?'策略版本':'版本',...(strategy?['模型版本','模型简称','策略简称']:[]),...tables[0].columns.map(c=>c.label)],
            ...tables.flatMap(table=>table.rows.map(({record,values})=>{
                const summary=H.strategyHistorySummary(record);
                return [...(sliced?[table.title]:[]),record.id,record.display_name||H.strategyReferenceText(record,record.name)||record.id,H.strategyVersionLabel(record),
                    ...(strategy?[summary.modelVersion||'未登记',summary.modelName||'未登记模型',summary.strategyName]:[]),...values.map(v=>v??'')];
            }))];
    }

    function strategyBenchmarkSummary(record){
        const summary=H.strategyHistorySummary(record),version=H.strategyVersionLabel(record);
        return {...summary,version,title:[version,summary.modelLabel,summary.strategyName].filter(Boolean).join(' · ')};
    }
    const recordTitle=record=>record.kind==='strategy'&&record.is_baseline?
        strategyBenchmarkSummary(record).title:record.display_name||H.strategyReferenceText(record,record.name)||record.id;

    function strategyHistoryAnnotations(record){
        const text=value=>typeof value==='string'?value.trim():'';
        const group=record.entity_type==='experiment_group'||Boolean(record.group_summary);
        const history=record.strategy_history&&typeof record.strategy_history==='object'?record.strategy_history:null;
        const reference=item=>{
            const version=H.strategyVersionLabel({kind:'strategy',track:record.track,version:item.version,display_version:item.display_version});
            const name=H.strategyReferenceText(record,text(item.label));
            const label=item.initial===true?'初始基线':recordLabelText({kind:'strategy',track:record.track,version,
                name:name||version||'未登记',id:''});
            return {version,label,ref:text(item.ref),available:item.available!==false};
        };
        const fallback=record.comparison_baseline||{};
        const baseline=history?.baseline&&typeof history.baseline==='object'?history.baseline:
            {...fallback,label:group?record.group_summary?.control_label:'',scope:group?'group_control':'historical'};
        const formatted=reference(baseline),baselineScope=baseline.scope==='group_control'?'group_control':'historical',baselineRef=formatted.ref;
        let candidates=Array.isArray(history?.promotions)?history.promotions:[];
        if(!history&&!group&&(record.is_baseline===true||record.registered===true&&record.promoted_version)&&
            !['invalid','missing','unsafe'].includes(record.source_status)){
            const version=record.promoted_version||record.version;
            if(text(version))candidates=[{ref:record.id,version,display_version:record.display_promoted_version,
                label:H.strategyHistorySummary(record).strategyName,current:record.current===true}];
        }
        const seen=new Set(),promotions=candidates.filter(item=>item&&typeof item==='object'&&text(item.ref)&&item.available!==false).map(item=>{
            const formatted=reference(item);
            return {ref:formatted.ref,version:formatted.version,label:H.strategyReferenceText(record,text(item.label)),
                current:item.current===true,isControl:item.is_control===true||formatted.ref===baselineRef};
        }).filter(item=>item.version&&!seen.has(item.ref+'|'+item.version)&&seen.add(item.ref+'|'+item.version));
        const historicalBaselines=(Array.isArray(history?.historical_baselines)?history.historical_baselines:[])
            .filter(item=>item&&typeof item==='object').map(item=>({...reference(item),
                members:(Array.isArray(item.member_labels)?item.member_labels:[]).filter(label=>text(label)).map(label=>H.strategyReferenceText(record,label))}));
        return {baselineLabel:formatted.label+(formatted.available?'':'（来源不可用）'),baselineScope,
            baselineRef:formatted.ref,baselineScopeLabel:baselineScope==='group_control'?'组内对照':'历史对照',
            promotions,historicalBaselines,promotionLabel:promotions.map(item=>item.version+(item.current?'（当前）':'')).join(' / '),
            promotionParameters:promotions.map(item=>item.version+'：'+item.label).join('\n'),
            historicalLabel:historicalBaselines.map(item=>(item.members.length?item.members.join('、')+' → ':'')+item.label+(item.available?'':'（来源不可用）')).join('\n'),group};
    }

    function modelHistorySummary(record){
        const text=value=>typeof value==='string'?value.trim():'';
        const baseline=record.comparison_baseline&&typeof record.comparison_baseline==='object'&&!Array.isArray(record.comparison_baseline)?record.comparison_baseline:{};
        const baselineVersion=text(baseline.version);
        return {changeDescription:text(record.change_summary)||'待填写改动说明',
            baselineRef:text(baseline.ref),baselineVersion,
            baselineLabel:baselineVersion||(baseline.initial===true?'初始基线':'未登记')};
    }

    const artifactTypes={
        report:['实验报告','该实验的原始研究报告。'],
        metrics:['评估结果','模型评估使用的原始指标或数据。'],
        original_metrics:['原运行评估结果','原始运行目录中的评估文件，可能已按保留规则清理。'],
        plan:['研究方案','实验设计、方法或复现说明。'],
        config:['运行配置','实验运行时使用的参数配置。'],
        metadata:['实验元数据','实验介绍、人工结论及资产清单。'],
        registry_record:['历史登记','保留原始登记字段和详细指标的历史事件。']
    };
    function modelProfileSummary(record){
        const text=value=>typeof value==='string'?value.trim():'';
        const object=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
        const profile=object(record.model_profile),sample=value=>{
            const source=object(value);return {pool:text(source.pool),time_range:text(source.time_range),filters:text(source.filters)};
        };
        const directory=text(record.directory),identity=directory.startsWith('backtest/experiments/')?
            directory.slice('backtest/experiments/'.length):directory.startsWith('models/')?directory:'';
        const sourceSeeds=Array.isArray(profile.seeds)?profile.seeds:Array.isArray(record.seeds)?record.seeds:[];
        const sourceArtifacts=Array.isArray(profile.artifacts)&&profile.artifacts.length?profile.artifacts:
            Array.isArray(record.resources)?record.resources:[];
        const artifacts=sourceArtifacts.filter(item=>item&&typeof item==='object'&&!Array.isArray(item)).map(item=>{
            const kind=text(item.kind),fallback=artifactTypes[kind]||['研究附件','实验归档中的原始附件。'];
            const resourceId=text(item.resource_id)||text(item.id);
            return {resourceId,name:text(item.name)||'未登记文件名',kind,label:text(item.label)||fallback[0],
                description:text(item.description)||fallback[1],available:item.available===true&&Boolean(resourceId)};
        });
        return {version:text(profile.version)||text(record.version),experimentId:text(profile.experiment_id)||identity,
            structure:text(profile.structure),features:text(profile.features),seeds:sourceSeeds.filter(seed=>Number.isInteger(seed)),
            trainingSample:sample(profile.training_sample),trainingTarget:text(profile.training_target),
            evaluationSample:sample(profile.evaluation_sample),earlyStopping:text(profile.early_stopping),
            predictionSignal:text(profile.prediction_signal),changes:text(profile.changes),
            modelPaths:(Array.isArray(profile.model_paths)?profile.model_paths:[]).filter(item=>text(object(item).path)).map(item=>({
                path:text(item.path),label:text(item.label),seed:Number.isInteger(item.seed)?item.seed:null,available:item.available===true})),artifacts};
    }

    function historySummary(record){
        const conclusions={promote:'晋升',promoted:'已晋升',baseline:'基线',regress:'退步',reject:'未通过',rejected:'未通过',improve:'提升',improved:'提升',inconclusive:'暂无定论',neutral:'持平',mixed:'结果不一致',invalid:'无效',failed:'失败'};
        const text=value=>typeof value==='string'?value.trim():'';
        const source=record.analysis&&typeof record.analysis==='object'?record.analysis:{};
        const analysis={conclusion:text(source.conclusion),evidence:Array.isArray(source.evidence)?source.evidence.filter(value=>text(value)).map(text):[],
            limitations:text(source.limitations),review_status:text(source.review_status),updated_at:text(source.updated_at)};
        const registryConclusion=conclusions[record.conclusion]||text(record.conclusion);
        const hasAnalysis=Boolean(analysis.conclusion||analysis.evidence.length||analysis.limitations);
        const benchmarkOutputs=Array.isArray(record.benchmark_outputs)?[...new Set(record.benchmark_outputs
            .filter(output=>output&&output.available===true&&text(output.ref)&&text(output.version))
            .map(output=>text(output.version)))]:[];
        return {...H.experimentSummary(record),
            name:record.display_name||H.strategyReferenceText(record,record.name)||record.id,
            conclusion:H.strategyReferenceText(record,analysis.conclusion||registryConclusion)||'待填写分析结论',
            conclusionSource:analysis.conclusion?'analysis':registryConclusion?'registry':'',
            registryConclusion,analysis,hasAnalysis,
            note:record.note&&record.note!==record.conclusion?H.strategyReferenceText(record,record.note):'',
            benchmarkOutputs,promotion:benchmarkOutputs.join(' / ')||H.strategyVersionLabel({...record,version:record.promoted_version||(record.is_baseline?record.version:'')||'',display_version:record.display_promoted_version||''})};
    }

    function reportLinks(record){
        return (record.resources||[]).filter(r=>r.kind==='report').map(resource=>resource.available?
            `<a class="report-link" href="/api/resources/${encodeURIComponent(resource.id)}" data-resource="${esc(resource.id)}" data-resource-name="${esc(resource.name)}">${esc(resource.name)} ↗</a>`:
            `<span class="card-sub">${esc(resource.name)} · 报告缺失</span>`).join('<br>')||'<span class="card-sub">未登记报告</span>';
    }

    return {recordTrack,experimentType,experimentTypes,strategyModels,recordIdentity,recordTitle,recordLabelText,strategyBenchmarkSummary,strategyHistoryAnnotations,strategyVersionLabel:H.strategyVersionLabel,strategyReferenceText:H.strategyReferenceText,modelHistorySummary,modelProfileSummary,strategyHistorySummary:H.strategyHistorySummary,recordsForTrack,availableTracks,definitionsForTrack,isComparable,comparisonIssue,orderVersions,orderHistory,metricDefinitions,createBenchmarkSelection,metricTables,renderMetricTables,metricCsvRows,historySummary,reportLinks};
});
