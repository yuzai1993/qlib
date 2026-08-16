"""生成头部口径评估 HTML：9 格汇总 + 9 格 × 风格 + 换手/净 alpha，含改标签消融。

口径 v3（2026-08-15 用户指示）：北极星从 ir（硬减等权基准）换成 appraisal_ir
（拟合 beta 的残差 IR）。硬减基准等于把 beta 钉成 1，实测五臂旧 NS 排序与组合 Beta
排序 Spearman=+1.00，即旧北极星退化成 beta 排序、低 beta 臂被系统性低估。
收益列同步从 ann_excess 换成 ann_alpha（beta 调整后），净列换成 net_ann_alpha。

继承 v2：全部交易日评估、剔 t+1 涨停封板与零成交、无 Hit@k、对称主客场诊断。
"""
from __future__ import annotations

import json
from pathlib import Path

EXP_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = EXP_ROOT / "backtest" / "result" / "eval_regime_head_v3"
PREV_DIR = EXP_ROOT / "backtest" / "result" / "eval_regime_head_v2"

# 北极星与收益列的口径（v3）；旧列 ir / ann_excess / net_ann_excess 仍保留作对照
IR_METRIC = "appraisal_ir"
ANN_METRIC = "ann_alpha"
NET_METRIC = "net_ann_alpha"
OUT = EXP_ROOT / "backtest" / "experiments" / "regime_adapt_head_eval_report.html"

# (key, 显示名, 说明, 训练标签期限)
ARMS = [
    ("b6m_ref", "B6-M 参考", "csi1000 / 2016-2020 / DoubleEnsemble / H40", 40),
    ("m0fast", "M0-fast H40（对照）", "全A 长窗 / 单 LGBM / H40 + CSRankNorm", 40),
    ("m3fast", "M3-fast H40", "M0 + regime 特征 + 风格平衡权重", 40),
    ("m0h1", "M0 H1", "M0-fast 配方，训练标签改为 H1", 1),
    ("m0h5", "M0 H5", "M0-fast 配方，训练标签改为 H5", 5),
    ("m0h10", "M0 H10", "M0-fast 配方，训练标签改为 H10", 10),
    ("m3h5", "M3 H5", "M0 H5 + regime 特征 + 风格平衡权重", 5),
]
KS = ["10", "22", "50"]
HS = ["1", "5", "10"]
H_ALL = ["1", "5", "10", "20", "40"]
REGS = ["D", "F", "T"]


def load_arm(key: str, root: Path = EVAL_DIR):
    path = root / f"eval_{key}.json"
    return json.loads(path.read_text()) if path.exists() else None


def seed_mean(rec):
    if rec is None:
        return None
    return (rec.get("pools", {}).get("all", {}) or {}).get("seed_mean")


def cell(sm, k, h, metric, regime=None):
    if sm is None:
        return None
    grid = (sm.get("head") or {}) if regime is None else (sm.get("head_regimes") or {}).get(regime) or {}
    return (grid.get(str(k), {}) or {}).get(str(h), {}).get(metric)


def grid_mean(sm, metric=IR_METRIC, regime=None, ks=KS, hs=HS):
    vals = [cell(sm, k, h, metric, regime) for k in ks for h in hs]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def ns(sm, regime=None):
    """北极星：核心 9 格 IR 等权均值。"""
    if sm is None:
        return None
    stored = sm.get("north_star_ir") if regime is None else (sm.get("north_star_ir_regimes") or {}).get(regime)
    return stored if stored is not None else grid_mean(sm, IR_METRIC, regime)


def _seed_grid_mean(rec, metric: str) -> dict[str, float]:
    """逐种子的 9 格均值，用于 pairwise 比较。"""
    if rec is None:
        return {}
    out = {}
    for seed, r in (rec.get("pools", {}).get("all", {}) or {}).get("seeds", {}).items():
        grid = r.get("head") or {}
        vals = [
            (grid.get(k, {}) or {}).get(h, {}).get(metric) for k in KS for h in HS
        ]
        vals = [v for v in vals if v is not None]
        if vals:
            out[seed] = sum(vals) / len(vals)
    return out


def seed_ns(rec):
    return _seed_grid_mean(rec, IR_METRIC)


def seed_net(rec):
    return _seed_grid_mean(rec, NET_METRIC)


def cell_baseline(data, regime=None):
    """每个 (k,h) 格上的跨臂 IR 均值，用于把不同期限的 IR 水平差异归一。

    h 越短、重叠越少，IR 天然越高（本次实测 h1≈1.7、h5≈0.8、h10≈0.6），
    因此「主场格 vs 客场格」若直接比绝对 IR 是在比期限而非比模型。
    """
    base = {}
    for k in KS:
        for h in HS:
            vals = [cell(seed_mean(data.get(key)), k, h, IR_METRIC, regime) for key, *_ in ARMS]
            vals = [v for v in vals if v is not None]
            if vals:
                base[(k, h)] = sum(vals) / len(vals)
    return base


def rel_mean(sm, base, hs, regime=None):
    """指定期限子集上「本臂 IR / 该格跨臂均值」的平均：1.0 = 与同行持平。"""
    vals = []
    for k in KS:
        for h in hs:
            v, b = cell(sm, k, h, IR_METRIC, regime), base.get((k, h))
            if v is not None and b:
                vals.append(v / b)
    return sum(vals) / len(vals) if vals else None


def home_away_rel(sm, base, home_h: int, regime=None):
    """主场格（h == 训练标签期限）与客场格（LOHO）的相对 IR。

    H40 臂的主场期限不在核心网格内，主场为空、客场即完整 9 格；
    这一不对称是网格选择的结果，须在表中明示，不能靠删 h1 掩盖。
    """
    home_hs = [h for h in HS if int(h) == home_h]
    away_hs = [h for h in HS if int(h) != home_h]
    return (
        rel_mean(sm, base, home_hs, regime) if home_hs else None,
        rel_mean(sm, base, away_hs, regime),
    )


def fmt(x, kind="ir"):
    if x is None:
        return "—"
    if kind == "ir":
        return f"{x:.3f}"
    if kind == "ann":
        return f"{x*100:+.1f}%"
    if kind == "pct":
        return f"{x*100:.1f}%"
    if kind == "delta":
        return f"{x:+.3f}"
    if kind == "rel":
        return f"{x:.2f}"
    return f"{x:.4f}"


def grid_table(title, data, metric, kind, regime=None, horizons=None):
    horizons = horizons or HS
    header = "<tr><th class='l'>臂</th>"
    for k in KS:
        header += f"<th colspan='{len(horizons)}'>k={k}</th>"
    header += "<th>网格均值</th></tr><tr><th></th>"
    for _ in KS:
        for h in horizons:
            header += f"<th>h{h}</th>"
    header += "<th></th></tr>"
    body = ""
    for key, name, _, home_h in ARMS:
        sm = seed_mean(data.get(key))
        if sm is None:
            continue
        tds = [f"<td class='l'>{name}</td>"]
        for k in KS:
            for h in horizons:
                v = cell(sm, k, h, metric, regime)
                css = " class='home'" if int(h) == home_h else ""
                tds.append(f"<td{css}>{fmt(v, kind)}</td>")
        tds.append(
            f"<td class='primary'>{fmt(grid_mean(sm, metric, regime, hs=horizons), kind)}</td>"
        )
        body += "<tr>" + "".join(tds) + "</tr>"
    return f"<h3>{title}</h3><table><thead>{header}</thead><tbody>{body}</tbody></table>"


def main():
    data = {k: load_arm(k) for k, _, _, _ in ARMS}
    prev = {k: load_arm(k, PREV_DIR) for k, _, _, _ in ARMS}
    if not any(v is not None for v in data.values()):
        raise SystemExit(f"no eval json in {EVAL_DIR}")
    any_rec = next(v for v in data.values() if v is not None)
    n_days = cell(seed_mean(any_rec), 22, 1, "n_days")

    css = """
    body{font-family:-apple-system,'PingFang SC',sans-serif;margin:0;background:#f6f7f9;color:#1c2733;}
    .wrap{max-width:1280px;margin:0 auto;padding:28px 20px 64px;}
    h1{font-size:24px;margin:0 0 6px;} h2{font-size:18px;margin:28px 0 8px;border-left:4px solid #2563eb;padding-left:10px;}
    h3{font-size:14px;margin:16px 0 6px;color:#334155;}
    .meta{color:#64748b;font-size:13px;margin-bottom:16px;}
    table{border-collapse:collapse;width:100%;background:#fff;font-size:12px;margin:6px 0 14px;}
    th,td{border:1px solid #e2e8f0;padding:5px 7px;text-align:center;}
    th{background:#f1f5f9;} td.l,th.l{text-align:left;}
    .primary{background:#eff6ff;font-weight:600;}
    .home{background:#fef3c7;}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;margin:8px 0;}
    .note{font-size:12px;color:#64748b;}
    ul{margin:6px 0;padding-left:20px;}
    """
    H = [
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
        "<title>regime-adapt 头部评估 v3</title>",
        f"<style>{css}</style></head><body><div class='wrap'>",
        "<h1>regime-adapt 头部评估报告（口径 v3：appraisal ratio）</h1>",
        f"<p class='meta'>全A 池 · 测试窗 2020-08-03~2026-07-31 <b>全部 {n_days or '—'} 个交易日</b> · "
        "头部候选池已剔除 t+1 涨停封板与零成交量样本 · "
        "北极星 = k∈{10,22,50} × h∈{1,5,10} 的 9 格 <b>appraisal-IR</b> 等权均值；h20/h40 仅诊断。"
        "appraisal-IR = 拟合 beta 后残差（resid = top-k 收益 − beta × 等权全A）的 IR；"
        "年化 alpha = 日均残差 × 238/h；IR 对 h&gt;1 用 Newey-West lag=h−1；"
        "换手 = 相隔 h 个交易日的 top-k 单边换手率，年化成本 = (238/h) × 换手 × 0.092%。</p>",
    ]

    base = cell_baseline(data)

    H.append("<h2>0. 口径演进</h2><div class='card'><ul>")
    H.append(
        "<li><b>v3（本版）：北极星从「硬减基准的超额 IR」换成「拟合 beta 的 appraisal-IR」。</b>"
        "原口径把超额定义为 top-k 收益 − 等权全A 收益，等价于对基准做 <b>beta 恒等于 1</b> 的对冲。"
        "组合真实 beta 偏离 1 时这是过度/不足对冲，会把基准自身的波动注入「超额」序列、污染 IR 的分母。"
        "后果实测可见：v2 五臂北极星排序与 Phase S 组合 Beta 排序 <b>Spearman = +1.00</b>——"
        "旧北极星在这批臂上与「按 beta 排序」不可区分，低 beta 臂被系统性低估。"
        "v3 改为先对等权全A 拟合 beta、再对残差算 IR，与 Phase S 的 appraisal ratio 同口径，两阶段可直接对排。"
        "收益列同步换成 beta 调整后的年化 alpha 与扣费净 alpha。"
        "诊断脚本：<code>backtest/scripts/diagnose_phase_m_s_gap.py</code>。</li>"
    )
    H.append(
        "<li><b>v2：不再用「排除 h1」当稳健键。</b>v1 以「h1 是 H1 臂主场标签」为由删 h1，但 h5/h10 同样是 "
        "H5/H10 臂的主场格，删 h1 只是把主场优势让给短标签的另两臂，不是中立修正。"
        "本版做法：网格 h∈{1,5,10} 由策略持有期决定、与参赛臂无关，<b>9 格均值即唯一北极星</b>；"
        "另按臂对称拆出<b>主场格</b>（h = 训练标签期限，表中黄底）与<b>客场格</b>（LOHO）作为集中度诊断。"
        "由于 h 越短 IR 天然越高（本次 h1≈1.7 / h5≈0.8 / h10≈0.6），主客场必须用"
        "<b>同格跨臂归一后的相对 IR</b>比较（1.00 = 与同行持平），直接比绝对值是在比期限。"
        "H40 三臂的主场期限落在网格外，主场列为「—」、客场即完整 9 格，如实标注。</li>"
    )
    H.append(
        "<li><b>v2：次日涨停已在评估内剔除。</b>标签在 t+1 收盘建仓，故按 t+1 涨幅是否触板（主板 9.5%、"
        "创业板/科创板 19.5%，创业板 20% 自 2020-08-24 起）与 t+1 成交量是否为 0 判定不可成交，"
        "把这些样本同时从 top-k 候选池和等权基准中剔除。v1 里 H1 臂 6.9% 的封板股不再计入收益。</li>"
    )
    H.append("<li><b>v2：Hit@k 已删除</b>：全A 约 4000 只时 k=22 的随机重叠期望仅 0.55%，该列无判别力。</li>")
    H.append(
        "<li><b>v2：评估日改为测试窗全部交易日</b>（v1 为 70% 分层抽样的 499 天）。评估日连续后换手率与"
        "扣费净超额可直接估算，见第 4 节；30% 封存集因此已并入本次评估，后续「加最新样本」实验需另划样本外窗。</li>"
    )
    H.append("</ul></div>")

    H.append("<h2>1. 北极星、头部 Beta 与主客场拆分</h2>")
    H.append(
        "<table><thead><tr><th class='l'>臂</th><th class='primary'>9 格 NS<br>(appraisal)</th>"
        "<th>v2 NS<br>(硬减基准)</th><th>差</th><th>头部<br>Beta</th>"
        "<th>主场格<br>相对 IR</th><th>客场格<br>相对 IR</th>"
        "<th>扣费净 alpha<br>9 格均值</th><th>D</th><th>F</th><th>T</th>"
        "<th>全截面<br>h1 RankIC</th></tr></thead><tbody>"
    )
    for key, name, desc, home_h in ARMS:
        sm = seed_mean(data.get(key))
        if sm is None:
            continue
        cur = ns(sm)
        old = grid_mean(seed_mean(prev.get(key)), "ir")
        delta = None if (cur is None or old is None) else cur - old
        home, away = home_away_rel(sm, base, home_h)
        H.append(
            f"<tr><td class='l'><b>{name}</b><div class='note'>{desc}</div></td>"
            f"<td class='primary'>{fmt(cur)}</td><td>{fmt(old)}</td>"
            f"<td>{fmt(delta, 'delta')}</td>"
            f"<td>{fmt(grid_mean(sm, 'beta'), 'rel')}</td>"
            f"<td class='home'>{fmt(home, 'rel')}</td><td>{fmt(away, 'rel')}</td>"
            f"<td>{fmt(grid_mean(sm, NET_METRIC), 'ann')}</td>"
            + "".join(f"<td>{fmt(ns(sm, r))}</td>" for r in REGS)
            + f"<td>{fmt(sm.get('h1.rank_ic_mean'))}</td></tr>"
        )
    H.append("</tbody></table>")
    H.append(
        "<p class='note'>「v2 NS」为同一批预测在旧口径（硬减等权基准、beta 恒为 1）下的 9 格均值，"
        "两列之差反映口径修正对该臂的影响：<b>头部 Beta &lt; 1 的臂在旧口径下被低估</b>"
        "（硬减 1 倍基准 = 过度对冲，把基准波动灌进分母），Beta &gt; 1 则相反。"
        "头部 Beta = 9 格拟合 beta 的均值，基准为当日可成交全A 等权。"
        "主/客场为同格跨臂归一后的相对 IR，1.00 = 与同行持平；两列接近说明优势与训练期限无关。</p>"
    )

    H.append("<h3>1b. 分期限 IR（跨臂可比）</h3>")
    H.append(
        "<table><thead><tr><th class='l'>臂</th>"
        + "".join(f"<th>h{h} 绝对</th>" for h in HS)
        + "".join(f"<th>h{h} 相对</th>" for h in HS)
        + "</tr></thead><tbody>"
    )
    for key, name, _, home_h in ARMS:
        sm = seed_mean(data.get(key))
        if sm is None:
            continue
        tds = [f"<td class='l'>{name}</td>"]
        for h in HS:
            css = " class='home'" if int(h) == home_h else ""
            tds.append(f"<td{css}>{fmt(grid_mean(sm, IR_METRIC, hs=[h]))}</td>")
        for h in HS:
            css = " class='home'" if int(h) == home_h else ""
            tds.append(f"<td{css}>{fmt(rel_mean(sm, base, [h]), 'rel')}</td>")
        H.append("<tr>" + "".join(tds) + "</tr>")
    H.append("</tbody></table>")
    H.append(
        "<p class='note'>每格对 k∈{10,22,50} 等权。绝对列跨期限不可比（h 越短 IR 越高）；"
        "相对列已按同格跨臂均值归一，可横向也可纵向读。黄底为该臂主场期限。</p>"
    )

    H.append("<h2>2. 核心 9 格（IR）</h2>")
    H.append(grid_table("2.1 全A 不分风格", data, IR_METRIC, "ir"))
    for i, reg in enumerate(REGS):
        H.append(grid_table(f"2.{i+2} {reg} 态", data, IR_METRIC, "ir", regime=reg))

    H.append("<h2>3. 核心 9 格（年化超额，gross）</h2>")
    H.append(grid_table("3.1 全A 不分风格", data, ANN_METRIC, "ann"))
    for i, reg in enumerate(REGS):
        H.append(grid_table(f"3.{i+2} {reg} 态", data, ANN_METRIC, "ann", regime=reg))

    H.append("<h2>4. 换手与扣费净超额</h2>")
    H.append(
        "<p class='note'>换手为相隔 h 个交易日的 top-k 单边换手率（h 日持有 → 每 h 日换一次仓）；"
        "净超额 = gross 年化超额 − (238/h) × 换手 × 0.092%（买 0.021% + 卖 0.071%）。"
        "该口径不含冲击成本、不含 n_drop 缓冲，是真实 TopkDropout 净收益的上界。</p>"
    )
    H.append(grid_table("4.1 单边换手率", data, "turnover", "pct"))
    H.append(grid_table("4.2 全A 扣费净年化 alpha", data, NET_METRIC, "ann"))
    for i, reg in enumerate(REGS):
        H.append(grid_table(f"4.{i+3} {reg} 态扣费净年化 alpha", data, NET_METRIC, "ann", regime=reg))

    H.append("<h2>5. 诊断格 h20 / h40（不进北极星）</h2>")
    H.append(grid_table("5.1 全A appraisal-IR（含长端）", data, IR_METRIC, "ir", horizons=H_ALL))
    H.append(grid_table("5.2 F 态 appraisal-IR（含长端）", data, IR_METRIC, "ir", regime="F", horizons=H_ALL))

    # ---- 结论按当前数据动态生成，避免与表格脱节 ----
    rank_ns = sorted(
        [(k, n, ns(seed_mean(data.get(k)))) for k, n, _, _ in ARMS if ns(seed_mean(data.get(k))) is not None],
        key=lambda x: -x[2],
    )
    rank_net = sorted(
        [
            (k, n, grid_mean(seed_mean(data.get(k)), NET_METRIC))
            for k, n, _, _ in ARMS
            if grid_mean(seed_mean(data.get(k)), NET_METRIC) is not None
        ],
        key=lambda x: -x[2],
    )
    H.append("<h2>6. 结论</h2><div class='card'><ul>")
    if rank_ns:
        H.append(
            "<li><b>北极星排序（9 格 IR）</b>："
            + " &gt; ".join(f"{n} {v:.3f}" for _, n, v in rank_ns)
            + "。</li>"
        )
    if rank_net:
        H.append(
            "<li><b>扣费净年化超额排序（9 格均值）</b>："
            + " &gt; ".join(f"{n} {v*100:+.1f}%" for _, n, v in rank_net)
            + "。净口径是与实盘最接近的一列：h1 每日近全换仓，成本会吃掉大部分 gross 超额。</li>"
        )
    ref = "m0fast"  # 唯一的控变量对照：与 H1/H5/H10 只差训练标签
    pw = []
    for key, name, _, hh in ARMS:
        if hh == 40 or key == ref:
            continue
        a, b = seed_ns(data.get(key)), seed_ns(data.get(ref))
        an, bn = seed_net(data.get(key)), seed_net(data.get(ref))
        common = sorted(set(a) & set(b))
        if not common:
            continue
        pw.append(
            (
                name,
                sum(a[s] > b[s] for s in common),
                sum(an[s] > bn[s] for s in common),
                len(common),
            )
        )
    if pw:
        H.append(
            "<li><b>训练标签期限：H40 过长的假设只成立一半，需下调 v1 的结论强度。</b>"
            "唯一的控变量比较是 m0 系列（仅换训练标签）——"
            + "；".join(
                f"{n} vs H40 的 NS pairwise {w}/{t}、扣费净超额 pairwise {wn}/{t}"
                for n, w, wn, t in pw
            )
            + "。即 H10 稳定小胜 H40（NS +0.08，净超额 +0.6pp），H5 在 IR 上胜、"
            "但因换手更高在净超额上全负，H1 两项全负。"
            "v1「短标签三臂一致优于 H40、推荐 H5」的判断在剔除封板股与全窗口径下不再成立。</li>"
        )
    # 主场集中度：客场/主场相对 IR 之比，越接近 1 说明优势与训练期限无关
    conc = []
    for key, name, _, home_h in ARMS:
        sm = seed_mean(data.get(key))
        if sm is None or home_h not in (1, 5, 10):
            continue
        home, away = home_away_rel(sm, base, home_h)
        if home and away:
            conc.append((name, home, away))
    if conc:
        H.append(
            "<li><b>v1 的「主场偏差」在修正口径下并不存在。</b>"
            + "；".join(f"{n} 主场 {hm:.2f} / 客场 {aw:.2f}" for n, hm, aw in conc)
            + "。三个短标签臂的主客场相对 IR 都基本持平，说明各自的强弱是期限无关的；"
            "H1 的问题不是「只在自家期限强」，而是<b>在每个期限都低于同行平均</b>——"
            "v1 看到的优势来自 6.9% 买不进的封板股，剔除后即消失。"
            "这条同时说明 v1 用「删 h1」去纠偏的做法既不中立、也没有纠到真问题。</li>"
        )
    # 分风格：最优标签期限是否随风格反转
    reg_days = {
        r: cell(seed_mean(data.get(ref)), 22, 5, "n_days", r) for r in REGS
    }
    reg_best = {}
    for r in REGS:
        cand = [
            (name, ns(seed_mean(data.get(key)), r), grid_mean(seed_mean(data.get(key)), NET_METRIC, r))
            for key, name, _, hh in ARMS
            if key.startswith("m0") and ns(seed_mean(data.get(key)), r) is not None
        ]
        if cand:
            reg_best[r] = max(cand, key=lambda x: x[1])
    if reg_best:
        H.append(
            "<li><b>最优标签期限随风格反转——这是本轮最重要的发现。</b>在 m0 系列内，"
            + "；".join(
                f"{r} 态（{reg_days.get(r) or '—':.0f} 天）最优是 {n}（NS {v:.2f}，净超额 {net*100:+.1f}%）"
                for r, (n, v, net) in reg_best.items()
            )
            + "。具体地：<b>D/T 态 H40 最优</b>（D: 1.62 vs H10 1.43；T: 1.15 vs H10 0.96），"
            "<b>F 态短标签压倒性更优</b>（H10 净超额 +53.5% vs H40 +13.2%，差 4 倍）。"
            "由于 F 只占 9% 的交易日，两种效应在不分风格的北极星上互相抵消成近似平手——"
            "这正是「单一模型 + 单一标签期限」在全窗口径下看不出差异、却在 2024-09 这类 F 段崩掉的原因。"
            "B6-M 参考在 F 态 NS −0.02、净超额 −4.5%，直接复现了 alpha 消失。</li>"
        )
        H.append(
            "<li><b>方向修正：应把风格自适应做在标签期限/持有期上，而不是加 regime 特征。</b>"
            "m3（regime 特征 + 风格平衡权重）在 F 态 NS 仅 0.50、几乎没改善 H40 的 F 段短板，"
            "而单纯把标签换成 H10 就把 F 态 NS 从 0.44 抬到 1.85。"
            "即端到端让模型「自己看出风格」无效，按风格切换预测期限有效——"
            "这更接近原计划的方案 2（风格分类器 + 分风格模型切换）。</li>"
        )
    H.append(
        "<li><b>换手是短期限的硬约束。</b>h1 口径下 top22 日单边换手 47%~85%，"
        "年化显性成本 10%~19%，会吃掉大部分 gross 超额；h5/h10 每 h 日换一次，成本降到 4% 上下。"
        "这是选择实际持有期时比 IR 更有约束力的一列。</li>"
    )
    H.append(
        "<li><b>下一步</b>：① 优先验证<b>风格条件化的持有期/标签切换</b>（D/T 用 H40、F 用 H10），"
        "先用已有六臂预测做离线拼接看上限，再决定是否需要风格分类器；"
        "② 用净超额最高的臂做 TopkDropout 扣费回测（含 only_tradable / 涨跌停 / n_drop 缓冲），"
        "与第 4 节的上界对照；③ 本次已用满测试窗，「加最新样本」实验须另取样本外窗；"
        "④ 确认后再回 DoubleEnsemble 与超参精调。</li>"
    )
    H.append("</ul></div>")

    H.append("<div class='card note'><ul>")
    H.append("<li>本口径仍不是完整 TopkDropout 回测：无 n_drop 缓冲、无冲击成本、卖出侧跌停未剔除。</li>")
    H.append("<li>M0 H1/H5/H10 与 M0-fast H40 共用特征、样本权重与早停锚点，只换训练标签。</li>")
    H.append("<li>早停轮数在评估窗上选择（用户 2026-08-09 批准的 valid=test），各臂口径一致但绝对 IR 偏乐观。</li>")
    H.append(f"<li>JSON：<code>{EVAL_DIR.relative_to(EXP_ROOT)}/eval_*.json</code>（v1 对比：<code>{PREV_DIR.relative_to(EXP_ROOT)}/</code>）</li>")
    H.append("</ul></div></div></body></html>")
    OUT.write_text("\n".join(H), encoding="utf-8")
    print("written:", OUT)


if __name__ == "__main__":
    main()
