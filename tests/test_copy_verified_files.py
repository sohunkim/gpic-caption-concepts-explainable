import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from copy_verified_files import copy_verified


def setup(tmp_path):
    data = b'original immutable source\n' * 20
    src = tmp_path / 'source'
    src.write_bytes(data)
    root = tmp_path / 'copy'
    config = {'output_root': str(root), 'files': [{'name': 'data', 'source': str(src),
               'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]}
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps(config))
    return cfg, src, root, data, config


def test_copy_and_idempotent_resume(tmp_path):
    cfg, src, root, data, _ = setup(tmp_path)
    copy_verified(cfg)
    copy_verified(cfg)
    assert (root / 'data').read_bytes() == src.read_bytes() == data
    assert json.loads((root / 'COMPLETE.json').read_text())['status'] == 'verified'


def test_partial_resume(tmp_path):
    cfg, _, root, data, _ = setup(tmp_path)
    root.mkdir()
    (root / 'data.partial').write_bytes(data[:31])
    copy_verified(cfg)
    assert (root / 'data').read_bytes() == data


def test_bad_prefix_does_not_promote(tmp_path):
    cfg, _, root, _, _ = setup(tmp_path)
    root.mkdir()
    (root / 'data.partial').write_bytes(b'wrong')
    with pytest.raises(ValueError, match='prefix'):
        copy_verified(cfg)
    assert not (root / 'COMPLETE.json').exists()


@pytest.mark.parametrize('name', ['../escape', '.', 'nested/data'])
def test_destination_escape(tmp_path, name):
    cfg, _, root, _, config = setup(tmp_path)
    config['files'][0]['name'] = name
    cfg.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='unsafe'):
        copy_verified(cfg)
    assert not root.exists()


def test_destination_not_overwritten(tmp_path):
    cfg, _, root, _, _ = setup(tmp_path)
    root.mkdir()
    (root / 'data').write_bytes(b'different')
    with pytest.raises(ValueError, match='overwrite'):
        copy_verified(cfg)
    assert (root / 'data').read_bytes() == b'different'
