(function () {
    'use strict';
    const H=window.PlatformHelpers, R=window.PlatformResearch, esc=H.escape, tracker=window.PlatformNavigation;
    const benchmarkSelection=R.createBenchmarkSelection();
    const content=document.getElementById('content'), dialog=document.getElementById('detail-dialog');
    const dialogBody=document.getElementById('dialog-body');
    let csrf=null, toastTimer=null, dialogGeneration=0;
    const state={page:'dashboard',catalog:null,analyses:[],kind:'model',module:'benchmark',view:'regime',query:'',status:'',direction:'',selected:new Set(),analysisQuery:'',analysisStatus:'active',analysisDate:''};
    const statusName=H.statusLabel;
    const statusBadge=r=>`<span class="badge ${['failed','invalid','error'].includes(r.status)?'badge-crit':r.status==='running'?'badge-warn':'badge-neutral'}">${esc(statusName(r.status))}</span>`;
    const date=H.formatDate;
    const current=token=>tracker.isCurrent(token);
    function toast(message){const el=document.getElementById('toast');el.textContent=message;el.style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.style.display='none',5000);}
    async function request(path,options={}){
        const response=await fetch(path,{credentials:'same-origin',...options});
        let data;try{data=await response.json();}catch(_){data={detail:`服务返回了无法读取的响应（HTTP ${response.status}）`};}
        if(!response.ok){const error=new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||'请求失败'));error.status=response.status;throw error;}return data;
    }
    async function mutate(path,method,payload){if(!csrf)csrf=(await request('/api/session')).csrf_token;return request(path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(payload)});}
    function handle(action){return function(event){try{Promise.resolve(action(event)).catch(error=>toast(error.message));}catch(error){toast(error.message);}};}
    function openDialog(title,html){dialogGeneration++;document.getElementById('dialog-title').textContent=title;dialogBody.innerHTML=html;if(!dialog.open)dialog.showModal();}
    dialog.addEventListener('close',()=>{dialogGeneration++;});
    document.getElementById('dialog-close').onclick=()=>dialog.close();
    dialog.addEventListener('click',event=>{if(event.target===dialog){const b=dialog.getBoundingClientRect();if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)dialog.close();}});
    function download(name,text,type='text/csv;charset=utf-8'){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    const pageHead=(eyebrow,title,subtitle,actions='')=>`<div class="page-head"><div><div class="eyebrow">${eyebrow}</div><h2>${title}</h2><div class="subtitle">${subtitle}</div></div><div class="page-actions">${actions}</div></div>`;
    function metricDefinitions(view=state.view){return R.metricDefinitions(state.catalog?.metric_definitions?.[state.kind]||[],state.kind,view);}
    const selectedRecords=()=>[...state.selected].map(id=>state.catalog.records.find(r=>r.id===id)).filter(Boolean);
    const baselines=()=>R.orderVersions(state.catalog.records.filter(r=>r.kind===state.kind&&r.is_baseline));
    function visibleRecords(){
        if(state.module==='records')return H.filterRecords(state.catalog.records,{kind:state.kind,query:state.query,status:state.status,direction:state.direction});
        const ids=benchmarkSelection.ids(state.kind);
        return baselines().filter(r=>ids.has(r.id));
    }
    function currentMetricTables(){return R.metricTables(visibleRecords(),H.metricColumns(metricDefinitions(),state.view,state.metricFocus),state.view,baselines());}
    async function renderResearch(token){
        const catalog=await request('/api/research');if(!current(token))return;
        state.catalog=catalog;benchmarkSelection.sync(catalog.records||[]);renderResearchShell(token);
    }
    function renderResearchShell(token){
        const all=state.catalog.records||[],history=state.module==='records',isModel=state.kind==='model';
        content.innerHTML=pageHead(isModel?'RESEARCH / MODELS':'RESEARCH / STRATEGIES',isModel?'模型实验':'策略实验','查看基线版本表现与实验记录。','<button id="export-csv">↓ 导出 CSV</button>')+
        `
        <div class="research-intro"><div class="segmented" aria-label="实验子模块">${[['benchmark','Benchmark'],['records','实验记录']].map(([key,label])=>`<button data-module="${key}" class="${state.module===key?'active':''}" aria-pressed="${state.module===key}">${label}</button>`).join('')}</div><span class="card-sub">${state.kind==='model'?'固定 top3 × h5 · 算术年化':'执行层复合收益 · CAGR'}</span></div>
        <div id="research-warnings">${(state.catalog.warnings||[]).map(w=>`<div class="notice warning">${esc(w)}</div>`).join('')}</div>
        ${history?`<div class="filters"><input type="search" id="research-search" aria-label="搜索实验" placeholder="搜索版本、实验名称或 ID" value="${esc(state.query)}"><select id="research-status" aria-label="实验状态"><option value="">全部状态</option>${[...new Set(all.filter(r=>r.kind===state.kind).map(r=>r.status).filter(Boolean))].sort().map(s=>`<option value="${esc(s)}" ${s===state.status?'selected':''}>${esc(statusName(s))}</option>`).join('')}</select><select id="research-direction" aria-label="实验方向"><option value="">全部方向</option>${[...new Set(all.filter(r=>r.kind===state.kind).map(r=>r.direction).filter(Boolean))].sort().map(s=>`<option value="${esc(s)}" ${s===state.direction?'selected':''}>${esc(s)}</option>`).join('')}</select></div>`:'<div id="research-core"></div><div id="research-controls"></div>'}
        <div id="research-table"></div><div id="compare-bar"></div><div class="source-line">研究源：${esc(state.catalog.sources?.root||'未提供')}<br>Registry：${esc(state.catalog.sources?.registry||'—')} · 源更新时间 ${esc(H.formatDate(state.catalog.sources?.modified_at,true))}</div>`;
        content.querySelectorAll('[data-module]').forEach(button=>button.onclick=()=>{state.module=button.dataset.module;renderResearchShell(token);});
        if(history){
            document.getElementById('research-search').oninput=event=>{state.query=event.target.value;renderResearchTable(token);};
            document.getElementById('research-status').onchange=event=>{state.status=event.target.value;renderResearchTable(token);};
            document.getElementById('research-direction').onchange=event=>{state.direction=event.target.value;renderResearchTable(token);};
        }else renderResearchControls(token);
        document.getElementById('export-csv').onclick=exportResearch;
        renderResearchTable(token);
    }
    function renderResearchControls(token){
        const definitions=metricDefinitions();
        if(state.metricFocus&&!definitions.some(d=>d.key===state.metricFocus))state.metricFocus='';
        document.getElementById('research-controls').innerHTML=`<div class="toolbar"><div class="segmented" aria-label="分维度指标">${[['regime','分风格指标'],['year','分年指标']].map(([key,label])=>`<button data-view="${key}" class="${state.view===key?'active':''}" aria-pressed="${state.view===key}">${label}</button>`).join('')}</div><select id="research-metric" aria-label="展示分维度指标"><option value="">全部指标</option>${definitions.map(d=>`<option value="${esc(d.key)}" ${state.metricFocus===d.key?'selected':''}>${esc(d.label)}</option>`).join('')}</select></div>`;
        content.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{state.view=button.dataset.view;renderResearchControls(token);renderResearchTable(token);});
        document.getElementById('research-metric').onchange=event=>{state.metricFocus=event.target.value;renderResearchTable(token);};
    }
    function exportResearch(){
        const rows=visibleRecords();
        const data=state.module==='records'?[['日期','实验 ID','实验内容','实验假设','结论','备注','报告链接','晋升版本','状态'],...rows.map(r=>{
            const s=R.historySummary(r),links=(r.resources||[]).filter(resource=>resource.kind==='report'&&resource.available).map(resource=>'/api/resources/'+encodeURIComponent(resource.id)).join('\n');
            return [s.date,r.id,s.name,r.hypothesis||'',r.conclusion?s.conclusion:'',s.note,links,s.promotion,s.status];
        })]:R.metricCsvRows(currentMetricTables());
        download(`qlib-${state.kind}-${state.module}-${state.view}.csv`,H.csv(data));toast(`已导出 ${rows.length} 条记录（指标为原始数值）`);
    }
    function bindResources(container){
        container.querySelectorAll('[data-resource]').forEach(button=>button.onclick=event=>{
            event?.preventDefault();const src='/api/resources/'+encodeURIComponent(button.dataset.resource);
            openDialog(button.dataset.resourceName,`<div class="notice">原始报告以隔离预览呈现。</div><iframe class="report-frame" sandbox="" referrerpolicy="no-referrer" title="原始研究报告" src="${src}"></iframe><a class="button-link" href="${src}?download=1">下载原始文件</a>`);
        });
    }
    function renderExperimentTable(token){
        const rows=visibleRecords();
        let html=rows.length?'<div class="table-wrap"><table class="experiment-history"><thead><tr><th>对比</th><th>日期</th><th>实验内容</th><th>结论</th><th>报告链接</th><th>晋升版本</th></tr></thead><tbody>'+rows.map(r=>{
            const s=R.historySummary(r);
            return `<tr><td><input type="checkbox" aria-label="比较 ${esc(s.name)}" data-select="${esc(r.id)}" ${state.selected.has(r.id)?'checked':''} ${!r.comparable?'disabled title="不满足正式比较条件"':''}></td><td>${esc(s.date)}<div>${statusBadge(r)}</div></td><td class="history-content"><button class="record-name" data-detail="${esc(r.id)}">${esc(s.name)}</button><div class="history-description">${esc(s.hypothesis)}</div><div class="card-sub">对照：${esc(s.baseline)} · ${esc(s.scope)}</div><div class="record-id" title="${esc(r.id)}">${esc(r.id)}</div></td><td class="history-conclusion"><div>${esc(s.conclusion)}</div>${s.note?`<div class="history-note">${esc(s.note)}</div>`:''}</td><td class="history-reports">${R.reportLinks(r)}<div><button class="text-button" data-detail="${esc(r.id)}">查看实验详情 →</button></div></td><td>${s.promotion?`<span class="badge badge-baseline">晋升 ${esc(s.promotion)}</span>`:'—'}${r.current?' <span class="badge badge-current">当前</span>':''}</td></tr>`;
        }).join('')+'</tbody></table></div>':'<div class="empty">没有匹配的实验记录。</div>';
        html+=`<div class="table-meta"><span>共 ${rows.length} 条实验记录</span><span>指标在实验详情中查看</span></div>`;
        document.getElementById('research-table').innerHTML=html;
        content.querySelectorAll('[data-select]').forEach(box=>box.onchange=()=>{if(box.checked)state.selected.add(box.dataset.select);else state.selected.delete(box.dataset.select);renderCompareBar(token);});
        content.querySelectorAll('[data-detail]').forEach(button=>button.onclick=()=>researchDetail(button.dataset.detail,token));
        bindResources(content);renderCompareBar(token);
    }
    function renderResearchTable(token){
        if(state.module==='records'){renderExperimentTable(token);return;}
        const rows=visibleRecords(),all=baselines(),ids=benchmarkSelection.ids(state.kind),issue=rows.length>1?H.comparisonIssue(rows):'';
        document.getElementById('research-core').innerHTML=all.length?
            R.renderMetricTables(R.metricTables(all,metricDefinitions('full'),'full'),{interactive:true,selection:ids,compact:state.kind==='strategy'}):
            '<div class="empty">暂无已晋升版本。</div>';
        document.getElementById('research-table').innerHTML=(issue?`<div class="notice warning">${esc(issue)}。以下仅展示各版本原始指标。</div>`:'')+
            (all.length?R.renderMetricTables(currentMetricTables(),{interactive:true}):'')+
            `<div class="table-meta"><span>分维度已选 ${rows.length} / ${all.length} 个版本</span><span>在核心指标首列勾选版本 · 缺失值为「—」</span></div>`;
        content.querySelectorAll('[data-benchmark]').forEach(box=>box.onchange=()=>{
            benchmarkSelection.set(state.kind,box.dataset.benchmark,box.checked);renderResearchTable(token);
        });
        const toggle=document.getElementById('benchmark-toggle-all');
        if(toggle){
            toggle.indeterminate=ids.size>0&&ids.size<all.length;
            toggle.onchange=()=>{benchmarkSelection.all(state.kind,toggle.checked);renderResearchTable(token);};
        }
        content.querySelectorAll('[data-detail]').forEach(button=>button.onclick=()=>researchDetail(button.dataset.detail,token));
        document.getElementById('compare-bar').innerHTML='';
    }
    function renderCompareBar(token){
        const box=document.getElementById('compare-bar');if(!state.selected.size){box.innerHTML='';return;}
        const issue=state.selected.size>12?'最多选择 12 条记录':H.comparisonIssue(selectedRecords());
        box.innerHTML=`<div class="compare-bar"><span>已选择 <strong>${state.selected.size}</strong> 条记录</span><span class="card-sub">${esc(issue||'已通过前端口径检查')}</span><button id="compare-now" class="primary" ${issue?'disabled':''}>同口径对比 →</button><button id="compare-clear">清空</button></div>`;
        document.getElementById('compare-clear').onclick=()=>{state.selected.clear();renderResearchTable(token);};
        document.getElementById('compare-now').onclick=handle(async()=>{
            const kind=state.kind,module=state.module,params=new URLSearchParams();state.selected.forEach(id=>params.append('id',id));
            const data=await request('/api/research/compare?'+params);
            if(!current(token)||state.kind!==kind||state.module!==module)return;
            openDialog('同口径实验对比','<div class="notice">服务端已校验研究轨道、评估池、时间窗与评估协议一致。</div><div id="detail-metrics"></div>');
            renderDetailMetrics(data.records||[],kind);
        });
    }
    function renderDetailMetrics(records,kind,view='full'){
        document.getElementById('detail-metrics').innerHTML=`<div class="segmented" aria-label="实验指标视图">${[['full','核心指标'],['year','分年指标'],['regime','分风格指标']].map(([key,label])=>`<button data-detail-view="${key}" class="${view===key?'active':''}" aria-pressed="${view===key}">${label}</button>`).join('')}</div>`+
            R.renderMetricTables(R.metricTables(records,R.metricDefinitions(state.catalog.metric_definitions?.[kind]||[],kind,view),view));
        dialogBody.querySelectorAll('[data-detail-view]').forEach(button=>button.onclick=()=>renderDetailMetrics(records,kind,button.dataset.detailView));
    }
    function researchDetail(id,token){
        const r=state.catalog.records.find(row=>row.id===id);if(!r)return;
        const summary=R.historySummary(r),fields=[['实验 ID',r.id],['晋升版本',summary.promotion],['记录状态',statusName(r.status)],['评估范围',H.scopeLabel(r.scope)],['评估池',r.pool],['日期范围',(r.window?.start||'—')+' ~ '+(r.window?.end||'—')],['种子',Array.isArray(r.seeds)?r.seeds.join(' / '):'未提供'],['基线引用',r.baseline_ref],['评估协议',typeof r.protocol==='object'?JSON.stringify(r.protocol):r.protocol]];
        openDialog(r.name||r.id,`<div class="detail-grid">${fields.map(([label,value])=>`<div class="detail-item"><small>${label}</small>${esc(value||'—')}</div>`).join('')}</div>${(r.warnings||[]).map(w=>`<div class="notice warning">${esc(w)}</div>`).join('')}<h3>实验内容</h3><div class="prose">${esc(r.hypothesis||'来源未提供实验假设。')}</div><h3>实验结论</h3><div class="prose">${esc(summary.conclusion)}</div>${summary.note?`<h3>记录备注</h3><div class="prose">${esc(summary.note)}</div>`:''}<h3>评估指标</h3>${!r.comparable?'<div class="notice warning">该实验独立展示，不纳入正式同口径比较。</div>':''}<div id="detail-metrics"></div><h3>原始报告与产物</h3><div class="page-actions resource-actions">${(r.resources||[]).map(resource=>resource.available?`<button data-resource="${esc(resource.id)}" data-resource-name="${esc(resource.name)}">预览 ${esc(resource.name)}</button><a class="button-link" href="/api/resources/${encodeURIComponent(resource.id)}?download=1">下载</a>`:`<span class="badge badge-warn">${esc(resource.name)} · 原产物已清理或缺失</span>`).join('')||'<span class="card-sub">未登记可访问资源</span>'}</div>${r.comparable?'<div class="form-actions"><button class="primary" id="detail-compare">加入同口径对比</button></div>':''}`);
        renderDetailMetrics([r],r.kind);bindResources(dialogBody);
        const add=document.getElementById('detail-compare');if(add)add.onclick=()=>{state.selected.add(id);dialog.close();if(current(token)){state.module='records';renderResearchShell(token);}};
    }
    async function renderAnalyses(token){const data=await request('/api/analyses');if(!current(token))return;state.analyses=data.records||[];renderAnalysisShell(token);}
    function renderAnalysisShell(token){const rows=state.analyses;content.innerHTML=pageHead('KNOWLEDGE / ARCHIVE','分析档案','让研究结论、方法与代码留在一起，沉淀可追溯的研究记录。','<button class="primary" id="new-analysis">＋ 新建分析</button>')+`<div class="card-grid"><div class="card"><div class="card-label">全部分析</div><div class="card-value">${rows.length.toString().padStart(2,'0')}</div><div class="card-sub">独立存储 · 版本留存</div></div><div class="card"><div class="card-label">平台分析</div><div class="card-value">${rows.filter(r=>!r.historical&&r.status!=='archived').length}</div><div class="card-sub">可以继续编辑与补充</div></div><div class="card"><div class="card-label">历史报告</div><div class="card-value">${rows.filter(r=>r.historical).length}</div><div class="card-sub">保留原文 · 只读归档</div></div><div class="card"><div class="card-label">已归档</div><div class="card-value">${rows.filter(r=>r.status==='archived'&&!r.historical).length}</div><div class="card-sub">支持随时恢复</div></div></div><div class="toolbar"><div class="segmented">${[['active','当前记录'],['all','全部'],['historical','历史报告'],['archived','已归档']].map(([key,label])=>`<button data-analysis-status="${key}" class="${key===state.analysisStatus?'active':''}">${label}</button>`).join('')}</div><span class="card-sub">报告 · 代码 · 关联实验</span></div><div class="filters"><input type="search" id="analysis-search" placeholder="搜索分析标题或标签" aria-label="搜索分析标题或标签" value="${esc(state.analysisQuery)}"><label for="analysis-date">更新日期</label><input type="date" id="analysis-date" value="${esc(state.analysisDate)}"><button id="analysis-reset">重置筛选</button></div><div id="analysis-list"></div>`;
        document.getElementById('new-analysis').onclick=()=>analysisForm(null,token);
        document.getElementById('analysis-search').oninput=event=>{state.analysisQuery=event.target.value;renderAnalysisList(token);};document.getElementById('analysis-date').onchange=event=>{state.analysisDate=event.target.value;renderAnalysisList(token);};document.getElementById('analysis-reset').onclick=()=>{state.analysisQuery='';state.analysisDate='';renderAnalysisShell(token);};
        content.querySelectorAll('[data-analysis-status]').forEach(button=>button.onclick=()=>{state.analysisStatus=button.dataset.analysisStatus;renderAnalysisShell(token);});renderAnalysisList(token);
    }
    function renderAnalysisList(token){const q=state.analysisQuery.toLowerCase().trim();const rows=state.analyses.filter(r=>(!q||[r.title,...(r.tags||[])].join(' ').toLowerCase().includes(q))&&(!state.analysisDate||date(r.updated_at||r.created_at)===state.analysisDate)&&(state.analysisStatus==='all'||(state.analysisStatus==='historical'?r.historical:state.analysisStatus==='archived'?!r.historical&&r.status==='archived':r.status!=='archived'||r.historical)));
        document.getElementById('analysis-list').innerHTML=rows.length?`<div class="analysis-grid">${rows.map(r=>`<article class="analysis-card"><div><span class="badge ${r.historical?'badge-baseline':r.status==='archived'?'badge-neutral':'badge-teal'}">${r.historical?'历史 · 只读':r.status==='archived'?'已归档':'研究分析'}</span>${r.attachments_missing?.length?'<span class="badge badge-warn">历史资料不完整</span>':''}</div><h3><button data-analysis="${esc(r.id)}">${esc(r.title)}</button></h3><div class="analysis-preview">${esc(r.conclusion||r.background||reportText(r).slice(0,150)||'点击查看完整报告与代码。')}</div><div style="margin-top:13px">${(r.tags||[]).map(tag=>`<span class="tag">${esc(tag)}</span>`).join('')}</div><div class="card-sub">${esc(date(r.updated_at||r.created_at))}<span style="float:right">${r.historical?'原始记录':`v${esc(r.version)}`} · ${(r.codes||[]).length} 份代码</span></div></article>`).join('')}</div><div class="table-meta"><span>共 ${rows.length} 份分析</span><span>历史原文与平台记录分别保留</span></div>`:'<div class="empty">暂无匹配的分析。创建第一份报告，或调整筛选条件。</div>';
        content.querySelectorAll('[data-analysis]').forEach(button=>button.onclick=()=>analysisDetail(button.dataset.analysis,token));
    }
    function reportText(r){return typeof r.report==='string'?r.report:(r.report?.content||r.report?.text||'');}
    function analysisDetail(id,token){const r=state.analyses.find(row=>row.id===id);if(!r)return;const report=reportText(r);
        openDialog(r.title,`<div class="page-actions">${r.historical?'<span class="badge badge-baseline">历史原文 · 只读</span>':`<button id="analysis-edit" class="primary">编辑分析</button><button id="analysis-archive">${r.status==='archived'?'恢复记录':'归档记录'}</button>`}<a class="button-link" href="/api/analyses/${encodeURIComponent(r.id)}/export">↓ 导出报告与代码</a></div><div class="source-line">更新 ${esc(H.formatDate(r.updated_at||r.created_at,true))} · 版本 ${esc(r.version||'历史')} ${r.source?'· 来源 '+esc(r.source):''}</div>${r.attachments_missing?.length?`<div class="notice warning">历史资料不完整：${r.attachments_missing.map(esc).join('；')}。原报告保留原文，缺失附件不代表已验证可复现。</div>`:''}${r.historical?`<h3>历史报告</h3><div class="markdown-report">${H.markdown(report||'历史报告内容不可用。')}</div><details class="source-audit"><summary>查看原文（审计）</summary><pre><code>${esc(report)}</code></pre></details>`:[['background','背景'],['method','方法'],['conclusion','结论'],['usage','代码使用说明']].map(([key,label])=>`<h3>${label}</h3><div class="prose">${esc(r[key]||'—')}</div>`).join('')}<h3>代码文件 <span class="card-sub">${(r.codes||[]).length} 份 · 仅展示与下载</span></h3>${(r.codes||[]).map((code,index)=>`<details ${index===0?'open':''}><summary>${esc(code.name)}</summary><pre><code>${esc(code.content)}</code></pre><button data-code-download="${index}">下载 ${esc(code.name)}</button></details>`).join('')||'<div class="notice warning">没有完整代码附件。</div>'}<h3>关联实验</h3><div class="prose">${esc((r.related_experiments||[]).join('\n')||'未关联实验')}</div>${!r.historical&&report?`<details><summary>查看生成的报告原文</summary><div class="prose">${esc(report)}</div></details>`:''}`);
        dialogBody.querySelectorAll('[data-code-download]').forEach(button=>button.onclick=()=>{const code=r.codes[Number(button.dataset.codeDownload)];download(code.name,code.content,'text/plain;charset=utf-8');});
        if(!r.historical){document.getElementById('analysis-edit').onclick=()=>analysisForm(r,token);document.getElementById('analysis-archive').onclick=handle(async()=>{const button=document.getElementById('analysis-archive'),archiveGeneration=dialogGeneration;button.disabled=true;try{const saved=await mutate('/api/analyses/'+encodeURIComponent(r.id),'PUT',{...analysisPayload(r),version:r.version,status:r.status==='archived'?'active':'archived'});if(!current(token))return;state.analyses=state.analyses.map(row=>row.id===saved.id?saved:row);if(archiveGeneration===dialogGeneration){dialog.close();renderAnalysisShell(token);}toast(saved.status==='archived'?'分析已归档，可以随时恢复。':'分析已恢复。');}catch(error){button.disabled=false;throw error;}});}
    }
    function analysisPayload(r){return Object.fromEntries(['title','background','method','conclusion','usage','tags','related_experiments','codes','status'].map(key=>[key,r[key]??(['tags','related_experiments','codes'].includes(key)?[]:key==='status'?'active':'')]));}
    function analysisForm(record,token){
        const draft=record?{...analysisPayload(record),codes:(record.codes||[]).map(code=>({...code}))}:{title:'',background:'',method:'',conclusion:'',usage:'',tags:[],related_experiments:[],codes:[{name:'analysis.py',content:''}],status:'active'};
        openDialog(record?'编辑分析 · v'+record.version:'新建分析',`<form id="analysis-form"><div class="notice">填写四个报告部分并附上至少一份代码。文件保存在平台独立档案目录。</div><div class="form-field"><label for="analysis-title">分析标题 <span class="required">*</span></label><input id="analysis-title" name="title" required maxlength="200" placeholder="一句话描述研究问题与结论" value="${esc(draft.title)}"></div>${[['background','背景','研究问题、动机与上下文'],['method','方法','数据、评估口径与分析过程'],['conclusion','结论','主要发现、证据与局限'],['usage','代码使用说明','环境、输入、执行方法和产物说明']].map(([key,label,placeholder])=>`<div class="form-field"><label for="section-${key}">${label} <span class="required">*</span></label><textarea id="section-${key}" name="${key}" required placeholder="${placeholder}">${esc(draft[key])}</textarea></div>`).join('')}<div class="detail-grid"><div class="form-field"><label for="analysis-tags">标签</label><input name="tags" id="analysis-tags" value="${esc((draft.tags||[]).join(', '))}" placeholder="用逗号分隔，如 市场风格, 诊断"></div><div class="form-field"><label for="analysis-related">关联实验 ID</label><input name="related" id="analysis-related" value="${esc((draft.related_experiments||[]).join(', '))}" placeholder="多个实验 ID 用逗号分隔"></div></div><div class="section-title"><h3>代码文件 <span class="required">*</span></h3><div class="page-actions"><button type="button" id="add-code">＋ 添加代码</button><button type="button" id="upload-code">↑ 上传文本</button><input type="file" id="code-upload" multiple hidden></div></div><div class="card-sub">最多 20 份，每份不超过 256 KiB。上传内容仅按文本存档。</div><div id="code-editors"></div><div class="form-error" id="analysis-form-error" role="alert"></div><div class="form-actions"><button type="button" id="cancel-analysis">取消</button><button type="submit" id="save-analysis" class="primary">保存分析</button></div></form>`);
        const formGeneration=dialogGeneration,editors=document.getElementById('code-editors');
        function captureCodes(){editors.querySelectorAll('.code-editor').forEach((el,index)=>{draft.codes[index]={name:el.querySelector('input').value,content:el.querySelector('textarea').value};});}
        function drawCodes(){editors.innerHTML=draft.codes.map((code,index)=>`<div class="code-editor"><div class="code-head"><input aria-label="代码文件名 ${index+1}" required value="${esc(code.name)}" placeholder="analysis.py"><button type="button" data-remove-code="${index}">移除</button></div><textarea aria-label="代码内容 ${index+1}" required class="code-input" spellcheck="false" placeholder="在此粘贴代码文本">${esc(code.content)}</textarea></div>`).join('');editors.querySelectorAll('[data-remove-code]').forEach(button=>button.onclick=()=>{captureCodes();draft.codes.splice(Number(button.dataset.removeCode),1);drawCodes();});}
        drawCodes();document.getElementById('cancel-analysis').onclick=()=>dialog.close();document.getElementById('add-code').onclick=()=>{captureCodes();if(draft.codes.length>=20){toast('最多保存 20 份代码。');return;}draft.codes.push({name:'analysis_'+(draft.codes.length+1)+'.py',content:''});drawCodes();};
        document.getElementById('upload-code').onclick=()=>document.getElementById('code-upload').click();
        document.getElementById('code-upload').onchange=handle(async event=>{const files=[...event.target.files];captureCodes();if(files.length+draft.codes.filter(code=>code.content.trim()).length>20)throw new Error('最多保存 20 份代码。');const loaded=[];for(const file of files){if(file.size>256*1024)throw new Error(file.name+' 超过 256 KiB。');const text=await file.text();if(text.includes('\u0000'))throw new Error(file.name+' 不是可读取的文本文件。');loaded.push({name:file.name,content:text});}if(!current(token)||!dialog.open||formGeneration!==dialogGeneration)return;draft.codes=draft.codes.filter(code=>code.content.trim()).concat(loaded);drawCodes();event.target.value='';});
        document.getElementById('analysis-form').onsubmit=async event=>{event.preventDefault();captureCodes();const form=event.currentTarget,errorBox=document.getElementById('analysis-form-error'),button=document.getElementById('save-analysis');const values=new FormData(form);const split=value=>String(value||'').split(/[,，\n]/).map(x=>x.trim()).filter(Boolean);const payload={...draft,title:String(values.get('title')).trim(),background:String(values.get('background')).trim(),method:String(values.get('method')).trim(),conclusion:String(values.get('conclusion')).trim(),usage:String(values.get('usage')).trim(),tags:split(values.get('tags')),related_experiments:split(values.get('related'))};errorBox.textContent='';if(!payload.codes.length){errorBox.textContent='请至少添加一份代码。';return;}button.disabled=true;button.textContent='正在保存…';try{const saved=await mutate(record?'/api/analyses/'+encodeURIComponent(record.id):'/api/analyses',record?'PUT':'POST',record?{...payload,version:record.version}:payload);if(!current(token))return;state.analyses=record?state.analyses.map(row=>row.id===saved.id?saved:row):[saved,...state.analyses];if(formGeneration!==dialogGeneration){toast('分析已保存。');return;}state.analysisStatus=saved.status==='archived'?'archived':'active';state.analysisQuery='';state.analysisDate='';renderAnalysisShell(token);analysisDetail(saved.id,token);toast('分析已保存，报告与代码已归档。');}catch(error){errorBox.textContent=error.status===409?'记录已被其他窗口更新。当前编辑内容已保留，请复制内容后关闭并刷新，再合并到最新版本。':error.message;button.disabled=false;button.textContent='保存分析';}};
    }
    async function navigate(page){
        if(page==='research')page='model-experiments';
        if(!['dashboard','batches','predictions','model-experiments','strategy-experiments','analyses'].includes(page))page='dashboard';
        const isResearch=['model-experiments','strategy-experiments'].includes(page);
        if(isResearch){
            const kind=page==='strategy-experiments'?'strategy':'model';
            if(kind!==state.kind){state.kind=kind;state.metricFocus='';state.status='';state.direction='';state.query='';state.selected.clear();}
        }
        state.page=page;const token=tracker.begin(page);window.PlatformMonitor.clear();if(dialog.open)dialog.close();
        const isMonitor=['dashboard','batches','predictions'].includes(page);
        document.getElementById('refresh').hidden=isMonitor;
        document.getElementById('module-name').textContent=isMonitor?'实盘监控':isResearch?(state.kind==='model'?'模型实验':'策略实验'):'分析档案';
        document.querySelectorAll('nav [data-page]').forEach(a=>a.classList.toggle('active',a.dataset.page===page||(a.parentElement.tagName==='NAV'&&a.dataset.page==='dashboard'&&isMonitor)));
        document.getElementById('monitor-nav').style.display=isMonitor?'block':'none';document.getElementById('monitor-nav').hidden=!isMonitor;
        content.innerHTML='<div class="loading">正在读取'+(isMonitor?'实盘快照':isResearch?'研究目录':'分析档案')+'…</div>';
        try{
            if(isMonitor){
                await window.PlatformMonitor.pages[page](token);
                if(current(token)){
                    const titles={dashboard:'概览',batches:'批次与成交',predictions:'预测信号'};
                    const heading=content.querySelector('h2');
                    if(heading){
                        const wrapper=document.createElement('div');wrapper.className='page-head';heading.before(wrapper);
                        const intro=document.createElement('div'),eyebrow=document.createElement('div');
                        eyebrow.className='eyebrow';eyebrow.textContent='LIVE / MONITOR';intro.append(eyebrow,heading);
                        const subtitle=document.createElement('div');subtitle.className='subtitle';subtitle.textContent='实盘监控 · '+titles[page]+' · 实盘账本只读查询';intro.append(subtitle);wrapper.append(intro);
                    }
                    content.querySelectorAll('table').forEach(table=>{
                        if(table.classList.contains('mini-table')||table.parentElement.classList.contains('table-wrap'))return;
                        const wrap=document.createElement('div');wrap.className='table-wrap';table.before(wrap);wrap.append(table);
                    });
                }
            }else if(isResearch)await renderResearch(token);
            else await renderAnalyses(token);
        }catch(error){
            if(!current(token))return;window.PlatformMonitor.clear();
            content.innerHTML=pageHead(isMonitor?'LIVE / MONITOR':'WORKSPACE',isMonitor?'实盘服务暂不可用':'数据读取失败',isMonitor?'实盘账本暂不可读；其他模块仍可继续使用。':'请检查数据源与服务状态后重试。')+`<div class="notice error">${esc(error.message)}</div><div class="panel"><h3 style="margin-top:0">${isMonitor?'实盘数据源未就绪':'请求未完成'}</h3><p class="subtitle">${isMonitor?'请检查实盘数据源；启动时缺失账本的实例需在账本恢复后重启平台。':'平台保留了模块导航，请尝试刷新数据。'}</p>${isMonitor?'':'<button id="retry-page" class="primary">重新读取</button>'}</div>`;
            const retry=document.getElementById('retry-page');if(retry)retry.onclick=()=>navigate(page);
        }
    }
    document.querySelectorAll('nav [data-page]').forEach(a=>a.onclick=event=>{event.preventDefault();const hash='#'+a.dataset.page;if(location.hash===hash)navigate(a.dataset.page);else location.hash=hash;});document.getElementById('refresh').onclick=()=>navigate(state.page);window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));window.addEventListener('unhandledrejection',event=>{event.preventDefault();toast(event.reason?.message||'请求失败，请重试。');});navigate(location.hash.slice(1)||'dashboard');
})();
