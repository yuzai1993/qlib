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

    function metricDefinitions(definitions,kind,view='full'){
        const visible=kind==='strategy'?definitions.filter(d=>d.key!=='annualized_return_arith'):definitions;
        return H.metricColumns(visible,view).map(d=>kind==='strategy'&&view==='full'?
            {...d,displayLabel:compactStrategyLabels[d.key]||d.label}:{...d});
    }

    function createBenchmarkSelection(){
        const selected={model:new Set(),strategy:new Set()},known={model:new Set(),strategy:new Set()};
        return {
            sync(records){
                for(const kind of ['model','strategy']){
                    const ids=new Set(records.filter(r=>r.kind===kind&&r.is_baseline).map(r=>r.id));
                    for(const id of ids)if(!known[kind].has(id))selected[kind].add(id);
                    for(const id of selected[kind])if(!ids.has(id))selected[kind].delete(id);
                    known[kind]=ids;
                }
            },
            ids(kind){return new Set(selected[kind]);},
            set(kind,id,checked){if(known[kind].has(id)){if(checked)selected[kind].add(id);else selected[kind].delete(id);}},
            all(kind,checked){selected[kind]=checked?new Set(known[kind]):new Set();}
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

    function recordLabel(record,interactive,compact=false){
        const name=record.name||record.id,label=(record.version?record.version+' · ':'')+name;
        const shown=compact?(record.version||name):label,title=compact?` title="${esc(label)}"`:'';
        return (interactive?`<button class="record-name" data-detail="${esc(record.id)}"${title}>${esc(shown)}</button>`:`<span class="record-name"${title}>${esc(shown)}</span>`)+
            (record.current?' <span class="badge badge-current">当前</span>':'')+
            (compact?'':`<div class="record-id" title="${esc(record.id)}">${esc(record.id)}</div>`);
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
                (selectable?`<td class="benchmark-selection"><input type="checkbox" data-benchmark="${esc(record.id)}" aria-label="展示 ${esc(record.version||record.name||record.id)}"${selection.has(record.id)?' checked':''}></td>`:'')+
                `<td class="record-column">${recordLabel(record,interactive,compact)}</td>${values.map((value,index)=>`<td>${H.formatMetric(value,table.columns[index].format)}</td>`).join('')}</tr>`).join('');
            return `<section class="metric-section" data-metric-table="${esc(table.key)}"><h3>${esc(table.title)}</h3>`+
                (!table.columns.length?'<div class="empty">暂无分维度数据。</div>':
                    `<div class="table-wrap"><table${tableClasses.length?` class="${tableClasses.join(' ')}"`:''}><thead><tr>${selectHeader}<th scope="col" class="record-column">版本 / 实验</th>${headers}</tr></thead><tbody>${rows}</tbody></table></div>`)+
                '</section>';
        }).join('');
    }

    function metricCsvRows(tables){
        if(!tables.length)return [['实验 ID','名称','版本']];
        const sliced=tables[0].view!=='full';
        return [[...(sliced?['指标']:[]),'实验 ID','名称','版本',...tables[0].columns.map(c=>c.label)],
            ...tables.flatMap(table=>table.rows.map(({record,values})=>[...(sliced?[table.title]:[]),record.id,record.name||record.id,record.version||'',...values.map(v=>v??'')]))];
    }

    function historySummary(record){
        const conclusions={promote:'晋升',promoted:'已晋升',baseline:'基线',regress:'退步',reject:'未通过',rejected:'未通过',improve:'提升',improved:'提升',inconclusive:'暂无定论',neutral:'持平',mixed:'结果不一致',invalid:'无效',failed:'失败'};
        return {...H.experimentSummary(record),
            conclusion:conclusions[record.conclusion]||record.conclusion||'未登记结论',
            note:record.note&&record.note!==record.conclusion?record.note:'',
            promotion:record.promoted_version||(record.is_baseline?record.version:'')||''};
    }

    function reportLinks(record){
        return (record.resources||[]).filter(r=>r.kind==='report').map(resource=>resource.available?
            `<a class="report-link" href="/api/resources/${encodeURIComponent(resource.id)}" data-resource="${esc(resource.id)}" data-resource-name="${esc(resource.name)}">${esc(resource.name)} ↗</a>`:
            `<span class="card-sub">${esc(resource.name)} · 报告缺失</span>`).join('<br>')||'<span class="card-sub">未登记报告</span>';
    }

    return {orderVersions,metricDefinitions,createBenchmarkSelection,metricTables,renderMetricTables,metricCsvRows,historySummary,reportLinks};
});
