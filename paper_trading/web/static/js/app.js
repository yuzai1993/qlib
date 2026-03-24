/* Paper Trading Dashboard - Single Page Application */

const API = '/api';
let currentPage = 'dashboard';
let chartInstances = {};

// ==================== Utility ====================

async function fetchJSON(url) {
    const resp = await fetch(url);
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
    Object.values(chartInstances).forEach(c => c.dispose());
    chartInstances = {};
}

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
    const [positions, dates] = await Promise.all([
        fetchJSON(`${API}/positions/current`),
        fetchJSON(`${API}/positions/dates`),
    ]);

    const dateOptions = dates.map(d => `<option value="${d}">${d}</option>`).join('');

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
            <h3>持仓分布</h3>
            <div class="chart-box" id="chart-pie"></div>
        </div>
    `;

    renderPositionTable(positions);
    renderPieChart(positions);
}

function renderPositionTable(data) {
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

    document.getElementById('pos-table-body').innerHTML = `
        <table>
            <thead><tr>
                <th>代码</th><th>名称</th><th class="text-right">股数</th>
                <th class="text-right">成本价</th><th class="text-right">现价</th>
                <th class="text-right">市值</th><th class="text-right">盈亏</th>
                <th class="text-right">盈亏%</th><th class="text-right">占比</th>
                <th class="text-right">持有天数</th>
            </tr></thead>
            <tbody>${tbody || '<tr><td colspan="10" style="text-align:center;color:#8b8fa3">暂无持仓</td></tr>'}</tbody>
        </table>
    `;
}

function renderPieChart(data) {
    if (!data.length) return;
    const chart = echarts.init(document.getElementById('chart-pie'));
    chartInstances['pie'] = chart;
    chart.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: {d}%' },
        series: [{
            type: 'pie', radius: ['40%', '70%'],
            label: { color: '#8b8fa3', fontSize: 12 },
            data: data.map(p => ({ name: (p.name || p.instrument), value: p.market_value })),
        }]
    });
}

window.loadPositions = async function() {
    const date = document.getElementById('pos-date').value;
    const url = date ? `${API}/positions?date=${date}` : `${API}/positions/current`;
    const data = await fetchJSON(url);
    renderPositionTable(data);
    disposeCharts();
    renderPieChart(data);
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

// ==================== Predictions Page ====================

async function renderPredictions(el) {
    const data = await fetchJSON(`${API}/predictions`);

    el.innerHTML = `
        <div class="page-header"><h2>预测信号</h2></div>
        <div class="table-container">
            <h3>最新预测排名 (${data.length > 0 ? data[0].date : '—'})</h3>
            <div id="pred-table-body"></div>
        </div>
    `;

    const tbody = data.slice(0, 50).map(p => `
        <tr>
            <td class="text-center">${p.rank}</td>
            <td>${p.instrument}</td>
            <td>${p.name || ''}</td>
            <td class="text-right">${Number(p.score).toFixed(6)}</td>
        </tr>
    `).join('');

    document.getElementById('pred-table-body').innerHTML = `
        <table>
            <thead><tr>
                <th class="text-center">排名</th><th>代码</th><th>名称</th>
                <th class="text-right">预测分数</th>
            </tr></thead>
            <tbody>${tbody || '<tr><td colspan="4" style="text-align:center;color:#8b8fa3">暂无预测数据</td></tr>'}</tbody>
        </table>
    `;
}

// ==================== Performance Page ====================

async function renderPerformance(el) {
    const [perf, monthly] = await Promise.all([
        fetchJSON(`${API}/performance`),
        fetchJSON(`${API}/performance/monthly`),
    ]);

    if (!perf || !perf.cumulative_return) {
        el.innerHTML = '<div class="loading">暂无足够数据生成绩效报告</div>';
        return;
    }

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

        <div class="chart-container">
            <h3>月度收益</h3>
            <div class="chart-box" id="chart-monthly"></div>
        </div>
    `;

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
renderPage('dashboard');

window.addEventListener('resize', () => {
    Object.values(chartInstances).forEach(c => c.resize());
});
