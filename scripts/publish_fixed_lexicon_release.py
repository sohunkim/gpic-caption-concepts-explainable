"""Publish a relocatable, receipt-verified release without modifying run roots."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gpic_concepts_v1.atomic_io import atomic_text_writer
from incident_gate import guarded_entrypoint

KIND = 'gpic-fixed-lexicon-release-v1'
RUN_KIND = 'gpic-fixed-lexicon-scaleout-v1'
BLOCK = 8 * 1024 * 1024


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(path) as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write('\n')


def digest(path, progress=None):
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(BLOCK), b''):
            result.update(block)
            if progress:
                progress(len(block))
    return result.hexdigest()


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def relative(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '\\' in value or ':' in value
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError(f'unsafe relative path: {value!r}')
    return path


def contained(root, name):
    path = root.joinpath(*relative(name).parts)
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError(f'symlink is not a release artifact: {path}')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'path escapes root: {path}')
    return path


def identity(path):
    value = read(path)
    payload = {k: v for k, v in value.items() if k != 'identity_sha256'}
    if value.get('kind') != RUN_KIND or value.get('identity_sha256') != json_digest(payload):
        raise ValueError(f'invalid immutable run identity: {path}')
    return value


def plan_release(source):
    source = source.resolve()
    manifest = identity(source / 'run_manifest.json')
    complete = read(source / 'COMPLETE.json')
    units = manifest['units']
    ids = [unit['unit_id'] for unit in units]
    if (not units or len(ids) != len(set(ids)) or any(not re.fullmatch(r'unit_\d+', uid) for uid in ids)
            or complete.get('status') != 'completed'
            or complete.get('identity_sha256') != manifest['identity_sha256']
            or complete.get('unit_count') != len(units)
            or complete.get('input_rows') != sum(u['rows'] for u in units)
            or set(complete['unit_sources']) != set(ids)):
        raise ValueError('incomplete or inconsistent run/unit coverage')
    shard_ids = [s['shard_id'] for u in units for s in u['shards']]
    if not shard_ids or any(not sid for sid in shard_ids) or len(shard_ids) != len(set(shard_ids)):
        raise ValueError('duplicate input shards across work units')
    if any(u['rows'] <= 0 or u['rows'] != sum(s['rows'] for s in u['shards']) for u in units):
        raise ValueError('unit/input shard row coverage mismatch')

    files, pairs, published_units, roots = {}, {}, [], set()

    def add(path, dest, expected=None):
        relative(dest)
        if dest in files:
            raise ValueError(f'duplicate release destination: {dest}')
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'missing or linked source file: {path}')
        size = path.stat().st_size
        if expected is not None and size != expected['size_bytes']:
            raise ValueError(f'source size mismatch: {path}')
        sha = expected['sha256'] if expected is not None else digest(path)
        if not re.fullmatch('[0-9a-f]{64}', sha):
            raise ValueError(f'invalid SHA256: {path}')
        files[dest] = {'source': str(path), 'path': dest, 'size_bytes': size, 'sha256': sha}

    add(source / 'run_manifest.json', 'provenance/source_run_manifest.json')
    add(source / 'COMPLETE.json', 'provenance/source_COMPLETE.json')
    handoff = manifest.get('verified_unit_handoff')
    handoff_units = {}
    if handoff:
        path = Path(handoff['path'])
        if digest(path) != handoff['sha256']:
            raise ValueError('handoff hash mismatch')
        record = read(path)
        handoff_units = {u['unit_id']: u for u in record['units']}
        if (len(handoff_units) != len(record['units'])
                or len(handoff_units) != handoff['reused_units']
                or not set(handoff_units).issubset(ids)
                or sum(u['rows'] for u in record['units']) != handoff['reused_rows']):
            raise ValueError('handoff unit coverage mismatch')
        add(path, 'provenance/handoff.json')

    for unit in units:
        uid = unit['unit_id']
        entry = complete['unit_sources'][uid]
        unit_dir = Path(entry['path'])
        if unit_dir.name != uid or unit_dir.parent.name != 'units' or unit_dir.is_symlink():
            raise ValueError(f'unexpected source unit layout: {unit_dir}')
        root = unit_dir.parent.parent.resolve()
        run = identity(root / 'run_manifest.json')
        if entry['source_revision'] != run['source_revision']:
            raise ValueError('unit producer revision mismatch')
        if uid in handoff_units:
            if root != Path(handoff['source_root']).resolve() or run['identity_sha256'] != handoff['source_identity_sha256']:
                raise ValueError('reused unit source identity mismatch')
        elif root != source or run['identity_sha256'] != manifest['identity_sha256']:
            raise ValueError('unrecorded external unit source')
        roots.add(str(root))
        run_dest = f"provenance/runs/{run['identity_sha256']}/run_manifest.json"
        if run_dest not in files:
            add(root / 'run_manifest.json', run_dest)
        receipt_path = root / 'receipts' / (uid + '.json')
        receipt = read(receipt_path)
        if (receipt.get('kind') != 'gpic-fixed-lexicon-unit-receipt-v2'
                or receipt.get('unit') != unit or receipt.get('run_identity_sha256') != run['identity_sha256']
                or receipt.get('retention', {}).get('policy') != manifest['retention_policy']):
            raise ValueError(f'unit receipt mismatch: {uid}')
        if uid in handoff_units and digest(receipt_path) != handoff_units[uid]['receipt_sha256']:
            raise ValueError(f'handoff receipt changed: {uid}')
        if uid in handoff_units and handoff_units[uid]['rows'] != unit['rows']:
            raise ValueError('handoff unit row coverage mismatch')
        receipt_dest = f'provenance/units/{uid}/receipt.json'
        add(receipt_path, receipt_dest)
        actual_paths = set()
        for artifact in receipt['artifacts']:
            parts = relative(artifact['path']).parts
            if parts[:2] != ('units', uid) or artifact['path'] in actual_paths:
                raise ValueError('artifact outside unit or duplicate artifact')
            actual_paths.add(artifact['path'])
            tail = parts[2:]
            if len(tail) == 5 and tail[:2] == ('stage456_sharded', 'shards') and tail[3] == 'stage5':
                dest = f'stage5/{uid}/{tail[2]}/stage5/{tail[4]}'
            elif len(tail) == 2 and tail[0] == 'stage5':
                dest = f'stage5/{uid}/shard_unsharded/stage5/{tail[1]}'
            else:
                dest = f"provenance/units/{uid}/" + '/'.join(tail)
            add(contained(root, artifact['path']), dest, artifact)
            name = PurePosixPath(dest).name
            if dest.startswith('stage5/') and name in ('canonical_mentions.jsonl', 'canonical_edges.jsonl'):
                pair = pairs.setdefault(str(PurePosixPath(dest).parent), {'unit_id': uid})
                pair['mentions' if name == 'canonical_mentions.jsonl' else 'edges'] = dest
        # Use the same retained-file boundary as the producer, including all count tables.
        expected = {str(p.relative_to(root).as_posix()) for p in unit_dir.glob('stage456_sharded/shards/shard_*/stage5/*')
                    if p.is_file() and p.suffix in ('.json', '.jsonl')}
        expected.update(str(p.relative_to(root).as_posix()) for p in unit_dir.glob('stage5/*')
                        if p.is_file() and p.suffix in ('.json', '.jsonl'))
        expected.update(str(p.relative_to(root).as_posix()) for p in unit_dir.glob('stage6/*') if p.is_file())
        expected.update(f'units/{uid}/{name}' for name in ('mixed_pipeline_summary.jsonl', 'pipeline_state.json', 'stage6/summary.jsonl'))
        if actual_paths != expected:
            raise ValueError(f'retained artifact coverage mismatch: {uid}')
        published_units.append({'unit_id': uid, 'rows': unit['rows'], 'source_revision': entry['source_revision'],
                                'receipt': receipt_dest, 'input_shards': unit['shards']})

    if set(p['unit_id'] for p in pairs.values()) != set(ids) or any(set(p) != {'unit_id', 'mentions', 'edges'} for p in pairs.values()):
        raise ValueError('missing Stage 5 mention/edge pairs')
    for uid in ids:
        unit_pairs = [p for p in pairs.values() if p['unit_id'] == uid]
        if len(unit_pairs) > 1 and any('/shard_unsharded/' in p['mentions'] for p in unit_pairs):
            raise ValueError('overlapping sharded and unsharded Stage 5 outputs')
    final_paths = set()
    for artifact in complete['artifacts']:
        parts = relative(artifact['path']).parts
        if len(parts) != 2 or parts[0] != 'stage6':
            raise ValueError('unexpected global count artifact')
        final_paths.add(artifact['path'])
        add(contained(source, artifact['path']), 'counts/' + parts[1], artifact)
    if final_paths != {p.relative_to(source).as_posix() for p in (source / 'stage6').iterdir() if p.is_file()}:
        raise ValueError('global count artifact coverage mismatch')
    if not any(path.endswith('.tsv') for path in final_paths):
        raise ValueError('missing global count tables')
    return {'kind': KIND, 'source_run_identity': manifest['identity_sha256'], 'source_roots': sorted(roots),
            'input_rows': complete['input_rows'], 'semantic_settings': manifest['semantic_settings'],
            'unit_count': len(units), 'units': published_units, 'stage5_shards': list(pairs.values()),
            'stage5_roots': [f'stage5/{uid}' for uid in ids], 'files': [files[k] for k in sorted(files)],
            'total_bytes': sum(f['size_bytes'] for f in files.values())}


@contextmanager
def release_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def verify_release(root, progress=None):
    root = root.resolve()
    complete = read(root / 'COMPLETE.json')
    manifest_path = root / 'manifest.json'
    manifest = read(manifest_path)
    if complete.get('manifest_sha256') != digest(manifest_path) or manifest.get('kind') != KIND:
        raise ValueError('release manifest seal mismatch')
    seen = set()
    for entry in manifest['artifacts']:
        if entry['path'] in seen:
            raise ValueError('duplicate release artifact')
        seen.add(entry['path'])
        path = contained(root, entry['path'])
        if path.stat().st_size != entry['size_bytes'] or digest(path, progress) != entry['sha256']:
            raise ValueError(f'release artifact mismatch: {path}')
    units = manifest['units']
    if (len(units) != manifest['unit_count'] or len({u['unit_id'] for u in units}) != len(units)
            or sum(u['rows'] for u in units) != manifest['input_rows']):
        raise ValueError('release unit coverage mismatch')
    for pair in manifest['stage5_shards']:
        if pair['mentions'] not in seen or pair['edges'] not in seen:
            raise ValueError('unsealed Stage 5 reference')
    if (set(p['unit_id'] for p in manifest['stage5_shards']) != {u['unit_id'] for u in units}
            or len({p['mentions'] for p in manifest['stage5_shards']}) != len(manifest['stage5_shards'])
            or len({p['edges'] for p in manifest['stage5_shards']}) != len(manifest['stage5_shards'])
            or complete.get('status') != 'completed'
            or complete.get('input_rows') != manifest['input_rows']
            or complete.get('files') != len(seen)
            or complete.get('bytes') != sum(a['size_bytes'] for a in manifest['artifacts'])):
        raise ValueError('release completion/Stage 5 coverage mismatch')
    return complete


def publish(source, destination, *, capacity_root, capacity_bytes, used_bytes, reserve_bytes):
    source, destination, capacity_root = source.resolve(), destination.absolute(), capacity_root.resolve()
    if destination.is_symlink() or not destination.resolve().is_relative_to(capacity_root):
        raise ValueError('release destination must be inside verified capacity root')
    destination = destination.resolve()
    if min(capacity_bytes, reserve_bytes) <= 0 or used_bytes < 0:
        raise ValueError('explicit positive capacity/reserve and measured usage are required')
    plan = plan_release(source)
    for name in plan['source_roots']:
        origin = Path(name)
        if destination == origin or destination.is_relative_to(origin) or origin.is_relative_to(destination):
            raise ValueError('release and source roots must be separate and non-nested')
    stage = destination.with_name(destination.name + '.partial')
    if stage.is_symlink():
        raise ValueError('linked staging directory')
    plan_hash = json_digest(plan)
    with release_lock(destination.with_name(destination.name + '.publish.lock')):
        if destination.exists():
            result = verify_release(destination, verification_progress())
            if result['plan_sha256'] != plan_hash:
                raise ValueError('existing release belongs to a different source plan')
            return result
        if stage.exists() and read(stage / 'provenance/publish_plan.json') != plan:
            raise ValueError('staging belongs to a different source plan')
        staged_bytes = 0
        for entry in plan['files']:
            path = contained(stage, entry['path'])
            partial = contained(stage, entry['path'] + '.partial')
            existing = path if path.exists() else partial
            if existing.is_file():
                staged_bytes += min(existing.stat().st_size, entry['size_bytes'])
        required_bytes = max(0, plan['total_bytes'] - staged_bytes)
        if used_bytes + required_bytes + reserve_bytes > capacity_bytes:
            raise ValueError('insufficient personal volume capacity with reserve')
        if shutil.disk_usage(capacity_root).free < required_bytes + reserve_bytes:
            raise ValueError('insufficient filesystem capacity with reserve')
        stage.mkdir(parents=True, exist_ok=True)
        write(stage / 'provenance/publish_plan.json', plan)
        state = {'state': 'copying', 'plan_sha256': plan_hash, 'total_files': len(plan['files']),
                 'total_bytes': plan['total_bytes'], 'verified_files': 0, 'verified_bytes': 0}
        last = 0.0

        def progress(amount=0, force=False):
            nonlocal last
            state['file_bytes_processed'] = state.get('file_bytes_processed', 0) + amount
            now = time.monotonic()
            if force or now - last >= 5:
                state['updated_at'] = datetime.now(timezone.utc).isoformat()
                write(stage / 'publish_progress.json', state)
                print(json.dumps(state, sort_keys=True), flush=True)
                last = now

        for entry in plan['files']:
            path = contained(stage, entry['path'])
            state.update(current_file=entry['path'], file_bytes_processed=0)
            progress(force=True)
            if path.exists():
                if path.stat().st_size != entry['size_bytes'] or digest(path, progress) != entry['sha256']:
                    raise ValueError(f'existing staged file is not verified: {path}')
            else:
                if shutil.disk_usage(capacity_root).free < entry['size_bytes'] + reserve_bytes:
                    raise ValueError('destination reserve exhausted before copy')
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.partial')
                if temporary.is_symlink():
                    raise ValueError('linked partial output')
                checksum = hashlib.sha256()
                total = 0
                state['state'] = 'copying'
                with Path(entry['source']).open('rb') as src, temporary.open('wb') as dst:
                    for block in iter(lambda: src.read(BLOCK), b''):
                        checksum.update(block)
                        dst.write(block)
                        total += len(block)
                        progress(len(block))
                    dst.flush()
                    os.fsync(dst.fileno())
                if total != entry['size_bytes'] or checksum.hexdigest() != entry['sha256']:
                    raise ValueError(f'source hash changed: {entry["source"]}')
                state['state'] = 'verifying_copy'
                state['file_bytes_processed'] = 0
                if digest(temporary, progress) != entry['sha256']:
                    raise ValueError(f'copied file hash mismatch: {temporary}')
                os.replace(temporary, path)
            state['state'] = 'copying'
            state['verified_files'] += 1
            state['verified_bytes'] += entry['size_bytes']
        # Metadata and receipt hashes must still be identical after a long copy.
        if plan_release(source) != plan:
            raise ValueError('source plan changed during publishing')
        manifest = {k: v for k, v in plan.items() if k not in ('files', 'source_roots')}
        manifest['artifacts'] = [{k: v for k, v in f.items() if k != 'source'} for f in plan['files']]
        write(stage / 'manifest.json', manifest)
        result = {'kind': KIND, 'status': 'completed', 'plan_sha256': plan_hash,
                  'manifest_sha256': digest(stage / 'manifest.json'), 'input_rows': plan['input_rows'],
                  'unit_count': plan['unit_count'], 'files': len(plan['files']), 'bytes': plan['total_bytes'],
                  'completed_at': datetime.now(timezone.utc).isoformat()}
        write(stage / 'COMPLETE.json', result)
        state['state'] = 'completed'
        progress(force=True)
        if destination.exists():
            raise ValueError('release destination appeared during publishing')
        os.rename(stage, destination)
        print(json.dumps(result, sort_keys=True), flush=True)
        return result


def verification_progress():
    total, last = 0, 0.0

    def report(amount):
        nonlocal total, last
        total += amount
        now = time.monotonic()
        if now - last >= 5:
            print(json.dumps({'state': 'verifying_release', 'bytes_hashed': total}), flush=True)
            last = now
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--capacity-root', type=Path)
    parser.add_argument('--capacity-bytes', type=int)
    parser.add_argument('--used-bytes', type=int)
    parser.add_argument('--reserve-bytes', type=int)
    args = parser.parse_args()
    if args.verify_only:
        print(json.dumps(verify_release(args.destination, verification_progress()), sort_keys=True), flush=True)
        return
    if any(getattr(args, name) is None for name in ('source', 'capacity_root', 'capacity_bytes', 'used_bytes', 'reserve_bytes')):
        parser.error('publishing requires source and explicit measured capacity arguments')
    publish(args.source, args.destination, capacity_root=args.capacity_root,
            capacity_bytes=args.capacity_bytes, used_bytes=args.used_bytes, reserve_bytes=args.reserve_bytes)


if __name__ == '__main__':
    guarded_entrypoint('publish_fixed_lexicon_release', main)
