"""回测结果归档：目录命名、Plotly→PNG、HTML 报告。"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

import pandas as pd

FigureLike = Any


def sanitize_note(note: Optional[str], max_len: int = 60) -> str:
    """将 --note 转为安全的目录名片段。"""
    if not note:
        return ""
    s = note.strip().replace(" ", "_")
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len]


def make_session_dir(result_root: Path, note: Optional[str] = None, now: Optional[datetime] = None) -> Path:
    """创建 backtest/result/YYYYMMDD_HHMMSS[_note]/ 目录。"""
    now = now or datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    suffix = sanitize_note(note)
    name = f"{stamp}_{suffix}" if suffix else stamp
    path = result_root / name
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


def _as_figure_list(figs: Union[FigureLike, Sequence[FigureLike], None]) -> list:
    if figs is None:
        return []
    if isinstance(figs, (list, tuple)):
        return list(figs)
    return [figs]


def save_plotly_pngs(figures: Iterable[FigureLike], out_dir: Path, basename: str, width: int = 1200, height: int = 700) -> list[Path]:
    """将 Plotly figure(s) 写成 PNG，多图时加序号后缀。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_list = _as_figure_list(figures)
    paths: list[Path] = []
    for i, fig in enumerate(fig_list, start=1):
        name = f"{basename}.png" if len(fig_list) == 1 else f"{basename}_{i:02d}.png"
        path = out_dir / name
        fig.write_image(str(path), format="png", width=width, height=height, scale=1)
        paths.append(path)
    return paths


def build_pred_label(pred: pd.DataFrame, label: pd.DataFrame) -> pd.DataFrame:
    """对齐 pred / label 为 score_ic / model_performance 所需的 pred_label。"""
    pred = pred.copy()
    label = label.copy()
    if isinstance(pred, pd.Series):
        pred = pred.to_frame("score")
    if isinstance(label, pd.Series):
        label = label.to_frame("label")
    if "score" not in pred.columns and pred.shape[1] == 1:
        pred = pred.rename(columns={pred.columns[0]: "score"})
    if "label" not in label.columns and label.shape[1] == 1:
        label = label.rename(columns={label.columns[0]: "label"})
    # 只保留需要的列
    pred = pred[["score"]] if "score" in pred.columns else pred.iloc[:, :1].rename(columns={pred.columns[0]: "score"})
    label = label[["label"]] if "label" in label.columns else label.iloc[:, :1].rename(columns={label.columns[0]: "label"})
    return pd.concat([label, pred], axis=1, sort=True).reindex(label.index)


def generate_run_figures(
    *,
    report_normal_df: pd.DataFrame,
    analysis_df: pd.DataFrame,
    pred_label: Optional[pd.DataFrame],
    figures_dir: Path,
) -> dict[str, list[str]]:
    """生成 notebook 四类图的静态 PNG，返回 {类别: [相对 figures/ 的文件名]}。"""
    from qlib.contrib.report import analysis_model, analysis_position

    figures_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, list[str]] = {}

    # 1) 净值报告
    figs = analysis_position.report_graph(report_normal_df, show_notebook=False)
    paths = save_plotly_pngs(figs, figures_dir, "report_graph")
    saved["report_graph"] = [p.name for p in paths]

    # 2) 风险分析
    figs = analysis_position.risk_analysis_graph(analysis_df, report_normal_df, show_notebook=False)
    paths = save_plotly_pngs(figs, figures_dir, "risk_analysis")
    saved["risk_analysis"] = [p.name for p in paths]

    if pred_label is not None and not pred_label.empty:
        # 3) IC
        figs = analysis_position.score_ic_graph(pred_label, show_notebook=False)
        paths = save_plotly_pngs(figs, figures_dir, "score_ic")
        saved["score_ic"] = [p.name for p in paths]

        # 4) 模型表现（可能多图）
        figs = analysis_model.model_performance_graph(pred_label, show_notebook=False)
        paths = save_plotly_pngs(figs, figures_dir, "model_performance")
        saved["model_performance"] = [p.name for p in paths]
    else:
        saved["score_ic"] = []
        saved["model_performance"] = []

    return saved


def _fmt_metric(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    if isinstance(v, (int, float)):
        return f"{v:.6f}"
    return html.escape(str(v))


def _metrics_table_html(metrics: Mapping[str, Any], title: str = "关键指标") -> str:
    rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{_fmt_metric(v)}</td></tr>"
        for k, v in metrics.items()
        if k not in ("error", "traceback")
    )
    return f"<h2>{html.escape(title)}</h2><table><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>{rows}</tbody></table>"


def _figures_html(figure_files: Mapping[str, Sequence[str]], figures_rel: str = "figures") -> str:
    parts = ["<h2>分析图</h2>"]
    titles = {
        "report_graph": "净值报告 (report_graph)",
        "risk_analysis": "风险分析 (risk_analysis)",
        "score_ic": "Score IC",
        "model_performance": "模型表现 (model_performance)",
    }
    for key, title in titles.items():
        files = figure_files.get(key) or []
        parts.append(f"<h3>{html.escape(title)}</h3>")
        if not files:
            parts.append("<p><em>未生成</em></p>")
            continue
        for name in files:
            parts.append(
                f'<p><img src="{html.escape(figures_rel + "/" + name)}" alt="{html.escape(name)}" style="max-width:100%;border:1px solid #ddd;"/></p>'
            )
    return "\n".join(parts)


def _mlruns_table_html(link: Mapping[str, Any]) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td><code>{html.escape(str(v))}</code></td></tr>"
        for k, v in link.items()
    )
    return f"<h2>MLruns 对应</h2><table><thead><tr><th>字段</th><th>值</th></tr></thead><tbody>{rows}</tbody></table>"


_HTML_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #222; }
table { border-collapse: collapse; margin: 12px 0 24px; }
th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: left; }
th { background: #f5f5f5; }
h1,h2,h3 { color: #111; }
code { background: #f0f0f0; padding: 1px 4px; }
a { color: #0645ad; }
"""


def write_run_html(
    path: Path,
    *,
    title: str,
    metrics: Mapping[str, Any],
    figure_files: Mapping[str, Sequence[str]],
    mlruns_link: Mapping[str, Any],
    note: str = "",
) -> None:
    note_html = f"<p>说明：{html.escape(note)}</p>" if note else ""
    body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"/><title>{html.escape(title)}</title><style>{_HTML_CSS}</style></head>
<body>
<h1>{html.escape(title)}</h1>
{note_html}
{_metrics_table_html(metrics)}
{_mlruns_table_html(mlruns_link)}
{_figures_html(figure_files)}
</body></html>
"""
    path.write_text(body, encoding="utf-8")


def write_index_html(
    path: Path,
    *,
    session_name: str,
    note: str,
    summary: Mapping[str, Any],
    runs: Sequence[Mapping[str, Any]],
) -> None:
    """父目录汇总页。runs 每项含 run, status, metrics 子集, report_href, mlruns 摘要。"""
    metric_keys = [
        "excess_with_cost_information_ratio",
        "excess_with_cost_annualized_return",
        "excess_with_cost_max_drawdown",
        "excess_cum_return",
        "portfolio_cum_return",
    ]
    header = "".join(f"<th>{html.escape(k)}</th>" for k in ["run", "status", *metric_keys, "report", "train_recorder", "backtest_recorder"])
    rows = []
    for r in runs:
        cells = [str(r.get("run", "")), str(r.get("status", ""))]
        for k in metric_keys:
            cells.append(_fmt_metric(r.get(k)))
        href = r.get("report_href", "")
        cells.append(f'<a href="{html.escape(href)}">report.html</a>' if href else "-")
        cells.append(html.escape(str(r.get("train_recorder_id", "-"))))
        cells.append(html.escape(str(r.get("backtest_recorder_id", "-"))))
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")

    mean = summary.get("metrics_mean", {})
    mean_rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{_fmt_metric(v)}</td>"
        f"<td>{_fmt_metric(summary.get('metrics_std', {}).get(k))}</td></tr>"
        for k, v in mean.items()
    )
    note_html = f"<p>说明：{html.escape(note)}</p>" if note else ""
    body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"/><title>{html.escape(session_name)}</title><style>{_HTML_CSS}</style></head>
<body>
<h1>回测汇总：{html.escape(session_name)}</h1>
{note_html}
<p>成功 {summary.get('success_runs', 0)} / {summary.get('total_runs', 0)}，
耗时 { _fmt_metric(summary.get('elapsed_seconds')) } 秒</p>
<h2>各 Run</h2>
<table><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>指标均值 ± 标准差</h2>
<table><thead><tr><th>指标</th><th>mean</th><th>std</th></tr></thead><tbody>{mean_rows}</tbody></table>
</body></html>
"""
    path.write_text(body, encoding="utf-8")
