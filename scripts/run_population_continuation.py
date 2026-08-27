"""Run a pinned population continuation after its predecessor has verified success.

The controller schedules existing producers; it does not implement extraction or
alter batching. Linux flock, incident gates, receipts, and explicit child argv
make the job independent of an interactive desktop connection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import shutil

from run_background_job import process_matches_record
from run_t5_lexical_followup import (digest_file, read_json, write_json, verify_t5)
from incident_gate import guarded_entrypoint


KIND = 'gpic-population-continuation-v1'


def predecessor_ready(config):
    job = read_json(Path(config['job']))
    if (Path(job['pipeline_state_dir']) / 'incident.json').exists():
        raise RuntimeError('predecessor incident is open; continuation blocked')
    running = process_matches_record(job)[0]
    complete_path = Path(config['output']) / 'COMPLETE.json'
    if running:
        return False
    if not complete_path.exists():
        raise RuntimeError('predecessor stopped without COMPLETE; no automatic restart')
    complete = read_json(complete_path)
    if (complete.get('status') != 'completed' or complete.get('input_rows') != config['rows']
            or complete.get('identity_sha256') != config['identity_sha256']):
        raise ValueError('predecessor COMPLETE identity or population mismatch')
    return True


def verify_config(config):
    if config.get('kind') != KIND:
        raise ValueError('unsupported continuation config')
    names = [step['name'] for step in config['steps']]
    if names != ['verify_predecessor', 'prepare_population', 'validate_population',
                 't5_formal', 'verify_t5', 'lexical_formal']:
        raise ValueError('continuation steps must preserve the verification gates')
    for pin in config['pinned_files']:
        path = Path(pin['path'])
        if path.stat().st_size != pin['size_bytes'] or digest_file(path) != pin['sha256']:
            raise ValueError(f'pinned input changed: {path}')
    for item in config['repositories']:
        def git(*argv):
            return subprocess.run(['git', '-C', item['path'], *argv], check=True,
                                  capture_output=True, text=True, timeout=30).stdout.strip()
        if git('rev-parse', 'HEAD') != item['commit'] or git('status', '--porcelain'):
            raise ValueError(f'checkout revision or cleanliness mismatch: {item["path"]}')
    allowed = Path(config['owned_output_root']).resolve()
    for name in ('population_output', 't5_output', 'lexical_output'):
        target = Path(config[name]).resolve()
        if allowed not in target.parents:
            raise ValueError(f'output outside the owned root: {target}')
    # Preflights are bounded local dependency checks, not inference jobs.
    for item in config['runtime_preflights']:
        subprocess.run(item['argv'], env={**os.environ, **item.get('env', {})},
                       check=True, stdin=subprocess.DEVNULL, timeout=60)


def verify_t5_result(config, report):
    root = Path(config['t5_output'])
    current = read_json(root / 'run_manifest.json')
    reference = read_json(Path(config['t5_reference_manifest']))
    for field in ('semantic_settings', 'producer_source_sha256', 'dependency_lock_sha256', 'source_revision'):
        if current[field] != reference[field]:
            raise ValueError(f'T5 changed locked {field}')
    return verify_t5({'t5_root': str(root), 't5_identity_sha256': current['identity_sha256'],
                      'input_manifest': str(Path(config['population_output']) / 'input_manifest.json'),
                      'expected_rows': config['expected_rows']}, report)


def stop_child(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=15)


def execute_step(step, config, report, *, gpus=None):
    root = Path(config['control_root'])
    log_path = root / 'logs' / f'{step["name"]}.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = list(step['argv'])
    if gpus is not None and step['name'] in {'t5_formal', 'lexical_formal'}:
        if argv.count('--gpus') != 1:
            raise ValueError('GPU override requires exactly one GPU argument')
        argv[argv.index('--gpus') + 1] = gpus
    with log_path.open('ab', buffering=0) as log:
        process = subprocess.Popen(argv, cwd=step['cwd'], env={**os.environ, **step.get('env', {})},
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        try:
            while process.poll() is None:
                progress = {}
                path = Path(step['progress']) if step.get('progress') else None
                if path and path.exists():
                    progress = read_json(path)
                free = shutil.disk_usage(config['owned_output_root']).free
                report(step['name'], child_pid=process.pid, log=str(log_path),
                       disk_free_bytes=free, child_progress=progress)
                if free < config['disk_floor_bytes']:
                    raise RuntimeError('disk headroom exhausted; keeping completed receipts, stopping child')
                time.sleep(config['poll_seconds'])
            if process.returncode != 0:
                raise RuntimeError(f'{step["name"]} exited {process.returncode}; see {log_path}')
        finally:
            stop_child(process)


def run(config_path, *, gpus=None):
    import fcntl

    config = read_json(config_path)
    root = Path(config['control_root'])
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = digest_file(config_path)
        pin_path = root / 'controller_identity.json'
        if pin_path.exists() and read_json(pin_path)['config_sha256'] != identity:
            raise ValueError('continuation config changed; use a new control root')
        write_json(pin_path, {'config_sha256': identity})

        def report(state, **details):
            value = {'kind': KIND, 'state': state, 'pid': os.getpid(),
                     'config_sha256': identity, 'updated_at': datetime.now(timezone.utc).isoformat(), **details}
            write_json(root / 'status.json', value)
            print(json.dumps(value, sort_keys=True), flush=True)

        try:
            report('preflight')
            verify_config(config)
            while not predecessor_ready(config['predecessor']):
                report('waiting_for_predecessor', predecessor=config['predecessor']['output'])
                time.sleep(config['poll_seconds'])
            for step in config['steps']:
                if step['name'] == 't5_formal':
                    verify_config(config)
                    if shutil.disk_usage(config['owned_output_root']).free < config['full_projected_bytes']:
                        raise RuntimeError('Full capacity estimate with headroom exceeds free storage')
                if step['name'] == 'verify_t5':
                    result = verify_t5_result(config, report)
                    write_json(root / 't5_verification.json', result)
                else:
                    execute_step(step, config, report, gpus=gpus)
                write_json(root / f'{step["name"]}_done.json', {'config_sha256': identity,
                           'step': step['name'], 'finished_at': datetime.now(timezone.utc).isoformat()})
            result = read_json(Path(config['lexical_output']) / 'COMPLETE.json')
            if result.get('status') != 'completed' or result.get('input_rows') != config['expected_rows']:
                raise ValueError('Full lexical completion population mismatch')
            report('completed', rows=config['expected_rows'])
            write_json(root / 'COMPLETE.json', read_json(root / 'status.json'))
        except BaseException as exc:
            report('failed', error=f'{type(exc).__name__}: {exc}')
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--gpus', help='Optional restart-only override for both producers.')
    args = parser.parse_args()
    def stop(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGTERM, stop)
    run(args.config, gpus=args.gpus)


if __name__ == '__main__':
    raise SystemExit(guarded_entrypoint('population_continuation', main))
