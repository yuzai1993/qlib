"""Parameter studies are editorial views; their source records own all numbers."""

import hashlib
import json

import pytest

from backtest.scripts.experiment_archive import scan_experiment_archives
from quant_platform.analyses import ConflictError
from quant_platform.research_annotations import ResearchAnnotations
from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, row, strategy_doc


def setup_group(root, **changes):
    rows = [row('bt/control', 'strategy', model_ref='model/v2', bt_version='v2', date='2026-08-20'),
            row('bt/variant', 'strategy', model_ref='model/v2', bt_version=None,
                report_kind='phase_m_v1_bt', date='2026-08-21')]
    docs = [strategy_doc(), strategy_doc()]
    docs[1]['ensemble']['full_period']['annualized_return'] = .37
    c = catalog(root, rows, docs)
    for source in rows:
        manifest(root, source['exp_id'], kind='strategy', model_ref='model/v2')
    directory = 'backtest/experiments/strategies/parameter-studies/top5'
    value = {'schema_version': 2, 'entity_type': 'experiment_group', 'exp_id': 'study/top5',
             'title': 'Top5 holding periods', 'kind': 'strategy', 'track': 'all_a',
             'archive_path': directory, 'model_ref': 'model/v2',
             'strategy_summary': {'name': 'Top5', 'description': 'Hold1 versus hold5'},
             'change_summary': '最短持1日改为5日', 'analysis': {'conclusion': '待核对'},
             'experiment_group': {'control_ref': 'bt/control',
                                  'members': [{'ref': 'bt/variant', 'label': '持5'},
                                              {'ref': 'bt/control', 'label': '持1'}],
                                  'axes': ['hold_thresh'], 'fixed_summary': 'Same signal and fees'},
             'docs': ['README.md'], 'reports': ['summary.html'], **changes}
    path = root / directory / 'metadata.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))
    (path.parent / 'README.md').write_text('Parameter study')
    (path.parent / 'summary.html').write_text('<p>Study</p>')
    return c, path, rows


def test_groups_discover_separately_and_collapse_only_the_history_projection(tmp_path):
    c, path, _ = setup_group(tmp_path)
    discovery = scan_experiment_archives(tmp_path)
    assert not discovery['errors']
    assert [g['exp_id'] for g in discovery['groups']] == ['study/top5']
    assert {m['exp_id'] for m in discovery['experiments']} == {'bt/control', 'bt/variant'}
    snapshot = c.snapshot()
    assert {r['id'] for r in snapshot['records']} == {'bt/control', 'bt/variant'}
    assert [r['id'] for r in snapshot['experiment_records']] == ['study/top5']
    assert [r['id'] for r in snapshot['benchmarks']] == ['bt/control']
    group = snapshot['experiment_records'][0]
    assert group['status'] == 'completed' and group['date'] == '2026-08-21'
    assert not any(group[k] for k in ('current', 'is_baseline', 'official', 'comparable'))
    assert all(v is None for v in group['metrics'].values())
    assert group['years'] == {} and group['regimes'] == {}
    assert group['analysis_revision'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert {r['kind'] for r in group['resources']} >= {'metadata', 'plan', 'report'}
    assert group['group_summary']['control_label'] == '持1'
    assert group['group_summary']['variant_count'] == 2
    core = group['core_results']
    assert core['kind'] == 'strategy_parameter_comparison' and core['status'] == 'available'
    assert [r['name'] for r in core['records']] == ['Baseline · 持1', '实验组 · 持5']
    assert [r['metrics']['annualized_return'] for r in core['records']] == [.26, .37]
    originals = {r['id']: r for r in snapshot['records']}
    for r in core['records']:
        for key in ('metrics', 'window', 'seeds', 'source'):
            assert r[key] == originals[r['id']][key]


@pytest.mark.parametrize('changes', [
    {'kind': 'model'}, {'current_baseline': False}, {'current_bt': True}, {'is_baseline': False},
    {'baseline_ref': 'bt/control'}, {'metrics': {'annualized_return': .9}},
    {'promoted_to': ''}, {'bt_version': 'v6'}, {'metrics_source': {'path': 'fake.json'}},
    {'reports': ['../outside.html']}, {'analysis': {'evidence': ['../outside.json']}},
    {'experiment_group': {'control_ref': 'bt/control', 'members': [{'ref': 'bt/control', 'label': 'a'}], 'axes': [], 'fixed_summary': ''}},
    {'experiment_group': {'control_ref': 'bt/control', 'members': [{'ref': 'bt/control', 'label': 'a'}, {'ref': 'bt/control', 'label': 'b'}], 'axes': ['hold_thresh'], 'fixed_summary': 'x'}},
])
def test_invalid_group_metadata_cannot_hide_sources_or_claim_authority(tmp_path, changes):
    c, _, _ = setup_group(tmp_path, **changes)
    result = c.snapshot()
    assert any(e.get('exp_id') == 'study/top5' for e in result['sources']['discovery']['errors'])
    assert {r['id'] for r in result['experiment_records']} == {'bt/control', 'bt/variant'}


@pytest.mark.parametrize('failure', ['missing', 'failed', 'model', 'window', 'cost'])
def test_bad_or_incompatible_source_never_disappears_behind_a_group(tmp_path, failure):
    c, _, rows = setup_group(tmp_path)
    source = tmp_path / rows[1]['eval_output']
    if failure == 'missing':
        source.unlink()
    elif failure in ('window', 'cost'):
        doc = json.loads(source.read_text())
        if failure == 'window':
            doc['backtest_window'] = ['2020-08-03', '2026-08-30']
        else:
            doc['fees']['open_cost'] = .01
        source.write_text(json.dumps(doc))
    else:
        rows[1]['state' if failure == 'failed' else 'model_ref'] = 'failed' if failure == 'failed' else 'model/v4'
        c.registry.write_text('\n'.join(json.dumps(r) for r in rows))
    snapshot = c.snapshot()
    assert {r['id'] for r in snapshot['experiment_records']} == {'bt/control', 'bt/variant', 'study/top5'}
    group = next(r for r in snapshot['experiment_records'] if r['id'] == 'study/top5')
    assert group['core_results']['status'] == 'unavailable'
    assert group['core_results']['records'] == [] and group['warnings']


def test_group_conclusion_updates_only_its_metadata_and_rejects_stale_revision(tmp_path):
    c, path, _ = setup_group(tmp_path)
    raw_registry = c.registry.read_bytes()
    members = {p: p.read_bytes() for p in (tmp_path / 'backtest/experiments/strategies/bt').rglob('*') if p.is_file()}
    group = c.snapshot()['experiment_records'][0]
    payload = {'id': group['id'], 'conclusion': '五日持仓改善收益', 'revision': group['analysis_revision']}
    result = ResearchAnnotations(tmp_path).update(payload)
    assert result['analysis']['conclusion'] == '五日持仓改善收益'
    assert result['analysis_revision'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert c.registry.read_bytes() == raw_registry
    assert all(p.read_bytes() == raw for p, raw in members.items())
    with pytest.raises(ConflictError):
        ResearchAnnotations(tmp_path).update(payload)


def test_group_detail_links_open_member_reports_without_extra_history_report_buttons(tmp_path):
    c, _, rows = setup_group(tmp_path)
    report = 'backtest/experiments/strategies/bt/variant/reports/source.html'
    target = tmp_path / report
    target.parent.mkdir(parents=True)
    target.write_text('<p>Frozen original report</p>')
    rows[1]['detail_report'] = report
    c.registry.write_text('\n'.join(json.dumps(r) for r in rows))
    snapshot = c.snapshot()
    source = next(r for r in snapshot['records'] if r['id'] == 'bt/variant')
    original = next(r for r in source['resources'] if r['kind'] == 'report')
    group = snapshot['experiment_records'][0]
    linked = next(r for r in group['resources'] if r['id'] == original['id'])
    assert linked['kind'] == 'variant_report' and linked['name'].startswith('持5')
    assert c.resource(linked['id']) == target
    assert original['kind'] == 'report' and original['name'] == 'source.html'
    assert [r['name'] for r in group['resources'] if r['kind'] == 'report'] == ['summary.html']


@pytest.mark.parametrize('failure', [None, 'hash', 'cost', 'window', 'benchmark', 'yaml', 'empty_benchmark', 'csv'])
def test_authenticated_runtime_configs_disambiguate_omitted_data_version(tmp_path, failure):
    import yaml
    c, path, rows = setup_group(tmp_path)
    metadata = json.loads(path.read_text())
    metadata['experiment_group']['axes'] = ['risk_degree']
    path.write_text(json.dumps(metadata))
    rows[1]['data_version'] = '2026-10-28'
    c.registry.write_text('\n'.join(json.dumps(r) for r in rows))
    for index, r in enumerate(rows):
        archive = tmp_path / 'backtest/experiments/strategies' / r['exp_id']
        (archive / 'configs').mkdir()
        (archive / 'data').mkdir()
        (archive / 'data/bench_ew_all.csv').write_text('date,return\n2026-01-01,0.01\n')
        if index and failure == 'benchmark':
            (archive / 'data/bench_ew_all.csv').write_text('date,return\n2026-01-01,0.02\n')
        if index and failure == 'empty_benchmark':
            (archive / 'data/bench_ew_all.csv').write_text('')
        if index and failure == 'csv':
            (archive / 'data/bench_ew_all.csv').write_text('date,return\n2026-01-01,' + '1' * 200000 + '\n')
        config = {'strategy': {'class': 'TopkDropoutStrategy', 'topk': 5, 'n_drop': 1,
                               'hold_thresh': 1, 'kwargs': {'risk_degree': .9 if index == 0 else 1.}},
                  'universe_filter': {'st_daily': f'/old-run-{index}/st_daily.csv', 'pool': 'all'},
                  'backtest': {'account': 300000, 'start_time': '2020-08-03', 'end_time': '2026-10-28',
                               'benchmark': {'equal_weight_csv': f'/old-run-{index}/bench_ew_all.csv'},
                               'exchange_kwargs': {'open_cost': .0000954, 'close_cost': .0005954}}}
        if index and failure == 'cost':
            config['backtest']['exchange_kwargs']['open_cost'] = .01
        if index and failure == 'window':
            config['backtest']['end_time'] = '2026-10-29'
        config_bytes = yaml.safe_dump(config).encode()
        if index and failure == 'yaml':
            config_bytes = b'{{{{'
        (archive / 'configs/config.yaml').write_bytes(config_bytes)
        source = tmp_path / r['eval_output']
        doc = json.loads(source.read_text())
        doc['provenance'] = {'config_sha256': hashlib.sha256(config_bytes).hexdigest(),
                             'source_pred_sha256': 'a' * 64, 'st_daily_sha256': 'b' * 64,
                             'model_manifest_sha256': 'c' * 64, 'forward_pred_sha256': 'd' * 64,
                             'evaluation_mode': 'frozen_parameter_diagnostic_not_selection'}
        if index and failure == 'hash':
            doc['provenance']['st_daily_sha256'] = 'e' * 64
        source.write_text(json.dumps(doc))
    result = c.snapshot()
    group = next(r for r in result['experiment_records'] if r['id'] == 'study/top5')
    assert group['core_results']['status'] == ('available' if failure is None else 'unavailable')
    assert len(result['experiment_records']) == (1 if failure is None else 3)
