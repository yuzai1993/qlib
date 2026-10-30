"""Copy the approved research subset; never mutate or remove the source tree."""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

SRC = Path('/Users/yuxianqi/Project/qlib_exp')
DST = Path('/Users/yuxianqi/Project/qlib')
AUDIT = DST / 'backtest/experiments/repository_migration_20261030'
RAW_EVAL = Path('backtest/experiments/research_eval_sources_20261030')
PLAN_PATH = Path('/private/tmp/qlib-cleanup-research-20261030.json')
WHITE_PATH = Path('/private/tmp/qlib-cleanup-artifact-whitelist.json')
REGISTRY = DST / 'backtest/experiments/registry.jsonl'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode()


def rebase(value, changes, field=''):
    if isinstance(value, dict):
        return {k: rebase(v, changes, f'{field}.{k}' if field else k) for k, v in value.items()}
    if isinstance(value, list):
        return [rebase(v, changes, f'{field}[{i}]') for i, v in enumerate(value)]
    if isinstance(value, str) and (value.startswith(str(SRC) + '/') or value.startswith('file://' + str(SRC) + '/')):
        after = value.replace(str(SRC), str(DST), 1)
        changes.append({'field': field, 'before': value, 'after': after})
        return after
    return value


def main():
    assert not (AUDIT / 'manifest.json').exists(), 'Migration already recorded; refuse to overwrite audit.'
    plan = json.loads(PLAN_PATH.read_text())
    whitelist = json.loads(WHITE_PATH.read_text())
    raw_paths = whitelist['recommendations']['must_preserve'][2]['paths']
    assert len(raw_paths) == 26
    # A non-session keep entry in the plan is not approval to copy that whole tree.
    session_dirs = [Path(p) for p in plan['keep_result_dirs'] if (Path(p) / 'meta.json').is_file()]
    assert len(session_dirs) == 27
    selected = {}
    for directory in session_dirs + [Path(p) for p in plan['keep_mlruns_dirs']]:
        for path in sorted(directory.rglob('*')):
            assert not path.is_symlink(), path
            if path.is_file():
                rel = path.relative_to(SRC)
                selected[rel] = path
    canonical_pred = Path('backtest/result/phase_s_regime/all_top5d1/m0h20es_ensemble_pred.pkl')
    selected[canonical_pred] = SRC / canonical_pred
    # Preserve each of the 26 original small JSONs exactly in a durable location.
    for rel in raw_paths:
        selected[RAW_EVAL / Path(rel).relative_to('backtest/result')] = SRC / rel
    # Existing B4 full-period canonical bundle must be compared, never overwritten.
    b4 = Path('backtest/experiments/strategy-stability/20260801_full_period')
    for path in sorted((SRC / b4).rglob('*')):
        if path.is_file():
            selected[path.relative_to(SRC)] = path

    records = []
    writes = []
    originals = {}
    for rel, source in sorted(selected.items(), key=lambda pair: str(pair[0])):
        assert source.is_file() and not source.is_symlink(), source
        target = DST / rel
        source_sha = sha(source)
        changes = []
        changed_data = None
        # Rebase runtime metadata only. Artifacts, numeric JSONs and provenance
        # code snapshots are always byte-preserved.
        if rel.name in ('meta.json', 'mlruns_link.json') and rel.parts[:2] == ('backtest', 'result'):
            before = json.loads(source.read_text())
            after = rebase(before, changes)
            if changes:
                changed_data = json_bytes(after)
        elif rel.name == 'meta.yaml' and rel.parts[0] == 'mlruns':
            before_text = source.read_text()
            after_text = before_text.replace('file://' + str(SRC) + '/', 'file://' + str(DST) + '/')
            if after_text != before_text:
                changed_data = after_text.encode()
                for before_line, after_line in zip(before_text.splitlines(), after_text.splitlines()):
                    if before_line != after_line:
                        changes.append({'field': before_line.split(':', 1)[0], 'before': before_line, 'after': after_line})
        after_sha = sha_bytes(changed_data) if changed_data is not None else source_sha
        action = 'copy_rebased' if changes else 'copy_exact'
        if target.exists():
            assert target.is_file() and not target.is_symlink(), target
            existing_sha = sha(target)
            assert existing_sha == after_sha, f'Conflicting target; no files written: {target} {existing_sha} != {after_sha}'
            action = 'reuse_identical'
        else:
            writes.append((source, target, changed_data))
        record = {'source': str(source), 'destination': str(rel), 'source_bytes': source.stat().st_size,
                  'source_sha256': source_sha, 'destination_bytes': len(changed_data) if changed_data is not None else source.stat().st_size,
                  'destination_sha256': after_sha, 'action': action}
        if changes:
            record['path_changes'] = changes
            originals[str(rel)] = source.read_text()
        if source.name == 'trained_model':
            record['kind'] = 'trained_model'
        records.append(record)

    before_registry = REGISTRY.read_bytes()
    lines = before_registry.decode().splitlines(keepends=True)
    rows_before = [json.loads(line) for line in lines if line.strip()]
    registry_changes = []
    output = []
    for index, line in enumerate(lines):
        if not line.strip():
            output.append(line)
            continue
        row = json.loads(line)
        before = json.loads(line)
        if row.get('exp_id') == 'baseline/b4-s-on-b6-m':
            row['result_dirs'] = [str(Path(p).relative_to(SRC)) if p.startswith(str(SRC) + '/') else p for p in row['result_dirs']]
        if row.get('phase_m_protocol') == 'v1' and row.get('eval_output') in raw_paths and not row.get('portable_eval_output'):
            row['portable_eval_output'] = str(RAW_EVAL / Path(row['eval_output']).relative_to('backtest/result'))
        if row != before:
            changed_fields = {k: {'before': before.get(k), 'after': row.get(k)} for k in row if before.get(k) != row.get(k)}
            assert set(changed_fields) <= {'result_dirs', 'portable_eval_output'}
            registry_changes.append({'line': index + 1, 'exp_id': row['exp_id'], 'fields': changed_fields})
            output.append(json.dumps(row, ensure_ascii=False) + '\n')
        else:
            output.append(line)
    after_registry = ''.join(output).encode()
    rows_after = [json.loads(line) for line in after_registry.decode().splitlines() if line.strip()]
    assert len(rows_before) == len(rows_after)
    assert [r.get('exp_id') for r in rows_before] == [r.get('exp_id') for r in rows_after]
    assert [r.get('baseline_ref') for r in rows_before] == [r.get('baseline_ref') for r in rows_after]
    assert [r.get('current_bt') for r in rows_before] == [r.get('current_bt') for r in rows_after]
    assert not set(whitelist['registry_exp_only_ids']) & {r.get('exp_id') for r in rows_after}

    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / 'registry.before.jsonl').write_bytes(before_registry)
    (AUDIT / 'metadata.originals.json').write_bytes(json_bytes(originals))
    (AUDIT / 'source_retention_plan.json').write_bytes(PLAN_PATH.read_bytes())
    (AUDIT / 'source_artifact_whitelist.json').write_bytes(WHITE_PATH.read_bytes())
    for source, target, data in writes:
        target.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            shutil.copy2(source, target)
        else:
            target.write_bytes(data)
    assert REGISTRY.read_bytes() == before_registry, 'Registry changed concurrently; refusing overwrite'
    REGISTRY.write_bytes(after_registry)
    for record in records:
        assert sha(DST / record['destination']) == record['destination_sha256'], record
        assert sha(Path(record['source'])) == record['source_sha256'], record
    manifest = {
        'date': datetime.now().isoformat(), 'source_root': str(SRC), 'destination_root': str(DST),
        'scope': 'Approved minimal historical/current baseline migration; no training, backtest, data collection, cleanup apply or source mutation.',
        'excluded': ['backtest/datasets/regime-adapt-cache (17.58 GiB)', '9 exp-only registry IDs/scripts/reports', 'all unselected sessions/recorders', 'duplicate phase_s_regime predictions'],
        'session_count': len(session_dirs), 'mlflow_experiment_count': len(plan['keep_mlruns_dirs']), 'original_eval_json_count': len(raw_paths),
        'file_count': len(records), 'source_bytes': sum(r['source_bytes'] for r in records),
        'destination_bytes': sum(r['destination_bytes'] for r in records),
        'copied_file_count': len(writes), 'copied_bytes': sum(r['destination_bytes'] for r in records if r['action'] != 'reuse_identical'),
        'reused_file_count': sum(r['action'] == 'reuse_identical' for r in records),
        'reused_bytes': sum(r['destination_bytes'] for r in records if r['action'] == 'reuse_identical'),
        'model_count': sum(r.get('kind') == 'trained_model' for r in records),
        'registry': {'path': str(REGISTRY.relative_to(DST)), 'before_sha256': sha_bytes(before_registry), 'after_sha256': sha_bytes(after_registry),
                     'before_rows': len(rows_before), 'after_rows': len(rows_after), 'changes': registry_changes,
                     'invariants': ['event order unchanged', 'all baseline_ref unchanged', 'all current_bt unchanged', 'all metric/model/config/protocol values unchanged']},
        'files': records,
    }
    (AUDIT / 'manifest.json').write_bytes(json_bytes(manifest))
    shutil.copy2(__file__, AUDIT / 'migration_script.py')
    print(json.dumps({k: v for k, v in manifest.items() if k not in ('files', 'registry')}, ensure_ascii=False, indent=2))
    print('registry_changed_rows', len(registry_changes))
    print('manifest', AUDIT / 'manifest.json')


if __name__ == '__main__':
    main()
