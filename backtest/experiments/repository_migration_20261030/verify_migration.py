from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from html.parser import HTMLParser

ROOT = Path('/Users/yuxianqi/Project/qlib')
AUDIT = ROOT / 'backtest/experiments/repository_migration_20261030'
sys.path.insert(0, str(ROOT / 'backtest/scripts'))
import build_phase_m_v1_report as model_report


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('href', 'src') and value:
                self.links.append(value)


manifest = json.loads((AUDIT / 'manifest.json').read_text())
failures = []
for record in manifest['files']:
    path = ROOT / record['destination']
    if not path.is_file() or path.is_symlink() or sha(path) != record['destination_sha256']:
        failures.append(str(path))
assert not failures, failures
registry = ROOT / manifest['registry']['path']
assert sha(registry) == manifest['registry']['after_sha256']
before = [json.loads(x) for x in (AUDIT / 'registry.before.jsonl').read_text().splitlines() if x.strip()]
after = [json.loads(x) for x in registry.read_text().splitlines() if x.strip()]
for left, right in zip(before, after):
    assert {k: v for k, v in left.items() if k not in ('result_dirs', 'portable_eval_output')} == {
        k: v for k, v in right.items() if k not in ('result_dirs', 'portable_eval_output')
    }
assert len(before) == len(after)

baselines, experiments = model_report.collect(after)
assert [b['version'] for b in baselines] == ['v1', 'v2', 'v3', 'v4']
baseline_metrics = []
for baseline in baselines:
    assert baseline['doc']
    cell = model_report.primary_cell(baseline['doc'])
    assert all(key in cell for key in ('net_ann', 'net_ann_vol', 'net_sharpe'))
    baseline_metrics.append({'version': baseline['version'], 'exp_id': baseline['exp_id'],
                             'primary': cell, 'rank_ic': model_report.official_rank_ic(baseline['doc'])})

model_groups = {}
for record in manifest['files']:
    if record.get('kind') != 'trained_model':
        continue
    path = Path(record['destination'])
    if path.parts[0] == 'mlruns':
        group = 'B6-M'
    elif 'rankices' in path.parts[2]:
        group = 'v4'
    elif 't3h5es' in path.parts[2]:
        group = 'v3'
    elif 't5h5es' in path.parts[2]:
        group = 'v2'
    else:
        group = 'v1'
    model_groups.setdefault(group, []).append({'path': str(path), 'sha256': record['destination_sha256'], 'bytes': record['destination_bytes']})
assert set(model_groups) == {'B6-M', 'v1', 'v2', 'v3', 'v4'}
assert all(len(group) == 5 for group in model_groups.values())

# Reports are existing portable snapshots or the rebuilt model hub. Only direct
# local resource references are inspected; provenance strings remain historical.
reports = {ROOT / r['detail_report'] for r in after if r.get('detail_report') and (r.get('phase_m_protocol') == 'v1' or str(r.get('report_kind', '')).startswith('phase_m_v1_bt'))}
reports.add(ROOT / 'backtest/experiments/phase_m_v1_report.html')
reports.update((ROOT / 'backtest/experiments/phase_m_v1_bt_metrics').glob('*.html'))
missing_reports = []
missing_links = []
link_count = 0
for path in sorted(reports):
    if not path.is_file():
        missing_reports.append(str(path.relative_to(ROOT)))
        continue
    parser = Links()
    parser.feed(path.read_text())
    for raw in parser.links:
        url = urlsplit(raw)
        if url.scheme or url.netloc or not url.path:
            continue
        target = (path.parent / unquote(url.path)).resolve()
        link_count += 1
        if not target.is_file():
            missing_links.append({'report': str(path.relative_to(ROOT)), 'link': raw, 'resolved': str(target)})

dry_run = json.loads((AUDIT / 'root_cleanup_dry_run.json').read_text())
assert dry_run['errors'] == []
validation = {
    'checked_target_files': len(manifest['files']), 'all_recorded_hashes_match': True,
    'all_25_model_hashes_unchanged': True, 'model_groups': model_groups,
    'registry_rows': len(after), 'registry_event_order_baseline_ref_current_bt_metrics_preserved': True,
    'model_hub_baselines': baseline_metrics, 'model_hub_experiment_count': len(experiments),
    'portable_original_eval_count': manifest['original_eval_json_count'],
    'report_count': len(reports), 'local_resource_links_checked': link_count,
    'missing_reports': missing_reports, 'missing_local_links': missing_links,
    'preexisting_missing_source': {'exp_id': 'regime-adapt/m0-h20es-densemble-s42-v1',
                                 'eval_output': 'backtest/result/eval_regime_ablation/eval_densemble_s42_vs_v2.json',
                                 'detail_report': 'backtest/experiments/regime_adapt_m0h20es_densemble_report.html',
                                 'note': 'Both source and destination already lacked these; no training, evaluation or invented replacement performed.'},
    'cleanup': {'repo_root': dry_run['repo_root'], 'errors': dry_run['errors'], 'keep_result_dirs': len(dry_run['keep_result_dirs']),
                'keep_mlruns_dirs': len(dry_run['keep_mlruns_dirs']), 'delete_result_dirs': len(dry_run['delete_result_dirs']),
                'delete_mlruns_dirs': len(dry_run['delete_mlruns_dirs']), 'apply_performed': False},
    'selected_retained_files': manifest['file_count'], 'selected_retained_bytes': sum(r['destination_bytes'] for r in manifest['files']),
    'newly_copied_files': manifest['copied_file_count'], 'newly_copied_bytes': manifest['copied_bytes'],
}
catalog_parity = json.loads((AUDIT / 'catalog_parity_after.json').read_text())
assert catalog_parity['destination_records'] == 36
assert catalog_parity['destination_available'] == 35
assert catalog_parity['old_available_records_preserved']
assert catalog_parity['old_metric_values_preserved']
assert [item['id'] for item in catalog_parity['destination_missing']] == ['regime-adapt/m0-h20es-densemble-s42-v1']
validation['catalog_parity'] = {key: value for key, value in catalog_parity.items() if key != 'checked'}
(AUDIT / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k: v for k, v in validation.items() if k not in ('model_groups', 'model_hub_baselines')}, ensure_ascii=False, indent=2))
