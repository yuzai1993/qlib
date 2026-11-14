"""Auditable five-seed probability ensembles; formal evaluation never selects models."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .modeling import CANDIDATES, CLASSES, PROTOCOL, SEEDS, artifact_record, file_fingerprint, temporal_split, write_json

PROBABILITY_COLUMNS = [f'p_{c}' for c in CLASSES]


def classification_metrics(y_true, probabilities, classes=CLASSES):
    """Return four-class metrics, with absent true-class recall represented by null.

    Probability columns follow ``classes`` and are explicitly reordered to D/F/T/P.
    Macro F1 averages all four classes (absent classes have F1=0); balanced accuracy
    averages recall only across classes with true support. Multiclass Brier is the
    mean sum of squared errors across four probability columns, range [0,2].
    """
    if len(classes) != 4 or set(classes) != set(CLASSES):
        raise ValueError('classes must be a permutation of D,F,T,P')
    y = np.asarray(y_true)
    p = np.asarray(probabilities, dtype=float)
    if y.ndim != 1 or not len(y) or not set(y) <= set(CLASSES):
        raise ValueError('nonempty true labels must be canonical classes')
    if p.shape != (len(y), 4) or not np.isfinite(p).all() or (p < 0).any() or (p > 1).any() or not np.allclose(p.sum(axis=1), 1, atol=1e-7, rtol=0):
        raise ValueError('probabilities require finite n-by-4 values in [0,1] with rows summing to one')
    p = p[:, [list(classes).index(c) for c in CLASSES]]
    truth = np.array([CLASSES.index(c) for c in y])
    predicted = p.argmax(axis=1)
    matrix = np.bincount(truth * 4 + predicted, minlength=16).reshape(4, 4)
    support, predicted_count = matrix.sum(axis=1), matrix.sum(axis=0)
    tp = matrix.diagonal()
    precision = np.divide(tp, predicted_count, out=np.zeros(4), where=predicted_count > 0)
    recall = np.divide(tp, support, out=np.zeros(4), where=support > 0)
    f1 = np.divide(2 * tp, support + predicted_count, out=np.zeros(4), where=support + predicted_count > 0)
    return {
        'accuracy': float(tp.sum() / len(y)), 'macro_f1': float(f1.mean()),
        'weighted_f1': float((f1 * support).sum() / len(y)),
        'balanced_accuracy': float(recall[support > 0].mean()),
        'logloss': float(-np.log(np.clip(p[np.arange(len(y)), truth], np.finfo(float).eps, 1)).mean()),
        'brier': float(np.square(p - np.eye(4)[truth]).sum(axis=1).mean()),
        'per_class': {c: {'precision': float(precision[i]), 'recall': float(recall[i]) if support[i] else None, 'f1': float(f1[i]), 'support': int(support[i])} for i, c in enumerate(CLASSES)},
        'confusion_matrix': matrix.tolist(), 'n_days': len(y),
        'macro_definition': 'fixed four classes; absent-class F1=0; balanced_accuracy averages supported classes',
        'brier_definition': 'mean(sum_class((probability-one_hot_truth)^2)); range [0,2]',
    }


def _frame_metrics(frame):
    return classification_metrics(frame.label.to_numpy(), frame[PROBABILITY_COLUMNS].to_numpy()) if len(frame) else None


def block_bootstrap(frame, replicates=1000, seed=42):
    """Percentile 95% CIs from overlapping continuous three-calendar-month blocks."""
    if replicates < 1 or frame.empty:
        raise ValueError('positive replicates and nonempty predictions required')
    classification_metrics(frame.label, frame[PROBABILITY_COLUMNS].to_numpy())
    months = pd.to_datetime(frame.target_date).dt.to_period('M')
    calendar = pd.period_range(months.min(), months.max(), freq='M')
    width = min(3, len(calendar))
    blocks = [np.flatnonzero(months.isin(calendar[start:start + width]).to_numpy()) for start in range(len(calendar) - width + 1)]
    blocks = [block for block in blocks if len(block)]
    rng = np.random.default_rng(seed)
    samples = {key: [] for key in ['accuracy', 'macro_f1', 'weighted_f1', 'balanced_accuracy', 'logloss', 'brier']}
    per_class = {c: {key: [] for key in ['precision', 'recall', 'f1']} for c in CLASSES}
    for _ in range(replicates):
        indices, count = [], 0
        while count < len(frame):
            block = blocks[int(rng.integers(len(blocks)))]
            indices.append(block)
            count += len(block)
        metric = _frame_metrics(frame.iloc[np.concatenate(indices)[:len(frame)]])
        for key in samples:
            samples[key].append(metric[key])
        for c in CLASSES:
            for key in per_class[c]:
                if metric['per_class'][c][key] is not None:
                    per_class[c][key].append(metric['per_class'][c][key])
    interval = lambda values: np.quantile(values, [.025, .975]).tolist() if values else None
    return {
        'method': 'moving calendar-month blocks; sampled rows truncated to original n_days; percentile 95%',
        'block_months': 3, 'effective_block_months': width, 'replicates': replicates, 'seed': seed,
        'metrics': {key: interval(values) for key, values in samples.items()},
        'per_class': {c: {key: interval(values) for key, values in fields.items()} for c, fields in per_class.items()},
        'limitation': 'Few independent P episodes can leave wide or unstable intervals; absent-class recall replicates omitted.',
    }


def temporal_diagnostics(frame):
    """Slices use target-day chronology; transition windows are ±5 observed trading days."""
    target_dates = pd.to_datetime(frame.target_date)
    changes = np.flatnonzero(frame.label.to_numpy()[1:] != frame.label.to_numpy()[:-1]) + 1
    switch = np.zeros(len(frame), dtype=bool)
    for index in changes:
        switch[max(0, index - 5):min(len(frame), index + 6)] = True
    calendar = pd.DatetimeIndex(frame.index).union(pd.DatetimeIndex(target_dates)).sort_values()
    positions = pd.DataFrame(index=calendar)
    positions['month'] = calendar.to_period('M')
    positions['position'] = positions.groupby('month').cumcount() + 1
    positions['length'] = positions.groupby('month')['month'].transform('size')
    target_positions = positions.reindex(pd.DatetimeIndex(target_dates))
    first = (target_positions.position == 1).to_numpy()
    last = (target_positions.position == target_positions.length).to_numpy()
    episodes = []
    labels = frame.label.to_numpy()
    boundaries = np.r_[0, changes, len(frame)]
    counts = {c: 0 for c in CLASSES}
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        cls = labels[start]
        counts[cls] += 1
        if cls == 'P':
            episodes.append({
                'start': str(target_dates.iloc[start].date()), 'end': str(target_dates.iloc[end - 1].date()),
                'n_days': int(end - start), 'metrics': _frame_metrics(frame.iloc[start:end]),
                'entry_window': _frame_metrics(frame.iloc[max(0, start - 5):min(len(frame), start + 6)]) if start > 0 else None,
                'exit_window': _frame_metrics(frame.iloc[max(0, end - 5):min(len(frame), end + 6)]) if end < len(frame) else None,
                'left_censored': bool(start == 0), 'right_censored': bool(end == len(frame)),
            })
    return {
        'per_year': {str(year): _frame_metrics(frame.loc[target_dates.dt.year == year]) for year in sorted(target_dates.dt.year.unique())},
        'month_position': {name: _frame_metrics(frame.iloc[np.flatnonzero(mask)]) for name, mask in [('first', first), ('middle', ~(first | last)), ('last', last)]},
        'month_position_definition': 'first/last target trading date in available signal+target calendar; middle=all other dates',
        'transition_window': _frame_metrics(frame.iloc[np.flatnonzero(switch)]),
        'outside_transition_window': _frame_metrics(frame.iloc[np.flatnonzero(~switch)]),
        'transition_dates': [str(target_dates.iloc[i].date()) for i in changes],
        'transition_definition': 'true target-label changes; union of +/-5 observed trading days; evaluation edges censored',
        'episode_counts': counts, 'p_episodes': episodes,
    }


def _verified_prediction(receipt):
    if receipt.get('status') != 'completed' or receipt.get('fit_count') != 1 or receipt.get('classes') != list(CLASSES) or receipt.get('classification_protocol') != PROTOCOL:
        raise ValueError('receipt is not a completed canonical classification fit')
    for group in ('inputs', 'artifacts'):
        for record in receipt[group].values():
            if file_fingerprint(record['path']) != record['sha256']:
                raise ValueError(f'{group} fingerprint mismatch: {record["path"]}')
    frame = pd.read_csv(receipt['artifacts']['predictions']['path'], index_col='date', parse_dates=['date', 'target_date'])
    if list(frame.columns) != ['target_date', 'label'] + PROBABILITY_COLUMNS or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError('prediction columns or date index invalid')
    targets = pd.read_parquet(receipt['inputs']['targets']['path'])
    _, mask = temporal_split(targets, receipt['phase'])
    expected = targets.loc[mask, ['target_date', 'label']].copy()
    expected['target_date'] = pd.to_datetime(expected.target_date)
    if not frame[['target_date', 'label']].equals(expected):
        raise ValueError('prediction coverage/targets differ from frozen input split')
    _frame_metrics(frame)
    return frame


def _ensemble(frames):
    first = frames[0]
    if any(not first[['target_date', 'label']].equals(other[['target_date', 'label']]) for other in frames[1:]):
        raise ValueError('ensemble seeds must have identical dates, targets and labels')
    out = first[['target_date', 'label']].copy()
    out[PROBABILITY_COLUMNS] = np.mean([f[PROBABILITY_COLUMNS].to_numpy() for f in frames], axis=0)
    return out


def _verify_final_provenance(final, inner, retained_inner_record):
    """Require the exact retained inner evidence, including frozen LGB round count."""
    link = final.get('inner_receipt')
    if not isinstance(link, dict) or not link.get('path') or not link.get('sha256'):
        raise ValueError('every final fit requires an inner receipt link')
    if Path(link['path']).resolve() != Path(retained_inner_record['path']).resolve() or link['sha256'] != retained_inner_record['sha256']:
        raise ValueError('final inner receipt path/hash must match retained matching inner receipt')
    if not Path(link['path']).is_file() or file_fingerprint(link['path']) != link['sha256']:
        raise ValueError('final inner receipt link missing or stale')
    if (inner.get('candidate'), inner.get('seed'), inner.get('phase'), inner.get('classification_protocol'), inner.get('status'), inner.get('fit_count')) != (final['candidate'], final['seed'], 'inner', PROTOCOL, 'completed', 1):
        raise ValueError('final inner receipt identity mismatch')
    if inner.get('classes') != final.get('classes') or inner.get('data_version') != final.get('data_version') or {k: v['sha256'] for k, v in inner['inputs'].items()} != {k: v['sha256'] for k, v in final['inputs'].items()}:
        raise ValueError('final inner receipt input signatures mismatch')
    record = final['artifacts']['config']
    if file_fingerprint(record['path']) != record['sha256']:
        raise ValueError('final config fingerprint mismatch')
    config = json.loads(Path(record['path']).read_text())
    if config.get('inner_receipt') != link:
        raise ValueError('final saved config inner receipt differs from receipt')
    if final['candidate'].startswith('lgb_'):
        rounds = inner.get('selected_iteration')
        if not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= 1000 or final.get('selected_iteration') != rounds or config.get('parameters', {}).get('n_estimators') != rounds:
            raise ValueError('final LightGBM selected_iteration/config rounds differ from inner selected_iteration')


def _group_importance(candidate, receipts, output):
    per_seed = {}
    for seed in SEEDS:
        receipt = receipts[(candidate, seed, 'final')][0]
        record = receipt['artifacts'].get('feature_importance')
        if record is None:
            raise ValueError('fitted-model feature importance artifact is required')
        importance = json.loads(Path(record['path']).read_text())
        if (importance.get('candidate'), importance.get('seed'), importance.get('phase'), importance.get('source'), importance.get('used_for_selection')) != (candidate, seed, 'final', 'fitted_model_training_only', False):
            raise ValueError('feature importance provenance mismatch')
        if importance.get('model') != receipt['artifacts']['model'] or importance.get('manifest') != receipt['inputs']['manifest']:
            raise ValueError('feature importance model/manifest fingerprint mismatch')
        per_seed[str(seed)] = importance | {'artifact': record}
    methods = {item['method'] for item in per_seed.values()}
    if len(methods) != 1:
        raise ValueError('importance methods must match across five seeds')
    groups = sorted({group for item in per_seed.values() for group in item['groups']})
    summary = {}
    for group in groups:
        values = [item['groups'].get(group, {}).get('normalized_importance', 0.) for item in per_seed.values()]
        if not np.isfinite(values).all() or min(values) < 0:
            raise ValueError('invalid normalized grouped importance')
        summary[group] = {'mean': float(np.mean(values)), 'std': float(np.std(values, ddof=1))}
    payload = {
        'method': next(iter(methods)), 'source': 'fitted_model_training_only', 'used_for_selection': False,
        'seed_aggregation': 'arithmetic mean and sample std of within-seed normalized group importance; all five seeds',
        'groups': summary, 'per_seed': per_seed,
        'limitations': per_seed[str(SEEDS[0])]['limitations'],
    }
    path = output / f'feature_importance_{candidate}.json'
    write_json(path, payload)
    return payload | {'artifact': artifact_record(path)}


def evaluate_archive(archive, result_dir=None, bootstrap_replicates=1000):
    """Verify all 40 receipts, evaluate frozen candidates, write a portable JSON bundle.

    Receipts may be archived copies: artifact paths are absolute and never inferred
    from receipt location. Candidate selection ONLY uses inner ensemble metrics.
    """
    archive = Path(archive).resolve()
    output = Path(result_dir).resolve() if result_dir else archive / 'metrics'
    receipts = {}
    for path in sorted(archive.rglob('receipt.json')):
        item = json.loads(path.read_text())
        if item.get('classification_protocol') != PROTOCOL:
            continue
        key = (item.get('candidate'), item.get('seed'), item.get('phase'))
        if key in receipts:
            raise ValueError(f'duplicate fit receipt: {key}')
        receipts[key] = (item, artifact_record(path))
    expected = {(c, s, phase) for c in CANDIDATES for s in SEEDS for phase in ['inner', 'final']}
    if set(receipts) != expected:
        raise ValueError(f'exactly 40 candidate/seed/phase receipts required; missing={sorted(expected - set(receipts))}; unexpected={set(receipts) - expected}')
    reference = receipts[(CANDIDATES[0], SEEDS[0], 'inner')][0]
    fingerprints = {k: v['sha256'] for k, v in reference['inputs'].items()}
    for item, _ in receipts.values():
        if item['data_version'] != reference['data_version'] or {k: v['sha256'] for k, v in item['inputs'].items()} != fingerprints:
            raise ValueError('all receipts must share frozen data_version and input fingerprints')
    for candidate in CANDIDATES:
        for seed in SEEDS:
            inner, retained = receipts[(candidate, seed, 'inner')]
            _verify_final_provenance(receipts[(candidate, seed, 'final')][0], inner, retained)
    frames = {key: _verified_prediction(value[0]) for key, value in receipts.items()}
    # Enforce identical coverage not just within a candidate, but across all candidates.
    for phase in ['inner', 'final']:
        _ensemble([frames[(c, s, phase)] for c in CANDIDATES for s in SEEDS])
    output.mkdir(parents=True, exist_ok=True)
    candidates = {}
    for candidate in CANDIDATES:
        inner = _ensemble([frames[(candidate, seed, 'inner')] for seed in SEEDS])
        final = _ensemble([frames[(candidate, seed, 'final')] for seed in SEEDS])
        path = output / f'ensemble_{candidate}.csv'
        final.to_csv(path, date_format='%Y-%m-%d', float_format='%.17g')
        inner_path = output / f'inner_ensemble_{candidate}.csv'
        inner.to_csv(inner_path, date_format='%Y-%m-%d', float_format='%.17g')
        per_seed = {str(seed): _frame_metrics(frames[(candidate, seed, 'final')]) for seed in SEEDS}
        candidates[candidate] = {
            'inner': _frame_metrics(inner), 'inner_per_seed': {str(seed): _frame_metrics(frames[(candidate, seed, 'inner')]) for seed in SEEDS},
            'evaluation': _frame_metrics(final), 'per_seed': per_seed,
            'seed_summary': {name: {'mean': float(np.mean([m[name] for m in per_seed.values()])), 'std': float(np.std([m[name] for m in per_seed.values()], ddof=1))} for name in ['accuracy', 'macro_f1', 'weighted_f1', 'balanced_accuracy', 'logloss', 'brier']},
            'confidence_intervals': block_bootstrap(final, replicates=bootstrap_replicates),
            'diagnostics': temporal_diagnostics(final),
            'feature_importance': _group_importance(candidate, receipts, output),
            'models': [receipts[(candidate, seed, 'final')][0]['artifacts']['model'] | {'seed': seed} for seed in SEEDS],
            'predictions': [receipts[(candidate, seed, 'final')][0]['artifacts']['predictions'] | {'seed': seed} for seed in SEEDS],
            'ensemble': artifact_record(path), 'inner_ensemble': artifact_record(inner_path),
            'receipts': {phase: [receipts[(candidate, seed, phase)][1] | {'seed': seed} for seed in SEEDS] for phase in ['inner', 'final']},
        }
    ranking = sorted(CANDIDATES, key=lambda c: (-candidates[c]['inner']['macro_f1'], -candidates[c]['inner']['accuracy'], candidates[c]['inner']['logloss'], CANDIDATES.index(c)))
    counts = receipts[(CANDIDATES[0], SEEDS[0], 'final')][0]['training_class_counts']
    if any(receipts[(c, s, 'final')][0]['training_class_counts'] != counts for c in CANDIDATES for s in SEEDS):
        raise ValueError('final training class counts differ across fits')
    evaluation = frames[(CANDIDATES[0], SEEDS[0], 'final')]
    prior = np.array([counts[c] for c in CLASSES], dtype=float)
    prior /= prior.sum()
    majority = np.zeros(4)
    majority[prior.argmax()] = 1
    baselines = {}
    for name, probabilities in [('training_majority', majority), ('training_prior', prior)]:
        baseline = evaluation[['target_date', 'label']].copy()
        baseline[PROBABILITY_COLUMNS] = np.tile(probabilities, (len(baseline), 1))
        path = output / f'{name}.csv'
        baseline.to_csv(path, date_format='%Y-%m-%d', float_format='%.17g')
        baselines[name] = {'training_class_counts': counts, 'probabilities': dict(zip(CLASSES, probabilities.tolist())), 'evaluation': _frame_metrics(baseline), 'predictions': artifact_record(path)}
    bundle = {
        'classification_protocol': PROTOCOL, 'classes': list(CLASSES), 'seeds': list(SEEDS),
        'data_version': reference['data_version'], 'inputs': reference['inputs'],
        'chosen_candidate': ranking[0], 'inner_ranking': ranking,
        'selection_rule': 'inner ensemble macro_f1 descending; accuracy descending; logloss ascending; frozen candidate order for exact ties',
        'evaluation_used_for_selection': False, 'n_fit_receipts': len(receipts),
        'candidates': candidates, 'baselines': baselines,
        'limitations': ['Frozen labels use all-history clustering and centered monthly smoothing: retrospective targets, not a causal regime system.', 'Formal evaluation never changes features, candidates, early stopping or thresholds.', 'Training majority/prior use only training counts; no current true retrospective label enters inputs.'],
    }
    write_json(output / 'evaluation.json', bundle)
    return bundle
