"""Self-contained HTML report rendered only from persisted evaluation/audit data."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path


def _escape(value):
    return html.escape(str(value), quote=True)


def _number(value):
    return 'NA' if value is None else f'{value:.4f}' if isinstance(value, (float, int)) else _escape(value)


def _table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{_escape(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def _json_block(value):
    return '<pre>' + _escape(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)) + '</pre>'


def _link(record, output, label=None):
    path = Path(record['path'])
    href = os.path.relpath(path, output.parent)
    return f'<a href="{_escape(href)}">{_escape(label or path.name)}</a>'


def _confusion_svg(matrix, classes):
    maximum = max(max(row) for row in matrix) or 1
    cells = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 330 320" role="img" aria-label="混淆矩阵：行是真实类别，列是预测类别"><rect width="330" height="320" fill="white"/>']
    cells += ['<text x="168" y="18" text-anchor="middle" font-size="13">预测类别 / predicted</text>']
    for i, cls in enumerate(classes):
        cells.append(f'<text x="{95 + i * 56}" y="43" text-anchor="middle">{_escape(cls)}</text>')
        cells.append(f'<text x="46" y="{81 + i * 56}" text-anchor="middle">{_escape(cls)}</text>')
    for row in range(4):
        for col in range(4):
            value = matrix[row][col]
            opacity = .08 + .85 * value / maximum
            cells.append(f'<rect x="{67 + col * 56}" y="{55 + row * 56}" width="54" height="54" fill="#197c8a" fill-opacity="{opacity:.4f}"/>')
            cells.append(f'<text x="{94 + col * 56}" y="{86 + row * 56}" text-anchor="middle" fill="{"white" if opacity > .6 else "#122238"}">{int(value)}</text>')
    cells += ['<text x="168" y="304" text-anchor="middle" font-size="12">行 = 真实类别 / true class</text></svg>']
    return ''.join(cells)


def render_report(bundle, output_path, label_audit=None, feature_audit=None, hub_path=None):
    """Render saved metrics and audits, optionally refreshing the dedicated report hub."""
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    candidates = bundle.get('candidates', {})
    classes = bundle.get('classes', list('DFTP'))
    pieces = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>次日市场风格四分类研究</title><style>body{font-family:system-ui,-apple-system,sans-serif;background:#f4f6fa;color:#17283a;max-width:1200px;margin:auto;padding:28px;line-height:1.6}h1,h2,h3{line-height:1.3}section,.notice{background:white;padding:22px;margin:22px 0;border-radius:12px;border:1px solid #dce3ed}.notice{border-left:5px solid #bb7319;background:#fff8ec}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{padding:9px 12px;border-bottom:1px solid #dce3ed;text-align:left;white-space:nowrap}th{background:#edf2f7}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}a{color:#125e9c}svg{max-width:360px}small{color:#5b6a7a}</style><h1>次日市场风格四分类</h1>']
    pieces += [f'<p>协议 {_escape(bundle.get("classification_protocol"))} · 数据版本 {_escape(bundle.get("data_version"))} · 固定种子 {_escape(bundle.get("seeds"))}</p>']
    pieces += ['<div class="notice"><strong>事后标签限制：</strong>预测目标为下一交易日所属月份的冻结 D/F/T/P 标签。全历史聚类与居中三个月平滑包含未来信息；严格按时点构造输入，也不能将此标签体系解释为完全独立的实时状态。P 片段数量有限，区块置信区间仍可能不稳定。</div>']
    chosen = bundle.get('chosen_candidate')
    pieces += [f'<p>训练期内层选中的候选：<strong>{_escape(chosen or "未完成")}</strong>。只按内层 macro-F1、accuracy、logloss 排序；正式评估段不参与选型。</p><section><h2>候选与训练期基线</h2>']
    rows = []
    for name, item in candidates.items():
        inner, final = item['inner'], item['evaluation']
        ci = item.get('confidence_intervals', {}).get('metrics', {})
        rows.append([_escape(name), _number(inner['accuracy']), _number(inner['macro_f1']), _number(final['accuracy']), _number(final['macro_f1']), _number(final['balanced_accuracy']), _number(final['logloss']), _number(final['brier']), _escape(ci.get('accuracy', 'NA'))])
    for name, item in bundle.get('baselines', {}).items():
        final = item['evaluation']
        rows.append([_escape(name), '—', '—', _number(final['accuracy']), _number(final['macro_f1']), _number(final['balanced_accuracy']), _number(final['logloss']), _number(final['brier']), '—'])
    pieces += [_table(['模型', '内层 accuracy', '内层 macro-F1', '评估 accuracy', '评估 macro-F1', 'balanced acc.', 'logloss', 'Brier', 'accuracy 95% CI'], rows)]
    pieces += ['<p><small>五种子概率做算术平均后 argmax；没有选择最佳种子。macro-F1 固定四类，缺真实样本类别 F1=0、recall=NA；没有预测样本的 precision=0。Balanced accuracy 只平均有真实样本类别。Brier 为四类平方误差和的日均，范围 [0,2]。置信区间使用固定种子42、连续3个月移动块、默认1000次重采样。</small></p></section>']
    for name, item in candidates.items():
        metric = item['evaluation']
        pieces += [f'<section id="{_escape(name)}"><h2>{_escape(name)}</h2><h3>各类别表现</h3>']
        pieces += [_table(['类别', 'precision', 'recall', 'F1', 'support'], [[_escape(cls)] + [_number(metric['per_class'][cls][key]) for key in ['precision', 'recall', 'f1', 'support']] for cls in classes])]
        svg = _confusion_svg(metric['confusion_matrix'], classes)
        svg_path = output.parent / f'confusion_{name}.svg'
        svg_path.write_text(svg)
        pieces += ['<h3>混淆矩阵</h3>', svg, f'<p><a href="{_escape(svg_path.name)}">下载混淆矩阵 SVG</a></p><h3>逐种子结果</h3>']
        pieces += [_table(['seed', 'accuracy', 'macro-F1', 'logloss'], [[_escape(seed), _number(value['accuracy']), _number(value['macro_f1']), _number(value['logloss'])] for seed, value in item.get('per_seed', {}).items()])]
        pieces += ['<details><summary>逐种子均值 / 样本标准差与区块置信区间</summary>', _json_block({'seed_summary': item.get('seed_summary'), 'confidence_intervals': item.get('confidence_intervals')}), '</details>']
        diag = item.get('diagnostics', {})
        pieces += ['<h3>分年与切换诊断</h3>', _table(['目标年', '天数', 'accuracy', 'macro-F1'], [[_escape(year), str(values['n_days']), _number(values['accuracy']), _number(values['macro_f1'])] for year, values in diag.get('per_year', {}).items()])]
        pieces += [_table(['目标月份位置', '天数', 'accuracy', 'macro-F1'], [[_escape(position), str(values['n_days']) if values else '0', _number(values['accuracy']) if values else 'NA', _number(values['macro_f1']) if values else 'NA'] for position, values in diag.get('month_position', {}).items()])]
        pieces += ['<details open><summary>状态切换 ±5 个交易日 / P 进入退出与片段</summary>', _json_block({k: v for k, v in diag.items() if k not in ['per_year', 'month_position']}), '</details>']
        importance = item.get('feature_importance')
        if importance:
            pieces += ['<h3>按特征组解释</h3>', f'<p>方法：<code>{_escape(importance["method"])}</code>。仅使用已拟合模型的训练证据；不使用正式评估样本做归因或筛选。</p>',
                       f'<p>{_escape(importance["seed_aggregation"])}</p>',
                       _table(['特征组', '五种子归一化均值', '样本标准差'], [[_escape(group), _number(values['mean']), _number(values['std'])] for group, values in sorted(importance['groups'].items(), key=lambda pair: -pair[1]['mean'])]),
                       '<p>缺失指示器归入原特征组。组大小、相关性和模型类型会影响权重；不同方法的大小不可直接比较。全部权重为零时代表模型没有可报告的非零重要性。</p>',
                       '<p>' + _escape(' '.join(importance['limitations'])) + '</p>',
                       f'<p>{_link(importance["artifact"], output, "保存的五种子特征组重要性与逐特征证据")}</p>']
        pieces += ['<h3>实际模型与预测</h3><ul>']
        for record in item.get('models', []):
            pieces += [f'<li>seed {_escape(record.get("seed"))} · {_link(record, output, "拟合模型")} · SHA256 <code>{_escape(record["sha256"])}</code></li>']
        for key, label in [('ensemble', '正式评估逐日五种子概率'), ('inner_ensemble', '内层逐日五种子概率')]:
            if key in item:
                pieces += [f'<li>{_link(item[key], output, label)} · SHA256 <code>{_escape(item[key]["sha256"])}</code></li>']
        pieces += ['</ul></section>']
    pieces += ['<section><h2>标签验收与历史差异审计</h2>', _json_block(label_audit if label_audit is not None else {'status': 'audit not supplied'}), '</section>']
    pieces += ['<section><h2>特征覆盖、排除的外部来源与可得时点</h2><p>无法验证历史披露时点、权限或训练期覆盖的外部序列不进入训练；缺失来源及原因以下方保存的审计为准。所有预处理仅在对应训练段拟合。</p>', _json_block(feature_audit if feature_audit is not None else {'status': 'audit not supplied'}), '</section>']
    pieces += ['<section><h2>协议与文件指纹</h2>', _json_block({key: value for key, value in bundle.items() if key not in ['candidates', 'baselines']}), '</section></html>']
    output.write_text(''.join(pieces))
    if hub_path:
        hub = Path(hub_path).resolve()
        hub.parent.mkdir(parents=True, exist_ok=True)
        href = os.path.relpath(output, hub.parent)
        hub.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>市场风格分类研究报告</title><h1>市场风格分类研究</h1><p><a href="' + _escape(href) + '">次日 D/F/T/P 四分类详细报告</a></p><p>事后月度标签；五种子概率平均；正式评估不参与选型。</p></html>')
    return output
