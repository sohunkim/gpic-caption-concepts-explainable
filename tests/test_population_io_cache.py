import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import copy_verified_files as io
import export_registry_caption_shards as exporter


def test_digest_releases_only_when_requested_and_after_read(tmp_path, monkeypatch):
    path = tmp_path / 'input'
    path.write_bytes(b'caption data')
    calls = []
    def release(handle):
        assert not handle.closed
        calls.append(handle.tell())
    monkeypatch.setattr(io, 'discard_cached_pages', release)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert io.digest(path) == expected
    assert calls == []
    assert io.digest(path, discard_cache=True) == expected
    assert calls == [len(b'caption data')]
    assert path.read_bytes() == b'caption data'


def test_advice_is_file_scoped(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(io.os, 'posix_fadvise', lambda *args: calls.append(args), raising=False)
    monkeypatch.setattr(io.os, 'POSIX_FADV_DONTNEED', 4, raising=False)
    path = tmp_path / 'file'
    path.write_bytes(b'data')
    with path.open('rb') as handle:
        assert io.discard_cached_pages(handle)
        assert calls == [(handle.fileno(), 0, 0, 4)]
    assert path.read_bytes() == b'data'


def test_unsupported_advice_preserves_io(tmp_path, monkeypatch):
    monkeypatch.delattr(io.os, 'posix_fadvise', raising=False)
    path = tmp_path / 'file'
    path.write_bytes(b'data')
    assert io.digest(path, discard_cache=True) == hashlib.sha256(b'data').hexdigest()


def test_advice_error_is_not_silently_ignored(tmp_path, monkeypatch):
    def fail(*_):
        raise OSError('advice failed')
    monkeypatch.setattr(io.os, 'posix_fadvise', fail, raising=False)
    monkeypatch.setattr(io.os, 'POSIX_FADV_DONTNEED', 4, raising=False)
    path = tmp_path / 'file'
    path.write_bytes(b'data')
    with pytest.raises(OSError, match='advice failed'):
        io.digest(path, discard_cache=True)


@pytest.fixture
def exported(tmp_path, monkeypatch):
    source = tmp_path / 'registry.parquet'
    source.write_bytes(b'fixture registry bytes')
    meta = tmp_path / 'registry_meta.json'
    meta.write_text(json.dumps({'status': 'complete', 'row_count': 3,
        'registry_fingerprint': 'registry', 'membership_fingerprint': 'members',
        'file_checksums': {'registry.parquet': io.digest(source)}}))
    config = {'registry': str(source), 'registry_meta': str(meta),
        'registry_sha256': io.digest(source), 'metadata_sha256': io.digest(meta),
        'registry_fingerprint': 'registry', 'source_population_id': 'test-population',
        'registry_id': 'test-registry', 'tier': 'full', 'expected_rows': 3,
        'rows_per_shard': 2, 'initial_bytes_per_row': 1024, 'capacity_headroom_factor': 1.5,
        'output_root': str(tmp_path / 'output')}
    records = [dict(key=f'{i:04d}', global_index=i, parent_global_index=i,
                    caption=f'Caption {i}.') for i in range(3)]
    # This fixture tests the export/receipt contract without requiring
    # PyArrow in the lightweight controller environment unit tests.
    arrow = ModuleType('pyarrow')
    parquet = ModuleType('pyarrow.parquet')
    arrow.parquet = parquet
    class FixtureParquet:
        def __init__(self, _path):
            self.metadata = SimpleNamespace(num_rows=3)
            self.schema_arrow = SimpleNamespace(names=list(records[0]))
        def iter_batches(self, **_kwargs):
            yield SimpleNamespace(to_pylist=lambda: records)
        def close(self):
            pass
    parquet.ParquetFile = FixtureParquet
    monkeypatch.setitem(sys.modules, 'pyarrow', arrow)
    monkeypatch.setitem(sys.modules, 'pyarrow.parquet', parquet)
    exporter.export_registry(config)
    return config, parquet


def test_completed_export_reuses_verified_bytes_without_reparsing(exported):
    config, parquet = exported
    root = Path(config['output_root'])
    before = {p.name: p.read_bytes() for p in (root / 'shards').iterdir()}
    def reject_reparse(*_):
        raise AssertionError('completed registry was reparsed')
    parquet.ParquetFile = reject_reparse
    exporter.export_registry(config)
    assert {p.name: p.read_bytes() for p in (root / 'shards').iterdir()} == before
    assert json.loads((root / 'progress.json').read_text())['reused_verified_export']


def test_export_writer_syncs_before_cache_release(exported, monkeypatch):
    config, _ = exported
    config = {**config, 'output_root': str(Path(config['output_root']).with_name('fresh'))}
    synced = []
    released = []
    real_sync = exporter.os.fsync

    def sync(fd):
        real_sync(fd)
        synced.append(fd)

    def release(handle):
        if str(handle.name).endswith('.partial'):
            assert synced[-1] == handle.fileno()
            assert handle.tell() == exporter.os.fstat(handle.fileno()).st_size
            assert handle.tell() > 0
            released.append(handle.name)

    monkeypatch.setattr(exporter.os, 'fsync', sync)
    monkeypatch.setattr(exporter, 'discard_cached_pages', release)
    exporter.export_registry(config)
    assert len(released) == 2


@pytest.mark.parametrize('tamper', ['shard', 'manifest', 'config', 'source'])
def test_completed_export_still_rejects_tampering(exported, tamper):
    config, _ = exported
    root = Path(config['output_root'])
    if tamper == 'shard':
        next((root / 'shards').iterdir()).write_bytes(b'bad')
    elif tamper == 'manifest':
        (root / 'input_manifest.json').write_text('{}')
    elif tamper == 'config':
        config['initial_bytes_per_row'] += 1
    else:
        Path(config['registry']).write_bytes(b'changed registry')
    with pytest.raises(ValueError):
        exporter.export_registry(config)


@pytest.mark.skipif(not hasattr(io.os, 'posix_fadvise'), reason='POSIX file-cache advice')
def test_real_cache_advice_preserves_bytes(tmp_path):
    path = tmp_path / 'fixture'
    value = b'input caption data\n' * 10000
    path.write_bytes(value)
    before = hashlib.sha256(value).hexdigest()
    assert io.digest(path, discard_cache=True) == before
    assert path.read_bytes() == value
