"""Stream a pinned, key-sorted official registry into immutable caption shards.

All registry columns are preserved. Only the required producer `id` alias is
added. Read buffering is independent of the locked inference grouping size.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

from copy_verified_files import atomic_json, digest
from incident_gate import guarded_entrypoint


def json_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def export_rows(rows, config, root):
    """Constant memory; strict key order proves uniqueness without a global set."""
    root = Path(root)
    config_hash = json_sha(config)
    state_path = root / 'build_state.json'
    previous = json.loads(state_path.read_text()) if state_path.exists() else {}
    if previous and previous['config_sha256'] != config_hash:
        raise ValueError('registry export identity changed')
    committed = previous.get('shards', [])
    committed_rows = 0
    for index, shard in enumerate(committed):
        path = root / 'shards' / f'shard_{index:06d}.jsonl'
        if (shard['path'] != str(path.absolute()) or shard['start'] != committed_rows
                or shard['rows'] != min(config['rows_per_shard'], config['expected_rows'] - committed_rows)
                or path.stat().st_size != shard['size_bytes'] or digest(path) != shard['sha256']):
            raise ValueError('committed input shard changed')
        committed_rows += shard['rows']
    (root / 'shards').mkdir(parents=True, exist_ok=True)
    ordered_ids = hashlib.sha256()
    previous_key, previous_parent = '', -1
    count = 0
    handle = None
    new_shards = list(committed)
    try:
        for row in rows:
            key = row.get('key')
            if not isinstance(key, str) or not key or key <= previous_key:
                raise ValueError(f'non-unique or unsorted registry key at row {count}')
            if row.get('global_index') != count:
                raise ValueError(f'non-dense registry order at row {count}')
            parent = row.get('parent_global_index')
            if not isinstance(parent, int) or parent <= previous_parent:
                raise ValueError(f'non-monotonic parent index at row {count}')
            if not isinstance(row.get('caption'), str) or not row['caption'].strip():
                raise ValueError(f'missing caption at row {count}')
            if row.get('id', key) != key:
                raise ValueError('producer id alias conflicts with registry key')
            if count >= config['expected_rows']:
                raise ValueError('registry has extra rows')
            previous_key, previous_parent = key, parent
            encoded_id = key.encode('utf-8') + b'\n'
            ordered_ids.update(encoded_id)
            if count < committed_rows:
                count += 1
                continue
            if handle is None:
                # Capacity estimate is refreshed at each shard boundary.
                used = sum(item['size_bytes'] for item in new_shards)
                per_row = used / count if count else config['initial_bytes_per_row']
                remaining = (config['expected_rows'] - count) * per_row
                if remaining * config['capacity_headroom_factor'] > shutil.disk_usage(root).free:
                    raise RuntimeError('insufficient input-shard storage with headroom')
                start = count
                target = root / 'shards' / f'shard_{len(new_shards):06d}.jsonl'
                if target.exists():
                    raise ValueError('unreceipted final shard exists; inspect before resuming')
                partial = target.with_suffix('.jsonl.partial')
                handle = partial.open('wb')
                shard_hash, shard_ids = hashlib.sha256(), hashlib.sha256()
            data = json.dumps({**row, 'id': key}, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n'
            handle.write(data)
            shard_hash.update(data)
            shard_ids.update(encoded_id)
            count += 1
            if count - start == config['rows_per_shard'] or count == config['expected_rows']:
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
                handle = None
                shard = {'shard_id': target.stem, 'path': str(target.absolute()),
                         'start': start, 'rows': count - start,
                         'size_bytes': partial.stat().st_size, 'sha256': shard_hash.hexdigest(),
                         'ordered_caption_id_sha256': shard_ids.hexdigest(),
                         'registry_join': {'matched_rows': count - start, 'not_in_registry_rows': 0}}
                os.replace(partial, target)
                new_shards.append(shard)
                atomic_json(state_path, {'config_sha256': config_hash, 'rows': count, 'shards': new_shards})
                atomic_json(root / 'progress.json', {'state': 'exporting', 'rows': count,
                            'total_rows': config['expected_rows'], 'shards': len(new_shards)})
        if count != config['expected_rows']:
            raise ValueError(f'registry row total differs: {count}')
    finally:
        if handle is not None:
            handle.close()
    return new_shards, ordered_ids.hexdigest()


def export_registry(config):
    import pyarrow.parquet as pq

    source, meta_path = Path(config['registry']), Path(config['registry_meta'])
    root = Path(config['output_root']).absolute()
    if source.resolve() == root.resolve() or root.resolve() in source.resolve().parents:
        raise ValueError('source and output overlap')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    if (digest(meta_path) != config['metadata_sha256']
            or meta['registry_fingerprint'] != config['registry_fingerprint']
            or meta['row_count'] != config['expected_rows'] or meta['status'] != 'complete'):
        raise ValueError('registry metadata differs from the approved source')
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / 'progress.json', {'state': 'verifying_registry'})
    if digest(source) != config['registry_sha256'] or meta['file_checksums']['registry.parquet'] != config['registry_sha256']:
        raise ValueError('registry checksum differs')
    parquet = pq.ParquetFile(source)
    if parquet.metadata.num_rows != config['expected_rows']:
        raise ValueError('Parquet footer row count differs')
    def rows():
        for batch in parquet.iter_batches(batch_size=1024, use_threads=False):
            yield from batch.to_pylist()
    shards, ids_sha = export_rows(rows(), config, root)
    lock = {'kind': 'gpic-caption-population-lock-v1',
            'source_population_id': config['source_population_id'], 'tier': config['tier'],
            'source_rows': config['expected_rows'], 'source_file_sha256': config['registry_sha256'],
            'source_ordered_caption_id_sha256': ids_sha,
            'registry': {'id': config['registry_id'], 'rows': config['expected_rows'],
                         'fingerprint': config['registry_fingerprint'],
                         'metadata_sha256': config['metadata_sha256'],
                         'membership_sha256': meta['membership_fingerprint']}}
    population = {**lock, 'kind': 'gpic-caption-population-selection-v1',
                  'selection': {'start': 0, 'end_exclusive': config['expected_rows'],
                                'rows': config['expected_rows'], 'ordered_caption_id_sha256': ids_sha},
                  'registry_join': {'matched_rows': config['expected_rows'], 'not_in_registry_rows': 0}}
    lock['identity_sha256'] = json_sha(lock)
    population['identity_sha256'] = json_sha(population)
    manifest = {'kind': 'gpic-caption-shards-v1', 'population': population, 'shards': shards}
    atomic_json(root / 'population_lock.json', lock)
    atomic_json(root / 'input_manifest.json', manifest)
    atomic_json(root / 'COMPLETE.json', {'status': 'verified', 'rows': config['expected_rows'],
                'config_sha256': json_sha(config), 'input_manifest_sha256': digest(root / 'input_manifest.json'),
                'population_lock_sha256': digest(root / 'population_lock.json'),
                'id_uniqueness': 'strictly_increasing_registry_key', 'preserved_columns': parquet.schema_arrow.names})
    atomic_json(root / 'progress.json', {'state': 'complete', 'rows': config['expected_rows']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config['rows_per_shard'] <= 0 or config['expected_rows'] <= 0 or config['capacity_headroom_factor'] < 1:
        raise ValueError('invalid export size or headroom')
    export_registry(config)


if __name__ == '__main__':
    raise SystemExit(guarded_entrypoint('export_registry_caption_shards', main))
