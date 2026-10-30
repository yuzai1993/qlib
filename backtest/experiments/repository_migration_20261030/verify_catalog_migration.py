"""Read-only catalog parity check; source snapshot survives source-root removal."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUDIT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from quant_platform.research import ResearchCatalog

parser = argparse.ArgumentParser()
parser.add_argument('--capture-source', action='store_true')
parser.add_argument('--output', default='catalog_parity_after.json')
args = parser.parse_args()
source_path = AUDIT / 'catalog_source_snapshot.json'
if args.capture_source:
    assert not source_path.exists(), 'Refusing to replace the captured source catalog'
    source = ResearchCatalog(Path('/Users/yuxianqi/Project/qlib_exp')).snapshot()
    source_path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + '\n')
else:
    source = json.loads(source_path.read_text())
current = ResearchCatalog(ROOT).snapshot()
by_id = {row['id']: row for row in current['records']}
old_available = [row for row in source['records'] if row['source_status'] == 'available']
regressions = []
checked = []
metric_differences = []
metric_values_checked = 0


def check_existing_values(old, new, record_id, field):
    global metric_values_checked
    if isinstance(old, dict):
        for key, value in old.items():
            check_existing_values(value, new.get(key) if isinstance(new, dict) else None,
                                  record_id, f'{field}.{key}')
    elif old is not None:
        metric_values_checked += 1
        if old != new:
            metric_differences.append({'id': record_id, 'field': field, 'old': old, 'new': new})


for old in old_available:
    new = by_id.get(old['id'])
    checked.append({'id': old['id'], 'old_source': old['source'],
                    'new_source': new.get('source') if new else None,
                    'old_status': old['source_status'],
                    'new_status': new.get('source_status') if new else 'absent'})
    if not new or new['source_status'] != 'available':
        regressions.append(checked[-1])
    else:
        for field in ('metrics', 'years', 'regimes'):
            check_existing_values(old[field], new[field], old['id'], field)

result = {
    'source_catalog_snapshot': str(source_path.relative_to(ROOT)),
    'source_records': len(source['records']), 'source_available': len(old_available),
    'destination_root': str(ROOT), 'destination_records': len(current['records']),
    'destination_available': sum(row['source_status'] == 'available' for row in current['records']),
    'destination_missing': [{'id': row['id'], 'source': row['source'], 'source_status': row['source_status']}
                            for row in current['records'] if row['source_status'] != 'available'],
    'old_available_records_preserved': not regressions, 'regressions': regressions, 'checked': checked,
    'old_metric_values_checked': metric_values_checked,
    'old_metric_values_preserved': not metric_differences, 'metric_differences': metric_differences,
}
(AUDIT / args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
(AUDIT / ('catalog_destination_before.json' if 'before' in args.output else 'catalog_destination_after.json')).write_text(
    json.dumps(current, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({key: value for key, value in result.items() if key != 'checked'}, ensure_ascii=False, indent=2))
assert not regressions, f'{len(regressions)} previously available catalog sources became unavailable'
assert not metric_differences, f'{len(metric_differences)} previously available catalog metric values changed'
