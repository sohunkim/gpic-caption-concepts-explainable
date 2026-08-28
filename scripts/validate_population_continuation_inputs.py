"""Validate exported population against the pinned T5 producer's own schema."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from copy_verified_files import digest, atomic_json
from incident_gate import guarded_entrypoint


def validate_export_files(config):
    root = Path(config['output_root'])
    complete = json.loads((root / 'COMPLETE.json').read_text())
    if (complete['status'] != 'verified' or complete['rows'] != config['expected_rows']
            or complete['input_manifest_sha256'] != digest(root / 'input_manifest.json')
            or complete['population_lock_sha256'] != digest(root / 'population_lock.json')):
        raise ValueError('population export completion differs')
    manifest = json.loads((root / 'input_manifest.json').read_text())
    lock = json.loads((root / 'population_lock.json').read_text())
    if (lock['source_file_sha256'] != config['registry_sha256']
            or lock['source_population_id'] != config['source_population_id']
            or lock['tier'] != config['tier'] or lock['source_rows'] != config['expected_rows']):
        raise ValueError('exported population does not match the approved registry')
    rows = 0
    for index, shard in enumerate(manifest['shards']):
        expected = root / 'shards' / f'shard_{index:06d}.jsonl'
        if (shard['path'] != str(expected.absolute()) or shard['start'] != rows
                or shard['rows'] != min(config['rows_per_shard'], config['expected_rows'] - rows)
                or expected.stat().st_size != shard['size_bytes'] or digest(expected, discard_cache=True) != shard['sha256']):
            raise ValueError(f'input shard identity/boundary mismatch: {index}')
        rows += shard['rows']
        atomic_json(root / 'validation_progress.json', {'state': 'validating', 'shards': index + 1, 'rows': rows})
    if rows != config['expected_rows']:
        raise ValueError('input shards do not cover the complete population')
    return root, complete, manifest, lock


def validate(config, producer_repo):
    sys.path.insert(0, str(Path(producer_repo) / 'src'))
    from gpic_frequency.population_identity import normalize_population_lock, validate_manifest_population

    root, complete, manifest, lock = validate_export_files(config)
    population = validate_manifest_population(manifest,
        [SimpleNamespace(rows=row['rows']) for row in manifest['shards']],
        population_lock=normalize_population_lock(lock))
    rows = sum(shard['rows'] for shard in manifest['shards'])
    atomic_json(root / 'validation.json', {'status': 'passed', 'rows': rows,
                'population_identity_sha256': population['identity_sha256'],
                'input_manifest_sha256': complete['input_manifest_sha256']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--producer-repo', type=Path, required=True)
    args = parser.parse_args()
    validate(json.loads(args.config.read_text()), args.producer_repo)


if __name__ == '__main__':
    raise SystemExit(guarded_entrypoint('validate_continuation_inputs', main))
