"""Audit/remove the explicitly retired Lite-prefix 1M, never the official Nano."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

from incident_gate import guarded_entrypoint
from publish_fixed_lexicon_release import digest, read, write

BASE = Path('/mnt/ddn/prod-runs/snu14ksh/gpic-scaleout')
TARGET = BASE / 'corrected-1m'
CONTROL = BASE / 'full-official-v1/continuation-ec69a34-20260828'
RELEASE = BASE / 'releases/lexical-lite10m-v1'
EVIDENCE = BASE / 'maintenance/lexical-release-20260828/obsolete-corrected-1m'


def inspect():
    if TARGET.is_symlink() or TARGET.resolve() != TARGET or TARGET.parent.resolve() != BASE:
        raise ValueError('unexpected deletion boundary')
    config = CONTROL / 'config.json'
    pending, evidence, refs = [config], {}, []
    while pending:
        path = pending.pop()
        if str(path) in evidence:
            continue
        raw = path.read_bytes()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError(f'oversized control metadata: {path}')
        text = raw.decode('utf-8')
        evidence[str(path)] = digest(path)
        data = json.loads(text)

        def visit(value):
            if isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, str):
                normalized = value.replace('\\', '/')
                if TARGET.as_posix() in normalized:
                    refs.append(str(path))
                if not normalized.startswith(('/mnt/nvme/gpic-scaleout/', BASE.as_posix() + '/')):
                    return
                item = Path(value)
                if (item.suffix == '.json' and item.name not in {'progress.json', 'status.json', 'job.json'}
                        and not item.name.endswith('_progress.json') and item.is_file()):
                    pending.append(item)
                elif item.is_dir():
                    pending.extend(p for p in (item / 'run_manifest.json', item / 'COMPLETE.json') if p.is_file())
        visit(data)

    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / 'cmdline').read_bytes().decode(errors='replace')
            if str(TARGET) in cmd:
                refs.append(f'process:{proc.name}')
            for fd in (proc / 'fd').iterdir():
                if fd.resolve().is_relative_to(TARGET):
                    refs.append(f'open_file:{fd}')
        except (FileNotFoundError, ProcessLookupError):
            continue
    if refs:
        raise ValueError(f'active references prevent cleanup: {refs}')
    files = []
    for path in sorted(TARGET.rglob('*')):
        if path.is_symlink() or not path.resolve().is_relative_to(TARGET):
            raise ValueError(f'linked/escaping deletion entry: {path}')
        if path.is_file():
            files.append({'path': path.relative_to(TARGET).as_posix(),
                          'size_bytes': path.stat().st_size, 'sha256': digest(path)})
    return {'target': str(TARGET), 'files': files, 'bytes': sum(f['size_bytes'] for f in files),
            'active_metadata': evidence, 'active_reference_count': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not TARGET.exists():
        print(json.dumps({'status': 'already_absent', 'target': str(TARGET)}), flush=True)
        return
    report = inspect()
    print(json.dumps({'status': 'audit_clear', 'target': str(TARGET), 'files': len(report['files']),
                      'bytes': report['bytes'], 'checked_metadata_files': len(report['active_metadata'])}), flush=True)
    if not args.apply:
        return
    complete = read(RELEASE / 'COMPLETE.json')
    if complete.get('status') != 'completed' or complete['manifest_sha256'] != digest(RELEASE / 'manifest.json'):
        raise ValueError('independent lexical release is not sealed')
    report.update(reason='User retired the obsolete Lite first-1M prefix; official Nano kept separately.',
                  lexical_release=complete, retired_at=datetime.now(timezone.utc).isoformat())
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for file in report['files']:
        src = TARGET / file['path']
        if src.suffix == '.json':
            dst = EVIDENCE / 'metadata' / file['path']
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            if digest(dst) != file['sha256']:
                raise ValueError('retirement metadata copy mismatch')
    write(EVIDENCE / 'retirement_plan.json', report)
    if inspect() != {k: report[k] for k in ('target', 'files', 'bytes', 'active_metadata', 'active_reference_count')}:
        raise ValueError('deletion target or active references changed during audit')
    # Only this user-approved absolute, fully enumerated boundary is removed.
    shutil.rmtree(TARGET)
    report['status'] = 'removed'
    report['verified_absent'] = not TARGET.exists()
    write(EVIDENCE / 'COMPLETE.json', report)
    print(json.dumps({'status': 'removed', 'bytes': report['bytes'], 'evidence': str(EVIDENCE)}), flush=True)


if __name__ == '__main__':
    if '--apply' in sys.argv:
        guarded_entrypoint('cleanup_obsolete_corrected_1m', main)
    else:
        main()
