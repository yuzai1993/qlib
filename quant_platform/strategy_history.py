"""Display historical strategy relationships without granting study authority."""

import re

from backtest.scripts.research_labels import strategy_display_version, strategy_reference_text


def _available(source, owner):
    return bool(source and source.get('kind') == 'strategy' and source.get('track') == owner.get('track')
                and source.get('source_status') == 'available'
                and source.get('status') in {'completed', 'promoted'})


def _label(source):
    summary = source.get('strategy_summary', {})
    text = summary.get('name') or source.get('name') or source.get('id', '')
    return strategy_reference_text(text, kind='strategy', track=source.get('track', ''))


def strategy_history(record: dict, by_id: dict, benchmarks: dict) -> dict:
    """Relate intact Benchmark identities, group controls and member controls."""
    grouped = record.get('entity_type') == 'experiment_group'
    summary = record.get('group_summary', {})
    group_ready = bool(grouped and record.get('core_results', {}).get('status') == 'available'
                       and _available(record, record))
    refs = list(summary.get('member_refs', [])) if grouped else [record['id']]
    member_labels = {child['id']: child.get('label', child.get('name', ''))
                     for child in record.get('core_results', {}).get('records', [])}
    members = [by_id.get(ref) for ref in refs]
    valid_members = all(_available(member, record) for member in members)
    promotion_sources = members if (group_ready and valid_members) or (not grouped and _available(record, record)) else []
    promotions = {}
    for member in promotion_sources:
        candidates = [member['id']]
        # A surviving individual candidate can still refer to its authenticated
        # promoted Benchmark. A group's claims come only from actual members.
        if not grouped and member.get('promoted_to'):
            candidates.append(member['promoted_to'])
        for ref in candidates:
            target = benchmarks.get(ref)
            if (not _available(target, record) or not target.get('is_baseline') or not target.get('version')):
                continue
            promotions[ref] = {
                'ref': ref, 'version': target['version'],
                'display_version': strategy_display_version(target['version'], track=target['track']),
                'label': (member_labels.get(ref) if grouped else None) or _label(target), 'current': bool(target.get('current')),
                'scope': 'member_history' if grouped else 'experiment_history',
                'is_control': bool(grouped and ref == summary.get('control_ref')),
            }

    def baseline(owner, *, scope, label=None, ready=True):
        declared = owner.get('comparison_baseline', {})
        initial = bool(declared.get('initial'))
        ref = declared.get('ref', '')
        target = by_id.get(ref)
        available = bool(ready and (initial or _available(target, record)))
        version = declared.get('version', '') if available and not initial else ''
        return {'ref': ref, 'version': version,
                'display_version': strategy_display_version(version, track=record['track']),
                'label': label or ('初始基线' if initial else _label(target) if target else ref),
                'initial': initial, 'scope': scope, 'available': available}

    selected_baseline = baseline(record, scope='group_control' if grouped else 'historical',
                                 label=summary.get('control_label') if grouped else None,
                                 ready=group_ready and valid_members if grouped else True)
    historical = {}
    if grouped:
        for ref, member in zip(refs, members):
            if not member:
                continue
            item = baseline(member, scope='historical', ready=group_ready and _available(member, record))
            if not item['ref'] and not item['initial']:
                continue
            key = (item['ref'], item['version'], item['initial'])
            if key not in historical:
                historical[key] = {key: item[key] for key in ('ref', 'version', 'display_version', 'label', 'available')}
                historical[key].update(member_refs=[], member_labels=[])
            historical[key]['member_refs'].append(ref)
            historical[key]['member_labels'].append(member_labels.get(ref) or _label(member))

    def version_key(item):
        return tuple(int(value) for value in re.findall(r'\d+', item['version'])), item['ref']

    return {'promotions': sorted(promotions.values(), key=version_key),
            'baseline': selected_baseline, 'historical_baselines': list(historical.values())}
