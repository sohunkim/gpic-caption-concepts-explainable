from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cleanup_obsolete_corrected_1m as cleanup
from publish_fixed_lexicon_release import digest, write


@pytest.fixture
def case(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    for name, value in [('BASE', base), ('TARGET', base / 'corrected-1m'),
                        ('CONTROL', base / 'control'), ('RELEASE', base / 'release'),
                        ('EVIDENCE', base / 'retirement')]:
        monkeypatch.setattr(cleanup, name, value)
    write(cleanup.TARGET / 'run_manifest.json', {'identity': 'old'})
    (cleanup.TARGET / 'output.jsonl').write_bytes(b'data\n')
    write(cleanup.CONTROL / 'config.json', {})
    write(cleanup.RELEASE / 'manifest.json', {})
    write(cleanup.RELEASE / 'COMPLETE.json', {'status': 'completed',
          'manifest_sha256': digest(cleanup.RELEASE / 'manifest.json')})
    # /proc is available only on the production Linux host; fixture has no processes.
    original = Path.iterdir
    monkeypatch.setattr(Path, 'iterdir', lambda p: iter(()) if p == Path('/proc') else original(p))
    monkeypatch.setattr(sys, 'argv', ['cleanup'])
    return base


def test_audit_is_read_only(case):
    result = cleanup.inspect()
    cleanup.main()
    assert result['bytes'] > 0 and len(result['files']) == 2
    assert cleanup.TARGET.exists() and not cleanup.EVIDENCE.exists()


@pytest.mark.parametrize('form', ['native', 'posix', 'nested'])
def test_reference_blocks_cleanup(case, monkeypatch, form):
    target = cleanup.TARGET / 'output.jsonl'
    reference = target.as_posix() if form == 'posix' else str(target)
    value = {'reference': reference} if form != 'nested' else {'steps': [{'inputs': [reference]}]}
    write(cleanup.CONTROL / 'config.json', value)
    monkeypatch.setattr(sys, 'argv', ['cleanup', '--apply'])
    with pytest.raises(ValueError, match='active references'):
        cleanup.main()
    assert cleanup.TARGET.exists()


def test_apply_preserves_retirement_metadata_and_only_removes_target(case, monkeypatch):
    original = (cleanup.TARGET / 'run_manifest.json').read_bytes()
    keep = case / 'corrected-nano-1m'
    keep.mkdir()
    monkeypatch.setattr(sys, 'argv', ['cleanup', '--apply'])
    cleanup.main()
    assert not cleanup.TARGET.exists() and keep.exists()
    assert (cleanup.EVIDENCE / 'metadata/run_manifest.json').read_bytes() == original
    assert cleanup.read(cleanup.EVIDENCE / 'COMPLETE.json')['verified_absent']


def test_unsealed_release_blocks_removal(case, monkeypatch):
    write(cleanup.RELEASE / 'manifest.json', {'changed': True})
    monkeypatch.setattr(sys, 'argv', ['cleanup', '--apply'])
    with pytest.raises(ValueError, match='not sealed'):
        cleanup.main()
    assert cleanup.TARGET.exists()
