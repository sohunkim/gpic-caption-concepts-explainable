import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_population_continuation as queue


@pytest.fixture
def predecessor(tmp_path,monkeypatch):
    (tmp_path/'state').mkdir()
    (tmp_path/'job.json').write_text(json.dumps({'pipeline_state_dir':str(tmp_path/'state')}))
    monkeypatch.setattr(queue,'process_matches_record',lambda _: (False,'not_running',''))
    return dict(job=str(tmp_path/'job.json'),output=str(tmp_path),rows=12,identity_sha256='abc')


def complete(config):
    (Path(config['output'])/'COMPLETE.json').write_text(json.dumps(
        dict(status='completed',input_rows=12,identity_sha256='abc')))


def test_live_predecessor_never_admits_full(predecessor,monkeypatch):
    complete(predecessor)
    monkeypatch.setattr(queue,'process_matches_record',lambda _: (True,'running',''))
    assert queue.predecessor_ready(predecessor) is False


def test_verified_predecessor_can_admit(predecessor):
    complete(predecessor)
    assert queue.predecessor_ready(predecessor)


def test_stopped_without_complete_is_not_auto_restarted(predecessor):
    with pytest.raises(RuntimeError,match='without COMPLETE'): queue.predecessor_ready(predecessor)


def test_incident_blocks_even_completed_predecessor(predecessor):
    complete(predecessor)
    (Path(predecessor['output'])/'state/incident.json').write_text('{}')
    with pytest.raises(RuntimeError,match='incident'): queue.predecessor_ready(predecessor)


def test_wrong_population_blocks(predecessor):
    complete(predecessor)
    predecessor['rows']=13
    with pytest.raises(ValueError): queue.predecessor_ready(predecessor)


def test_missing_gate_or_reordered_steps_block():
    with pytest.raises(ValueError,match='verification gates'):
        queue.verify_config({'kind':queue.KIND,'steps':[{'name':'t5_formal'}]})


def test_locked_t5_semantics_checked_before_verification(tmp_path,monkeypatch):
    reference={'semantic_settings':{'cuda_tf32':False}}
    (tmp_path/'reference.json').write_text(json.dumps(reference))
    (tmp_path/'run_manifest.json').write_text(json.dumps({'semantic_settings':{'cuda_tf32':True}}))
    called=[]
    monkeypatch.setattr(queue,'verify_t5',lambda *_: called.append(True))
    with pytest.raises(ValueError,match='semantic_settings'):
        queue.verify_t5_result(dict(t5_output=str(tmp_path),t5_reference_manifest=str(tmp_path/'reference.json')),print)
    assert called==[]
