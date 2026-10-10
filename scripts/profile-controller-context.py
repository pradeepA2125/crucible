#!/usr/bin/env python3
"""Attribute a controller artifact without printing prompts, paths, or source content."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/agentd-py'))
from agentd.context_compaction import bounded_tree
from agentd.context_metrics import logical_context, serialized_size


def report(artifact: dict) -> dict:
    system = artifact['system_instructions']
    payload = artifact['user_payload']
    measurement = artifact.get('context_measurement')
    catalog = None
    # Old artifacts predate component instrumentation. Locate only a real named tool array.
    decoder = json.JSONDecoder()
    catalog_text = ''
    for offset, char in enumerate(system):
        if char != '[':
            continue
        try:
            value, consumed = decoder.raw_decode(system[offset:])
        except ValueError:
            continue
        if (isinstance(value, list) and value and all(isinstance(d, dict)
                and 'name' in d and 'parameters' in d for d in value)):
            catalog, catalog_text = value, system[offset:offset + consumed]
            break
    if measurement is None:
        parts = {'tool_catalog': len(catalog_text),
                 'other_system_blocks_unattributed': len(system) - len(catalog_text)}
        measurement = logical_context(system, payload, artifact.get('schema', {}), parts)
    rows = []
    total = len(system) + serialized_size(payload) + serialized_size(artifact.get('schema', {}))
    for prefix, parts in [('system', measurement['system_parts']),
                          ('payload', measurement['payload_parts'])]:
        for name, chars in parts.items():
            rows.append({'component': f'{prefix}.{name}', 'chars': chars,
                         'percent': round(100 * chars / total, 2)})
    rows.append({'component': 'response_schema', 'chars': measurement['schema_chars'],
                 'percent': round(100 * measurement['schema_chars'] / total, 2)})
    candidates = []
    if catalog is not None:
        saving = len(catalog_text) - len(json.dumps(catalog, sort_keys=True, separators=(',', ':')))
        candidates.append({'change': 'Compact catalog, retaining every tool/schema',
                           'saved_chars': saving, 'percent': round(100 * saving / total, 2)})
    tree = payload.get('retrieval_seed', {}).get('repository_structure', [])
    if isinstance(tree, list):
        kept = bounded_tree(tree)
        saving = serialized_size(tree) - serialized_size(kept)
        candidates.append({'change': 'Tree candidate: 300 entries / 16k serialized characters',
                           'saved_chars': saving, 'percent': round(100 * saving / total, 2)})
    saving = sum(candidate['saved_chars'] for candidate in candidates)
    candidates.append({'change': 'Combined catalog and tree (disjoint components)',
                       'saved_chars': saving, 'percent': round(100 * saving / total, 2)})
    return {'total_logical_chars': total, 'components': sorted(rows, key=lambda r: -r['chars']),
            'retrieval_parts': measurement.get('retrieval_parts', {}), 'candidates': candidates,
            'note': 'Logical character attribution, not physical provider tokens or latency. '
                    'Nested retrieval parts overlap payload.retrieval_seed. Old artifacts cannot '
                    'separate individual system blocks. Removal candidates do not authorize '
                    'dropping instructions or tools. Validate quality and cache behavior live.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    data = report(json.loads(args.artifact.read_text()))
    lines = ['# Controller context attribution', '',
             f"Logical input: {data['total_logical_chars']:,} characters.", '',
             '| Component | Characters | Share |', '|---|---:|---:|']
    lines += [f"| {r['component']} | {r['chars']:,} | {r['percent']}% |" for r in data['components']]
    lines += ['', '| Candidate retaining access/semantics | Characters saved | Input share recovered |',
              '|---|---:|---:|']
    lines += [f"| {r['change']} | {r['saved_chars']:,} | {r['percent']}% |"
              for r in data['candidates']]
    lines += ['', 'Nested retrieval fields (already included above):', '']
    lines += [f'- {k}: {v:,} characters' for k, v in data['retrieval_parts'].items()]
    lines += ['', data['note'], '']
    rendered = '\n'.join(lines)
    if args.output:
        args.output.write_text(rendered)
        args.output.with_suffix('.json').write_text(json.dumps(data, indent=2) + '\n')
    print(rendered)


if __name__ == '__main__':
    main()
