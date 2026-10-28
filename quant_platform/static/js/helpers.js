(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.PlatformHelpers=api;})(typeof globalThis==='undefined'?null:globalThis,function(){
    'use strict';
    const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    function formatMetric(value,format){if(value==null||!Number.isFinite(Number(value)))return '—';const n=Number(value);return format==='pct'?(n*100).toFixed(2)+'%':n.toFixed(format==='rankic'?4:2);}
    function metricsFor(r,view,slice){return view==='year'?(r.years?.[slice]||{}):view==='regime'?(r.regimes?.[slice]||{}):(r.metrics||{});}
    function metricColumns(definitions,view,focus){const defs=['year','regime'].includes(view)?definitions.filter(d=>d.format!=='rankic'):definitions;const selected=focus?defs.filter(d=>d.key===focus):[];return selected.length?selected:defs;}
    const statuses={complete:'已完成',completed:'已完成',success:'已完成',ok:'已完成',running:'运行中',failed:'失败',error:'错误',archived:'已归档',active:'进行中',missing:'资料缺失',invalid:'解析异常',partial:'不完整',historical:'历史归档',promoted:'已晋升',baseline:'基线',candidate:'候选',preregistered:'已预登记',planned:'已计划',pending:'待处理',cancelled:'已取消',aborted:'已中止',superseded:'已取代',unknown:'未标注'};
    const scopes={standard:'标准评估',single_seed_diagnostic:'单种子诊断',extended_window_diagnostic:'延长窗诊断',pool_diagnostic:'指定池诊断',seed_incomplete:'种子不完整',legacy_fallback:'历史指标回退',legacy_backtest:'历史回测口径'};
    const statusLabel=value=>statuses[value]||value||'未标注';
    const scopeLabel=value=>scopes[value]||value||'未标注';
    function formatDate(value,withTime=false){
        if(!value)return '—';
        const parsed=new Date(value);if(!Number.isFinite(parsed.getTime()))return '—';
        const parts=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(parsed).map(p=>[p.type,p.value]));
        const day=parts.year+'-'+parts.month+'-'+parts.day;
        return withTime?day+' '+parts.hour+':'+parts.minute+':'+parts.second+' 北京时间':day;
    }
    function experimentSummary(record){
        const reports=(record.resources||[]).filter(resource=>resource.kind==='report'&&resource.available).length;
        const source={available:'指标源可读',missing:'指标源缺失',invalid:'指标源异常',unsafe:'来源路径不可访问',unconfigured:'未登记指标源',unregistered:'未登记指标源'}[record.source_status]||record.source_status||'未提供来源状态';
        return {date:formatDate(record.date),name:record.name||record.id,hypothesis:record.hypothesis||'来源未提供实验假设',baseline:record.baseline_ref||'未标注对照基线',status:statusLabel(record.status),scope:scopeLabel(record.scope),source:source+' · '+reports+' 份可用报告'};
    }
    function markdown(text){
        // Escape source before recognizing a deliberately small, inert Markdown subset.
        // Links and images stay as text; only static structural tags are introduced.
        const lines=escape(text).replace(/\r\n?/g,'\n').split('\n'),out=[];
        const inline=value=>value.replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
        const cells=line=>line.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(value=>value.trim());
        const separator=line=>Boolean(line)&&/^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
        const fence=line=>/^\s*(`{3,}|~{3,})/.exec(line);
        const list=line=>/^\s*(?:[-*+]\s+|\d+[.)]\s+)/.test(line);
        const quote=line=>/^\s*&gt;\s?/.test(line);
        const heading=line=>/^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
        const rule=line=>/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line);
        let i=0;
        while(i<lines.length){
            const line=lines[i];
            if(!line.trim()){i++;continue;}
            const codeFence=fence(line);
            if(codeFence){
                const marker=codeFence[1],code=[];i++;
                while(i<lines.length&&!lines[i].trim().startsWith(marker))code.push(lines[i++]);
                if(i<lines.length)i++;
                out.push('<pre><code>'+code.join('\n')+'</code></pre>');continue;
            }
            const title=heading(line);
            if(title){const level=title[1].length;out.push('<h'+level+'>'+inline(title[2])+'</h'+level+'>');i++;continue;}
            if(rule(line)){out.push('<hr>');i++;continue;}
            if(line.includes('|')&&separator(lines[i+1])){
                const headers=cells(line),rows=[];i+=2;
                while(i<lines.length&&lines[i].trim()&&lines[i].includes('|')&&!fence(lines[i]))rows.push(cells(lines[i++]));
                out.push('<div class="table-wrap"><table><thead><tr>'+headers.map(value=>'<th>'+inline(value)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+headers.map((_,index)=>'<td>'+inline(row[index]||'')+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>');continue;
            }
            if(list(line)){
                const ordered=/^\s*\d+[.)]\s+/.test(line),tag=ordered?'ol':'ul',items=[];
                while(i<lines.length&&list(lines[i])&&/^\s*\d+[.)]\s+/.test(lines[i])===ordered){items.push('<li>'+inline(lines[i++].replace(/^\s*(?:[-*+]\s+|\d+[.)]\s+)/,''))+'</li>');}
                out.push('<'+tag+'>'+items.join('')+'</'+tag+'>');continue;
            }
            if(quote(line)){
                const quoted=[];while(i<lines.length&&quote(lines[i]))quoted.push(inline(lines[i++].replace(/^\s*&gt;\s?/,'')));
                out.push('<blockquote>'+quoted.join('<br>')+'</blockquote>');continue;
            }
            const paragraph=[inline(line)];i++;
            while(i<lines.length&&lines[i].trim()&&!fence(lines[i])&&!heading(lines[i])&&!rule(lines[i])&&!list(lines[i])&&!quote(lines[i])&&!(lines[i].includes('|')&&separator(lines[i+1])))paragraph.push(inline(lines[i++]));
            out.push('<p>'+paragraph.join('<br>')+'</p>');
        }
        return out.join('\n');
    }
    function filterRecords(rows,f){const q=(f.query||'').trim().toLowerCase();return rows.filter(r=>(!f.kind||r.kind===f.kind)&&(!f.status||r.status===f.status)&&(!f.direction||r.direction===f.direction)&&(!f.baseline||r.is_baseline)&&(!q||[r.id,r.name,r.version,...(r.tags||[])].join(' ').toLowerCase().includes(q)));}
    function sortRecords(rows,key,order='desc',view='full',slice=''){const sign=order==='asc'?1:-1;return [...rows].sort((a,b)=>{const av=metricsFor(a,view,slice)[key],bv=metricsFor(b,view,slice)[key];if(av==null)return bv==null?0:1;if(bv==null)return -1;return (Number(av)-Number(bv))*sign;});}
    function comparisonIssue(rows){if(rows.length<2)return '请至少选择两条记录';if(rows.some(r=>!r.comparable))return '所选记录包含非正式或不完整结果，不能参与正式比较';if(!rows[0].comparison_key||rows.some(r=>r.comparison_key!==rows[0].comparison_key))return '所选记录的研究轨道、评估池或评估口径不同';return '';}
    function csv(rows){return '\uFEFF'+rows.map(row=>row.map(v=>{let s=String(v??'');if(/^[\s]*[=+\-@]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';}).join(',')).join('\r\n');}
    function lineChart(el,dates,series){const values=series.flatMap(s=>s.data).filter(v=>v!=null&&Number.isFinite(Number(v))).map(Number);if(!values.length){el.innerHTML='<div class="empty">暂无曲线数据</div>';return;}const min=Math.min(...values),max=Math.max(...values),span=max-min||1;const colors=['#089caa','#6577d7'];const lines=series.map((s,i)=>{let d='',gap=true;s.data.forEach((v,j)=>{if(v==null){gap=true;return;}d+=(gap?'M':'L')+(56+j*920/Math.max(1,dates.length-1)).toFixed(2)+','+(226-(Number(v)-min)*188/span).toFixed(2)+' ';gap=false;});return '<path d="'+d+'" fill="none" stroke="'+colors[i%2]+'" stroke-width="2.4"/>';}).join('');el.innerHTML='<div class="chart-key">'+series.map((s,i)=>'<span style="color:'+colors[i%2]+'">● '+escape(s.name)+'</span>').join('')+'</div><svg viewBox="0 0 1000 264" role="img" aria-label="'+escape(series.map(s=>s.name).join(' / '))+'"><path d="M56 24V226H976" fill="none" stroke="#dce5ed"/><text x="8" y="42" fill="#7a8799" font-size="12">'+max.toFixed(3)+'</text><text x="8" y="226" fill="#7a8799" font-size="12">'+min.toFixed(3)+'</text>'+lines+'<text x="56" y="252" fill="#7a8799" font-size="12">'+escape(dates[0]||'')+'</text><text x="900" y="252" fill="#7a8799" font-size="12">'+escape(dates.at(-1)||'')+'</text></svg><div class="card-sub">本地图表 · 悬浮交互图表库离线时自动启用</div>';}
    return {escape,formatMetric,metricsFor,metricColumns,statusLabel,scopeLabel,formatDate,experimentSummary,markdown,filterRecords,sortRecords,comparisonIssue,csv,lineChart};
});
