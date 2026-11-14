"""One-fit temporal jobs for the frozen retrospective market-style classifier.

This module never registers experiments or controls the research budget. Callers must
wrap every invocation with the formal Harness fit_start/result transaction.
"""
from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

CLASSES = ('D', 'F', 'T', 'P')
SEEDS = (42, 1000, 2000, 3000, 4000)
CANDIDATES = ('logistic_full', 'lgb_core', 'lgb_full', 'extra_full')
PROTOCOL = 'monthly-k4-next-day-v1'
WINDOWS = {
    'inner': ('2004-01-02', '2017-11-30', '2018-01-02', '2020-06-30'),
    'final': ('2004-01-02', '2020-07-31', '2020-08-03', '2026-07-31'),
}


def file_fingerprint(path):
    """SHA256 of actual bytes, streamed to keep model hashing bounded."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def artifact_record(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': file_fingerprint(path), 'size_bytes': path.stat().st_size}


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def temporal_split(targets, phase):
    """Signal AND target must stay in the declared sequential segment."""
    if phase not in WINDOWS:
        raise ValueError('phase must be inner or final')
    start, end, eval_start, eval_end = map(pd.Timestamp, WINDOWS[phase])
    target_dates = pd.to_datetime(targets['target_date'])
    if (target_dates.notna() & (target_dates <= targets.index)).any():
        raise ValueError('target_date must be after signal date')
    train = (targets.index >= start) & (targets.index <= end) & (target_dates <= end)
    evaluate = (targets.index >= eval_start) & (targets.index <= eval_end) & (target_dates <= eval_end)
    return np.asarray(train), np.asarray(evaluate)


def _coverage(frame):
    return {
        'n_days': len(frame), 'signal_start': str(frame.index.min().date()),
        'signal_end': str(frame.index.max().date()),
        'target_start': str(pd.to_datetime(frame.target_date).min().date()),
        'target_end': str(pd.to_datetime(frame.target_date).max().date()),
    }


def _training_features(train, requested):
    kept, dropped = [], {}
    for name in requested:
        series = train[name]
        if series.isna().all():
            dropped[name] = 'all_missing_in_training'
        elif series.nunique(dropna=True) <= 1:
            dropped[name] = 'constant_in_training'
        elif any(series.equals(train[prior]) for prior in kept):
            dropped[name] = 'exact_duplicate_in_training'
        else:
            kept.append(name)
    if not kept:
        raise ValueError('no nonconstant training features')
    return kept, dropped


def _estimator(candidate, seed, rounds):
    from sklearn.ensemble import ExtraTreesClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    if candidate.startswith('lgb_'):
        from lightgbm import LGBMClassifier
        return LGBMClassifier(
            objective='multiclass', num_class=4, num_leaves=15, max_depth=5,
            min_child_samples=40, learning_rate=.03, colsample_bytree=.8,
            subsample=.8, subsample_freq=1, reg_lambda=5., n_estimators=rounds,
            random_state=seed, n_jobs=4, verbosity=-1, deterministic=True, force_col_wise=True,
        )
    steps = [('imputer', SimpleImputer(strategy='median', add_indicator=True))]
    if candidate == 'logistic_full':
        steps += [('scaler', StandardScaler()), ('classifier', LogisticRegression(C=.1, max_iter=3000, solver='lbfgs', random_state=seed))]
    else:
        steps += [('classifier', ExtraTreesClassifier(n_estimators=500, max_depth=8, min_samples_leaf=20, max_features=.7, random_state=seed, n_jobs=4))]
    return Pipeline(steps)


def _read_inputs(features, targets, manifest):
    x, y = pd.read_parquet(features), pd.read_parquet(targets)
    meta = json.loads(Path(manifest).read_text())
    for frame in (x, y):
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.name != 'date':
            raise ValueError('inputs require a DatetimeIndex named date')
        if frame.index.has_duplicates or not frame.index.is_monotonic_increasing or frame.index.hasnans or frame.index.tz is not None:
            raise ValueError('date index must be unique, sorted, non-null and timezone naive')
    if not x.index.equals(y.index):
        raise ValueError('feature and target date indices must match exactly')
    if x.columns.has_duplicates or not all(pd.api.types.is_numeric_dtype(dtype) for dtype in x.dtypes):
        raise ValueError('features must have unique numeric columns')
    if np.isinf(x.to_numpy(dtype=float)).any():
        raise ValueError('infinite features are forbidden')
    if not {'target_date', 'label'} <= set(y.columns):
        raise ValueError('targets require target_date and label')
    y['target_date'] = pd.to_datetime(y.target_date)
    return x, y, meta


def fitted_feature_importance(estimator, candidate, names, manifest, model_record):
    """Training-fitted model evidence only, with missing indicators assigned to source groups.

    These method-specific magnitudes describe a fitted model, not out-of-sample
    contribution or causal effects. Values must not be compared across methods.
    """
    sources = list(names)
    indicators = [False] * len(names)
    if candidate.startswith('lgb_'):
        values = estimator.booster_.feature_importance(importance_type='gain').astype(float)
        method = 'training_split_gain'
        limitation = 'Split gain favors available split opportunities and distributes correlated-feature credit arbitrarily.'
    else:
        classifier = estimator.named_steps['classifier']
        if candidate == 'logistic_full':
            values = np.abs(classifier.coef_).mean(axis=0)
            method = 'mean_absolute_standardized_multiclass_coefficient'
            limitation = 'Absolute coefficients lose direction and class specificity; correlated inputs and regularization redistribute coefficient mass.'
        else:
            values = classifier.feature_importances_
            method = 'training_impurity_decrease'
            limitation = 'Impurity importance favors split opportunities and can distribute correlated-feature credit arbitrarily.'
        extra = estimator.named_steps['imputer'].indicator_.features_
        sources += [names[index] for index in extra]
        indicators += [True] * len(extra)
    if len(values) != len(sources) or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError('invalid fitted feature importance')
    groups, features = {}, []
    total = float(np.sum(values))
    for source, indicator, value in zip(sources, indicators, values):
        group = manifest['features'][source]['group']
        raw = float(value)
        groups[group] = groups.get(group, 0.) + raw
        features.append({'source_feature': source, 'is_missing_indicator': indicator, 'group': group,
                         'raw_importance': raw, 'normalized_importance': raw / total if total else 0.})
    return {
        'source': 'fitted_model_training_only', 'method': method,
        'used_for_selection': False, 'model': model_record,
        'normalization': 'sum of nonnegative transformed-feature importances within this fitted seed; zero when all importances are zero',
        'status': 'available' if total else 'no_nonzero_model_importance',
        'limitations': [limitation, 'Descriptive fitted-model evidence, not causal or held-out attribution; magnitude is not comparable across model methods.', 'No formal evaluation observations or labels were used to calculate importance; no feature selection follows this diagnostic.'],
        'features': features,
        'groups': {group: {'raw_importance': raw, 'normalized_importance': raw / total if total else 0.} for group, raw in sorted(groups.items())},
    }


def run_fit_job(features, targets, manifest, candidate, seed, phase, output_dir, inner_receipt=None):
    """Perform exactly one classifier fit and persist its real artifacts.

    Every final job preserves the same candidate/seed/input inner receipt. Final
    LightGBM iterations come from it. Absolute paths permit receipt archiving.
    """
    if candidate not in CANDIDATES or seed not in SEEDS or phase not in WINDOWS:
        raise ValueError('unknown candidate, seed or phase')
    output = Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f'refusing to overwrite job artifacts: {output}')
    x, y, meta = _read_inputs(features, targets, manifest)
    inputs = {name: artifact_record(path) for name, path in [('features', features), ('targets', targets), ('manifest', manifest)]}
    train_mask, eval_mask = temporal_split(y, phase)
    yt, ye = y.loc[train_mask], y.loc[eval_mask]
    if yt.empty or ye.empty:
        raise ValueError('training and evaluation segments must be nonempty')
    if set(yt.label) != set(CLASSES):
        raise ValueError('all four training classes are required')
    if not set(ye.label) <= set(CLASSES):
        raise ValueError('unknown evaluation class')
    requested = meta['core_features' if candidate == 'lgb_core' else 'full_features']
    if not requested or len(requested) != len(set(requested)) or not set(requested) <= set(x.columns):
        raise ValueError('manifest features must be unique existing columns')
    names, dropped = _training_features(x.loc[train_mask], requested)
    if any(not isinstance(meta.get('features', {}).get(name, {}).get('group'), str) or not meta['features'][name]['group'].strip() for name in names):
        raise ValueError('manifest requires a nonempty feature group for every fitted feature')
    rounds = 1000 if candidate.startswith('lgb_') else None
    inner_record = None
    if phase == 'final':
        if inner_receipt is None:
            raise ValueError('every final fit requires a matching inner receipt')
        inner_record = artifact_record(inner_receipt)
        inner = json.loads(Path(inner_receipt).read_text())
        if inner.get('status') != 'completed' or inner.get('fit_count') != 1:
            raise ValueError('inner receipt must be a completed one-fit job')
        if (inner.get('candidate'), inner.get('seed'), inner.get('phase'), inner.get('classification_protocol')) != (candidate, seed, 'inner', PROTOCOL):
            raise ValueError('inner receipt identity mismatch')
        if inner.get('classes') != list(CLASSES) or inner.get('data_version') != meta['data_version']:
            raise ValueError('inner receipt class/data identity mismatch')
        if set(inner['inputs']) != set(inputs) or any(inner['inputs'][key]['sha256'] != value['sha256'] for key, value in inputs.items()):
            raise ValueError('inner receipt input fingerprints mismatch')
        if candidate.startswith('lgb_'):
            rounds = inner.get('selected_iteration')
            if not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= 1000:
                raise ValueError('inner receipt must contain a valid selected_iteration')
        # The receipt is evidence of a real completed fit, not an arbitrary round count.
        for record in inner['artifacts'].values():
            if file_fingerprint(record['path']) != record['sha256']:
                raise ValueError('inner artifact fingerprint mismatch')
    counts = yt.label.value_counts().reindex(CLASSES).astype(int)
    class_weights = np.sqrt(len(yt) / counts.to_numpy())
    class_weights /= class_weights.mean()
    encoded = yt.label.map({c: i for i, c in enumerate(CLASSES)}).to_numpy()
    weights = class_weights[encoded]
    estimator = _estimator(candidate, seed, rounds)
    import sklearn
    config = {
        'classification_protocol': PROTOCOL, 'candidate': candidate, 'seed': seed, 'phase': phase,
        'classes': list(CLASSES), 'requested_feature_names': requested, 'feature_names': names,
        'dropped_features': dropped, 'class_counts': counts.to_dict(),
        'class_weights': dict(zip(CLASSES, class_weights.tolist())), 'parameters': estimator.get_params(deep=False),
        'versions': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__, 'sklearn': sklearn.__version__},
        'inputs': inputs, 'inner_receipt': inner_record,
    }
    # Pipeline object-valued parameters are represented explicitly, not pickled in JSON.
    if not candidate.startswith('lgb_'):
        config['parameters'] = {name: step.get_params() for name, step in estimator.steps}
        config['parameters']['imputer']['missing_values'] = 'NaN'
    else:
        import lightgbm
        config['versions']['lightgbm'] = lightgbm.__version__
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'config.json', config)
    fit_kwargs = {}
    if candidate.startswith('lgb_'):
        fit_kwargs['sample_weight'] = weights
        if phase == 'inner':
            from lightgbm import early_stopping, log_evaluation
            fit_kwargs.update(eval_set=[(x.loc[eval_mask, names], ye.label.map({c: i for i, c in enumerate(CLASSES)}).to_numpy())], eval_metric='multi_logloss', callbacks=[early_stopping(50, verbose=False), log_evaluation(0)])
    else:
        fit_kwargs['classifier__sample_weight'] = weights
    with threadpool_limits(limits=4):
        estimator.fit(x.loc[train_mask, names], encoded, **fit_kwargs)
        probabilities = estimator.predict_proba(x.loc[eval_mask, names])
    probabilities = probabilities[:, [list(estimator.classes_).index(i) for i in range(4)]]
    from .evaluation import classification_metrics
    metrics = classification_metrics(ye.label, probabilities)
    prediction = ye[['target_date', 'label']].copy()
    prediction[[f'p_{c}' for c in CLASSES]] = probabilities
    prediction.to_csv(output / 'predictions.csv', date_format='%Y-%m-%d', float_format='%.17g')
    joblib.dump(estimator, output / 'model.joblib', compress=3)
    model_record = artifact_record(output / 'model.joblib')
    importance = fitted_feature_importance(estimator, candidate, names, meta, model_record)
    importance.update(candidate=candidate, seed=seed, phase=phase, manifest=inputs['manifest'])
    write_json(output / 'feature_importance.json', importance)
    iteration = int(estimator.best_iteration_ or rounds) if candidate.startswith('lgb_') else (int(np.max(estimator.named_steps['classifier'].n_iter_)) if candidate == 'logistic_full' else 500)
    receipt = {
        'classification_protocol': PROTOCOL, 'status': 'completed', 'fit_count': 1,
        'candidate': candidate, 'seed': seed, 'phase': phase, 'classes': list(CLASSES),
        'data_version': meta['data_version'], 'inputs': inputs, 'feature_names': names,
        'requested_feature_names': requested, 'dropped_features': dropped,
        'train_coverage': _coverage(yt), 'eval_coverage': _coverage(ye),
        'training_class_counts': counts.to_dict(), 'selected_iteration': iteration,
        'inner_receipt': inner_record, 'metrics': metrics,
        'artifacts': {name: artifact_record(output / file) for name, file in [('model', 'model.joblib'), ('predictions', 'predictions.csv'), ('config', 'config.json'), ('feature_importance', 'feature_importance.json')]},
    }
    write_json(output / 'receipt.json', receipt)
    return receipt
