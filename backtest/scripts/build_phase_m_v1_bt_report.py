"""Phase M v1 执行层回测总报告：只认五种子均值信号的单次回测。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

EXP_ROOT = Path(__file__).resolve().parents[2]
EXP_DIR = EXP_ROOT / "backtest" / "experiments"
REGISTRY = EXP_DIR / "registry.jsonl"
HUB_OUT = EXP_DIR / "phase_m_v1_bt_report.html"

YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]
REGIMES = ["D", "F", "T"]
FULL_COLS = [
    ("annualized_return", "累乘年化", "pct"),
    ("annualized_return_arith", "算术年化", "pct"),
    ("sharpe_ratio", "夏普", "num"),
    ("alpha", "Alpha", "pct"),
    ("beta", "Beta", "num"),
    ("max_drawdown", "最大回撤", "pct"),
    ("calmar_ratio", "Calmar", "num"),
    ("annualized_volatility", "年化波动", "pct"),
    ("annualized_one_way_turnover", "年化单边换手", "turn"),
]
YEAR_METRICS = [
    ("annualized_return", "累乘年化", "pct"),
    ("annualized_return_arith", "算术年化", "pct"),
    ("sharpe_ratio", "夏普", "num"),
    ("alpha", "Alpha", "pct"),
    ("beta", "Beta", "num"),
    ("max_drawdown", "最大回撤", "pct"),
    ("annualized_one_way_turnover", "年化单边换手", "turn"),
]
CSS = """
body{font-family:-apple-system,'PingFang SC',sans-serif;margin:0;background:#f6f7f9;color:#1c2733;}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 64px;}
h1{font-size:24px;margin:0 0 6px;} h2{font-size:18px;margin:28px 0 8px;border-left:4px solid #2563eb;padding-left:10px;}
h3{font-size:14px;margin:16px 0 6px;color:#334155;}
.meta,.note{color:#64748b;font-size:13px;}
table{border-collapse:collapse;width:100%;background:#fff;font-size:12px;margin:6px 0 14px;}
th,td{border:1px solid #e2e8f0;padding:5px 7px;text-align:center;}
th{background:#f1f5f9;} td.l,th.l{text-align:left;}
.primary{background:#eff6ff;font-weight:600;}
.current{background:#dbeafe;font-weight:600;}
a{color:#2563eb;}
"""


def fmt(v: Optional[float], kind: str) -> str:
    if v is None:
        return "—"
    if kind == "pct":
        return f"{v*100:+.1f}%"
    if kind == "turn":
        return f"{v:.1f}x"
    return f"{v:.2f}"


def ensemble_of(doc: dict[str, Any]) -> dict[str, Any]:
    ens = doc.get("ensemble")
    if not ens:
        raise ValueError("official Phase M v1 backtest metrics must come from ensemble")
    return ens


def rel_from_exp(path: Path) -> str:
    return Path(os.path.relpath(path, EXP_DIR)).as_posix()


def session_report_href(session_dir: str) -> str:
    return rel_from_exp(EXP_ROOT / session_dir / "run_01" / "report.html")


def figure_iframes(session_dir: str, figures: dict[str, Any]) -> list[str]:
    out = []
    files = figures.get("report_graph") or []
    if not files:
        return ["<p class='note'>无净值图</p>"]
    root = EXP_ROOT / session_dir / "run_01" / "figures"
    for name in files:
        href = rel_from_exp(root / name)
        out.append(
            f'<iframe src="{href}" title="{name}" loading="lazy" '
            'style="width:100%;height:420px;border:1px solid #e2e8f0;border-radius:6px;"></iframe>'
        )
    return out


def _row_metrics(block: dict, cols: list[tuple[str, str, str]]) -> list[str]:
    return [f"<td>{fmt(block.get(key), kind)}</td>" for key, _, kind in cols]


def _matrix_table(
    title: str,
    rows: list[tuple[str, dict]],
    columns: list[str],
    key: str,
    kind: str,
    bucket: str,
) -> str:
    parts = [
        f"<h3>{title}</h3>",
        "<table><thead><tr><th class='l'>版本</th>"
        + "".join(f"<th>{c}</th>" for c in columns)
        + "</tr></thead><tbody>",
    ]
    for name, ens in rows:
        cells = [f"<td class='l'>{name}</td>"]
        src = ens.get(bucket) or {}
        for col in columns:
            cells.append(f"<td>{fmt((src.get(col) or {}).get(key), kind)}</td>")
        parts.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def render_hub(baselines: list[dict], experiments: list[dict]) -> str:
    # 锚点与策略参数一律由 current baseline 派生，避免晋升后页头留着旧版本
    cur = next((b for b in baselines if b.get("current")), None)
    anchor = (
        f"BT {cur['bt_version']} · {cur['display_name']}" if cur else "未设定（无 current baseline）"
    )
    strategy = (cur or {}).get("strategy") or ""
    H = [
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
        "<title>Phase M v1 执行层回测</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        "<h1>Phase M v1 执行层回测</h1>",
        "<p class='meta'>官方指标只认<b>五种子均值信号</b>："
        "先对五种子 pred 做日截面 z-score，再等权平均，回测一次。"
        f"不是五次回测指标的算术平均。当前对照锚点是 <b>{anchor}</b>。"
        + (f"策略 {strategy}。" if strategy else "")
        + "日频 ST / 等权全A / 窗 2020-08-03~2026-07-31。"
        "主年化是扣费净值累乘后再折年（CAGR）；算术年化（日均×250）只作审计。"
        "夏普分子仍是算术年化。</p>",
    ]

    H.append("<h2>1. 历史 baseline</h2>")
    H.append(
        "<table><thead><tr><th class='l'>版本</th><th class='l'>名称</th>"
        + "".join(f"<th>{lab}</th>" for _, lab, _ in FULL_COLS)
        + "<th>带图报告</th></tr></thead><tbody>"
    )
    for item in baselines:
        ens = ensemble_of(item["doc"])
        href = session_report_href(ens["session_dir"])
        cls = "current" if item.get("current") else "primary"
        tag = " · 当前" if item.get("current") else ""
        H.append(
            f"<tr class='{cls}'><td class='l'>"
            f"{item['bt_version']}{tag}</td><td class='l'>{item['display_name']}</td>"
            + "".join(_row_metrics(ens["full_period"], FULL_COLS))
            + f"<td><a href='{href}'>report.html</a></td></tr>"
        )
    H.append("</tbody></table>")

    named = [
        (f"{item['bt_version']} {item['display_name']}", ensemble_of(item["doc"]))
        for item in baselines
    ]
    H.append("<h2>2. 历史 baseline 分年</h2>")
    for key, lab, kind in YEAR_METRICS:
        H.append(_matrix_table(f"分年 · {lab}", named, YEARS, key, kind, "years"))

    H.append("<h2>3. 历史 baseline 分风格</h2>")
    for key, lab, kind in YEAR_METRICS:
        H.append(_matrix_table(f"分风格 · {lab}", named, REGIMES, key, kind, "regimes"))

    H.append("<h2>4. 实验</h2>")
    H.append(
        "<table><thead><tr><th class='l'>实验</th><th class='l'>对照 baseline</th>"
        "<th>累乘年化</th><th>夏普</th><th>Alpha</th><th>回撤</th><th>详细报告</th></tr></thead><tbody>"
    )
    for exp in experiments:
        ens = ensemble_of(exp["doc"])
        fp = ens["full_period"]
        detail = exp.get("detail_report") or ""
        link = Path(detail).name if detail else ""
        H.append(
            f"<tr><td class='l'>{exp['display_name']}</td>"
            f"<td class='l'>{exp.get('baseline_name', '')}</td>"
            f"<td>{fmt(fp.get('annualized_return'), 'pct')}</td>"
            f"<td>{fmt(fp.get('sharpe_ratio'), 'num')}</td>"
            f"<td>{fmt(fp.get('alpha'), 'pct')}</td>"
            f"<td>{fmt(fp.get('max_drawdown'), 'pct')}</td>"
            f"<td><a href='{link}'>{link or '—'}</a></td></tr>"
        )
    if not experiments:
        H.append("<tr><td class='l' colspan='7'>暂无实验</td></tr>")
    H.append("</tbody></table>")
    H.append("</div></body></html>")
    return "".join(H)


def render_experiment(experiment: dict, baseline: dict) -> str:
    exp_ens = ensemble_of(experiment["doc"])
    base_ens = ensemble_of(baseline["doc"])
    rows = [
        (f"实验组 {experiment['display_name']}", exp_ens),
        (f"基准组 {baseline['display_name']}", base_ens),
    ]
    H = [
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
        f"<title>{experiment['display_name']} 对照回测</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        f"<h1>{experiment['display_name']} 对照回测</h1>",
        "<p class='meta'>实验组对比基准组。指标来自五种子均值信号单次回测，"
        f"对照 <b>{baseline['display_name']}</b>（{baseline['bt_version']}）。"
        "主年化是扣费净值累乘后再折年（CAGR）；算术年化（日均×250）只作审计。"
        "夏普分子仍是算术年化。"
        " <a href='phase_m_v1_bt_report.html'>返回总报告</a></p>",
    ]
    if experiment.get("hypothesis") or experiment.get("eval_protocol") or experiment.get("conclusion"):
        notes = ["<p class='note'>"]
        if experiment.get("eval_protocol"):
            notes.append(f"<b>协议</b>：{experiment['eval_protocol']}<br>")
        if experiment.get("hypothesis"):
            notes.append(f"<b>假设</b>：{experiment['hypothesis']}<br>")
        if experiment.get("conclusion"):
            notes.append(f"<b>结论</b>：{experiment['conclusion']}")
        notes.append("</p>")
        H.extend(notes)
    H += [
        "<h2>1. 全周期</h2>",
        "<table><thead><tr><th class='l'>组别</th>"
        + "".join(f"<th>{lab}</th>" for _, lab, _ in FULL_COLS)
        + "<th>带图报告</th></tr></thead><tbody>",
    ]
    for i, (name, ens) in enumerate(rows):
        cls = " class='primary'" if i == 0 else ""
        href = session_report_href(ens["session_dir"])
        H.append(
            f"<tr{cls}><td class='l'>{name}</td>"
            + "".join(_row_metrics(ens["full_period"], FULL_COLS))
            + f"<td><a href='{href}'>report.html</a></td></tr>"
        )
    H.append("</tbody></table>")
    H.append("<h2>2. 分年</h2>")
    for key, lab, kind in YEAR_METRICS:
        H.append(_matrix_table(f"分年 · {lab}", rows, YEARS, key, kind, "years"))
    H.append("<h2>3. 分风格</h2>")
    for key, lab, kind in YEAR_METRICS:
        H.append(_matrix_table(f"分风格 · {lab}", rows, REGIMES, key, kind, "regimes"))
    H.append("<h2>4. 实验组图</h2>")
    H.extend(figure_iframes(exp_ens["session_dir"], exp_ens.get("figures") or {}))
    H.append("<h2>5. 基准组图</h2>")
    H.extend(figure_iframes(base_ens["session_dir"], base_ens.get("figures") or {}))
    H.append("</div></body></html>")
    return "".join(H)


def load_registry(path: Path = REGISTRY) -> list[dict]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _load_doc(rel: str) -> dict:
    path = EXP_ROOT / rel
    if not path.is_file():
        raise FileNotFoundError(rel)
    return json.loads(path.read_text(encoding="utf-8"))


def catalog_from_registry(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    baselines, experiments = [], []
    names = {}
    for rec in rows:
        kind = rec.get("report_kind")
        if kind == "phase_m_v1_bt_baseline":
            item = {
                "bt_version": rec["bt_version"],
                "display_name": rec.get("display_name") or rec["exp_id"],
                "eval_output": rec["eval_output"],
                "current": bool(rec.get("current_bt")),
                # eval_protocol 的第 2 段是策略参数（signal | strategy | 涨跌停 | 过滤 | …）
                "strategy": (rec.get("eval_protocol") or "").split("|")[1].strip()
                if len((rec.get("eval_protocol") or "").split("|")) > 1
                else None,
                "doc": _load_doc(rec["eval_output"]),
            }
            names[rec["exp_id"]] = f"{item['bt_version']} {item['display_name']}"
            baselines.append(item)
        elif kind == "phase_m_v1_bt":
            experiments.append(rec)
    resolved = []
    for rec in experiments:
        resolved.append(
            {
                "exp_id": rec["exp_id"],
                "display_name": rec.get("display_name") or rec["exp_id"],
                "baseline_ref": rec.get("baseline_ref"),
                "baseline_name": names.get(rec.get("baseline_ref"), rec.get("baseline_ref") or ""),
                "detail_report": rec.get("detail_report"),
                "eval_output": rec["eval_output"],
                "hypothesis": rec.get("hypothesis"),
                "eval_protocol": rec.get("eval_protocol"),
                "conclusion": rec.get("conclusion"),
                "doc": _load_doc(rec["eval_output"]),
            }
        )
    return baselines, resolved


def write_all() -> list[Path]:
    baselines, experiments = catalog_from_registry(load_registry())
    written = [HUB_OUT]
    HUB_OUT.write_text(render_hub(baselines, experiments), encoding="utf-8")
    by_id = {f"{b['bt_version']}": b for b in baselines}
    # also index by registry exp later via baseline_ref matching display
    base_by_ref = {item["display_name"]: item for item in baselines}
    for rec in load_registry():
        if rec.get("report_kind") == "phase_m_v1_bt_baseline":
            by_id[rec["exp_id"]] = next(
                b for b in baselines if b["bt_version"] == rec["bt_version"]
            )
    for exp in experiments:
        base = by_id.get(exp.get("baseline_ref") or "")
        if base is None or not exp.get("detail_report"):
            continue
        dest = EXP_ROOT / exp["detail_report"]
        dest.write_text(render_experiment(exp, base), encoding="utf-8")
        written.append(dest)
    return written


def main() -> None:
    paths = write_all()
    for path in paths:
        print(f"written: {path}")


if __name__ == "__main__":
    main()
