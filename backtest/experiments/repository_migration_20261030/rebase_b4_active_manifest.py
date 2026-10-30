from pathlib import Path
import hashlib
import json

ROOT = Path('/Users/yuxianqi/Project/qlib')
AUDIT = ROOT / 'backtest/experiments/repository_migration_20261030'
PATH = ROOT / 'backtest/experiments/strategy-stability/20260801_full_period/prediction_manifest.json'

def sha(data):
    return hashlib.sha256(data).hexdigest()

before = PATH.read_bytes()
doc = json.loads(before)
old = doc['predictions'][0]['path']
assert old == '/Users/yuxianqi/Project/qlib_exp/backtest/experiments/strategy-stability/20260801_full_period/predictions/b6-m/csi1000_full.pkl'
new = old.replace('/Users/yuxianqi/Project/qlib_exp/', '/Users/yuxianqi/Project/qlib/', 1)
assert Path(new).is_file()
assert sha(Path(new).read_bytes()) == doc['predictions'][0]['prediction_sha256']
doc['predictions'][0]['path'] = new
after = (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode()
originals = json.loads((AUDIT / 'metadata.originals.json').read_text())
originals[str(PATH.relative_to(ROOT))] = before.decode()
(AUDIT / 'metadata.originals.json').write_text(json.dumps(originals, ensure_ascii=False, indent=2) + '\n')
manifest = json.loads((AUDIT / 'manifest.json').read_text())
record = next(r for r in manifest['files'] if r['destination'] == str(PATH.relative_to(ROOT)))
assert record['destination_sha256'] == sha(before)
record.update({'action': 'rebase_existing_identical_source', 'destination_sha256': sha(after), 'destination_bytes': len(after),
               'path_changes': [{'field': 'predictions[0].path', 'before': old, 'after': new}]})
manifest['destination_bytes'] += len(after) - len(before)
manifest['reused_bytes'] += len(after) - len(before)
manifest['existing_metadata_rebased_count'] = 1
PATH.write_bytes(after)
(AUDIT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print('B4 active prediction path rebased; historical sources and numeric/model hashes preserved.')
