/* Paper Trading Dashboard - Single Page Application */

const API = '/api';
let currentPage = 'dashboard';
let currentInstance = '';
let chartInstances = {};

// ==================== Utility ====================

function instanceParam() {
    return currentInstance ? `instance=${encodeURIComponent(currentInstance)}` : '';
}

async function fetchJSON(url) {
    const sep = url.includes('?') ? '&' : '?';
    const fullUrl = currentInstance ? `${url}${sep}${instanceParam()}` : url;
    const resp = await fetch(fullUrl);
    return resp.json();
}

function fmt(val, type) {
    if (val == null || val === undefined) return '—';
    switch (type) {
        case 'money': return '¥' + Number(val).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        case 'pct': return (val * 100).toFixed(2) + '%';
        case 'pct_sign': return (val >= 0 ? '+' : '') + (val * 100).toFixed(2) + '%';
        case 'price': return Number(val).toFixed(2);
        case 'int': return Math.round(val).toLocaleString();
        case 'ratio': return Number(val).toFixed(3);
        default: return String(val);
    }
}

function colorClass(val) {
    if (val > 0) return 'positive';
    if (val < 0) return 'negative';
    return '';
}

function disposeCharts() {
    Object.values(chartInstances).forEach(c => { try { c.dispose(); } catch(e) {} });
    chartInstances = {};
}

// ==================== Instance Selector ====================

async function loadInstances() {
    try {
        const resp = await fetch(`${API}/instances`);
        const instances = await resp.json();
        const sel = document.getElementById('instance-selector');
        if (!instances.length) {
            sel.innerHTML = '';
            return;
        }
        if (instances.length === 1) {
            currentInstance = instances[0].id;
            sel.innerHTML = `<div class="instance-name">${instances[0].name}</div>`;
            return;
        }
        const options = instances.map(i =>
            `<option value="${i.id}" ${i.id === currentInstance ? 'selected' : ''}>${i.name}</option>`
        ).join('');
        sel.innerHTML = `<select id="instance-select" onchange="switchInstance(this.value)">${options}</select>`;
        if (!currentInstance) currentInstance = instances[0].id;
    } catch (e) {
        console.warn('Failed to load instances', e);
    }
}

window.switchInstance = function(id) {
    currentInstance = id;
    disposeCharts();
    renderPage(currentPage);
};

// ==================== Navigation ====================

document.querySelectorAll('.sidebar nav a').forEach(link => {
    link.addEventListener('click', e => {
        e.preventDefault();
        const page = link.dataset.page;
        if (page === currentPage) return;
        document.querySelectorAll('.sidebar nav a').forEach(a => a.classList.remove('active'));
        link.classList.add('active');
        currentPage = page;
        disposeCharts();
        renderPage(page);
    });
});

async function renderPage(page) {
    const el = document.getElementById('content');
    el.innerHTML = '<div class="loading">加载中...</div>';
    switch (page) {
        case 'dashboard': return renderDashboard(el);
        case 'positions': return renderPositions(el);
        case 'orders': return renderOrders(el);
        case 'stockpnl': return renderStockPnl(el);
        case 'predictions': return renderPredictions(el);
        case 'performance': return renderPerformance(el);
        case 'system': return renderSystem(el);
    }
}

// ==================== Dashboard Page ====================

async function renderDashboard(el) {
    const [overview, summaries] = await Promise.all([
        fetchJSON(`${API}/overview`),
        fetchJSON(`${API}/account/summary`),
    ]);

    if (overview.error) {
        el.innerHTML = '<div class="loading">暂无数据，请先执行 init 和 run</div>';
        return;
    }

    const s = overview.summary;
    const perf = overview.performance || {};

    el.innerHTML = `
        <div class="page-header"><h2>账户概览</h2></div>
        <div class="card-grid">
            <div class="card">
                <div class="card-label">账户总资产</div>
                <div class="card-value">${fmt(s.total_value, 'money')}</div>
            </div>
            <div class="card">
                <div class="card-label">当日盈亏</div>
                <div class="card-value ${colorClass(s.daily_return)}">${fmt(s.daily_return, 'pct_sign')}</div>
            </div>
            <div class="card">
                <div class="card-label">累计收益率</div>
                <div class="card-value ${colorClass(s.cumulative_return)}">${fmt(s.cumulative_return, 'pct_sign')}</div>
            </div>
            <div class="card">
                <div class="card-label">现金余额</div>
                <div class="card-value">${fmt(s.cash, 'money')}</div>
            </div>
            <div class="card">
                <div class="card-label">持仓数量</div>
                <div class="card-value">${s.position_count || 0}</div>
            </div>
            <div class="card">
                <div class="card-label">最大回撤</div>
                <div class="card-value negative">${fmt(perf.max_drawdown, 'pct')}</div>
            </div>
            <div class="card">
                <div class="card-label">夏普比率</div>
                <div class="card-value">${fmt(perf.sharpe_ratio, 'ratio')}</div>
            </div>
            <div class="card">
                <div class="card-label">超额收益</div>
                <div class="card-value ${colorClass(s.excess_return)}">${fmt(s.excess_return, 'pct_sign')}</div>
            </div>
            <div class="card">
                <div class="card-label">运行天数</div>
                <div class="card-value">${overview.trading_days || 0}</div>
            </div>
        </div>

        <div class="chart-container">
            <h3>净值曲线 vs 基准</h3>
            <div class="chart-box" id="chart-nav"></div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
            <div class="chart-container">
                <h3>超额收益曲线</h3>
                <div class="chart-box" id="chart-excess"></div>
            </div>
            <div class="chart-container">
                <h3>每日盈亏</h3>
                <div class="chart-box" id="chart-pnl"></div>
            </div>
        </div>
    `;

    if (summaries.length > 0) {
        renderNavChart(summaries);
        renderExcessChart(summaries);
        renderPnlChart(summaries);
    }
}

function renderNavChart(data) {
    const chart = echarts.init(document.getElementById('chart-nav'));
    chartInstances['nav'] = chart;
    const dates = data.map(d => d.date);
    const navValues = data.map(d => (1 + d.cumulative_return).toFixed(4));
    const benchValues = data.map(d => (1 + d.benchmark_cumulative_return).toFixed(4));
    chart.setOption({
        tooltip: { trigger: 'axis' },
        legend: { data: ['策略净值', '基准净值'], textStyle: { color: '#8b8fa3' }, top: 0 },
        grid: { left: 60, right: 20, top: 40, bottom: 40 },
        xAxis: { type: 'category', data: dates, axisLabel: { color: '#8b8fa3' }, axisLine: { lineStyle: { color: '#2a2e45' }} },
        yAxis: { type: 'value', axisLabel: { color: '#8b8fa3' }, splitLine: { lineStyle: { color: '#2a2e45' } } },
        series: [
            { name: '策略净值', type: 'line', data: navValues, smooth: true, lineStyle: { color: '#6366f1', width: 2 }, itemStyle: { color: '#6366f1' }, showSymbol: false },
            { name: '基准净值', type: 'line', data: benchValues, smooth: true, lineStyle: { color: '#64748b', width: 1.5 }, itemStyle: { color: '#64748b' }, showSymbol: false },
        ]
    });
}

function renderExcessChart(data) {
    const chart = echarts.init(document.getElementById('chart-excess'));
    chartInstances['excess'] = chart;
    chart.setOption({
        tooltip: { trigger: 'axis', formatter: p => p[0].name + '<br/>' + fmt(p[0].value, 'pct_sign') },
        grid: { left: 60, right: 20, top: 20, bottom: 40 },
        xAxis: { type: 'category', data: data.map(d => d.date), axisLabel: { color: '#8b8fa3' }, axisLine: { lineStyle: { color: '#2a2e45' }} },
        yAxis: { type: 'value', axisLabel: { color: '#8b8fa3', formatter: v => (v*100).toFixed(1)+'%' }, splitLine: { lineStyle: { color: '#2a2e45' } } },
        series: [{
            type: 'line', data: data.map(d => d.excess_return), smooth: true, showSymbol: false,
            areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{offset:0,color:'rgba(99,102,241,0.3)'},{offset:1,color:'rgba(99,102,241,0)'}] } },
            lineStyle: { color: '#6366f1', width: 2 }, itemStyle: { color: '#6366f1' },
        }]
    });
}

function renderPnlChart(data) {
    const chart = echarts.init(document.getElementById('chart-pnl'));
    chartInstances['pnl'] = chart;
    chart.setOption({
        tooltip: { trigger: 'axis', formatter: p => p[0].name + '<br/>' + fmt(p[0].value, 'pct_sign') },
        grid: { left: 60, right: 20, top: 20, bottom: 40 },
        xAxis: { type: 'category', data: data.map(d => d.date), axisLabel: { color: '#8b8fa3' }, axisLine: { lineStyle: { color: '#2a2e45' }} },
        yAxis: { type: 'value', axisLabel: { color: '#8b8fa3', formatter: v => (v*100).toFixed(1)+'%' }, splitLine: { lineStyle: { color: '#2a2e45' } } },
        series: [{
            type: 'bar', data: data.map(d => ({
                value: d.daily_return,
                itemStyle: { color: d.daily_return >= 0 ? '#ef4444' : '#22c55e' }
            })),
        }]
    });
}

// ==================== Positions Page ====================

async function renderPositions(el) {
    const [positions, dates, summaryResp] = await Promise.all([
        fetchJSON(`${API}/positions/current`),
        fetchJSON(`${API}/positions/dates`),
        fetchJSON(`${API}/overview`),
    ]);

    const dateOptions = dates.map(d => `<option value="${d}">${d}</option>`).join('');
    const cash = summaryResp.summary ? summaryResp.summary.cash : 0;

    el.innerHTML = `
        <div class="page-header"><h2>持仓管理</h2></div>

        <div class="table-container">
            <div class="filters">
                <label>历史持仓:</label>
                <select id="pos-date">
                    <option value="">当前持仓</option>
                    ${dateOptions}
                </select>
                <button onclick="loadPositions()">查询</button>
            </div>
            <div id="pos-table-body"></div>
        </div>

        <div class="chart-container">
            <h3>持仓分布（含现金）</h3>
            <div class="chart-box" id="chart-pie"></div>
        </div>
    `;

    renderPositionTable(positions, cash);
    renderPieChart(positions, cash);
}

function renderPositionTable(data, cash) {
    const totalMV = data.reduce((s, p) => s + (p.market_value || 0), 0);
    const totalValue = totalMV + (cash || 0);
    const cashWeight = totalValue > 0 ? cash / totalValue : 0;

    const tbody = data.map(p => `
        <tr>
            <td>${p.instrument}</td>
            <td>${p.name || ''}</td>
            <td class="text-right">${fmt(p.shares, 'int')}</td>
            <td class="text-right">${fmt(p.cost_price, 'price')}</td>
            <td class="text-right">${fmt(p.current_price, 'price')}</td>
            <td class="text-right">${fmt(p.market_value, 'money')}</td>
            <td class="text-right ${colorClass(p.profit)}"><b>${fmt(p.profit, 'money')}</b></td>
            <td class="text-right ${colorClass(p.profit_rate)}"><b>${fmt(p.profit_rate, 'pct_sign')}</b></td>
            <td class="text-right">${fmt(p.weight, 'pct')}</td>
            <td class="text-right">${p.holding_days || 0}</td>
        </tr>
    `).join('');

    const cashRow = `
        <tr class="cash-row">
            <td>💰</td>
            <td>现金</td>
            <td class="text-right">—</td>
            <td class="text-right">—</td>
            <td class="text-right">—</td>
            <td class="text-right">${fmt(cash, 'money')}</td>
            <td class="text-right">—</td>
            <td class="text-right">—</td>
            <td class="text-right">${fmt(cashWeight, 'pct')}</td>
            <td class="text-right">—</td>
        </tr>
    `;

    document.getElementById('pos-table-body').innerHTML = `
        <table>
            <thead><tr>
                <th>代码</th><th>名称</th><th class="text-right">股数</th>
                <th class="text-right">成本价</th><th class="text-right">现价</th>
                <th class="text-right">市值</th><th class="text-right">盈亏</th>
                <th class="text-right">盈亏%</th><th class="text-right">占比</th>
                <th class="text-right">持有天数</th>
            </tr></thead>
            <tbody>
                ${tbody || ''}
                ${cashRow}
                ${!tbody && !cash ? '<tr><td colspan="10" style="text-align:center;color:#8b8fa3">暂无持仓</td></tr>' : ''}
            </tbody>
        </table>
    `;
}

function renderPieChart(data, cash) {
    const chart = echarts.init(document.getElementById('chart-pie'));
    chartInstances['pie'] = chart;

    const pieData = data.map(p => ({ name: (p.name || p.instrument), value: p.market_value || 0 }));
    if (cash > 0) {
        pieData.push({ name: '现金', value: cash, itemStyle: { color: '#64748b' } });
    }

    if (!pieData.length) return;

    chart.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: {d}%' },
        series: [{
            type: 'pie', radius: ['40%', '70%'],
            label: { color: '#8b8fa3', fontSize: 12 },
            data: pieData,
        }]
    });
}

window.loadPositions = async function() {
    const date = document.getElementById('pos-date').value;
    const url = date ? `${API}/positions?date=${date}` : `${API}/positions/current`;
    const [data, summaryResp] = await Promise.all([
        fetchJSON(url),
        fetchJSON(`${API}/overview`),
    ]);
    const cash = summaryResp.summary ? summaryResp.summary.cash : 0;
    renderPositionTable(data, cash);
    disposeCharts();
    renderPieChart(data, cash);
};

// ==================== Orders Page ====================

async function renderOrders(el) {
    el.innerHTML = `
        <div class="page-header"><h2>交易记录</h2></div>
        <div class="table-container">
            <div class="filters">
                <label>开始:</label><input type="date" id="ord-start">
                <label>结束:</label><input type="date" id="ord-end">
                <label>方向:</label>
                <select id="ord-dir">
                    <option value="">全部</option>
                    <option value="BUY">买入</option>
                    <option value="SELL">卖出</option>
                </select>
                <button onclick="loadOrders()">查询</button>
            </div>
            <div id="ord-table-body"><div class="loading">加载中...</div></div>
        </div>
    `;
    await loadOrders();
}

window.loadOrders = async function() {
    const start = document.getElementById('ord-start').value;
    const end = document.getElementById('ord-end').value;
    const dir = document.getElementById('ord-dir').value;
    let url = `${API}/orders?`;
    if (start) url += `start=${start}&`;
    if (end) url += `end=${end}&`;
    if (dir) url += `direction=${dir}&`;
    const data = await fetchJSON(url);

    const tbody = data.map(o => `
        <tr>
            <td>${o.date}</td>
            <td>${o.instrument}</td>
            <td>${o.name || ''}</td>
            <td><span class="tag ${o.direction === 'BUY' ? 'tag-buy' : 'tag-sell'}">${o.direction === 'BUY' ? '买入' : '卖出'}</span></td>
            <td class="text-right">${fmt(o.filled_shares, 'int')}</td>
            <td class="text-right">${fmt(o.price, 'price')}</td>
            <td class="text-right">${fmt(o.amount, 'money')}</td>
            <td class="text-right">${fmt(o.commission, 'price')}</td>
            <td><span class="tag ${o.status === 'FILLED' || o.status === 'PARTIAL' ? 'tag-filled' : 'tag-rejected'}">${o.status}</span></td>
            <td>${o.reject_reason || ''}</td>
        </tr>
    `).join('');

    document.getElementById('ord-table-body').innerHTML = `
        <table>
            <thead><tr>
                <th>日期</th><th>代码</th><th>名称</th><th>方向</th>
                <th class="text-right">成交股数</th><th class="text-right">价格</th>
                <th class="text-right">金额</th><th class="text-right">佣金</th>
                <th>状态</th><th>原因</th>
            </tr></thead>
            <tbody>${tbody || '<tr><td colspan="10" style="text-align:center;color:#8b8fa3">暂无交易记录</td></tr>'}</tbody>
        </table>
    `;
};

// ==================== Stock P&L Page ====================

let stockPnlData = [];
let stockPnlSortCol = 'total_pnl';
let stockPnlSortDir = 'desc';

async function renderStockPnl(el) {
    stockPnlData = await fetchJSON(`${API}/stock-pnl`);

    el.innerHTML = `
        <div class="page-header"><h2>个股盈亏</h2></div>
        <div class="card-grid" id="stockpnl-summary"></div>
        <div class="table-container">
            <div id="stockpnl-table-body"><div class="loading">加载中...</div></div>
        </div>
    `;

    renderStockPnlSummary();
    renderStockPnlTable();
}

function renderStockPnlSummary() {
    const data = stockPnlData;
    if (!data.length) return;

    const totalPnl = data.reduce((s, d) => s + (d.total_pnl || 0), 0);
    const totalCommission = data.reduce((s, d) => s + (d.total_commission || 0), 0);
    const totalRealized = data.reduce((s, d) => s + (d.realized_pnl || 0), 0);
    const totalUnrealized = data.reduce((s, d) => s + (d.unrealized_pnl || 0), 0);
    const winners = data.filter(d => d.total_pnl > 0).length;
    const losers = data.filter(d => d.total_pnl < 0).length;

    document.getElementById('stockpnl-summary').innerHTML = `
        <div class="card">
            <div class="card-label">总盈亏</div>
            <div class="card-value ${colorClass(totalPnl)}">${fmt(totalPnl, 'money')}</div>
        </div>
        <div class="card">
            <div class="card-label">已实现盈亏</div>
            <div class="card-value ${colorClass(totalRealized)}">${fmt(totalRealized, 'money')}</div>
        </div>
        <div class="card">
            <div class="card-label">未实现盈亏</div>
            <div class="card-value ${colorClass(totalUnrealized)}">${fmt(totalUnrealized, 'money')}</div>
        </div>
        <div class="card">
            <div class="card-label">总佣金</div>
            <div class="card-value">${fmt(totalCommission, 'money')}</div>
        </div>
        <div class="card">
            <div class="card-label">交易标的数</div>
            <div class="card-value">${data.length}</div>
        </div>
        <div class="card">
            <div class="card-label">盈利 / 亏损</div>
            <div class="card-value"><span class="positive">${winners}</span> / <span class="negative">${losers}</span></div>
        </div>
    `;
}

function renderStockPnlTable() {
    const data = [...stockPnlData];
    data.sort((a, b) => {
        let va = a[stockPnlSortCol] || 0, vb = b[stockPnlSortCol] || 0;
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return stockPnlSortDir === 'asc' ? -1 : 1;
        if (va > vb) return stockPnlSortDir === 'asc' ? 1 : -1;
        return 0;
    });

    function sortIcon(col) {
        if (stockPnlSortCol !== col) return '';
        return stockPnlSortDir === 'asc' ? ' ▲' : ' ▼';
    }

    const tbody = data.map(d => `
        <tr>
            <td>${d.instrument}</td>
            <td>${d.name || ''}</td>
            <td><span class="tag ${d.status === '持有中' ? 'tag-filled' : 'tag-rejected'}">${d.status}</span></td>
            <td class="text-right">${fmt(d.total_buy_amount, 'money')}</td>
            <td class="text-right">${fmt(d.total_sell_amount, 'money')}</td>
            <td class="text-right">${fmt(d.total_commission, 'money')}</td>
            <td class="text-right ${colorClass(d.realized_pnl)}"><b>${fmt(d.realized_pnl, 'money')}</b></td>
            <td class="text-right ${colorClass(d.unrealized_pnl)}"><b>${fmt(d.unrealized_pnl, 'money')}</b></td>
            <td class="text-right ${colorClass(d.total_pnl)}"><b>${fmt(d.total_pnl, 'money')}</b></td>
            <td class="text-right ${colorClass(d.return_rate)}"><b>${fmt(d.return_rate, 'pct_sign')}</b></td>
            <td class="text-right">${d.trade_days || 0}</td>
            <td>${d.first_trade_date || ''}</td>
            <td>${d.last_trade_date || ''}</td>
        </tr>
    `).join('');

    document.getElementById('stockpnl-table-body').innerHTML = `
        <table>
            <thead><tr>
                <th class="sortable" data-col="instrument">代码${sortIcon('instrument')}</th>
                <th>名称</th>
                <th>状态</th>
                <th class="text-right sortable" data-col="total_buy_amount">买入总额${sortIcon('total_buy_amount')}</th>
                <th class="text-right sortable" data-col="total_sell_amount">卖出总额${sortIcon('total_sell_amount')}</th>
                <th class="text-right sortable" data-col="total_commission">佣金${sortIcon('total_commission')}</th>
                <th class="text-right sortable" data-col="realized_pnl">已实现盈亏${sortIcon('realized_pnl')}</th>
                <th class="text-right sortable" data-col="unrealized_pnl">未实现盈亏${sortIcon('unrealized_pnl')}</th>
                <th class="text-right sortable" data-col="total_pnl">总盈亏${sortIcon('total_pnl')}</th>
                <th class="text-right sortable" data-col="return_rate">收益率${sortIcon('return_rate')}</th>
                <th class="text-right sortable" data-col="trade_days">交易天数${sortIcon('trade_days')}</th>
                <th>首次交易</th>
                <th>最近交易</th>
            </tr></thead>
            <tbody>${tbody || '<tr><td colspan="13" style="text-align:center;color:#8b8fa3">暂无交易数据</td></tr>'}</tbody>
        </table>
    `;

    document.querySelectorAll('#stockpnl-table-body .sortable').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.col;
            if (stockPnlSortCol === col) {
                stockPnlSortDir = stockPnlSortDir === 'asc' ? 'desc' : 'asc';
            } else {
                stockPnlSortCol = col;
                stockPnlSortDir = 'desc';
            }
            renderStockPnlTable();
        });
    });
}

// ==================== Predictions Page ====================

let predStockList = [];
let predCurrentPage = 0;
let predSortBy = 'rank';
let predSortOrder = 'asc';
const PRED_PAGE_SIZE = 50;

async function renderPredictions(el) {
    const [datesData, instrumentsData] = await Promise.all([
        fetchJSON(`${API}/predictions/dates`),
        fetchJSON(`${API}/predictions/instruments`),
    ]);

    predStockList = instrumentsData || [];
    predCurrentPage = 0;

    const dateOptions = (datesData || []).map(d => `<option value="${d}">${d}</option>`).join('');

    el.innerHTML = `
        <div class="page-header"><h2>预测信号</h2></div>

        <div class="table-container">
            <div class="filters">
                <label>日期:</label>
                <select id="pred-date">
                    <option value="">最新</option>
                    ${dateOptions}
                </select>
                <label>股票代码:</label>
                <div class="autocomplete-wrap">
                    <input type="text" id="pred-instrument" placeholder="如 SH600000" autocomplete="off">
                    <div class="autocomplete-list" id="pred-instrument-ac"></div>
                </div>
                <label>股票名称:</label>
                <div class="autocomplete-wrap">
                    <input type="text" id="pred-name" placeholder="如 浦发银行" autocomplete="off">
                    <div class="autocomplete-list" id="pred-name-ac"></div>
                </div>
                <button onclick="searchPredictions(0)">查询</button>
            </div>
            <div id="pred-info" style="padding:8px 20px;color:var(--text-muted);font-size:13px;"></div>
            <div id="pred-table-body"><div class="loading">加载中...</div></div>
            <div class="pagination" id="pred-pagination"></div>
        </div>

        <div class="chart-container">
            <h3>天级预测信号均值</h3>
            <div class="filters" style="border:none;padding:8px 0;">
                <label>筛选股票:</label>
                <div class="autocomplete-wrap" style="min-width:300px;">
                    <input type="text" id="mean-instruments" placeholder="输入代码/名称搜索，多只用逗号分隔" autocomplete="off" style="width:100%">
                    <div class="autocomplete-list" id="mean-instruments-ac"></div>
                </div>
                <button onclick="loadDailyMean()">刷新</button>
            </div>
            <div class="chart-box" id="chart-daily-mean"></div>
        </div>
    `;

    setupAutocomplete('pred-instrument', 'pred-instrument-ac', 'instrument');
    setupAutocomplete('pred-name', 'pred-name-ac', 'name');
    setupAutocomplete('mean-instruments', 'mean-instruments-ac', 'both');

    await Promise.all([
        searchPredictions(0),
        loadDailyMean(),
    ]);
}

function setupAutocomplete(inputId, listId, mode) {
    const input = document.getElementById(inputId);
    const list = document.getElementById(listId);
    if (!input || !list) return;

    let debounceTimer;
    input.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            const val = input.value.trim().toLowerCase();
            if (!val || val.length < 1) { list.innerHTML = ''; list.style.display = 'none'; return; }

            const lastPart = val.includes(',') ? val.split(',').pop().trim() : val;
            if (!lastPart) { list.innerHTML = ''; list.style.display = 'none'; return; }

            const matches = predStockList.filter(s => {
                if (mode === 'instrument') return s.instrument && s.instrument.toLowerCase().includes(lastPart);
                if (mode === 'name') return s.name && s.name.toLowerCase().includes(lastPart);
                return (s.instrument && s.instrument.toLowerCase().includes(lastPart)) ||
                       (s.name && s.name.toLowerCase().includes(lastPart));
            }).slice(0, 10);

            if (!matches.length) { list.innerHTML = ''; list.style.display = 'none'; return; }

            list.innerHTML = matches.map(s =>
                `<div class="ac-item" data-instrument="${s.instrument}" data-name="${s.name || ''}">${s.instrument} ${s.name || ''}</div>`
            ).join('');
            list.style.display = 'block';

            list.querySelectorAll('.ac-item').forEach(item => {
                item.addEventListener('mousedown', e => {
                    e.preventDefault();
                    const selected = mode === 'name' ? item.dataset.name : item.dataset.instrument;
                    if (val.includes(',')) {
                        const parts = input.value.split(',');
                        parts[parts.length - 1] = selected;
                        input.value = parts.join(',') + ',';
                    } else {
                        input.value = selected;
                    }
                    list.innerHTML = '';
                    list.style.display = 'none';
                });
            });
        }, 200);
    });

    input.addEventListener('blur', () => {
        setTimeout(() => { list.innerHTML = ''; list.style.display = 'none'; }, 200);
    });
}

window.searchPredictions = async function(page) {
    predCurrentPage = page || 0;
    const date = document.getElementById('pred-date').value;
    const instrument = document.getElementById('pred-instrument').value.trim();
    const name = document.getElementById('pred-name').value.trim();
    const offset = predCurrentPage * PRED_PAGE_SIZE;

    let url = `${API}/predictions?limit=${PRED_PAGE_SIZE}&offset=${offset}`;
    url += `&sort_by=${predSortBy}&sort_order=${predSortOrder}`;
    if (date) url += `&date=${date}`;
    if (instrument) url += `&instrument=${encodeURIComponent(instrument)}`;
    if (name) url += `&name=${encodeURIComponent(name)}`;

    const result = await fetchJSON(url);
    const data = result.data || [];
    const total = result.total || 0;

    document.getElementById('pred-info').textContent =
        `共 ${total} 条记录，显示第 ${offset + 1} - ${Math.min(offset + PRED_PAGE_SIZE, total)} 条`;

    function predSortIcon(col) {
        if (predSortBy !== col) return '';
        return predSortOrder === 'asc' ? ' ▲' : ' ▼';
    }

    const tbody = data.map(p => `
        <tr>
            <td class="text-center">${p.rank}</td>
            <td>${p.instrument}</td>
            <td>${p.name || ''}</td>
            <td>${p.date}</td>
            <td class="text-right">${Number(p.score).toFixed(6)}</td>
        </tr>
    `).join('');

    document.getElementById('pred-table-body').innerHTML = `
        <table>
            <thead><tr>
                <th class="text-center sortable" data-sortcol="rank">排名${predSortIcon('rank')}</th>
                <th class="sortable" data-sortcol="instrument">代码${predSortIcon('instrument')}</th>
                <th>名称</th>
                <th>日期</th>
                <th class="text-right sortable" data-sortcol="score">预测分数${predSortIcon('score')}</th>
            </tr></thead>
            <tbody>${tbody || '<tr><td colspan="5" style="text-align:center;color:#8b8fa3">暂无预测数据</td></tr>'}</tbody>
        </table>
    `;

    document.querySelectorAll('#pred-table-body .sortable').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.sortcol;
            if (predSortBy === col) {
                predSortOrder = predSortOrder === 'asc' ? 'desc' : 'asc';
            } else {
                predSortBy = col;
                predSortOrder = col === 'score' ? 'desc' : 'asc';
            }
            searchPredictions(0);
        });
    });

    const totalPages = Math.ceil(total / PRED_PAGE_SIZE);
    let pagHtml = '';
    if (totalPages > 1) {
        if (predCurrentPage > 0) pagHtml += `<button onclick="searchPredictions(${predCurrentPage - 1})">上一页</button>`;
        pagHtml += `<span>第 ${predCurrentPage + 1} / ${totalPages} 页</span>`;
        if (predCurrentPage < totalPages - 1) pagHtml += `<button onclick="searchPredictions(${predCurrentPage + 1})">下一页</button>`;
    }
    document.getElementById('pred-pagination').innerHTML = pagHtml;
};

window.loadDailyMean = async function() {
    const instrumentsInput = document.getElementById('mean-instruments');
    const val = instrumentsInput ? instrumentsInput.value.trim() : '';
    let url = `${API}/predictions/daily-mean`;
    if (val) {
        const codes = val.split(',').map(s => s.trim()).filter(Boolean);
        if (codes.length) url += `?instruments=${encodeURIComponent(codes.join(','))}`;
    }

    const data = await fetchJSON(url);

    const container = document.getElementById('chart-daily-mean');
    if (!container) return;
    if (chartInstances['dailyMean']) {
        try { chartInstances['dailyMean'].dispose(); } catch(e) {}
    }

    if (!data.length) {
        container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted)">暂无数据</div>';
        return;
    }

    container.innerHTML = '';
    const chart = echarts.init(container);
    chartInstances['dailyMean'] = chart;
    chart.setOption({
        tooltip: { trigger: 'axis', formatter: p => `${p[0].name}<br/>均值: ${Number(p[0].value).toFixed(6)}` },
        grid: { left: 80, right: 20, top: 20, bottom: 40 },
        xAxis: { type: 'category', data: data.map(d => d.date), axisLabel: { color: '#8b8fa3' }, axisLine: { lineStyle: { color: '#2a2e45' }} },
        yAxis: { type: 'value', axisLabel: { color: '#8b8fa3' }, splitLine: { lineStyle: { color: '#2a2e45' } } },
        series: [{
            type: 'line', data: data.map(d => d.mean_score), smooth: true, showSymbol: false,
            lineStyle: { color: '#6366f1', width: 2 }, itemStyle: { color: '#6366f1' },
            areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{offset:0,color:'rgba(99,102,241,0.2)'},{offset:1,color:'rgba(99,102,241,0)'}] } },
        }]
    });
};

// ==================== Performance Page ====================

async function renderPerformance(el) {
    const [perf, monthly, yearly, daily] = await Promise.all([
        fetchJSON(`${API}/performance`),
        fetchJSON(`${API}/performance/monthly`),
        fetchJSON(`${API}/performance/yearly`),
        fetchJSON(`${API}/performance/daily`),
    ]);

    if (!perf || !perf.cumulative_return) {
        el.innerHTML = '<div class="loading">暂无足够数据生成绩效报告</div>';
        return;
    }

    const yearlyRows = (yearly || []).map(y => `
        <tr>
            <td>${y.year}</td>
            <td class="text-right ${colorClass(y.return)}">${fmt(y.return, 'pct_sign')}</td>
            <td class="text-right">${y.trading_days}</td>
            <td class="text-right">${fmt(y.win_rate, 'pct')}</td>
            <td class="text-right negative">${fmt(y.max_drawdown, 'pct')}</td>
            <td class="text-right">${fmt(y.start_value, 'money')}</td>
            <td class="text-right">${fmt(y.end_value, 'money')}</td>
        </tr>
    `).join('');

    el.innerHTML = `
        <div class="page-header"><h2>绩效分析</h2></div>
        <div class="perf-grid">
            <div class="perf-item"><span class="label">累计收益率</span><span class="value ${colorClass(perf.cumulative_return)}">${fmt(perf.cumulative_return, 'pct_sign')}</span></div>
            <div class="perf-item"><span class="label">年化收益率</span><span class="value ${colorClass(perf.annualized_return)}">${fmt(perf.annualized_return, 'pct_sign')}</span></div>
            <div class="perf-item"><span class="label">最大回撤</span><span class="value negative">${fmt(perf.max_drawdown, 'pct')}</span></div>
            <div class="perf-item"><span class="label">夏普比率</span><span class="value">${fmt(perf.sharpe_ratio, 'ratio')}</span></div>
            <div class="perf-item"><span class="label">信息比率</span><span class="value">${fmt(perf.information_ratio, 'ratio')}</span></div>
            <div class="perf-item"><span class="label">胜率</span><span class="value">${fmt(perf.win_rate, 'pct')}</span></div>
            <div class="perf-item"><span class="label">盈亏比</span><span class="value">${fmt(perf.profit_loss_ratio, 'ratio')}</span></div>
            <div class="perf-item"><span class="label">日均换手率</span><span class="value">${fmt(perf.avg_daily_turnover, 'pct')}</span></div>
            <div class="perf-item"><span class="label">累计手续费</span><span class="value">${fmt(perf.total_commission, 'money')}</span></div>
            <div class="perf-item"><span class="label">基准累计收益</span><span class="value ${colorClass(perf.benchmark_cumulative_return)}">${fmt(perf.benchmark_cumulative_return, 'pct_sign')}</span></div>
            <div class="perf-item"><span class="label">超额收益</span><span class="value ${colorClass(perf.excess_return)}">${fmt(perf.excess_return, 'pct_sign')}</span></div>
            <div class="perf-item"><span class="label">最终资产</span><span class="value">${fmt(perf.final_value, 'money')}</span></div>
        </div>

        ${yearlyRows ? `
        <div class="table-container">
            <h3>年度绩效</h3>
            <table>
                <thead><tr>
                    <th>年份</th><th class="text-right">收益率</th><th class="text-right">交易天数</th>
                    <th class="text-right">胜率</th><th class="text-right">最大回撤</th>
                    <th class="text-right">期初资产</th><th class="text-right">期末资产</th>
                </tr></thead>
                <tbody>${yearlyRows}</tbody>
            </table>
        </div>
        ` : ''}

        <div class="table-container">
            <h3>日度绩效明细</h3>
            <div style="max-height:400px;overflow-y:auto;">
                <table>
                    <thead><tr>
                        <th>日期</th><th class="text-right">日收益</th><th class="text-right">累计收益</th>
                        <th class="text-right">总资产</th><th class="text-right">基准日收益</th>
                        <th class="text-right">基准累计</th><th class="text-right">超额收益</th>
                    </tr></thead>
                    <tbody id="daily-perf-body"></tbody>
                </table>
            </div>
        </div>

        <div class="chart-container">
            <h3>月度收益</h3>
            <div class="chart-box" id="chart-monthly"></div>
        </div>
    `;

    if (daily && daily.length) {
        const dailyTbody = daily.slice().reverse().map(d => `
            <tr>
                <td>${d.date}</td>
                <td class="text-right ${colorClass(d.daily_return)}">${fmt(d.daily_return, 'pct_sign')}</td>
                <td class="text-right ${colorClass(d.cumulative_return)}">${fmt(d.cumulative_return, 'pct_sign')}</td>
                <td class="text-right">${fmt(d.total_value, 'money')}</td>
                <td class="text-right ${colorClass(d.benchmark_return)}">${fmt(d.benchmark_return, 'pct_sign')}</td>
                <td class="text-right ${colorClass(d.benchmark_cumulative_return)}">${fmt(d.benchmark_cumulative_return, 'pct_sign')}</td>
                <td class="text-right ${colorClass(d.excess_return)}">${fmt(d.excess_return, 'pct_sign')}</td>
            </tr>
        `).join('');
        document.getElementById('daily-perf-body').innerHTML = dailyTbody;
    }

    if (monthly.length > 0) renderMonthlyChart(monthly);
}

function renderMonthlyChart(data) {
    const chart = echarts.init(document.getElementById('chart-monthly'));
    chartInstances['monthly'] = chart;
    const months = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
    chart.setOption({
        tooltip: { trigger: 'axis', formatter: p => p[0].name + '<br/>' + fmt(p[0].value, 'pct_sign') },
        grid: { left: 60, right: 20, top: 20, bottom: 40 },
        xAxis: { type: 'category', data: data.map(d => d.year + '-' + months[d.month-1]), axisLabel: { color: '#8b8fa3' }, axisLine: { lineStyle: { color: '#2a2e45' }} },
        yAxis: { type: 'value', axisLabel: { color: '#8b8fa3', formatter: v => (v*100).toFixed(1)+'%' }, splitLine: { lineStyle: { color: '#2a2e45' } } },
        series: [{
            type: 'bar', data: data.map(d => ({
                value: d.return,
                itemStyle: { color: d.return >= 0 ? '#ef4444' : '#22c55e' }
            })),
        }]
    });
}

// ==================== System Page ====================

async function renderSystem(el) {
    const [status, logs] = await Promise.all([
        fetchJSON(`${API}/system/status`),
        fetchJSON(`${API}/logs?limit=50`),
    ]);

    el.innerHTML = `
        <div class="page-header"><h2>系统状态</h2></div>
        <div class="card-grid">
            <div class="card">
                <div class="card-label">策略名称</div>
                <div class="card-value" style="font-size:16px">${status.config_name || '—'}</div>
            </div>
            <div class="card">
                <div class="card-label">最后交易日</div>
                <div class="card-value" style="font-size:16px">${status.last_trading_date || '—'}</div>
            </div>
            <div class="card">
                <div class="card-label">最后预测日</div>
                <div class="card-value" style="font-size:16px">${status.last_prediction_date || '—'}</div>
            </div>
            <div class="card">
                <div class="card-label">股票名称更新</div>
                <div class="card-value" style="font-size:14px">${status.stock_names_updated_at ? status.stock_names_updated_at.split('T')[0] : '—'}</div>
            </div>
        </div>

        <div class="table-container">
            <h3>最近日志文件</h3>
            <div style="padding:12px 20px">
                ${status.recent_log_files.map(f => `<span style="margin-right:12px;color:var(--accent-light)">${f}</span>`).join('')}
            </div>
        </div>

        <div class="table-container">
            <h3>系统日志</h3>
            <div class="log-list">
                ${logs.map(l => `
                    <div class="log-entry">
                        <span class="log-time">${l.timestamp}</span>
                        <span class="log-level-${l.level}">[${l.level}]</span>
                        <span>[${l.module || ''}]</span>
                        <span>${l.message}</span>
                    </div>
                `).join('')}
                ${logs.length === 0 ? '<div style="color:var(--text-muted);text-align:center;padding:20px">暂无日志</div>' : ''}
            </div>
        </div>
    `;
}

// ==================== Init ====================

async function init() {
    await loadInstances();
    renderPage('dashboard');
}
init();

window.addEventListener('resize', () => {
    Object.values(chartInstances).forEach(c => { try { c.resize(); } catch(e) {} });
});
