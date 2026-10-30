"""One-shot correction for two legacy phase_m_v1_backtest portable sources."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[3]
AUDIT = Path(__file__).resolve().parent
REGISTRY = ROOT / 'backtest/experiments/registry.jsonl'
PORTABLE = {
    'regime-adapt/m0h20-all-top5d1-bt': 'backtest/experiments/research_eval_sources_20261030/phase_s_regime/all_top5d1/m0h20_before_fix.json',
    'regime-adapt/m0h20-all-top5d1-st-daily-v2': 'backtest/experiments/research_eval_sources_20261030/phase_s_regime/all_top5d1/m0h20.json',
}

def sha(data):
    return hashlib.sha256(data).hexdigest()

manifest = json.loads((AUDIT / 'manifest.json').read_text())
before = REGISTRY.read_bytes()
assert manifest['registry']['after_sha256'] == sha(before), 'Registry changed after the recorded migration'
after_lines = []
changes = []
for line_no, line in enumerate(before.decode().splitlines(keepends=True), 1):
    if not line.strip():
        after_lines.append(line)
        continue
    row = json.loads(line)
    if row['exp_id'] in PORTABLE:
        assert row['report_kind'] == 'phase_m_v1_backtest'
        assert 'portable_eval_output' not in row
        target = ROOT / PORTABLE[row['exp_id']]
        assert target.is_file()
        evidence = next(item for item in manifest['files'] if item['destination'] == PORTABLE[row['exp_id']])
        assert sha(target.read_bytes()) == evidence['destination_sha256'] == evidence['source_sha256']
        row['portable_eval_output'] = PORTABLE[row['exp_id']]
        changes.append({'line': line_no, 'exp_id': row['exp_id'], 'fields': {
            'portable_eval_output': {'before': None, 'after': row['portable_eval_output']}
        }})
        after_lines.append(json.dumps(row, ensure_ascii=False) + '\n')
    else:
        after_lines.append(line)
assert len(changes) == 2
after = ''.join(after_lines).encode()
old_rows = [json.loads(line) for line in before.decode().splitlines() if line.strip()]
new_rows = [json.loads(line) for line in after.decode().splitlines() if line.strip()]
assert len(old_rows) == len(new_rows)
for old, new in zip(old_rows, new_rows):
    assert {key: value for key, value in old.items() if key != 'portable_eval_output'} == {
        key: value for key, value in new.items() if key != 'portable_eval_output'
    }
assert REGISTRY.read_bytes() == before
REGISTRY.write_bytes(after)
manifest['registry']['stages'] = manifest['registry'].get('stages', []) + [{
    'reason': 'Restore two previously available legacy phase_m_v1_backtest catalog sources',
    'before_sha256': sha(before), 'after_sha256': sha(after), 'changes': changes,
}]
manifest['registry']['after_sha256'] = sha(after)
manifest['registry']['changes'] = sorted(manifest['registry']['changes'] + changes, key=lambda item: item['line'])
manifest['registry']['invariants'] = sorted(set(manifest['registry']['invariants'] + [
    'all original eval_output unchanged', 'all previously available legacy catalog sources remain available'
]))
(AUDIT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print('Updated only portable_eval_output on 2 legacy rows; registry SHA:', sha(after))
