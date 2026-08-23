"""Phase M v1 模型评估总报告：历史 baseline 版本 + 分年/分风格 + 实验记录。"""
from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
EXP_ROOT = BACKTEST_ROOT.parent
DEFAULT_REGISTRY = BACKTEST_ROOT / "experiments" / "registry.jsonl"
DEFAULT_OUTPUT = BACKTEST_ROOT / "experiments" / "phase_m_v1_report.html"

YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]
REGIMES = ["D", "F", "T"]
PRIMARY_K, PRIMARY_H = "3", "5"

# 只认这两条为总报告 baseline 版本；其它 phase_m_protocol=v1 行进第 4 块。
BASELINE_VERSIONS = (
    {
        "version": "v1",
        "name": "M0 H20",
        "exp_id": "regime-adapt/m0-h20-label-v4",
        "current": False,
    },
    {
        "version": "v2",
        "name": "M0 H20 ES",
        "exp_id": "regime-adapt/m0-h20-t5h5-es-v1",
        "current": True,
    },
)

PRIMARY_KEYS = (
    ("net_ann", "扣费净年化", "ann"),
    ("net_ann_vol", "扣费波动", "vol"),
    ("net_sharpe", "扣费夏普", "sharpe"),
    ("ann", "非扣费年化", "ann"),
    ("turnover", "日换手", "vol"),
)
# 全宇宙日截面 Spearman，读官方合成信号的 h5（与主格期限 / v4 早停 y 对齐）
GLOBAL_KEYS = (
    ("rank_ic_mean", "全局 RankIC", "ric"),
)

CSS = """
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
     margin:0;background:#f6f7f9;color:#1c2733;}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 64px;}
h1{font-size:24px;margin:0 0 6px;} h2{font-size:18px;margin:28px 0 8px;
   border-left:4px solid #2563eb;padding-left:10px;}
h3{font-size:14px;margin:16px 0 6px;color:#334155;}
.meta,.note{color:#64748b;font-size:13px;}
table{border-collapse:collapse;width:100%;background:#fff;font-size:12px;margin:6px 0 14px;}
th,td{border:1px solid #e2e8f0;padding:5px 7px;text-align:center;}
th{background:#f1f5f9;} td.l,th.l{text-align:left;}
.primary{background:#eff6ff;font-weight:600;}
.current{background:#dbeafe;font-weight:600;}
a{color:#2563eb;}
.empty{color:#999;}
"""


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def fmt(v: Optional[float], kind: str) -> str:
    if v is None:
        return '<span class="empty">—</span>'
    if kind == "ann":
        return f"{v * 100:+.1f}%"
    if kind == "vol":
        return f"{v * 100:.1f}%"
    if kind == "sharpe":
        return f"{v:.2f}"
    if kind == "ric":
        return f"{v:.4f}"
    return f"{v:.4f}"


def load_registry(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"registry 第 {i} 行不是合法 JSON: {exc}") from exc
    return rows


def load_eval_doc(rel: str) -> Optional[dict]:
    if not rel:
        return None
    path = EXP_ROOT / rel
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _official(doc: Optional[dict]) -> dict:
    if not doc:
        return {}
    pool = ((doc.get("pools") or {}).get("all") or {})
    return pool.get("ensemble") or pool.get("seed_mean") or {}


def primary_cell(doc: Optional[dict]) -> dict:
    head = _official(doc).get("head") or {}
    return (head.get(PRIMARY_K) or {}).get(PRIMARY_H) or {}


def official_rank_ic(doc: Optional[dict]) -> Optional[float]:
    official = _official(doc)
    block = official.get("h5") or official.get("mean_h") or {}
    return _num(block, "rank_ic_mean")


def slice_cell(doc: Optional[dict], bucket: str, col: str) -> dict:
    root = _official(doc).get(bucket) or {}
    return ((root.get(col) or {}).get(PRIMARY_K) or {}).get(PRIMARY_H) or {}


def _num(cell: dict, key: str) -> Optional[float]:
    v = cell.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _matrix_table(
    title: str,
    rows: Sequence[tuple[str, Optional[dict]]],
    columns: Sequence[str],
    key: str,
    kind: str,
    bucket: str,
) -> str:
    parts = [
        f"<h3>{_esc(title)}</h3>",
        "<table><thead><tr><th class='l'>版本</th>"
        + "".join(f"<th>{_esc(c)}</th>" for c in columns)
        + "</tr></thead><tbody>",
    ]
    for name, doc in rows:
        cells = [f"<td class='l'>{_esc(name)}</td>"]
        for col in columns:
            cells.append(f"<td>{fmt(_num(slice_cell(doc, bucket, col), key), kind)}</td>")
        parts.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def render_hub(baselines: Sequence[dict], experiments: Sequence[dict]) -> str:
    named = [(f"{b['version']} {b['name']}", b.get("doc")) for b in baselines]
    H = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<title>Phase M v1 总报告</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        "<h1>Phase M v1 总报告</h1>",
        "<p class='meta'>主格 <b>top3 × h5</b>。这里所有收益列都是<b>头部绝对收益</b>："
        "每天 top-k 等权的 h 日前瞻收益，年化按 <code>×238/h</code>，不再减全市场。"
        "官方数字来自五种子日截面 z-score 等权合成后再算一次，不是五种子指标算术平均。"
        "全A · 2020-08-03~2026-07-31 · 日频 ST + 成交额≥1000万 + 上市≥60 + 剔 t+1 涨停。"
        "执行层回测也是绝对收益，但年化按 ×250、持有期规则不同，"
        "两者<b>不可直接相减</b>（见规范第 5.1.3 节）。数字来自 eval JSON，禁止手写。"
        " CSI1000 历史 Phase M 见 <a href='report.html'>report.html</a>；"
        "执行层回测见 <a href='phase_m_v1_bt_report.html'>phase_m_v1_bt_report.html</a>。</p>",
        "<h2>1. 历史 baseline</h2>",
        "<table><thead><tr><th class='l'>版本</th><th class='l'>名称</th>"
        + "".join(f"<th>{_esc(lab)}</th>" for _, lab, _ in PRIMARY_KEYS)
        + "".join(f"<th>{_esc(lab)}</th>" for _, lab, _ in GLOBAL_KEYS)
        + "<th>详细报告</th></tr></thead><tbody>",
    ]
    for item in baselines:
        cell = primary_cell(item.get("doc"))
        detail = Path(str(item.get("detail_report") or "")).name
        cls = " class='current'" if item.get("current") else " class='primary'"
        tag = " · 当前" if item.get("current") else ""
        H.append(
            f"<tr{cls}><td class='l'>{_esc(item['version'])}</td>"
            f"<td class='l'>{_esc(item['name'])}{tag}</td>"
            + "".join(
                f"<td>{fmt(_num(cell, key), kind)}</td>" for key, _, kind in PRIMARY_KEYS
            )
            + "".join(
                f"<td>{fmt(official_rank_ic(item.get('doc')), kind)}</td>"
                for _, _, kind in GLOBAL_KEYS
            )
            + (
                f"<td><a href='{_esc(detail)}'>{_esc(detail)}</a></td></tr>"
                if detail
                else "<td><span class='empty'>—</span></td></tr>"
            )
        )
    H.append("</tbody></table>")

    H.append("<h2>2. 历史 baseline 分年</h2>")
    for key, lab, kind in PRIMARY_KEYS:
        H.append(_matrix_table(f"分年 · {lab}", named, YEARS, key, kind, "head_years"))

    H.append("<h2>3. 历史 baseline 分风格</h2>")
    for key, lab, kind in PRIMARY_KEYS:
        H.append(_matrix_table(f"分风格 · {lab}", named, REGIMES, key, kind, "head_regimes"))

    H.append("<h2>4. 历史实验记录</h2>")
    H.append(
        "<table><thead><tr><th class='l'>日期</th><th class='l'>实验</th>"
        "<th class='l'>内容 / 假设</th><th>详细报告</th></tr></thead><tbody>"
    )
    if experiments:
        for exp in experiments:
            detail = Path(str(exp.get("detail_report") or "")).name
            link = (
                f"<a href='{_esc(detail)}'>{_esc(detail)}</a>"
                if detail
                else "<span class='empty'>—</span>"
            )
            H.append(
                f"<tr><td class='l'>{_esc(exp.get('date') or '')}</td>"
                f"<td class='l'>{_esc(exp.get('display_name') or exp.get('exp_id') or '')}"
                f"<div class='note'>{_esc(exp.get('exp_id') or '')}</div></td>"
                f"<td class='l'>{_esc(exp.get('hypothesis') or exp.get('note') or '')}</td>"
                f"<td>{link}</td></tr>"
            )
    else:
        H.append("<tr><td class='l' colspan='4'>暂无实验</td></tr>")
    H.append("</tbody></table>")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    H.append(
        f"<p class='note'>生成时间 {generated} · "
        "规范 <code>backtest/EXPERIMENT_STANDARD.md</code> 第 5.1.2 节。</p>"
        "</div></body></html>"
    )
    return "".join(H)


def _display_name(row: dict) -> str:
    if row.get("display_name"):
        return str(row["display_name"])
    hh = row.get("train_label_horizon")
    if hh is not None and str(row.get("arm") or "").startswith("m0-h"):
        return f"M0 H{hh}"
    return str(row.get("arm") or row.get("exp_id") or "")


def _baseline_specs(rows: Sequence[dict]) -> list[dict]:
    specs = [dict(s) for s in BASELINE_VERSIONS]
    known = {s["exp_id"] for s in specs}
    for row in rows:
        ver = row.get("baseline_version")
        eid = row.get("exp_id")
        if not ver or not eid or eid in known:
            continue
        if str(row.get("phase_m_protocol") or "") != "v1":
            continue
        specs.append(
            {
                "version": str(ver),
                "name": row.get("display_name") or row.get("arm") or str(ver),
                "exp_id": eid,
                "current": False,
            }
        )
        known.add(eid)
    specs.sort(key=lambda s: s["version"])
    if specs:
        latest = max(s["version"] for s in specs)
        for spec in specs:
            spec["current"] = spec["version"] == latest
    return specs


def collect(rows: Sequence[dict]) -> tuple[list[dict], list[dict]]:
    by_id = {r.get("exp_id"): r for r in rows}
    specs = _baseline_specs(rows)
    baselines = []
    for spec in specs:
        row = by_id.get(spec["exp_id"]) or {}
        baselines.append(
            {
                **spec,
                "doc": load_eval_doc(str(row.get("eval_output") or "")),
                "detail_report": row.get("detail_report"),
            }
        )
    baseline_ids = {s["exp_id"] for s in specs}
    experiments = []
    for row in rows:
        if row.get("state") == "archived":
            continue
        if str(row.get("phase_m_protocol") or "") != "v1":
            continue
        if row.get("exp_id") in baseline_ids:
            continue
        experiments.append(
            {
                "display_name": _display_name(row),
                "exp_id": row.get("exp_id"),
                "hypothesis": row.get("hypothesis") or row.get("note"),
                "detail_report": row.get("detail_report"),
                "date": row.get("date"),
            }
        )
    experiments.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("exp_id") or "")))
    return baselines, experiments


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="registry.jsonl → Phase M v1 总报告")
    p.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    rows = load_registry(args.registry)
    baselines, experiments = collect(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_hub(baselines, experiments), encoding="utf-8")
    print(
        f"{len(baselines)} 个 baseline / {len(experiments)} 条实验记录 → {args.output}"
    )


if __name__ == "__main__":
    main()
