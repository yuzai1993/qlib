/* 实盘监控仪表盘 SPA */

const content = document.getElementById('content');
const chartManager = MonitorRuntime.createChartManager(window);
const navigationTracker = MonitorRuntime.createNavigationTracker();

async function api(path) {
    const resp = await fetch('/api' + path);
    if (!resp.ok) throw new Error(`${path}: HTTP ${resp.status}`);
    return resp.json();
}

/* ---------- 格式化 ---------- */

const fmtMoney = v => v == null ? '—' :
    v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = v => v == null ? '—' : `${(v * 100).toFixed(2)}%`;
const fmtNav = v => v == null ? '—' : Number(v).toFixed(4);
const fmtSharpe = v => v == null ? '—' : Number(v).toFixed(2);
const pctClass = v => v == null ? '' : (v >= 0 ? 'pos' : 'neg');
const navClass = v => v == null ? '' : (v >= 1 ? 'pos' : 'neg');
const fmtNum = v => v == null ? '—' : v.toLocaleString('zh-CN');
const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function modeBadge(mode) {
    return mode === 'LIVE'
        ? '<span class="badge badge-live">LIVE</span>'
        : '<span class="badge badge-sim">SIM</span>';
}

/* ---------- 概览 ---------- */

async function renderDashboard(token) {
    const [ov, nav, pos] = await Promise.all([
        api('/overview'), api('/nav'), api('/positions'),
    ]);
    if (!navigationTracker.isCurrent(token)) return;
    const s = ov.snapshot;

    let html = `<h2>概览 <span class="card-sub">快照日 ${s ? esc(s.date) : '—'}</span></h2>`;

    if (!s) {
        html += '<div class="empty">暂无快照数据。首次运行：run_monitor.py --stage report</div>';
    } else {
        html += `<div class="card-grid">
            <div class="card"><div class="card-label">总资产</div>
                <div class="card-value">${fmtMoney(s.total_value)}</div></div>
            <div class="card"><div class="card-label">净值</div>
                <div class="card-value ${navClass(ov.nav)}">${fmtNav(ov.nav)}</div></div>
            <div class="card"><div class="card-label">夏普</div>
                <div class="card-value ${pctClass(ov.sharpe)}">${fmtSharpe(ov.sharpe)}</div></div>
            <div class="card"><div class="card-label">最大回撤</div>
                <div class="card-value ${pctClass(ov.max_drawdown)}">${fmtPct(ov.max_drawdown)}</div></div>
        </div>`;
        html += '<div class="chart" id="nav-chart"></div>';
    }

    html += '<h3>持仓</h3>' + positionsTable(pos);

    content.innerHTML = html;
    if (s) drawNavChart(nav, ov.benchmark_name || '基准');
}

function drawNavChart(nav, benchmarkName) {
    chartManager.clear();
    const el = document.getElementById('nav-chart');
    if (!el || !nav.length) return;
    const chart = echarts.init(el, 'dark');
    const dates = nav.map(r => r.date);
    const acct = nav.map(r => (1 + (r.cumulative_return ?? 0)).toFixed(4));
    const bench = nav.map(r => r.benchmark_cumulative_return == null ? null :
        (1 + r.benchmark_cumulative_return).toFixed(4));
    chart.setOption({
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', valueFormatter: v => v == null ? '—' : Number(v).toFixed(4) },
        legend: { data: ['账户', benchmarkName] },
        grid: { left: 50, right: 20, top: 40, bottom: 30 },
        xAxis: { type: 'category', data: dates },
        yAxis: { type: 'value', scale: true,
                 axisLabel: { formatter: v => Number(v).toFixed(2) } },
        series: [
            { name: '账户', type: 'line', data: acct, showSymbol: false,
              lineStyle: { width: 2 } },
            { name: benchmarkName, type: 'line', data: bench, showSymbol: false,
              lineStyle: { width: 1.5, type: 'dashed' } },
        ],
    });
    chartManager.replace(chart);
}

function codeCell(code, name) {
    if (!name) return esc(code);
    return `${esc(code)} <span class="card-sub">${esc(name)}</span>`;
}

function scoreCell(score, rank) {
    if (score == null) return '—';
    const rankStr = rank != null ? ` <span class="card-sub">#${rank}</span>` : '';
    return `${Number(score).toFixed(4)}${rankStr}`;
}

function positionsTable(data) {
    const rows = data.positions || [];
    const cash = data.cash;
    const cashWeight = data.cash_weight;
    const predDate = data.prediction_date;
    if (!rows.length && cash == null) return '<div class="empty">无持仓</div>';
    let html = `<table><thead><tr>
        <th>代码 / 名称</th><th>数量</th><th>成本</th><th>现价</th>
        <th>市值</th><th>盈亏</th><th>权重</th>
        <th>预测分${predDate ? ` <span class="card-sub">${esc(predDate)}</span>` : ''}</th>
        </tr></thead><tbody>`;
    for (const p of rows) {
        html += `<tr><td style="text-align:left">${codeCell(p.stock_code, p.name)}</td>
            <td>${fmtNum(p.shares)}</td>
            <td>${p.avg_cost?.toFixed(2) ?? '—'}</td>
            <td>${p.close_price?.toFixed(2) ?? '—'}</td>
            <td>${fmtMoney(p.market_value)}</td>
            <td class="${pctClass(p.profit)}">${fmtMoney(p.profit)}</td>
            <td>${fmtPct(p.weight)}</td>
            <td>${scoreCell(p.score, p.score_rank)}</td></tr>`;
    }
    if (cash != null) {
        html += `<tr class="cash-row"><td style="text-align:left">现金</td>
            <td>—</td><td>—</td><td>—</td>
            <td>${fmtMoney(cash)}</td><td>—</td>
            <td>${cashWeight != null ? fmtPct(cashWeight) : '—'}</td>
            <td>—</td></tr>`;
    }
    html += '</tbody></table>';
    return html;
}

/* ---------- 批次与成交 ---------- */

async function renderBatches(token) {
    const batches = await api('/batches');
    if (!navigationTracker.isCurrent(token)) return;
    let html = '<h2>批次与成交</h2>';
    if (!batches.length) {
        content.innerHTML = html + '<div class="empty">暂无批次</div>';
        return;
    }
    html += `<table><thead><tr>
        <th>批次</th><th>策略</th><th>执行通道</th><th>交易日</th><th>模式</th><th>账号</th><th>状态</th>
        <th>计划</th><th>终态</th><th>缺失</th><th>发布于</th></tr></thead><tbody>`;
    for (const b of batches) {
        const isSuperseded = b.lifecycle_status === 'SUPERSEDED';
        const status = isSuperseded
            ? `<span class="badge badge-muted">已废弃 → ${esc(b.superseded_by || '—')}</span>`
            : '<span class="badge badge-ok">有效</span>';
        const missing = isSuperseded ? '—' : b.missing;
        const missCls = isSuperseded ? '' : (b.missing > 0 ? 'neg' : 'pos');
        html += `<tr class="clickable ${isSuperseded ? 'row-muted' : ''}" data-batch="${esc(b.batch_id)}">
            <td>${esc(b.batch_id)}</td><td>${esc(b.strategy_id || '—')}</td>
            <td>${esc(b.execution_profile || '—')}</td><td>${esc(b.trade_date)}</td>
            <td>${modeBadge(b.mode)}</td>
            <td>${esc(b.account_id || '—')}</td><td>${status}</td>
            <td>${b.planned}</td><td>${b.terminal}</td>
            <td class="${missCls}">${missing}</td>
            <td>${esc(b.created_at || '')}</td></tr>`;
        html += `<tr class="fills-row" id="fills-${esc(b.batch_id)}" style="display:none">
            <td colspan="11"></td></tr>`;
    }
    html += '</tbody></table>';
    content.innerHTML = html;

    content.querySelectorAll('tr.clickable').forEach(tr => {
        tr.onclick = () => toggleBatchDetail(tr.dataset.batch, token);
    });
}

async function toggleBatchDetail(batchId, token) {
    if (!navigationTracker.isCurrent(token)) return;
    const row = document.getElementById('fills-' + batchId);
    if (row.style.display !== 'none') { row.style.display = 'none'; return; }
    const detail = await api(`/batches/${batchId}`);
    if (!navigationTracker.isCurrent(token)) return;
    const orders = detail.orders || [];
    const fills = detail.fills || [];
    const fillById = Object.fromEntries(fills.map(f => [f.client_order_id, f]));

    const signalDate = detail.signal_date;
    let html = `<h3 style="margin:8px 0">执行计划${signalDate
        ? ` <span class="card-sub">信号日 ${esc(signalDate)}</span>` : ''}</h3>`;
    if (!orders.length) {
        html += '<div class="empty" style="padding:16px">无执行计划（历史批次未入库；新发布的批次会自动保存）</div>';
    } else {
        html += `<table><thead><tr>
            <th>订单号</th><th>代码 / 名称</th><th>方向</th><th>数量</th>
            <th>限价</th><th>优先级</th><th>预测分</th><th>回执</th></tr></thead><tbody>`;
        for (const o of orders) {
            const f = fillById[o.client_order_id];
            let fillCell = '<span class="card-sub">等待回执</span>';
            if (f) {
                const stCls = ['REJECTED', 'ERROR'].includes(f.status) ? 'badge-crit'
                    : (f.status === 'FILLED' ? 'badge-ok' : 'badge-warn');
                fillCell = `<span class="badge ${stCls}">${esc(f.status)}</span>`
                    + (f.filled_qty
                        ? ` ${fmtNum(f.filled_qty)}@${f.avg_price?.toFixed(2) ?? '—'}`
                        : '');
            }
            html += `<tr>
                <td>${esc(o.client_order_id)}</td>
                <td style="text-align:left">${codeCell(o.stock_code, o.name)}</td>
                <td>${esc(o.side)}</td>
                <td>${fmtNum(o.quantity)}</td>
                <td>${o.limit_price?.toFixed(2) ?? '—'}</td>
                <td>${o.priority ?? '—'}</td>
                <td>${scoreCell(o.score, o.score_rank)}</td>
                <td style="text-align:left">${fillCell}</td></tr>`;
        }
        html += '</tbody></table>';
    }

    if (fills.length) {
        html += '<h3 style="margin:16px 0 8px">成交回执明细</h3>';
        html += `<table><thead><tr><th>订单号</th><th>代码 / 名称</th><th>方向</th>
            <th>状态</th><th>委托量</th><th>成交量</th><th>均价</th><th>费用</th><th>信息</th>
            </tr></thead><tbody>`;
        for (const f of fills) {
            const stCls = ['REJECTED', 'ERROR'].includes(f.status) ? 'badge-crit'
                : (f.status === 'FILLED' ? 'badge-ok' : 'badge-warn');
            html += `<tr><td>${esc(f.client_order_id)}</td>
                <td style="text-align:left">${codeCell(f.stock_code, f.name)}</td>
                <td>${esc(f.side)}</td>
                <td><span class="badge ${stCls}">${esc(f.status)}</span></td>
                <td>${fmtNum(f.requested_qty)}</td><td>${fmtNum(f.filled_qty)}</td>
                <td>${f.avg_price?.toFixed(2) ?? '—'}</td>
                <td>${f.applied_fee ? f.applied_fee.toFixed(2) : '—'}</td>
                <td style="text-align:left">${esc(f.message || '')}</td></tr>`;
        }
        html += '</tbody></table>';
    }

    row.children[0].innerHTML = html;
    row.style.display = '';
}

/* ---------- 预测信号 ---------- */

let predInstruments = [];
const predInstrumentResource = MonitorRuntime.createLazyResource(
    () => api('/predictions/instruments'));
let predPage = 0;
let predSortBy = 'rank';
let predSortOrder = 'asc';
let predToken = null;
let predSummaryGeneration = 0;
let predChartGeneration = 0;
let predSearchGeneration = 0;
const PRED_PAGE_SIZE = 50;

function predDisplayCode(s) {
    return s.stock_code || s.instrument || '';
}

async function renderPredictions(token) {
    return MonitorRuntime.loadPredictionPage({
        loadDates: () => api('/predictions/dates'),
        isCurrent: () => navigationTracker.isCurrent(token),
        renderShell: dates => renderPredictionShell(dates, token),
        loadPrimary: () => Promise.all([
            loadPredSummary(token),
            loadPredMeanChart(token),
            predSearch(0, token),
        ]),
        instrumentResource: predInstrumentResource,
        acceptInstruments: rows => { predInstruments = rows || []; },
        rejectInstruments: () => { predInstruments = []; },
    });
}

function renderPredictionShell(dates, token) {
    predPage = 0;
    predSortBy = 'rank';
    predSortOrder = 'asc';
    predToken = token;

    let html = '<h2>预测信号</h2>';
    if (!dates.length) {
        content.innerHTML = html + `<div class="empty">暂无预测数据。
            新发布的批次会自动落库；历史数据可运行
            backfill_predictions.py 回填</div>`;
        return false;
    }

    const dateOptions = dates.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join('');
    html += `<div class="filters">
        <label>信号日期</label>
        <select id="pred-date">${dateOptions}</select>
        <label>标的（代码或名称）</label>
        <div class="autocomplete-wrap">
            <input type="text" id="pred-query" placeholder="如 600000.SH / 浦发银行" autocomplete="off">
            <div class="autocomplete-list" id="pred-query-ac"></div>
        </div>
        <button id="pred-search-btn">查询</button>
        <button id="pred-reset-btn" class="btn-muted">重置</button>
    </div>`;

    html += '<div id="pred-summary"></div>';
    html += `<h3>每日预测信号均值
        <span class="card-sub" id="pred-mean-sub">全市场</span></h3>
        <div class="chart" id="pred-mean-chart" style="height:300px"></div>`;
    html += '<h3>预测明细</h3>';
    html += '<div id="pred-info" class="card-sub" style="margin-bottom:8px"></div>';
    html += '<div id="pred-table"><div class="loading">加载中...</div></div>';
    html += '<div id="pred-pagination" style="margin-top:10px"></div>';
    content.innerHTML = html;

    setupPredAutocomplete(token);
    document.getElementById('pred-search-btn').onclick = () => {
        predSearch(0, token);
        loadPredMeanChart(token);
    };
    document.getElementById('pred-reset-btn').onclick = () => {
        document.getElementById('pred-query').value = '';
        predSearch(0, token);
        loadPredMeanChart(token);
    };
    document.getElementById('pred-date').onchange = () => {
        loadPredSummary(token);
        predSearch(0, token);
    };
    document.getElementById('pred-query').addEventListener('keydown', ev => {
        if (ev.key === 'Enter') {
            predSearch(0, token);
            loadPredMeanChart(token);
        }
    });

    return true;
}

function setupPredAutocomplete(token) {
    const input = document.getElementById('pred-query');
    const list = document.getElementById('pred-query-ac');
    let timer;
    input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
            const q = input.value.trim().toLowerCase();
            if (!q) { list.innerHTML = ''; list.style.display = 'none'; return; }
            const matches = predInstruments.filter(s =>
                (s.instrument && s.instrument.toLowerCase().includes(q)) ||
                (s.stock_code && s.stock_code.toLowerCase().includes(q)) ||
                (s.name && s.name.toLowerCase().includes(q))
            ).slice(0, 10);
            if (!matches.length) { list.innerHTML = ''; list.style.display = 'none'; return; }
            list.innerHTML = matches.map(s =>
                `<div class="ac-item" data-code="${esc(predDisplayCode(s))}">
                    ${esc(predDisplayCode(s))} <span class="card-sub">${esc(s.name || '')}</span></div>`
            ).join('');
            list.style.display = 'block';
            list.querySelectorAll('.ac-item').forEach(item => {
                item.addEventListener('mousedown', ev => {
                    ev.preventDefault();
                    input.value = item.dataset.code;
                    list.innerHTML = '';
                    list.style.display = 'none';
                    predSearch(0, token);
                    loadPredMeanChart(token);
                });
            });
        }, 150);
    });
    input.addEventListener('blur', () => {
        setTimeout(() => { list.innerHTML = ''; list.style.display = 'none'; }, 200);
    });
}

function predQueryParams() {
    const q = (document.getElementById('pred-query')?.value || '').trim();
    if (!q) return {};
    // 含中日韩字符按名称查，否则按代码查
    return /[\u4e00-\u9fff]/.test(q) ? { name: q } : { instrument: q };
}

async function loadPredSummary(token) {
    if (!navigationTracker.isCurrent(token)) return;
    const generation = ++predSummaryGeneration;
    const date = document.getElementById('pred-date').value;
    const s = await api(`/predictions/summary?date=${encodeURIComponent(date)}&n=3`);
    if (!navigationTracker.isCurrent(token)
            || generation !== predSummaryGeneration) return;
    const box = document.getElementById('pred-summary');
    const item = (p, cls) => `<tr>
        <td>#${p.rank}</td>
        <td style="text-align:left">${codeCell(predDisplayCode(p), p.name)}</td>
        <td class="${cls}">${Number(p.score).toFixed(4)}</td></tr>`;
    box.innerHTML = `<div class="card-grid">
        <div class="card"><div class="card-label">信号日 ${esc(s.date || '—')}</div>
            <div class="card-value">${s.mean_score != null ? Number(s.mean_score).toFixed(4) : '—'}</div>
            <div class="card-sub">全市场均值 · ${fmtNum(s.count)} 只</div></div>
        <div class="card"><div class="card-label">Top 3</div>
            <table class="mini-table"><tbody>${(s.top || []).map(p => item(p, 'pos')).join('')}</tbody></table></div>
        <div class="card"><div class="card-label">Bottom 3</div>
            <table class="mini-table"><tbody>${(s.bottom || []).map(p => item(p, 'neg')).join('')}</tbody></table></div>
    </div>`;
}

function resolvePredInstrument(q) {
    /* 输入（QMT/qlib 代码或中文名）→ 唯一标的；解析失败返回 null。 */
    if (!q) return null;
    const lower = q.toLowerCase();
    return predInstruments.find(s =>
            (s.stock_code && s.stock_code.toLowerCase() === lower) ||
            (s.instrument && s.instrument.toLowerCase() === lower) ||
            s.name === q)
        || predInstruments.find(s =>
            (s.stock_code && s.stock_code.toLowerCase().includes(lower)) ||
            (s.instrument && s.instrument.toLowerCase().includes(lower)) ||
            (s.name && s.name.includes(q)))
        || null;
}

function isCompletePredInstrument(q) {
    return /^(?:[A-Za-z]{2}\d{6}|\d{6}\.[A-Za-z]{2})$/.test(q);
}

async function loadPredMeanChart(token) {
    if (!navigationTracker.isCurrent(token)) return;
    const generation = ++predChartGeneration;
    const q = (document.getElementById('pred-query')?.value || '').trim();
    const hit = resolvePredInstrument(q);
    const stockInstrument = hit
        ? (hit.instrument || hit.stock_code)
        : (isCompletePredInstrument(q) ? q : null);

    const meanReq = api('/predictions/daily-mean');
    const stockReq = stockInstrument
        ? api(`/predictions/daily-mean?instruments=${encodeURIComponent(stockInstrument)}`)
        : Promise.resolve(null);
    const [meanData, stockData] = await Promise.all([meanReq, stockReq]);
    if (!navigationTracker.isCurrent(token)
            || generation !== predChartGeneration) return;

    const stockLabel = hit
        ? `${predDisplayCode(hit)}${hit.name ? ' ' + hit.name : ''}`
        : (stockInstrument || '');
    const subEl = document.getElementById('pred-mean-sub');
    if (subEl) subEl.textContent = stockInstrument
        ? `全市场均值 vs ${stockLabel}` : '全市场';

    const el = document.getElementById('pred-mean-chart');
    if (!el) return;
    if (!meanData.length) {
        chartManager.clear();
        el.innerHTML = '<div class="loading">暂无数据</div>';
        return;
    }
    el.innerHTML = '';

    const dates = meanData.map(d => d.date);
    const series = [{
        name: '全市场均值', type: 'line', smooth: true,
        showSymbol: dates.length < 30,
        data: meanData.map(d => d.mean_score),
        lineStyle: { width: 2 },
    }];
    if (stockInstrument && stockData) {
        const byDate = Object.fromEntries(
            stockData.map(d => [d.date, d.mean_score]));
        series.push({
            name: stockLabel, type: 'line', smooth: true,
            showSymbol: dates.length < 30, connectNulls: true,
            data: dates.map(d => byDate[d] ?? null),
            lineStyle: { width: 2 },
        });
    }

    chartManager.clear();
    const chart = echarts.init(el, 'dark');
    chart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'axis',
            valueFormatter: v => v == null ? '—' : Number(v).toFixed(6),
        },
        legend: series.length > 1
            ? { data: series.map(s => s.name), top: 0 } : undefined,
        grid: { left: 70, right: 20, top: series.length > 1 ? 36 : 20, bottom: 30 },
        xAxis: { type: 'category', data: dates },
        yAxis: { type: 'value', scale: true,
                 axisLabel: { formatter: v => Number(v).toFixed(4) } },
        series,
    });
    chartManager.replace(chart);
}

window.predSearch = async function (page, token = predToken) {
    if (!navigationTracker.isCurrent(token)) return;
    const generation = ++predSearchGeneration;
    const requestPage = page || 0;
    const sortBy = predSortBy;
    const sortOrder = predSortOrder;
    predPage = requestPage;
    const date = document.getElementById('pred-date').value;
    const params = new URLSearchParams({
        limit: PRED_PAGE_SIZE,
        offset: requestPage * PRED_PAGE_SIZE,
        sort_by: sortBy,
        sort_order: sortOrder,
    });
    if (date) params.set('date', date);
    const extra = predQueryParams();
    if (extra.instrument) params.set('instrument', extra.instrument);
    if (extra.name) params.set('name', extra.name);

    const result = await api('/predictions?' + params.toString());
    if (!navigationTracker.isCurrent(token)
            || generation !== predSearchGeneration) return;
    const rows = result.data || [];
    const total = result.total || 0;
    const offset = requestPage * PRED_PAGE_SIZE;

    document.getElementById('pred-info').textContent = total
        ? `共 ${total} 条，显示 ${offset + 1} - ${Math.min(offset + PRED_PAGE_SIZE, total)}`
        : '';

    const sortIcon = col => sortBy !== col ? ''
        : (sortOrder === 'asc' ? ' ▲' : ' ▼');

    let html;
    if (!rows.length) {
        html = '<div class="empty">无匹配的预测记录</div>';
    } else {
        html = `<table><thead><tr>
            <th class="sortable" data-col="rank">排名${sortIcon('rank')}</th>
            <th class="sortable" data-col="instrument">代码 / 名称${sortIcon('instrument')}</th>
            <th>信号日期</th>
            <th class="sortable" data-col="score">预测分数${sortIcon('score')}</th>
            </tr></thead><tbody>`;
        for (const p of rows) {
            html += `<tr><td>#${p.rank}</td>
                <td style="text-align:left">${codeCell(predDisplayCode(p), p.name)}</td>
                <td>${esc(p.date)}</td>
                <td>${Number(p.score).toFixed(6)}</td></tr>`;
        }
        html += '</tbody></table>';
    }
    document.getElementById('pred-table').innerHTML = html;

    document.querySelectorAll('#pred-table .sortable').forEach(th => {
        th.style.cursor = 'pointer';
        th.onclick = () => {
            const col = th.dataset.col;
            if (predSortBy === col) {
                predSortOrder = predSortOrder === 'asc' ? 'desc' : 'asc';
            } else {
                predSortBy = col;
                predSortOrder = col === 'score' ? 'desc' : 'asc';
            }
            predSearch(0, token);
        };
    });

    const totalPages = Math.ceil(total / PRED_PAGE_SIZE);
    let pag = '';
    if (totalPages > 1) {
        if (requestPage > 0) pag += `<button onclick="predSearch(${requestPage - 1})">上一页</button> `;
        pag += `<span class="card-sub">第 ${requestPage + 1} / ${totalPages} 页</span>`;
        if (requestPage < totalPages - 1) pag += ` <button onclick="predSearch(${requestPage + 1})">下一页</button>`;
    }
    document.getElementById('pred-pagination').innerHTML = pag;
};

/* ---------- 路由 ---------- */

const PAGES = {
    dashboard: renderDashboard,
    batches: renderBatches,
    predictions: renderPredictions,
};

async function navigate(page) {
    const token = navigationTracker.begin(page);
    chartManager.clear();
    document.querySelectorAll('.sidebar nav a').forEach(a =>
        a.classList.toggle('active', a.dataset.page === page));
    content.innerHTML = '<div class="loading">加载中...</div>';
    try {
        await PAGES[page](token);
    } catch (e) {
        if (navigationTracker.isCurrent(token)) {
            chartManager.clear();
            content.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
        }
    }
}

document.querySelectorAll('.sidebar nav a').forEach(a => {
    a.onclick = (ev) => { ev.preventDefault(); navigate(a.dataset.page); };
});

navigate('dashboard');
