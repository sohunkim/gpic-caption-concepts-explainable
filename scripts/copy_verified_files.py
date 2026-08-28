"""Copy immutable files within cluster storage; retain resumable partials."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from incident_gate import guarded_entrypoint

BLOCK = 8 * 1024 * 1024


def discard_cached_pages(handle) -> bool:
    """Release clean pages after one-pass I/O; never flush global kernel caches."""
    if not hasattr(os, 'posix_fadvise') or not hasattr(os, 'POSIX_FADV_DONTNEED'):
        return False
    os.posix_fadvise(handle.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    return True


def digest(path: Path, *, discard_cache: bool = False) -> str:
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(BLOCK), b''):
            result.update(chunk)
        if discard_cache:
            discard_cached_pages(handle)
    return result.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def copy_verified(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding='utf-8'))
    root = Path(config['output_root']).resolve()
    items = config['files']
    if not items or len({item['name'] for item in items}) != len(items):
        raise ValueError('empty or duplicate file list')
    for item in items:
        target = (root / item['name']).resolve()
        source = Path(item['source']).resolve()
        if target.parent != root or source == root or root in source.parents:
            raise ValueError('unsafe or overlapping copy paths')
        if len(item['sha256']) != 64 or len(bytes.fromhex(item['sha256'])) != 32:
            raise ValueError('invalid expected SHA256')
        if source.stat().st_size != item['size_bytes']:
            raise ValueError('source size changed')
    root.mkdir(parents=True, exist_ok=True)
    config_hash = digest(config_path)
    pin = root / 'copy_config.sha256'
    if pin.exists() and pin.read_text().strip() != config_hash:
        raise ValueError('copy identity changed')
    if not pin.exists():
        with pin.open('x', encoding='ascii') as handle:
            handle.write(config_hash + '\n')
    results = []
    for item in items:
        source, target = Path(item['source']), root / item['name']
        if target.exists():
            if target.stat().st_size != item['size_bytes'] or digest(target) != item['sha256']:
                raise ValueError('existing destination differs; refusing overwrite')
        else:
            partial = target.with_name(target.name + '.partial')
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > item['size_bytes']:
                raise ValueError('partial is larger than source')
            if item['size_bytes'] - offset > shutil.disk_usage(root).free * 0.9:
                raise RuntimeError('insufficient destination capacity with headroom')
            last = 0.0
            with source.open('rb') as src:
                if offset:
                    with partial.open('rb') as old:
                        for chunk in iter(lambda: old.read(BLOCK), b''):
                            if src.read(len(chunk)) != chunk:
                                raise ValueError('partial prefix differs from source')
                with partial.open('ab') as dst:
                    for chunk in iter(lambda: src.read(BLOCK), b''):
                        dst.write(chunk)
                        offset += len(chunk)
                        if time.monotonic() - last >= 5:
                            if shutil.disk_usage(root).free < BLOCK * 2:
                                raise RuntimeError('destination is full')
                            atomic_json(root / 'progress.json', {'state': 'copying', 'file': item['name'],
                                        'bytes': offset, 'total_bytes': item['size_bytes'], 'updated_at': time.time()})
                            last = time.monotonic()
                    dst.flush()
                    os.fsync(dst.fileno())
            atomic_json(root / 'progress.json', {'state': 'verifying', 'file': item['name'], 'updated_at': time.time()})
            if partial.stat().st_size != item['size_bytes'] or digest(partial) != item['sha256']:
                raise ValueError('copied file checksum mismatch')
            os.replace(partial, target)
        results.append({'path': str(target), 'size_bytes': item['size_bytes'], 'sha256': item['sha256']})
    atomic_json(root / 'COMPLETE.json', {'status': 'verified', 'config_sha256': config_hash, 'files': results})
    atomic_json(root / 'progress.json', {'state': 'complete', 'updated_at': time.time()})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    copy_verified(args.config)


if __name__ == '__main__':
    raise SystemExit(guarded_entrypoint('copy_verified_files', main))
