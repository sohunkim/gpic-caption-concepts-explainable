from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_t5_lexical_followup as followup
import planned_pause
from planned_pause import PauseControl, STATE_FILE, request_pause
from incident_gate import guarded_entrypoint, RUN_TOKEN_ENV, STATE_DIR_ENV


def save(path, value):
    followup.write_json(path, value)
    return path


@pytest.fixture
def run_fixture(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text('{"id":"first","caption":"a"}\n{"id":"second","caption":"b"}\n', encoding="utf-8")
    root = tmp_path / "formal"
    output = root / "shards/shard_000000/canonical.jsonl"
    output.parent.mkdir(parents=True)
    output.write_bytes(source.read_bytes())
    source_info = followup.scan_jsonl(source)
    shard = {"shard_id": "shard_000000", "path": str(source),
             **{key: source_info[key] for key in ("rows", "sha256", "size_bytes")}}
    input_manifest = save(tmp_path / "input.json", {"shards": [shard]})
    identity = "a" * 64
    manifest = {"identity_sha256": identity, "shards": [shard],
                "input_manifest_sha256": followup.digest_file(input_manifest),
                "grouping_fingerprint_schema": "gpic-factual-grouping-fingerprint-v1"}
    save(root / "run_manifest.json", manifest)
    grouping = {"schema": manifest["grouping_fingerprint_schema"], "outer_item_count": 2,
                "outer_group_count": 1, "sentence_group_count": 1, "sentence_item_count": 2,
                "outer_group_plan_sha256": "b" * 64, "sentence_group_plan_sha256": "c" * 64,
                "model_input_plan_sha256": "d" * 64}
    encoded = json.dumps({"shard_id": shard["shard_id"], "grouping_fingerprints": grouping},
                         sort_keys=True, separators=(",", ":")).encode()
    rollup = {"schema": grouping["schema"], "shard_count": 1,
              "ordered_shard_grouping_sha256": hashlib.sha256(len(encoded).to_bytes(8, "big") + encoded).hexdigest(),
              **{key: grouping[key] for key in ("outer_item_count", "outer_group_count",
                                               "sentence_item_count", "sentence_group_count")}}
    save(root / "COMPLETE.json", {**manifest, "grouping_fingerprint_rollup": rollup})
    receipt = {"kind": "gpic-factual-scene-graph-shard-receipt-v3", "shard_id": shard["shard_id"],
               "input": shard, "output": {"path": str(output), **followup.scan_jsonl(output)},
               "input_id_sequence_sha256": source_info["id_sequence_sha256"],
               "run_identity_sha256": identity, "grouping_fingerprints": grouping,
               "runtime_batching": {"oom_splits": 0}, "runtime_sentence_batching": {"oom_splits": 0}}
    receipt_path = save(root / "receipts/shard_000000.json", receipt)
    save(root / "progress.json", {"state": "completed", "completed_shards": 1, "total_shards": 1})
    status = save(tmp_path / "status.json", {"state": "complete"})
    config = {"t5_root": str(root), "input_manifest": str(input_manifest), "t5_identity_sha256": identity,
              "expected_rows": 2, "t5_status": str(status), "heartbeat_stale_seconds": 300,
              "t5_session": "fixture"}
    return config, receipt_path, output


def test_validates_output_bytes_rows_order_and_grouping(run_fixture):
    config, _, _ = run_fixture
    events = []
    result = followup.verify_t5(config, lambda state, **details: events.append(state))
    assert result["rows"] == 2
    assert result["status"] == "verified"
    assert events == ["verifying_t5"]
    assert followup.t5_state(config)[0]


def test_verification_releases_consumed_input_and_output_cache(run_fixture, monkeypatch):
    config, _, output = run_fixture
    calls = []

    def release(handle):
        assert not handle.closed
        path = Path(handle.name)
        assert handle.tell() == path.stat().st_size
        calls.append(path)

    monkeypatch.setattr(followup, "discard_cached_pages", release)
    assert followup.verify_t5(config, lambda *a, **kw: None)["rows"] == 2
    source = Path(followup.input_shards(Path(config["input_manifest"]))[0]["path"])
    assert calls == [source, output]


def test_repeated_pause_does_not_replace_a_pending_request(tmp_path, monkeypatch):
    control = PauseControl.start(tmp_path, "fixture")
    first = request_pause(tmp_path, expected_attempt=control.attempt)

    def reject_rewrite(*_):
        raise AssertionError("pending request was rewritten")

    monkeypatch.setattr(planned_pause, "_write", reject_rewrite)
    assert request_pause(tmp_path, expected_attempt=control.attempt) == first
    assert control.requested()


def test_population_metadata_does_not_change_shard_identity(run_fixture):
    config, _, _ = run_fixture
    path = Path(config["input_manifest"])
    payload = followup.read_json(path)
    payload["shards"][0].update({"shard_index": 0, "first_source_row": 0,
                                "last_source_row": 1, "registry_join": {"matched_rows": 2}})
    save(path, payload)
    root = Path(config["t5_root"])
    for filename in ("run_manifest.json", "COMPLETE.json"):
        value = followup.read_json(root / filename)
        value["input_manifest_sha256"] = followup.digest_file(path)
        save(root / filename, value)
    assert followup.verify_t5(config, lambda *a, **kw: None)["rows"] == 2


def test_output_corruption_blocks(run_fixture):
    config, _, output = run_fixture
    output.write_bytes(output.read_bytes().replace(b'"a"', b'"z"'))
    with pytest.raises(ValueError, match="sha256 mismatch"):
        followup.verify_t5(config, lambda *a, **kw: None)


def test_reordered_caption_ids_block_even_with_updated_output_hash(run_fixture):
    config, path, output = run_fixture
    output.write_bytes(b"".join(reversed(output.read_bytes().splitlines(keepends=True))))
    receipt = followup.read_json(path)
    receipt["output"]["sha256"] = followup.digest_file(output)
    save(path, receipt)
    with pytest.raises(ValueError, match="caption count/order mismatch"):
        followup.verify_t5(config, lambda *a, **kw: None)


@pytest.mark.parametrize("mutation", ["missing", "extra", "identity", "oom", "path", "rollup", "input"])
def test_invalid_receipts_and_completion_block(run_fixture, mutation):
    config, path, output = run_fixture
    receipt = followup.read_json(path)
    root = Path(config["t5_root"])
    if mutation == "missing":
        path.unlink()
    elif mutation == "extra":
        save(path.with_name("unexpected.json"), receipt)
    elif mutation == "identity":
        receipt["run_identity_sha256"] = "f" * 64
        save(path, receipt)
    elif mutation == "oom":
        receipt["runtime_sentence_batching"]["oom_splits"] = 1
        save(path, receipt)
    elif mutation == "path":
        receipt["output"]["path"] = str(output.parent.parent / "foreign.jsonl")
        save(path, receipt)
    elif mutation == "rollup":
        complete = followup.read_json(root / "COMPLETE.json")
        complete["grouping_fingerprint_rollup"]["outer_item_count"] = 1
        save(root / "COMPLETE.json", complete)
    elif mutation == "input":
        save(Path(config["input_manifest"]), {"shards": []})
    with pytest.raises(ValueError):
        followup.verify_t5(config, lambda *a, **kw: None)


@pytest.mark.parametrize("state", ["failed", "interrupted"])
def test_failure_never_triggers_downstream(run_fixture, state):
    config, _, _ = run_fixture
    save(Path(config["t5_status"]), {"state": state})
    with pytest.raises(RuntimeError, match="followup blocked"):
        followup.t5_state(config)


def test_incident_blocks_even_when_complete_exists(run_fixture):
    config, _, _ = run_fixture
    save(Path(config["t5_root"]) / "incident.json", {})
    with pytest.raises(RuntimeError, match="incident"):
        followup.t5_state(config)


def test_live_wait_uses_heartbeat_not_total_runtime(run_fixture, monkeypatch):
    config, _, _ = run_fixture
    save(Path(config["t5_status"]), {"state": "running"})
    save(Path(config["t5_root"]) / "progress.json", {
        "state": "running", "started_at": "2000-01-01T00:00:00+00:00",
        "updated_at": datetime.now(timezone.utc).isoformat()})
    calls = []
    monkeypatch.setattr(followup, "checked", lambda command: calls.append(command))
    assert not followup.t5_state(config)[0]
    assert calls == [["tmux", "has-session", "-t", "fixture"]]


def test_stale_heartbeat_does_not_wait_forever(run_fixture):
    config, _, _ = run_fixture
    save(Path(config["t5_status"]), {"state": "running"})
    save(Path(config["t5_root"]) / "progress.json", {
        "state": "running", "updated_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})
    with pytest.raises(RuntimeError, match="heartbeat stale"):
        followup.t5_state(config)


def test_downstream_nonzero_stops_chain(tmp_path):
    config = {"queue_root": str(tmp_path), "poll_seconds": 0.01}
    with pytest.raises(RuntimeError, match="exited 7"):
        followup.run_step({"name": "fixture", "argv": [sys.executable, "-c", "raise SystemExit(7)"]},
                          config, lambda *a, **kw: None)


def test_changed_pin_blocks(tmp_path):
    path = tmp_path / "inventory.tsv"
    path.write_text("old")
    config = {"pinned_files": [{"path": str(path), "size_bytes": 3, "sha256": followup.digest_file(path)}]}
    path.write_text("new")
    with pytest.raises(ValueError, match="pinned file changed"):
        followup.verify_pins(config)


@pytest.mark.parametrize("fail_smoke", [False, True])
def test_full_chain_checks_smoke_before_formal(run_fixture, monkeypatch, tmp_path, fail_smoke):
    config, _, _ = run_fixture
    config.update({"kind": followup.KIND, "queue_root": str(tmp_path / "queue"),
                   "lexical_output": str(tmp_path / "lexical"),
                   "steps": [{"name": "smoke"}, {"name": "verify_smoke"}, {"name": "formal"}]})
    path = save(tmp_path / "config.json", config)
    calls = []
    monkeypatch.setattr(followup, "verify_pins", lambda config: None)

    def step(item, config, report, pause=None):
        calls.append(item["name"])
        if item["name"] == "verify_smoke" and fail_smoke:
            raise ValueError("smoke mismatch")
        if item["name"] == "formal":
            save(Path(config["lexical_output"]) / "COMPLETE.json", {"status": "completed", "input_rows": 2})
    monkeypatch.setattr(followup, "run_step", step)
    if fail_smoke:
        with pytest.raises(ValueError, match="smoke mismatch"):
            followup.run(path)
        assert calls == ["smoke", "verify_smoke"]
        assert followup.read_json(tmp_path / "queue/status.json")["state"] == "failed"
        assert not (tmp_path / "queue/COMPLETE.json").exists()
    else:
        followup.run(path)
        assert calls == ["smoke", "verify_smoke", "formal"]
        assert followup.read_json(tmp_path / "queue/COMPLETE.json")["state"] == "completed"


def test_prepare_preserves_venv_python_path(run_fixture, monkeypatch, tmp_path):
    config, _, _ = run_fixture
    fake_root = tmp_path / "repo"
    save(fake_root / "resources/gpic_inventory/current/inventory_bundle.json", {})
    prep = fake_root / "resources/lexicons/preposition_mwes.tsv"
    prep.parent.mkdir(parents=True)
    prep.write_text("surface\n")
    monkeypatch.setattr(followup, "ROOT", fake_root)
    monkeypatch.setattr(followup, "checked", lambda *a, **kw: "a" * 40)
    monkeypatch.setattr(followup, "verify_pins", lambda config: None)
    python = tmp_path / "venv/bin/python"
    args = argparse.Namespace(
        queue_root=str(tmp_path / "newqueue"), t5_root=config["t5_root"], t5_status=config["t5_status"],
        t5_identity=config["t5_identity_sha256"], input_manifest=config["input_manifest"],
        t5_session="fixture", smoke_manifest=config["input_manifest"], smoke_baseline=str(tmp_path / "baseline"),
        smoke_output=str(tmp_path / "smoke"), lexical_output=str(tmp_path / "lexical"),
        python=str(python), gpus="auto", cuda_library_root=None)
    real_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw:
                        Path("/system/python") if self == python else real_resolve(self, *a, **kw))
    result = followup.read_json(followup.prepare(args))
    assert result["steps"][0]["argv"][0] == str(python.absolute())
    assert result["steps"][3]["argv"][0] == str(python.absolute())


@pytest.mark.parametrize("gpus", ["0", "1,3", "0,1,2,3,4,5,6,7", "auto"])
def test_restart_overrides_only_gpu_and_resume_without_mutating_config(gpus):
    argv = ["python", "scaleout.py", "--gpus", "0,1", "--batch-size", "192",
            "--input-shards-per-unit", "10", "--output-root", "/same/output"]
    config = {"steps": [{"name": name, "argv": list(argv)} for name in
                         ("lexical_smoke", "verify_lexical_resume", "lexical_formal")]}
    before = json.dumps(config, sort_keys=True)
    result = followup.execution_steps(config, gpus=gpus, resume=True)
    for step in result:
        assert step["argv"][3] == gpus
        assert step["argv"][:3] + step["argv"][4:-1] == argv[:3] + argv[4:]
        assert step["argv"][-1] == "--resume"
    assert json.dumps(config, sort_keys=True) == before


@pytest.mark.parametrize("gpus", ["", "0,0", "0,", ",1"])
def test_invalid_gpu_override_is_rejected(gpus):
    with pytest.raises(ValueError, match="unique non-empty"):
        followup.execution_steps({"steps": []}, gpus=gpus, resume=True)


@pytest.mark.parametrize("fail", [False, True])
def test_pause_forwards_to_real_child_and_does_not_mask_failure(tmp_path, fail):
    queue_root, child_root = tmp_path / "queue", tmp_path / "child"
    control = PauseControl.start(queue_root, "fixture")
    request_pause(queue_root)
    argv = [sys.executable, str(Path(__file__).parent / "fixtures/lexical_pause_child.py"),
            "--output-root", str(child_root)]
    if fail:
        argv.append("--fail-after-pause")
    events = []
    expected = RuntimeError if fail else followup.PlannedPause
    with pytest.raises(expected):
        followup.run_step({"name": "lexical_formal", "argv": argv},
                          {"queue_root": str(queue_root), "poll_seconds": 0.01},
                          lambda state, **kw: events.append(state), pause=control)
    assert "draining" in events
    assert followup.read_json(child_root / STATE_FILE)["status"] == ("failed" if fail else "paused"), (
        queue_root / "logs/lexical_formal.log").read_text(encoding="utf-8", errors="replace")


def test_planned_pause_exits_without_incident_and_resume_keeps_gates(run_fixture, tmp_path, monkeypatch):
    config, _, _ = run_fixture
    queue_root = tmp_path / "queue"
    config.update({"kind": followup.KIND, "queue_root": str(queue_root),
                   "lexical_output": str(tmp_path / "lexical"),
                   "steps": [{"name": "verify_smoke"}, {"name": "formal"}]})
    path = save(tmp_path / "config.json", config)
    pins = []
    calls = []
    request_on_pin = [True]

    def verify_pins(config):
        pins.append(True)
        if request_on_pin:
            request_on_pin.pop()
            request_pause(queue_root)

    monkeypatch.setattr(followup, "verify_pins", verify_pins)
    monkeypatch.delenv(RUN_TOKEN_ENV, raising=False)
    state_dir = tmp_path / "incident"
    monkeypatch.setenv(STATE_DIR_ENV, str(state_dir))
    assert guarded_entrypoint("fixture", lambda: followup.run(path)) == 0
    assert followup.read_json(queue_root / "status.json")["state"] == "paused"
    assert not (queue_root / "COMPLETE.json").exists()
    assert not (state_dir / "incident.json").exists()
    assert not (state_dir / "running.json").exists()

    def step(item, config, report, pause=None):
        calls.append(item["name"])
        if item["name"] == "formal":
            save(Path(config["lexical_output"]) / "COMPLETE.json", {"status": "completed", "input_rows": 2})

    monkeypatch.setattr(followup, "run_step", step)
    before = path.read_bytes()
    assert guarded_entrypoint("fixture", lambda: followup.run(path, resume=True, gpus="0")) == 0
    assert calls == ["verify_smoke", "formal"]
    assert followup.read_json(queue_root / "status.json")["state"] == "completed"
    assert path.read_bytes() == before
