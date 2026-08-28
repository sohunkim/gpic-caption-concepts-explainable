from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import publish_fixed_lexicon_release as release


def save_identity(path, value):
    value = deepcopy(value)
    value.pop('identity_sha256', None)
    value['identity_sha256'] = release.json_digest(value)
    release.write(path, value)
    return value


def artifact(path, root):
    return {'path': path.relative_to(root).as_posix(), 'size_bytes': path.stat().st_size,
            'sha256': release.digest(path)}


def finish_unit(root, unit, identity):
    folder = root / 'units' / unit['unit_id']
    for name in ('mixed_pipeline_summary.jsonl', 'pipeline_state.json', 'stage6/summary.jsonl',
                 'stage456_sharded/shards/shard_0000/stage5/canonical_mentions.jsonl',
                 'stage456_sharded/shards/shard_0000/stage5/canonical_edges.jsonl'):
        release.write(folder / name, {'unit': unit['unit_id']})
    (folder / 'stage6/object_counts.tsv').write_bytes(b'key\tcount\nthing\t1\n')
    receipt = {'kind': 'gpic-fixed-lexicon-unit-receipt-v2', 'unit': unit,
               'run_identity_sha256': identity, 'retention': {'policy': 'canonical_counts'},
               'artifacts': [artifact(p, root) for p in sorted(folder.rglob('*')) if p.is_file()]}
    path = root / 'receipts' / (unit['unit_id'] + '.json')
    release.write(path, receipt)
    return path


@pytest.fixture
def case(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    units = [{'unit_id': f'unit_{i:06d}', 'rows': 1,
              'shards': [{'shard_id': f'input_{i}', 'rows': 1, 'sha256': str(i) * 64}]}
             for i in range(2)]
    template = {'kind': release.RUN_KIND, 'source_revision': 'old', 'units': units,
                'semantic_settings': {'model': 'fixture'}, 'retention_policy': 'canonical_counts'}
    prior = save_identity(old / 'run_manifest.json', template)
    receipt = finish_unit(old, units[0], prior['identity_sha256'])
    handoff = tmp_path / 'handoff.json'
    release.write(handoff, {'units': [{'unit_id': units[0]['unit_id'], 'rows': 1,
                                     'receipt_sha256': release.digest(receipt)}]})
    template['source_revision'] = 'new'
    template['verified_unit_handoff'] = {'path': str(handoff), 'sha256': release.digest(handoff),
        'source_root': str(old), 'source_identity_sha256': prior['identity_sha256'],
        'reused_units': 1, 'reused_rows': 1}
    current = save_identity(new / 'run_manifest.json', template)
    finish_unit(new, units[1], current['identity_sha256'])
    (new / 'stage6').mkdir()
    (new / 'stage6/object_counts.tsv').write_bytes(b'key\tcount\nthing\t2\n')
    complete = {'status': 'completed', 'identity_sha256': current['identity_sha256'], 'unit_count': 2,
                'input_rows': 2, 'unit_sources': {
                    units[0]['unit_id']: {'path': str(old / 'units' / units[0]['unit_id']), 'source_revision': 'old'},
                    units[1]['unit_id']: {'path': str(new / 'units' / units[1]['unit_id']), 'source_revision': 'new'}},
                'artifacts': [artifact(new / 'stage6/object_counts.tsv', new)]}
    release.write(new / 'COMPLETE.json', complete)
    return old, new, tmp_path / 'release'


def publish(case, **kwargs):
    _, source, destination = case
    options = dict(capacity_root=destination.parent, capacity_bytes=10**9, used_bytes=0, reserve_bytes=100)
    options.update(kwargs)
    return release.publish(source, destination, **options)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_independent_release_preserves_all_bytes_and_provenance(case):
    old, source, destination = case
    before = snapshot(old), snapshot(source)
    plan = release.plan_release(source)
    result = publish(case)
    assert result['input_rows'] == 2 and result['unit_count'] == 2
    for entry in plan['files']:
        assert (destination / entry['path']).read_bytes() == Path(entry['source']).read_bytes()
    assert (snapshot(old), snapshot(source)) == before
    assert publish(case) == result
    old.rename(old.with_name('old_unavailable'))
    source.rename(source.with_name('new_unavailable'))
    assert release.verify_release(destination) == result
    manifest = release.read(destination / 'manifest.json')
    assert [u['source_revision'] for u in manifest['units']] == ['old', 'new']
    assert all(not Path(p).is_absolute() for p in manifest['stage5_roots'])
    assert len(manifest['stage5_shards']) == 2


@pytest.mark.parametrize('damage', ['missing_file', 'changed_bytes', 'changed_receipt', 'missing_global', 'extra_global'])
def test_invalid_source_never_promotes(case, damage):
    old, source, dest = case
    mention = old / 'units/unit_000000/stage456_sharded/shards/shard_0000/stage5/canonical_mentions.jsonl'
    if damage == 'missing_file':
        mention.unlink()
    elif damage == 'changed_bytes':
        mention.write_bytes(b'X' * mention.stat().st_size)
    elif damage == 'changed_receipt':
        release.write(old / 'receipts/unit_000000.json', {})
    elif damage == 'missing_global':
        (source / 'stage6/object_counts.tsv').unlink()
    else:
        (source / 'stage6/unrecorded.tsv').write_bytes(b'bad')
    with pytest.raises((ValueError, FileNotFoundError)):
        publish(case)
    assert not dest.exists()


def test_missing_edge_cannot_be_hidden_by_a_new_receipt(case):
    _, source, dest = case
    path = source / 'receipts/unit_000001.json'
    receipt = release.read(path)
    target = next(a for a in receipt['artifacts'] if a['path'].endswith('canonical_edges.jsonl'))
    (source / target['path']).unlink()
    receipt['artifacts'].remove(target)
    release.write(path, receipt)
    with pytest.raises(ValueError, match='missing Stage 5'):
        publish(case)
    assert not dest.exists()


@pytest.mark.parametrize('damage', ['duplicate_unit', 'duplicate_input', 'row_count'])
def test_bad_input_coverage(case, damage):
    _, source, _ = case
    value = release.read(source / 'run_manifest.json')
    if damage == 'duplicate_unit':
        value['units'][1] = deepcopy(value['units'][0])
    elif damage == 'duplicate_input':
        value['units'][1]['shards'] = deepcopy(value['units'][0]['shards'])
    else:
        value['units'][1]['shards'][0]['rows'] = 2
    value = save_identity(source / 'run_manifest.json', value)
    complete = release.read(source / 'COMPLETE.json')
    complete['identity_sha256'] = value['identity_sha256']
    release.write(source / 'COMPLETE.json', complete)
    with pytest.raises(ValueError):
        publish(case)


def test_resume_after_interrupted_copy_rechecks_existing_bytes(case, monkeypatch):
    _, source, dest = case
    original = release.os.replace
    interrupted = False

    def replace(src, dst):
        nonlocal interrupted
        if str(dst).endswith('canonical_mentions.jsonl') and not interrupted:
            interrupted = True
            raise OSError('simulated interruption before promotion')
        return original(src, dst)

    monkeypatch.setattr(release.os, 'replace', replace)
    with pytest.raises(OSError, match='simulated'):
        publish(case)
    assert not dest.exists()
    assert dest.with_name(dest.name + '.partial').exists()
    monkeypatch.setattr(release.os, 'replace', original)
    publish(case)
    release.verify_release(dest)


def test_changed_partial_plan_blocks_resume(case):
    _, source, dest = case
    stage = dest.with_name(dest.name + '.partial')
    release.write(stage / 'provenance/publish_plan.json', {'wrong': True})
    with pytest.raises(ValueError, match='different source plan'):
        publish(case)


@pytest.mark.parametrize('bad', ['quota', 'disk'])
def test_capacity_reserve_is_enforced(case, monkeypatch, bad):
    if bad == 'disk':
        from types import SimpleNamespace
        monkeypatch.setattr(release.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match='capacity'):
        publish(case, capacity_bytes=1 if bad == 'quota' else 10**9)
    assert not case[2].exists()


@pytest.mark.parametrize('name', ['../outside', '/outside', 'a\\b', 'a/../b'])
def test_unsafe_paths_rejected(name):
    with pytest.raises(ValueError, match='unsafe'):
        release.relative(name)


def test_source_destination_nesting_is_rejected(case):
    old, source, _ = case
    with pytest.raises(ValueError, match='non-nested'):
        publish((old, source, source / 'release'))


def test_lock_prevents_two_publishers(case):
    lock = case[2].with_name(case[2].name + '.publish.lock')
    with release.release_lock(lock):
        with pytest.raises(OSError):
            publish(case)


def test_release_corruption_is_detected(case):
    publish(case)
    (case[2] / 'counts/object_counts.tsv').write_bytes(b'bad')
    with pytest.raises(ValueError, match='artifact mismatch'):
        release.verify_release(case[2])
