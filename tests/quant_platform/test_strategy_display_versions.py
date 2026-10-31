"""Strategy labels use s while registry identities and model versions stay intact."""

import hashlib
from pathlib import Path

import pytest

from quant_platform.research import ResearchCatalog
from tests.quant_platform.test_strategy_columns import setup
from tests.quant_platform.test_strategy_groups import setup_group


def test_catalog_adds_strategy_display_version_without_changing_model_or_raw_version(tmp_path):
    c, strategy = setup(tmp_path)
    before_registry = c.registry.read_bytes()
    source = tmp_path / strategy['eval_output']
    before_source = source.read_bytes()
    snapshot = c.snapshot()
    benchmark = next(r for r in snapshot['benchmarks'] if r['id'] == strategy['exp_id'])
    assert benchmark['version'] == 'v5' and benchmark.get('display_version') == 's5'
    assert benchmark['model_summary']['version'] == 'v2'
    assert next(r for r in snapshot['benchmarks'] if r['kind'] == 'model' and r['version'] == 'v2').get('display_version', 'v2') == 'v2'
    assert c.registry.read_bytes() == before_registry and source.read_bytes() == before_source


def test_group_baseline_and_core_rows_gain_display_versions_without_rewriting_them(tmp_path):
    c, _, _ = setup_group(tmp_path)
    snapshot = c.snapshot()
    group = snapshot['experiment_records'][0]
    assert group['version'] == '' and group.get('display_version') == ''
    baseline = group['comparison_baseline']
    assert baseline['version'] == 'v2' and baseline.get('display_version') == 's2'
    control = group['core_results']['records'][0]
    assert control['version'] == 'v2' and control.get('display_version') == 's2'
    assert group['model_summary']['ref'] == 'model/v2'


@pytest.mark.parametrize('value,kind,track,expected', [
    ('v5', 'strategy', 'all_a', 's5'), ('BT v3', 'strategy', 'all_a', 's3'),
    ('s2', 'strategy', 'all_a', 's2'), ('v1.1', 'strategy', 'all_a', 's1.1'),
    ('v2', 'model', 'all_a', 'v2'), ('v1.0', 'strategy', 'csi1000', 'v1.0'),
    ('B4-S', 'strategy', 'csi1000', 'B4-S'), ('BT v5 draft', 'strategy', 'all_a', 'BT v5 draft'),
    ('model-v2', 'strategy', 'all_a', 'model-v2'), ('', 'strategy', 'all_a', ''),
])
def test_shared_strategy_label_only_converts_declared_whole_all_a_versions(value, kind, track, expected):
    from backtest.scripts.research_labels import strategy_display_version
    assert strategy_display_version(value, kind=kind, track=track) == expected


def test_real_current_strategy_and_benchmark_metrics_keep_their_original_versions():
    root = Path(__file__).resolve().parents[2]
    registry = root / 'backtest/experiments/registry.jsonl'
    before = hashlib.sha256(registry.read_bytes()).hexdigest()
    snapshot = ResearchCatalog(root).snapshot()
    assert len(snapshot['benchmarks']) == 9
    current_model = next(r for r in snapshot['benchmarks'] if r['kind'] == 'model' and r['current'])
    current_strategy = next(r for r in snapshot['benchmarks'] if r['kind'] == 'strategy' and r['current'])
    assert current_model['version'] == 'v2'
    assert current_strategy['version'] == 'v5' and current_strategy.get('display_version') == 's5'
    assert current_strategy['id'] == 'baseline/phase-m-v1-bt-v5'
    assert current_strategy['model_summary']['version'] == 'v2'
    assert {r.get('display_version') for r in snapshot['benchmarks'] if r['kind'] == 'strategy'} == {'s1', 's2', 's3', 's4', 's5'}
    assert hashlib.sha256(registry.read_bytes()).hexdigest() == before


def test_display_names_convert_explicit_strategy_labels_and_keep_raw_text(tmp_path):
    from tests.quant_platform.test_strategy_columns import update
    c, strategy = setup(tmp_path)
    update(tmp_path, strategy['exp_id'], title='BT v5 · v2模型',
           analysis={'conclusion': 'BT v5对照v2模型，维持原指标'})
    record = next(r for r in c.snapshot()['records'] if r['id'] == strategy['exp_id'])
    assert record['name'] == 'BT v5 · v2模型'
    assert record.get('display_name') == 's5 · v2模型'
    assert record['analysis']['conclusion'] == 'BT v5对照v2模型，维持原指标'


@pytest.mark.parametrize('value,kind,track,expected', [
    ('模型v2，对照BT v5，原模型v4。', 'strategy', 'all_a', '模型v2，对照s5，原模型v4。'),
    ('BT v3与BT v4对照', 'strategy', 'all_a', 's3与s4对照'),
    ('BT v1.0', 'strategy', 'csi1000', 'BT v1.0'),
    ('模型v2，对照BT v5', 'model', 'all_a', '模型v2，对照BT v5'),
    ('s5对应v2模型', 'strategy', 'all_a', 's5对应v2模型'),
])
def test_shared_strategy_reference_text_never_relabels_bare_model_versions(value, kind, track, expected):
    from backtest.scripts.research_labels import strategy_reference_text
    assert strategy_reference_text(value, kind=kind, track=track) == expected
