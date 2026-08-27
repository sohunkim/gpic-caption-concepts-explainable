import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from export_registry_caption_shards import export_rows


def rows(n=5):
    return [dict(key=f'{i:04d}', global_index=i, parent_global_index=i * 3,
                 caption=f'A caption {i}.', caption_type='medium', tar_shard='original.tar',
                 latent_path_s256='latent.safetensors', latent_row_s256=i) for i in range(n)]


def config(n=5):
    return dict(expected_rows=n, rows_per_shard=2, capacity_headroom_factor=1.5, initial_bytes_per_row=1024)


def test_preserve_markers_and_resume(tmp_path):
    source=rows()
    first, ids=export_rows(iter(source), config(), tmp_path)
    assert [r['rows'] for r in first]==[2,2,1]
    assert [r['start'] for r in first]==[0,2,4]
    actual=[]
    for shard in first:
        actual.extend(json.loads(line) for line in Path(shard['path']).read_text().splitlines())
    assert actual==[{**r,'id':r['key']} for r in source]
    assert export_rows(iter(source),config(),tmp_path)==(first,ids)


def test_interrupted_uncommitted_shard_can_resume(tmp_path):
    def interrupted():
        yield from rows()[:3]
        raise OSError('interrupted reader')
    with pytest.raises(OSError): export_rows(interrupted(),config(),tmp_path)
    assert json.loads((tmp_path/'build_state.json').read_text())['rows']==2
    result,_=export_rows(iter(rows()),config(),tmp_path)
    assert sum(r['rows'] for r in result)==5


@pytest.mark.parametrize('field,value', [('key','0000'),('global_index',0),('parent_global_index',0),
                                         ('caption',None),('id','wrong')])
def test_invalid_source_fails(field,value,tmp_path):
    source=rows()
    source[1][field]=value
    with pytest.raises(ValueError): export_rows(iter(source),config(),tmp_path)
    assert not (tmp_path/'COMPLETE.json').exists()


def test_receipt_tamper_rejected(tmp_path):
    shards,_=export_rows(iter(rows()),config(),tmp_path)
    Path(shards[0]['path']).write_text('bad')
    with pytest.raises(ValueError,match='committed'): export_rows(iter(rows()),config(),tmp_path)


def test_reject_changed_config_and_short_input(tmp_path):
    export_rows(iter(rows()),config(),tmp_path)
    with pytest.raises(ValueError,match='identity'): export_rows(iter(rows()),config(6),tmp_path)
    other=tmp_path/'other'
    with pytest.raises(ValueError,match='total'): export_rows(iter(rows()[:2]),config(),other)
