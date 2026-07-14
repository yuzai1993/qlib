/* 实盘监控仪表盘 SPA */

const content = document.getElementById('content');
let currentPage = 'dashboard';
let refreshTimer = null;

async function api(path) {
    const resp = await fetch('/api' + path);
    if (!resp.ok) throw new Error(`${path}: HTTP ${resp.status}`);
    return resp.json();
}

/* ---------- 格式化 ---------- */

const fmtMoney = v => v == null ? '—' :
    v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = v => v == null ? '—' : `${(v * 100).toFixed(2)}%`;
const pctClass = v => v == null ? '' : (v >= 0 ? 'pos' : 'neg');
const fmtNum = v => v == null ? '—' : v.toLocaleString('zh-CN');
const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function modeBadge(mode) {
    return mode === 'LIVE'
        ? '<span class="badge badge-live">LIVE</span>'
        : '<span class="badge badge-sim">SIM</span>';
}

function levelBadge(level) {
    const cls = level === 'CRIT' ? 'badge-crit' : 'badge-warn';
    return `<span class="badge ${cls}">${esc(level)}</span>`;
}

/* ---------- 概览 ---------- */

const STAGE_NAMES = { postmarket: '盘后对账', report: '快照日报', evening: '信号发布' };

async function renderDashboard() {
    const [ov, nav] = await Promise.all([api('/overview'), api('/nav')]);
    const s = ov.snapshot;

    document.getElementById('strategy-badge').textContent =
        `${ov.strategy_id} · ${ov.mode}`;

    let html = `<h2>概览 <span class="card-sub">快照日 ${s ? esc(s.date) : '—'}</span></h2>`;

    html += '<div class="stage-lights">';
    for (const st of ['postmarket', 'report', 'evening']) {
        const e = ov.stages[st];
        const status = e ? e.status : 'NONE';
        const tip = e ? `${e.message || 'OK'} @ ${e.at}` : '今日未运行';
        html += `<div class="stage-light" title="${esc(tip)}">
            <span class="dot dot-${status}"></span>${STAGE_NAMES[st]}
            <span class="card-sub">${e ? esc(e.status) : '未运行'}</span></div>`;
    }
    html += '</div>';

    if (!s) {
        html += '<div class="empty">暂无快照数据。首次运行：run_monitor.py --stage report</div>';
        content.innerHTML = html;
        return;
    }

    html += `<div class="card-grid">
        <div class="card"><div class="card-label">总资产</div>
            <div class="card-value">${fmtMoney(s.total_value)}</div>
            <div class="card-sub">现金 ${fmtMoney(s.cash)} · 应收 ${fmtMoney(s.receivables ?? 0)}<br>
            待上市 ${fmtMoney(s.pending_market_value ?? 0)} · 红利税准备 ${fmtMoney(s.tax_provision ?? 0)}</div></div>
        <div class="card"><div class="card-label">日收益</div>
            <div class="card-value ${pctClass(s.daily_return)}">${fmtPct(s.daily_return)}</div></div>
        <div class="card"><div class="card-label">累计收益</div>
            <div class="card-value ${pctClass(s.cumulative_return)}">${fmtPct(s.cumulative_return)}</div>
            <div class="card-sub">基准 ${fmtPct(s.benchmark_cumulative_return)}</div></div>
        <div class="card"><div class="card-label">当日超额</div>
            <div class="card-value ${pctClass(s.excess_return)}">${fmtPct(s.excess_return)}</div></div>
        <div class="card"><div class="card-label">持仓 / 换手</div>
            <div class="card-value">${s.position_count} 只</div>
            <div class="card-sub">换手 ${fmtPct(s.turnover)} · 费用 ${fmtMoney(s.fees ?? 0)}</div></div>
    </div>`;

    html += '<div class="chart" id="nav-chart"></div>';

    if (ov.recent_alerts.length) {
        html += '<h3>最近告警</h3><table><tbody>';
        for (const a of ov.recent_alerts.slice(0, 3)) {
            html += `<tr><td>${esc(a.trade_date)}</td><td>${levelBadge(a.level)}</td>
                <td style="text-align:left">${esc(a.rule)}: ${esc(a.message)}</td></tr>`;
        }
        html += '</tbody></table>';
    }

    content.innerHTML = html;
    drawNavChart(nav);
}

function drawNavChart(nav) {
    const el = document.getElementById('nav-chart');
    if (!el || !nav.length) return;
    const chart = echarts.init(el, 'dark');
    const dates = nav.map(r => r.date);
    const acct = nav.map(r => ((r.cumulative_return ?? 0) * 100).toFixed(3));
    const bench = nav.map(r => r.benchmark_cumulative_return == null ? null :
        (r.benchmark_cumulative_return * 100).toFixed(3));
    chart.setOption({
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', valueFormatter: v => v == null ? '—' : v + '%' },
        legend: { data: ['账户', '沪深300'] },
        grid: { left: 50, right: 20, top: 40, bottom: 30 },
        xAxis: { type: 'category', data: dates },
        yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
        series: [
            { name: '账户', type: 'line', data: acct, showSymbol: false,
              lineStyle: { width: 2 } },
            { name: '沪深300', type: 'line', data: bench, showSymbol: false,
              lineStyle: { width: 1.5, type: 'dashed' } },
        ],
    });
    window.addEventListener('resize', () => chart.resize(), { once: true });
}

/* ---------- 持仓 ---------- */

async function renderPositions() {
    const data = await api('/positions');
    let html = '<h2>持仓</h2>';
    html += `<div>回看日期：<select id="pos-date"><option value="">当前（最新快照）</option></select></div>`;
    html += '<div id="pos-table">' + positionsTable(data.positions, data.cash) + '</div>';
    content.innerHTML = html;

    const hist = await api('/positions/history?date=' +
        (data.positions[0]?.snapshot_date || ''));
    const sel = document.getElementById('pos-date');
    for (const d of hist.dates) {
        const opt = document.createElement('option');
        opt.value = d; opt.textContent = d;
        sel.appendChild(opt);
    }
    sel.onchange = async () => {
        const box = document.getElementById('pos-table');
        if (!sel.value) {
            const cur = await api('/positions');
            box.innerHTML = positionsTable(cur.positions, cur.cash);
            return;
        }
        const h = await api('/positions/history?date=' + sel.value);
        box.innerHTML = positionsTable(h.positions, null);
    };
}

function codeCell(code, name) {
    if (!name) return esc(code);
    return `${esc(code)} <span class="card-sub">${esc(name)}</span>`;
}

function positionsTable(rows, cash) {
    if (!rows.length) return '<div class="empty">无持仓</div>';
    let html = `<table><thead><tr>
        <th>代码 / 名称</th><th>数量</th><th>成本</th><th>现价</th>
        <th>市值</th><th>盈亏</th><th>权重</th></tr></thead><tbody>`;
    for (const p of rows) {
        html += `<tr><td style="text-align:left">${codeCell(p.stock_code, p.name)}</td>
            <td>${fmtNum(p.shares)}</td>
            <td>${p.avg_cost?.toFixed(2) ?? '—'}</td>
            <td>${p.close_price?.toFixed(2) ?? '—'}</td>
            <td>${fmtMoney(p.market_value)}</td>
            <td class="${pctClass(p.profit)}">${fmtMoney(p.profit)}</td>
            <td>${fmtPct(p.weight)}</td></tr>`;
    }
    html += '</tbody></table>';
    if (cash != null) html += `<h3>现金 ${fmtMoney(cash)}</h3>`;
    return html;
}

/* ---------- 批次与成交 ---------- */

async function renderBatches() {
    const batches = await api('/batches');
    let html = '<h2>批次与成交</h2>';
    if (!batches.length) {
        content.innerHTML = html + '<div class="empty">暂无批次</div>';
        return;
    }
    html += `<table><thead><tr>
        <th>批次</th><th>交易日</th><th>模式</th>
        <th>计划</th><th>终态</th><th>缺失</th><th>发布于</th></tr></thead><tbody>`;
    for (const b of batches) {
        const missCls = b.missing > 0 ? 'neg' : 'pos';
        html += `<tr class="clickable" data-batch="${esc(b.batch_id)}">
            <td>${esc(b.batch_id)}</td><td>${esc(b.trade_date)}</td>
            <td>${modeBadge(b.mode)}</td>
            <td>${b.planned}</td><td>${b.terminal}</td>
            <td class="${missCls}">${b.missing}</td>
            <td>${esc(b.created_at || '')}</td></tr>`;
        html += `<tr class="fills-row" id="fills-${esc(b.batch_id)}" style="display:none">
            <td colspan="7"></td></tr>`;
    }
    html += '</tbody></table>';
    content.innerHTML = html;

    content.querySelectorAll('tr.clickable').forEach(tr => {
        tr.onclick = () => toggleBatchDetail(tr.dataset.batch);
    });
}

async function toggleBatchDetail(batchId) {
    const row = document.getElementById('fills-' + batchId);
    if (row.style.display !== 'none') { row.style.display = 'none'; return; }
    const detail = await api(`/batches/${batchId}`);
    const orders = detail.orders || [];
    const fills = detail.fills || [];
    const fillById = Object.fromEntries(fills.map(f => [f.client_order_id, f]));

    let html = '<h3 style="margin:8px 0">执行计划</h3>';
    if (!orders.length) {
        html += '<div class="empty" style="padding:16px">无执行计划（历史批次未入库；新发布的批次会自动保存）</div>';
    } else {
        html += `<table><thead><tr>
            <th>订单号</th><th>代码 / 名称</th><th>方向</th><th>数量</th>
            <th>限价</th><th>优先级</th><th>回执</th></tr></thead><tbody>`;
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

/* ---------- 资金流水 ---------- */

const FLOW_NAMES = {
    DEPOSIT: '入金', WITHDRAW: '出金', CORRECTION: '校正',
    DIVIDEND: '分红派息', DIVIDEND_TAX: '红利税', BONUS_SHARES: '送转股',
};

async function renderCashflows() {
    const data = await api('/cashflows?limit=200');
    let html = `<h2>资金流水 <span class="card-sub">当前现金 ${fmtMoney(data.cash)}</span></h2>`;
    if (!data.flows.length) {
        content.innerHTML = html + `<div class="empty">暂无资金流水。
            出入金/校正用 record_cash_flow.py 记录；分红送股由 report 阶段自动入账</div>`;
        return;
    }
    html += `<table><thead><tr><th>交易日</th><th>类型</th><th>金额</th>
        <th>关联股票</th><th>备注</th><th>记录时间</th></tr></thead><tbody>`;
    for (const r of data.flows) {
        const amtCls = r.amount > 0 ? 'pos' : (r.amount < 0 ? 'neg' : '');
        html += `<tr><td>${esc(r.trade_date)}</td>
            <td>${esc(FLOW_NAMES[r.flow_type] || r.flow_type)}</td>
            <td class="${amtCls}">${fmtMoney(r.amount)}</td>
            <td>${r.stock_code ? codeCell(r.stock_code, r.name) : '—'}</td>
            <td style="text-align:left">${esc(r.note || '')}</td>
            <td>${esc(r.created_at)}</td></tr>`;
    }
    html += '</tbody></table>';
    content.innerHTML = html;
}

/* ---------- 流程健康 ---------- */

async function renderPipeline() {
    const data = await api('/pipeline?days=14');
    const dates = Object.keys(data.days).sort().reverse();
    let html = '<h2>流程健康 <span class="card-sub">绿=OK 黄=WARN 红=FAIL</span></h2>';
    if (!dates.length) {
        content.innerHTML = html + '<div class="empty">暂无流程事件</div>';
        return;
    }
    const stages = data.stages;
    html += `<div class="matrix" style="grid-template-columns: 110px repeat(${stages.length}, 1fr)">`;
    html += '<div class="matrix-cell matrix-head">日期</div>';
    for (const st of stages)
        html += `<div class="matrix-cell matrix-head">${STAGE_NAMES[st] || esc(st)}</div>`;
    for (const d of dates) {
        html += `<div class="matrix-cell matrix-head">${esc(d)}</div>`;
        for (const st of stages) {
            const e = data.days[d][st];
            const cls = e ? `cell-${e.status}` : '';
            const tip = e ? `${e.message || 'OK'} @ ${e.at}` : '未运行';
            html += `<div class="matrix-cell ${cls}" title="${esc(tip)}">${e ? esc(e.status) : '—'}</div>`;
        }
    }
    html += '</div>';
    content.innerHTML = html;
}

/* ---------- 告警 ---------- */

async function renderAlerts() {
    const alerts = await api('/alerts?limit=100');
    let html = '<h2>告警历史</h2>';
    if (!alerts.length) {
        content.innerHTML = html + '<div class="empty">暂无告警，一切正常</div>';
        return;
    }
    html += `<table><thead><tr><th>交易日</th><th>级别</th><th>规则</th>
        <th>内容</th><th>推送</th><th>时间</th></tr></thead><tbody>`;
    for (const a of alerts) {
        html += `<tr><td>${esc(a.trade_date)}</td><td>${levelBadge(a.level)}</td>
            <td>${esc(a.rule)}</td>
            <td style="text-align:left">${esc(a.message)}</td>
            <td>${a.sent_ok ? `已推(${esc(a.channel || '')})` : '未推'}</td>
            <td>${esc(a.created_at)}</td></tr>`;
    }
    html += '</tbody></table>';
    content.innerHTML = html;
}

/* ---------- 路由 ---------- */

const PAGES = {
    dashboard: renderDashboard,
    positions: renderPositions,
    batches: renderBatches,
    cashflows: renderCashflows,
    pipeline: renderPipeline,
    alerts: renderAlerts,
};

async function navigate(page) {
    currentPage = page;
    document.querySelectorAll('.sidebar nav a').forEach(a =>
        a.classList.toggle('active', a.dataset.page === page));
    content.innerHTML = '<div class="loading">加载中...</div>';
    try {
        await PAGES[page]();
    } catch (e) {
        content.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
    }
}

document.querySelectorAll('.sidebar nav a').forEach(a => {
    a.onclick = (ev) => { ev.preventDefault(); navigate(a.dataset.page); };
});

function scheduleRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => {
        if (currentPage === 'dashboard') navigate('dashboard');
    }, 60000);
}

navigate('dashboard');
scheduleRefresh();
