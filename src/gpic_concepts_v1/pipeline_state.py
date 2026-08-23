"""Pipeline artifact state helpers for formal-run gates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from gpic_concepts_v1.atomic_io import atomic_text_writer


PIPELINE_STATE_SCHEMA_VERSION = 1
PIPELINE_STATE_FILE_NAME = "pipeline_state.json"


class PipelineStateError(RuntimeError):
    """Raised when a formal pipeline artifact has missing or invalid state."""


def artifact_state_path(artifact_path: str | Path) -> Path:
    path = Path(artifact_path)
    return path.with_name(f"{path.name}.pipeline_state.json")


def output_dir_state_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / PIPELINE_STATE_FILE_NAME


def write_pipeline_state(path: str | Path, state: Mapping[str, Any]) -> None:
    payload = {"schema_version": PIPELINE_STATE_SCHEMA_VERSION, **dict(state)}
    with atomic_text_writer(Path(path), newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")


def read_pipeline_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineStateError(f"missing_pipeline_state: {state_path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineStateError(f"invalid_pipeline_state_json: {state_path}") from exc
    if not isinstance(data, dict):
        raise PipelineStateError(f"pipeline_state_must_be_object: {state_path}")
    return data


def require_action_inventory_sidecar_state(action_inventory_path: str | Path) -> dict[str, Any]:
    state_path = artifact_state_path(action_inventory_path)
    state = read_pipeline_state(state_path)
    problems: list[str] = []
    if state.get("schema_version") != PIPELINE_STATE_SCHEMA_VERSION:
        problems.append("unsupported_schema_version")
    if state.get("artifact_type") != "gpic_observed_action_inventory":
        problems.append("wrong_artifact_type")
    if state.get("preview_mode") is True:
        problems.append("preview_action_inventory_not_formal")
    if state.get("action_inventory_preposition_mwe_aware") is not True:
        problems.append("missing_action_inventory_preposition_mwe_aware_flag")
    if state.get("preposition_mwe_detection_before_action") is not True:
        problems.append("missing_preposition_mwe_detection_before_action_flag")
    if problems:
        raise PipelineStateError(
            "invalid_action_inventory_pipeline_state: "
            + ",".join(problems)
            + f" path={state_path}"
        )
    return state


def require_stage5_lexicon_bundle_state(lexicon_dir: str | Path) -> dict[str, Any]:
    state_path = output_dir_state_path(lexicon_dir)
    state = read_pipeline_state(state_path)
    problems: list[str] = []
    if state.get("schema_version") != PIPELINE_STATE_SCHEMA_VERSION:
        problems.append("unsupported_schema_version")
    if state.get("artifact_type") != "stage5_lexicon_bundle":
        problems.append("wrong_artifact_type")
    if state.get("preview_mode") is True:
        problems.append("preview_lexicon_bundle_not_formal")
    if state.get("action_canonical_exported") is not True:
        problems.append("missing_action_canonical_export")
    if problems:
        raise PipelineStateError(
            "invalid_stage5_lexicon_bundle_pipeline_state: "
            + ",".join(problems)
            + f" path={state_path}"
        )
    return state


def build_action_inventory_state(
    *,
    input_path: str,
    output_path: str,
    needs_manual_output: str,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    decision_counts = summary.get("decision_status_counts")
    if not isinstance(decision_counts, Mapping):
        decision_counts = {}
    needs_manual_rows = int(decision_counts.get("needs_manual", 0) or 0)
    return {
        "artifact_type": "gpic_observed_action_inventory",
        "stage": "3.5",
        "status": "needs_manual" if needs_manual_rows else "resolved",
        "preview_mode": False,
        "input": input_path,
        "output": output_path,
        "needs_manual_output": needs_manual_output,
        "action_inventory_preposition_mwe_aware": True,
        "preposition_mwe_detection_before_action": True,
        "relation_mwe_match_total": summary.get("relation_mwe_match_total", 0),
        "relation_mwe_consumed_token_total": summary.get(
            "relation_mwe_consumed_token_total",
            0,
        ),
        "decision_status_counts": dict(decision_counts),
        "needs_manual_rows": needs_manual_rows,
    }


def build_stage5_lexicon_bundle_state(
    *,
    attribute_inventory_path: str,
    action_canonical_inventory_path: str | None,
    output_dir: str,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "artifact_type": "stage5_lexicon_bundle",
        "stage": "5",
        "status": "ready",
        "preview_mode": False,
        "attribute_inventory": attribute_inventory_path,
        "action_canonical_inventory": action_canonical_inventory_path,
        "output_dir": output_dir,
        "action_canonical_exported": action_canonical_inventory_path is not None,
        "attribute_synonym_rows": summary.get("attribute_synonym_rows", 0),
        "action_synonym_rows": summary.get("action_synonym_rows", 0),
        "action_synonym_rows_added": summary.get("action_synonym_rows_added", 0),
        "action_raw_fallback_rows_skipped": summary.get(
            "action_raw_fallback_rows_skipped",
            0,
        ),
    }


def build_mixed_formal_pipeline_state(
    *,
    output_dir: str,
    object_inventory: str,
    attribute_inventory: str,
    action_inventory: str | None,
    lexicon_dir: str,
    runtime_action_lookup_preview: bool,
    stage_summaries: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "artifact_type": "mixed_formal_pipeline_output",
        "stage": "1-6",
        "status": "completed",
        "preview_mode": bool(runtime_action_lookup_preview),
        "output_dir": output_dir,
        "object_inventory": object_inventory,
        "attribute_inventory": attribute_inventory,
        "action_inventory": action_inventory,
        "lexicon_dir": lexicon_dir,
        "runtime_action_lookup_preview": bool(runtime_action_lookup_preview),
        "stage1_done": "stage1" in stage_summaries,
        "stage3_done": "stage3_combined" in stage_summaries,
        "stage4_done": "stage4" in stage_summaries,
        "stage5_done": "stage5" in stage_summaries,
        "stage6_done": "stage6" in stage_summaries,
    }
