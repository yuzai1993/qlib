"""Contract tests for four-class market-level research (synthetic inputs only)."""
import json

import joblib
import numpy as np
import pandas as pd
import pytest

from backtest.market_style import modeling, evaluation, reporting


def test_confusion_and_absent_class_metrics_are_fixed_four_class():
    p = np.array([[.8, .1, .05, .05], [.1, .8, .05, .05], [.7, .1, .1, .1]])
    result = evaluation.classification_metrics(['D', 'F', 'F'], p)
    assert result['accuracy'] == pytest.approx(2 / 3)
    assert result['macro_f1'] == pytest.approx(1 / 3)
    assert result['balanced_accuracy'] == pytest.approx(.75)
    assert result['per_class']['T']['recall'] is None
    assert result['per_class']['P']['precision'] == 0
    assert result['confusion_matrix'] == [[1, 0, 0, 0], [1, 1, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    json.dumps(result, allow_nan=False)


def test_probability_column_order_is_respected_and_invalid_probabilities_rejected():
    result = evaluation.classification_metrics(['D', 'P'], [[.1, .1, .1, .7], [.7, .1, .1, .1]], classes=['P', 'T', 'F', 'D'])
    assert result['accuracy'] == 1
    for p in ([[1, 0, 0]], [[.7, .2, .2, 0]], [[np.nan, .3, .3, .4]], [[-.1, .3, .4, .4]]):
        with pytest.raises(ValueError):
            evaluation.classification_metrics(['D'], p)


def test_temporal_split_excludes_cross_boundary_targets():
    dates = pd.to_datetime(['2017-11-29', '2017-11-30', '2018-01-02', '2020-06-30', '2020-07-30', '2020-07-31', '2020-08-03', '2026-07-31'])
    targets = pd.DataFrame({'target_date': pd.to_datetime(['2017-11-30', '2017-12-01', '2018-01-03', '2020-07-01', '2020-07-31', '2020-08-03', '2020-08-04', '2026-08-03']), 'label': ['D'] * 8}, index=pd.DatetimeIndex(dates, name='date'))
    inner_train, inner_eval = modeling.temporal_split(targets, 'inner')
    final_train, final_eval = modeling.temporal_split(targets, 'final')
    assert list(targets.index[inner_train]) == [pd.Timestamp('2017-11-29')]
    assert list(targets.index[inner_eval]) == [pd.Timestamp('2018-01-02')]
    assert targets.index[final_train][-1] == pd.Timestamp('2020-07-30')
    assert list(targets.index[final_eval]) == [pd.Timestamp('2020-08-03')]


def fit_inputs(tmp_path):
    train = pd.bdate_range('2017-01-02', periods=48)
    valid = pd.bdate_range('2018-01-02', periods=8)
    final = pd.bdate_range('2020-08-03', periods=8)
    dates = train.append(valid).append(final).rename('date')
    x = pd.DataFrame({'a': np.tile([1., 2., 3., 4.], 16), 'b': np.tile([1., np.nan, 2., 3.], 16), 'constant': 1.}, index=dates)
    x.loc[valid, 'b'] = 99999
    y = pd.DataFrame({'target_date': dates + pd.offsets.BDay(), 'label': np.tile(['D', 'F', 'T', 'P'], 16)}, index=dates)
    fp, tp, mp = tmp_path / 'features.parquet', tmp_path / 'targets.parquet', tmp_path / 'manifest.json'
    x.to_parquet(fp)
    y.to_parquet(tp)
    mp.write_text(json.dumps({'core_features': ['a', 'b'], 'full_features': list(x.columns), 'data_version': '2026-11-08', 'features': {'a': {'group': 'trend'}, 'b': {'group': 'liquidity'}, 'constant': {'group': 'calendar'}}}))
    return fp, tp, mp


def test_fit_saves_real_model_train_only_preprocessing_and_refuses_overwrite(tmp_path):
    fp, tp, mp = fit_inputs(tmp_path)
    out = tmp_path / 'inner'
    receipt = modeling.run_fit_job(fp, tp, mp, 'logistic_full', 42, 'inner', out)
    artifact = joblib.load(out / 'model.joblib')
    imputer = artifact.named_steps['imputer']
    assert imputer.statistics_.tolist() == [2.5, 2.0]
    assert receipt['feature_names'] == ['a', 'b']
    assert receipt['train_coverage']['n_days'] == 48
    assert receipt['eval_coverage']['n_days'] == 8
    assert receipt['fit_count'] == 1
    predictions = pd.read_csv(out / 'predictions.csv')
    assert list(predictions.columns) == ['date', 'target_date', 'label', 'p_D', 'p_F', 'p_T', 'p_P']
    assert np.allclose(predictions[['p_D', 'p_F', 'p_T', 'p_P']].sum(axis=1), 1)
    assert modeling.file_fingerprint(out / 'model.joblib') == receipt['artifacts']['model']['sha256']
    with pytest.raises(FileExistsError):
        modeling.run_fit_job(fp, tp, mp, 'logistic_full', 42, 'inner', out)


def test_final_lgb_requires_matching_inner_selection_before_creating_output(tmp_path):
    fp, tp, mp = fit_inputs(tmp_path)
    out = tmp_path / 'final'
    with pytest.raises(ValueError, match='inner'):
        modeling.run_fit_job(fp, tp, mp, 'lgb_full', 42, 'final', out)
    assert not out.exists()


def test_fit_rejects_missing_training_class(tmp_path):
    fp, tp, mp = fit_inputs(tmp_path)
    y = pd.read_parquet(tp)
    y.loc[y.label == 'P', 'label'] = 'D'
    y.to_parquet(tp)
    with pytest.raises(ValueError, match='class'):
        modeling.run_fit_job(fp, tp, mp, 'logistic_full', 42, 'inner', tmp_path / 'out')


def test_block_bootstrap_is_reproducible_and_switch_diagnostics_use_target_dates():
    dates = pd.bdate_range('2021-01-04', periods=80)
    frame = pd.DataFrame({'target_date': dates + pd.offsets.BDay(), 'label': ['D'] * 30 + ['P'] * 20 + ['T'] * 30}, index=pd.DatetimeIndex(dates, name='date'))
    for cls in ['D', 'F', 'T', 'P']:
        frame['p_' + cls] = .7 * (frame.label == cls) + .075
    frame[[f'p_{c}' for c in 'DFTP']] /= frame[[f'p_{c}' for c in 'DFTP']].sum(axis=1).to_numpy()[:, None]
    a = evaluation.block_bootstrap(frame, replicates=20)
    b = evaluation.block_bootstrap(frame, replicates=20)
    assert a == b
    assert a['block_months'] == 3
    assert a['metrics']['accuracy'] == [1., 1.]
    diag = evaluation.temporal_diagnostics(frame)
    assert len(diag['p_episodes']) == 1
    assert diag['p_episodes'][0]['n_days'] == 20
    assert diag['transition_window']['n_days'] == 22


def test_report_escapes_audits_and_links_hub(tmp_path):
    bundle = {'classification_protocol': 'monthly-k4-next-day-v1', 'classes': list('DFTP'), 'seeds': [42, 1000, 2000, 3000, 4000], 'data_version': '2026-11-08', 'chosen_candidate': 'logistic_full', 'candidates': {}, 'baselines': {}}
    detail = tmp_path / 'detail.html'
    hub = tmp_path / 'hub.html'
    reporting.render_report(bundle, detail, label_audit={'note': '<script>x</script>'}, hub_path=hub)
    html = detail.read_text()
    assert '<script>x</script>' not in html
    assert '&lt;script&gt;' in html
    assert '事后' in html
    assert 'detail.html' in hub.read_text()


def test_evaluate_requires_complete_archive_before_writing_outputs(tmp_path):
    archive = tmp_path / 'archive'
    archive.mkdir()
    with pytest.raises(ValueError, match='40'):
        evaluation.evaluate_archive(archive)
    assert not (archive / 'metrics').exists()


@pytest.fixture(scope='module')
def forty_fit_archive(tmp_path_factory):
    import shutil

    tmp_path = tmp_path_factory.mktemp('market_style_forty_fits')
    fp, tp, mp = fit_inputs(tmp_path)
    archive = tmp_path / 'archive'
    for candidate in modeling.CANDIDATES:
        for seed in modeling.SEEDS:
            inner = tmp_path / 'actual' / candidate / str(seed) / 'inner'
            for phase in ['inner', 'final']:
                actual = tmp_path / 'actual' / candidate / str(seed) / phase
                archived_inner = archive / 'docs' / candidate / str(seed) / 'inner/receipt.json'
                modeling.run_fit_job(fp, tp, mp, candidate, seed, phase, actual, archived_inner if phase == 'final' else None)
                dest = archive / 'docs' / candidate / str(seed) / phase
                dest.mkdir(parents=True)
                shutil.copy2(actual / 'receipt.json', dest / 'receipt.json')
    return archive, tmp_path


def test_actual_forty_fit_receipts_evaluate_portably_and_detect_corruption(forty_fit_archive):
    """Removing fingerprints/seed alignment or ranking on final scores breaks this."""
    archive, tmp_path = forty_fit_archive
    bundle = evaluation.evaluate_archive(archive, bootstrap_replicates=5)
    assert bundle['n_fit_receipts'] == 40
    assert bundle['evaluation_used_for_selection'] is False
    assert bundle['baselines']['training_prior']['probabilities'] == {'D': .25, 'F': .25, 'T': .25, 'P': .25}
    ranked = bundle['inner_ranking']
    assert bundle['chosen_candidate'] == ranked[0]
    for candidate in modeling.CANDIDATES:
        result = bundle['candidates'][candidate]
        assert len(result['models']) == len(result['predictions']) == 5
        assert result['evaluation']['n_days'] == 8
        assert len(result['receipts']['inner']) == len(result['receipts']['final']) == 5
    output = archive / 'report.html'
    reporting.render_report(bundle, output)
    assert (archive / 'confusion_lgb_full.svg').exists()
    assert (archive / 'metrics/evaluation.json').exists()
    prediction = tmp_path / 'actual/logistic_full/42/final/predictions.csv'
    original = prediction.read_bytes()
    try:
        prediction.write_text(prediction.read_text().replace('2020-08-03', '2020-08-02'))
        with pytest.raises(ValueError, match='fingerprint'):
            evaluation.evaluate_archive(archive, bootstrap_replicates=1)
    finally:
        prediction.write_bytes(original)


def test_final_lgb_rejects_uncompleted_inner_receipt(tmp_path):
    fp, tp, mp = fit_inputs(tmp_path)
    inner = tmp_path / 'inner'
    modeling.run_fit_job(fp, tp, mp, 'lgb_core', 42, 'inner', inner)
    receipt = json.loads((inner / 'receipt.json').read_text())
    receipt['status'] = 'failed'
    (inner / 'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='completed'):
        modeling.run_fit_job(fp, tp, mp, 'lgb_core', 42, 'final', tmp_path / 'final', inner / 'receipt.json')


@pytest.mark.parametrize('candidate', modeling.CANDIDATES)
def test_all_final_candidates_require_inner_provenance(tmp_path, candidate):
    fp, tp, mp = fit_inputs(tmp_path)
    with pytest.raises(ValueError, match='inner'):
        modeling.run_fit_job(fp, tp, mp, candidate, 42, 'final', tmp_path / 'final')
    assert not (tmp_path / 'final').exists()


def test_fitted_group_importance_maps_missing_indicators_and_saves_evidence(tmp_path):
    fp, tp, mp = fit_inputs(tmp_path)
    receipt = modeling.run_fit_job(fp, tp, mp, 'logistic_full', 42, 'inner', tmp_path / 'inner')
    record = receipt['artifacts']['feature_importance']
    importance = json.loads(open(record['path']).read())
    model = joblib.load(tmp_path / 'inner/model.joblib')
    coefficients = np.abs(model.named_steps['classifier'].coef_).mean(axis=0)
    assert importance['method'] == 'mean_absolute_standardized_multiclass_coefficient'
    assert importance['source'] == 'fitted_model_training_only'
    assert importance['used_for_selection'] is False
    assert importance['groups']['trend']['raw_importance'] == pytest.approx(coefficients[0])
    assert importance['groups']['liquidity']['raw_importance'] == pytest.approx(coefficients[1] + coefficients[2])
    assert importance['features'][2]['source_feature'] == 'b'
    assert importance['features'][2]['is_missing_indicator'] is True
    assert importance['model']['sha256'] == receipt['artifacts']['model']['sha256']
    assert sum(g['normalized_importance'] for g in importance['groups'].values()) == pytest.approx(1)
    assert modeling.file_fingerprint(record['path']) == record['sha256']


@pytest.mark.parametrize('tamper', ['missing', 'stale_hash', 'foreign_path', 'identity', 'inputs', 'rounds', 'config_rounds'])
def test_archive_rejects_broken_final_inner_chain_before_outputs(forty_fit_archive, tmp_path, tamper):
    archive, _ = forty_fit_archive
    path = archive / 'docs/lgb_full/42/final/receipt.json'
    original = path.read_bytes()
    item = json.loads(original)
    extra_restore = None
    try:
        if tamper == 'missing':
            item['inner_receipt']['path'] = str(tmp_path / 'missing.json')
        elif tamper == 'stale_hash':
            item['inner_receipt']['sha256'] = 'deadbeef'
        elif tamper == 'foreign_path':
            item['inner_receipt'] = modeling.artifact_record(archive / 'docs/lgb_full/1000/inner/receipt.json')
        elif tamper in ['identity', 'inputs']:
            # Retained matching path/hash still has to describe this final fit.
            inner_path = archive / 'docs/lgb_full/42/inner/receipt.json'
            extra_restore = inner_path, inner_path.read_bytes()
            inner = json.loads(extra_restore[1])
            if tamper == 'identity':
                inner['candidate'] = 'lgb_core'
            else:
                inner['inputs']['targets']['sha256'] = 'deadbeef'
            inner_path.write_text(json.dumps(inner))
            item['inner_receipt'] = modeling.artifact_record(inner_path)
        elif tamper == 'rounds':
            item['selected_iteration'] += 1
        elif tamper == 'config_rounds':
            config_path = __import__('pathlib').Path(item['artifacts']['config']['path'])
            extra_restore = config_path, config_path.read_bytes()
            config = json.loads(extra_restore[1])
            config['parameters']['n_estimators'] += 1
            config_path.write_text(json.dumps(config))
            item['artifacts']['config'] = modeling.artifact_record(config_path)
        path.write_text(json.dumps(item))
        with pytest.raises(ValueError):
            evaluation.evaluate_archive(archive, result_dir=tmp_path / 'result', bootstrap_replicates=1)
        assert not (tmp_path / 'result').exists()
    finally:
        path.write_bytes(original)
        if extra_restore:
            extra_restore[0].write_bytes(extra_restore[1])


def test_group_importance_aggregates_five_seeds_and_is_rendered(forty_fit_archive, tmp_path):
    archive, _ = forty_fit_archive
    bundle = evaluation.evaluate_archive(archive, tmp_path / 'metrics', bootstrap_replicates=1)
    item = bundle['candidates']['logistic_full']['feature_importance']
    assert item['seed_aggregation'] == 'arithmetic mean and sample std of within-seed normalized group importance; all five seeds'
    assert item['used_for_selection'] is False
    assert set(item['groups']) == {'trend', 'liquidity'}
    trend = [seed['groups']['trend']['normalized_importance'] for seed in item['per_seed'].values()]
    assert item['groups']['trend']['mean'] == pytest.approx(np.mean(trend))
    assert item['groups']['trend']['std'] == pytest.approx(np.std(trend, ddof=1))
    assert modeling.file_fingerprint(item['artifact']['path']) == item['artifact']['sha256']
    report = tmp_path / 'report.html'
    reporting.render_report(bundle, report)
    content = report.read_text()
    assert '按特征组解释' in content
    assert 'feature_importance_logistic_full.json' in content
    assert 'mean_absolute_standardized_multiclass_coefficient' in content
    assert 'liquidity' in content
