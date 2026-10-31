"""History annotations expose member promotions without promoting a study group."""

import json
from pathlib import Path

import pytest

from quant_platform.research import ResearchCatalog
from tests.quant_platform.test_strategy_groups import setup_group


def test_group_history_lists_verified_promoted_members_and_its_separate_control(tmp_path):
    c, path, rows = setup_group(tmp_path)
    rows[1].update(report_kind='phase_m_v1_bt_baseline', bt_version='v5')
    c.registry.write_text('\n'.join(json.dumps(row) for row in rows))
    snapshot = c.snapshot()
    group = snapshot['experiment_records'][0]
    history = group.get('strategy_history', {})
    assert [(p['ref'], p['display_version'], p['label'], p['current']) for p in history.get('promotions', [])] == [
        ('bt/control', 's2', '持1', False), ('bt/variant', 's5', '持5', True)]
    assert history['baseline'] == {'ref': 'bt/control', 'version': 'v2', 'display_version': 's2',
                                   'label': '持1', 'initial': False, 'scope': 'group_control', 'available': True}
    assert group['promoted_version'] == '' and not group['current'] and not group['is_baseline']
    assert group['version'] == '' and all(v is None for v in group['metrics'].values())


@pytest.mark.parametrize('failure', ['missing', 'failed', 'kind', 'track'])
def test_invalid_group_members_cannot_publish_member_promotions(tmp_path, failure):
    c, _, rows = setup_group(tmp_path)
    rows[1]['baseline_ref'] = 'bt/control'
    c.registry.write_text('\n'.join(json.dumps(row) for row in rows))
    if failure == 'missing':
        (tmp_path / rows[1]['eval_output']).unlink()
    elif failure == 'failed':
        rows[1]['state'] = 'failed'
        c.registry.write_text('\n'.join(json.dumps(row) for row in rows))
    else:
        path = tmp_path / 'backtest/experiments/strategies/bt/variant/metadata.json'
        value = json.loads(path.read_text())
        value['kind' if failure == 'kind' else 'track'] = 'model' if failure == 'kind' else 'csi1000'
        path.write_text(json.dumps(value))
    snapshot = c.snapshot()
    group = next(r for r in snapshot['experiment_records'] if r['id'] == 'study/top5')
    assert group['core_results']['status'] == 'unavailable'
    assert group.get('strategy_history', {}).get('promotions') == []
    assert group['strategy_history']['baseline']['available'] is False
    assert group['strategy_history']['historical_baselines'][0]['ref'] == 'bt/control'
    assert group['strategy_history']['historical_baselines'][0]['available'] is False


def test_history_keeps_different_member_historical_baselines_separate_from_group_control(tmp_path):
    c, path, rows = setup_group(tmp_path)
    rows[1]['baseline_ref'] = 'bt/control'
    c.registry.write_text('\n'.join(json.dumps(row) for row in rows))
    group = c.snapshot()['experiment_records'][0]
    assert group.get('strategy_history', {}).get('historical_baselines') == [
        {'ref': 'bt/control', 'version': 'v2', 'display_version': 's2', 'label': 'bt/control',
         'member_refs': ['bt/variant'], 'member_labels': ['持5'], 'available': True}]
    assert group['strategy_history']['baseline']['scope'] == 'group_control'


def test_real_history_covers_all_eight_rows_and_preserves_registry_and_versions():
    root = Path(__file__).resolve().parents[2]
    registry = root / 'backtest/experiments/registry.jsonl'
    before = registry.read_bytes()
    snapshot = ResearchCatalog(root).snapshot()
    history = {r['id']: r for r in snapshot['experiment_records'] if r['kind'] == 'strategy'}
    assert len(history) == 8
    top5 = history['strategy-group/v2-top5-hold-force100']
    assert [p['display_version'] for p in top5.get('strategy_history', {}).get('promotions', [])] == ['s2', 's3', 's5']
    assert top5['strategy_history']['baseline']['display_version'] == 's2'
    assert [p['display_version'] for p in history['strategy-group/v4-cohort-force100-refill']['strategy_history']['promotions']] == ['s4']
    first = history['baseline/phase-m-v1-bt-v1']['strategy_history']
    assert first['baseline']['initial'] and first['promotions'][0]['display_version'] == 's1'
    assert first['promotions'][0]['label'] == 'Top5换1·持1'
    assert history['regime-adapt/m0h20es-all-ladder-k5h5-bt']['strategy_history']['baseline']['display_version'] == 's2'
    assert history['regime-adapt/m0h20rankices-all-ladder-k3h5-e20260830-bt']['strategy_history']['baseline']['display_version'] == 's4'
    assert len(snapshot['benchmarks']) == 9
    assert next(r for r in snapshot['benchmarks'] if r['kind'] == 'strategy' and r['current'])['version'] == 'v5'
    assert registry.read_bytes() == before
    assert all('strategy_history' not in r for r in [*snapshot['records'], *snapshot['benchmarks']])


@pytest.mark.parametrize('target_changes', [
    {'kind': 'model'}, {'track': 'csi1000'}, {'source_status': 'invalid'},
    {'is_baseline': False}, {'status': 'failed'},
])
def test_individual_cannot_claim_promotion_to_an_unverified_or_unrelated_benchmark(target_changes):
    from quant_platform.strategy_history import strategy_history
    candidate = {'id': 'trial', 'kind': 'strategy', 'track': 'all_a', 'status': 'completed',
                 'source_status': 'available', 'promoted_to': 'unrelated', 'comparison_baseline': {}}
    target = {'id': 'unrelated', 'kind': 'strategy', 'track': 'all_a', 'status': 'completed',
              'source_status': 'available', 'is_baseline': True, 'version': 'v9', **target_changes}
    projected = strategy_history(candidate, {'trial': candidate, 'unrelated': target}, {'unrelated': target})
    assert projected['promotions'] == []


def test_overlapping_study_groups_publish_no_promotions_and_do_not_hide_sources(tmp_path):
    c, path, _ = setup_group(tmp_path)
    second = json.loads(path.read_text())
    second['exp_id'] = 'study/overlap'
    second['archive_path'] = 'backtest/experiments/strategies/parameter-studies/overlap'
    target = tmp_path / second['archive_path'] / 'metadata.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(second))
    snapshot = c.snapshot()
    assert {r['id'] for r in snapshot['experiment_records']} == {'bt/control', 'bt/variant', 'study/top5', 'study/overlap'}
    groups = [r for r in snapshot['experiment_records'] if r.get('entity_type') == 'experiment_group']
    assert all(g['strategy_history']['promotions'] == [] for g in groups)
    assert all(g['core_results']['status'] == 'unavailable' for g in groups)
