from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from incident_gate import guarded_entrypoint

from publish_inventory_bundle import DEFAULT_TARGET_DIR as DEFAULT_CURRENT_INVENTORY_DIR
from publish_inventory_bundle import publish_inventory_bundle

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.inventory_bundle import (
    build_inventory_bundle_state,
    load_inventory_bundle,
    merge_bundle_path,
    write_inventory_bundle,
)
from gpic_concepts_v1.inventory_validation import final_manual_resolution_blockers
from gpic_concepts_v1.pipeline_state import (
    PipelineStateError,
    output_dir_state_path,
    read_pipeline_state,
)


SCHEMA_VERSION = 1
DEFAULT_NGRAM_EVIDENCE = (
    ROOT / "resources" / "source_labels" / "google_ngram_canonical_frequency_evidence.tsv"
)
DEFAULT_PREPOSITION_MWE_LEXICON = ROOT / "resources" / "lexicons" / "preposition_mwes.tsv"
DEFAULT_BASE_LEXICON_DIR = ROOT / "resources" / "lexicons"
DEFAULT_CURRENT_INVENTORY_BUNDLE = DEFAULT_CURRENT_INVENTORY_DIR / "inventory_bundle.json"
DEFAULT_SUBCOMMAND_TIMEOUT_SECONDS = int(
    os.environ.get("GPIC_STAGE35_SUBCOMMAND_TIMEOUT_SECONDS", "0")
)


@dataclass(frozen=True)
class WorkflowConfig:
    stage3_records: Path
    output_dir: Path
    object_inventory: Path
    inventory_input_mode: str = "explicit"
    source_inventory_bundle: Path | None = None
    attribute_prior_inventory: Path | None = None
    action_prior_inventory: Path | None = None
    attribute_manual_resolved: Path | None = None
    attribute_manual_decisions: Path | None = None
    action_manual_decisions: Path | None = None
    attribute_inventory: Path | None = None
    attribute_resolved_inventory: Path | None = None
    attribute_canonical_inventory: Path | None = None
    action_inventory: Path | None = None
    action_resolved_inventory: Path | None = None
    action_canonical_inventory: Path | None = None
    preposition_mwe_lexicon: Path = DEFAULT_PREPOSITION_MWE_LEXICON
    ngram_evidence: Path = DEFAULT_NGRAM_EVIDENCE
    base_lexicon_dir: Path = DEFAULT_BASE_LEXICON_DIR
    lexicon_output_dir: Path | None = None
    state_output: Path | None = None
    publish_current: bool = False
    publish_target_dir: Path = DEFAULT_CURRENT_INVENTORY_DIR
    snapshot_label: str = ""
    publish_summary: Path | None = None
    subcommand_timeout_seconds: int = DEFAULT_SUBCOMMAND_TIMEOUT_SECONDS


@dataclass(frozen=True)
class WorkflowPaths:
    inventory_dir: Path
    attribute_inventory: Path
    attribute_inventory_progress: Path
    attribute_inventory_checkpoint: Path
    attribute_needs_manual: Path
    attribute_resolved_inventory: Path
    attribute_resolved_copy: Path
    attribute_manual_decisions_copy: Path
    attribute_canonical_inventory: Path
    attribute_canonical_ambiguous: Path
    action_inventory: Path
    action_inventory_progress: Path
    action_inventory_checkpoint: Path
    action_needs_manual: Path
    action_resolved_inventory: Path
    action_resolved_output: Path
    action_canonical_inventory: Path
    action_canonical_ambiguous: Path
    lexicon_output_dir: Path
    state_output: Path


@dataclass(frozen=True)
class WorkflowDecision:
    action: str
    status: str
    next_required_step: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowRunResult:
    decision: WorkflowDecision
    executed_steps: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Advance Stage 3.5 observed inventory preparation until the next "
            "manual/canonical blocker or final Stage 5 lexicon export."
        )
    )
    parser.add_argument("--stage3-records", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--use-current-inventory",
        action="store_true",
        help=(
            "Production mode: load object/attribute/action/base lexicons only "
            "from resources/gpic_inventory/current/inventory_bundle.json. "
            "Explicit inventory inputs are rejected in this mode."
        ),
    )
    parser.add_argument("--prior-inventory-bundle")
    parser.add_argument("--object-inventory")
    parser.add_argument("--attribute-prior-inventory")
    parser.add_argument("--action-prior-inventory")
    parser.add_argument("--attribute-manual-resolved")
    parser.add_argument("--attribute-manual-decisions")
    parser.add_argument("--action-manual-decisions")
    parser.add_argument("--attribute-inventory")
    parser.add_argument("--attribute-resolved-inventory")
    parser.add_argument("--attribute-canonical-inventory")
    parser.add_argument("--action-inventory")
    parser.add_argument("--action-resolved-inventory")
    parser.add_argument("--action-canonical-inventory")
    parser.add_argument("--preposition-mwe-lexicon", default=str(DEFAULT_PREPOSITION_MWE_LEXICON))
    parser.add_argument("--ngram-evidence", default=str(DEFAULT_NGRAM_EVIDENCE))
    parser.add_argument("--base-lexicon-dir")
    parser.add_argument("--lexicon-output-dir")
    parser.add_argument("--state-output")
    parser.add_argument(
        "--publish-current",
        action="store_true",
        help=(
            "When the workflow reaches complete, publish the generated "
            "inventory bundle into resources/gpic_inventory/current."
        ),
    )
    parser.add_argument("--publish-target-dir", default=str(DEFAULT_CURRENT_INVENTORY_DIR))
    parser.add_argument("--snapshot-label", default="")
    parser.add_argument("--publish-summary")
    parser.add_argument(
        "--subcommand-timeout-seconds",
        type=int,
        default=DEFAULT_SUBCOMMAND_TIMEOUT_SECONDS,
        help=(
            "Timeout for each Stage 3.5 child script. 0 disables the child "
            "wall-clock timeout; large progress-producing runs should rely on "
            "progress heartbeat monitoring and checkpoint/resume instead."
        ),
    )
    parser.add_argument(
        "--allow-explicit-inventory-inputs",
        action="store_true",
        help=(
            "Allow publishing from manually supplied inventory paths. This is "
            "for tests/migrations only; production runs should use --use-current-inventory."
        ),
    )
    return parser.parse_args()


def main() -> None:
    config = config_from_args(parse_args())
    result = run_workflow(config)
    print(
        json.dumps(
            {
                "status": result.decision.status,
                "next_required_step": result.decision.next_required_step,
                "action": result.decision.action,
                "executed_steps": result.executed_steps,
                **result.decision.details,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def config_from_args(args: argparse.Namespace) -> WorkflowConfig:
    subcommand_timeout_seconds = getattr(
        args,
        "subcommand_timeout_seconds",
        DEFAULT_SUBCOMMAND_TIMEOUT_SECONDS,
    )
    if subcommand_timeout_seconds < 0:
        raise ValueError("--subcommand-timeout-seconds must be >= 0")

    explicit_inventory_args = [
        "--object-inventory" if args.object_inventory else "",
        "--attribute-prior-inventory" if args.attribute_prior_inventory else "",
        "--action-prior-inventory" if args.action_prior_inventory else "",
        "--base-lexicon-dir" if args.base_lexicon_dir else "",
        "--prior-inventory-bundle" if args.prior_inventory_bundle else "",
    ]
    explicit_inventory_args = [arg for arg in explicit_inventory_args if arg]
    if args.use_current_inventory:
        if explicit_inventory_args:
            raise ValueError(
                "--use-current-inventory cannot be combined with explicit inventory inputs: "
                + ", ".join(explicit_inventory_args)
            )
        bundle_path = DEFAULT_CURRENT_INVENTORY_BUNDLE
    else:
        if args.publish_current and not args.allow_explicit_inventory_inputs:
            raise ValueError(
                "--publish-current requires --use-current-inventory. "
                "Use --allow-explicit-inventory-inputs only for one-off migrations."
            )
        bundle_path = Path(args.prior_inventory_bundle) if args.prior_inventory_bundle else None

    bundle = load_inventory_bundle(bundle_path) if bundle_path is not None else None
    object_inventory = merge_bundle_path(
        field_name="object_inventory",
        explicit_path=args.object_inventory,
        bundled_path=bundle.object_inventory if bundle else None,
    )
    if object_inventory is None:
        raise ValueError("object_inventory is required unless --prior-inventory-bundle is provided")
    attribute_prior_inventory = merge_bundle_path(
        field_name="attribute_inventory",
        explicit_path=args.attribute_prior_inventory,
        bundled_path=bundle.attribute_inventory if bundle else None,
    )
    action_prior_inventory = merge_bundle_path(
        field_name="action_inventory",
        explicit_path=args.action_prior_inventory,
        bundled_path=bundle.action_inventory if bundle else None,
    )
    base_lexicon_dir = merge_bundle_path(
        field_name="lexicon_dir",
        explicit_path=args.base_lexicon_dir,
        bundled_path=bundle.lexicon_dir if bundle else None,
    )
    return WorkflowConfig(
        stage3_records=Path(args.stage3_records),
        output_dir=Path(args.output_dir),
        object_inventory=object_inventory,
        inventory_input_mode="current" if args.use_current_inventory else "explicit",
        source_inventory_bundle=bundle_path,
        attribute_prior_inventory=attribute_prior_inventory,
        action_prior_inventory=action_prior_inventory,
        attribute_manual_resolved=_optional_path(args.attribute_manual_resolved),
        attribute_manual_decisions=_optional_path(args.attribute_manual_decisions),
        action_manual_decisions=_optional_path(args.action_manual_decisions),
        attribute_inventory=_optional_path(args.attribute_inventory),
        attribute_resolved_inventory=_optional_path(args.attribute_resolved_inventory),
        attribute_canonical_inventory=_optional_path(args.attribute_canonical_inventory),
        action_inventory=_optional_path(args.action_inventory),
        action_resolved_inventory=_optional_path(args.action_resolved_inventory),
        action_canonical_inventory=_optional_path(args.action_canonical_inventory),
        preposition_mwe_lexicon=Path(args.preposition_mwe_lexicon),
        ngram_evidence=Path(args.ngram_evidence),
        base_lexicon_dir=base_lexicon_dir or DEFAULT_BASE_LEXICON_DIR,
        lexicon_output_dir=_optional_path(args.lexicon_output_dir),
        state_output=_optional_path(args.state_output),
        publish_current=bool(args.publish_current),
        publish_target_dir=Path(args.publish_target_dir),
        snapshot_label=args.snapshot_label,
        publish_summary=_optional_path(args.publish_summary),
        subcommand_timeout_seconds=subcommand_timeout_seconds,
    )


def run_workflow(config: WorkflowConfig) -> WorkflowRunResult:
    executed_steps: list[str] = []
    for _ in range(20):
        decision = determine_next_step(config)
        if decision.status.startswith("blocked") or decision.status == "complete":
            _write_blocker_subsets(config, decision)
            _write_workflow_state(config, decision, executed_steps)
            return WorkflowRunResult(decision=decision, executed_steps=executed_steps)
        _write_workflow_state(config, decision, executed_steps)
        _execute_decision(config, decision)
        executed_steps.append(decision.action)

    decision = WorkflowDecision(
        action="stop",
        status="blocked_workflow_loop_limit",
        next_required_step="inspect_stage35_workflow",
        details={"loop_limit": 20},
    )
    _write_workflow_state(config, decision, executed_steps)
    return WorkflowRunResult(decision=decision, executed_steps=executed_steps)


def determine_next_step(config: WorkflowConfig) -> WorkflowDecision:
    paths = workflow_paths(config)
    if not config.object_inventory.exists():
        return _blocked(
            "blocked_missing_object_inventory",
            "provide_resolved_object_inventory",
            object_inventory=str(config.object_inventory),
        )

    object_blockers = _object_inventory_blockers(config.object_inventory)
    if object_blockers:
        return _blocked(
            "blocked_object_inventory",
            "resolve_object_inventory",
            object_inventory=str(config.object_inventory),
            blocker_count=len(object_blockers),
            blocker_examples=object_blockers[:10],
        )

    attribute_current = _current_inventory(
        resolved_path=paths.attribute_resolved_inventory,
        raw_path=paths.attribute_inventory,
    )
    if not attribute_current.exists() and not paths.attribute_canonical_inventory.exists():
        return _run(
            "build_attribute_inventory",
            "build_attribute_inventory",
            output=str(paths.attribute_inventory),
        )

    if not paths.attribute_canonical_inventory.exists():
        attr_rows = _read_tsv_rows(attribute_current)
        attr_needs_manual = _rows_with_status(attr_rows, "needs_manual")
        if attr_needs_manual:
            if config.attribute_manual_resolved is not None:
                return _run(
                    "apply_attribute_manual_resolution",
                    "apply_attribute_manual_resolution",
                    input=str(attribute_current),
                    manual_resolved=str(config.attribute_manual_resolved),
                    output=str(paths.attribute_resolved_inventory),
                )
            return _blocked(
                "blocked_attribute_needs_manual",
                "resolve_attribute_manual",
                attribute_inventory=str(attribute_current),
                needs_manual_output=str(paths.attribute_needs_manual),
                blocker_count=len(attr_needs_manual),
                decision_status_counts=_count_by(attr_rows, "decision_status"),
            )

        return _run(
            "enrich_attribute_canonical",
            "build_attribute_canonical_inventory",
            input=str(attribute_current),
            output=str(paths.attribute_canonical_inventory),
        )

    attribute_canonical_blockers = _attribute_canonical_blockers(
        paths.attribute_canonical_inventory,
    )
    if attribute_canonical_blockers:
        missing_ngram_pairs = _missing_ngram_evidence_pairs(
            attribute_canonical_blockers,
            config.ngram_evidence,
        )
        if missing_ngram_pairs:
            return _run(
                "refresh_attribute_google_ngram_evidence",
                "refresh_google_ngram_evidence",
                attribute_canonical_inventory=str(paths.attribute_canonical_inventory),
                ngram_evidence=str(config.ngram_evidence),
                missing_evidence_pairs=missing_ngram_pairs[:20],
                missing_evidence_pair_count=len(missing_ngram_pairs),
            )
        return _blocked(
            "blocked_attribute_canonical",
            "resolve_attribute_canonical",
            attribute_canonical_inventory=str(paths.attribute_canonical_inventory),
            blocker_count=len(attribute_canonical_blockers),
            blocker_examples=attribute_canonical_blockers[:10],
        )

    action_current = _current_inventory(
        resolved_path=paths.action_resolved_inventory,
        raw_path=paths.action_inventory,
    )
    if not action_current.exists() and not paths.action_canonical_inventory.exists():
        return _run(
            "build_action_inventory",
            "build_action_inventory",
            output=str(paths.action_inventory),
        )

    if not paths.action_canonical_inventory.exists():
        action_rows = _read_tsv_rows(action_current)
        action_needs_manual = _rows_with_status(action_rows, "needs_manual")
        if action_needs_manual:
            if config.action_manual_decisions is not None:
                return _run(
                    "apply_action_manual_resolution",
                    "apply_action_manual_resolution",
                    input=str(action_current),
                    manual_decisions=str(config.action_manual_decisions),
                    output=str(paths.action_resolved_inventory),
                )
            return _blocked(
                "blocked_action_needs_manual",
                "resolve_action_manual",
                action_inventory=str(action_current),
                needs_manual_output=str(paths.action_needs_manual),
                blocker_count=len(action_needs_manual),
                decision_status_counts=_count_by(action_rows, "decision_status"),
            )

        return _run(
            "enrich_action_canonical",
            "build_action_canonical_inventory",
            input=str(action_current),
            output=str(paths.action_canonical_inventory),
        )

    action_canonical_blockers = _action_canonical_blockers(paths.action_canonical_inventory)
    if action_canonical_blockers:
        missing_ngram_pairs = _missing_ngram_evidence_pairs(
            action_canonical_blockers,
            config.ngram_evidence,
        )
        if missing_ngram_pairs:
            return _run(
                "refresh_action_google_ngram_evidence",
                "refresh_google_ngram_evidence",
                action_canonical_inventory=str(paths.action_canonical_inventory),
                ngram_evidence=str(config.ngram_evidence),
                missing_evidence_pairs=missing_ngram_pairs[:20],
                missing_evidence_pair_count=len(missing_ngram_pairs),
            )
        return _blocked(
            "blocked_action_canonical",
            "resolve_action_canonical",
            action_canonical_inventory=str(paths.action_canonical_inventory),
            blocker_count=len(action_canonical_blockers),
            blocker_examples=action_canonical_blockers[:10],
        )

    if not _stage5_lexicon_bundle_ready(paths.lexicon_output_dir):
        return _run(
            "export_stage5_lexicon_bundle",
            "export_stage5_lexicon_bundle",
            output_dir=str(paths.lexicon_output_dir),
        )

    return WorkflowDecision(
        action="complete",
        status="complete",
        next_required_step="formal_stage4_5_6",
        details={
            "attribute_canonical_inventory": str(paths.attribute_canonical_inventory),
            "action_canonical_inventory": str(paths.action_canonical_inventory),
            "lexicon_output_dir": str(paths.lexicon_output_dir),
        },
    )


def workflow_paths(config: WorkflowConfig) -> WorkflowPaths:
    inventory_dir = config.output_dir / "inventory"
    default_attribute_inventory = inventory_dir / "gpic_observed_attribute_inventory_stage35_workflow.tsv"
    default_action_inventory = inventory_dir / "gpic_observed_action_inventory_stage35_workflow.tsv"
    attribute_inventory = config.attribute_inventory or default_attribute_inventory
    action_inventory = config.action_inventory or default_action_inventory
    attribute_resolved = config.attribute_resolved_inventory or (
        inventory_dir / "gpic_observed_attribute_inventory_stage35_workflow_manual_resolved.tsv"
    )
    action_resolved = config.action_resolved_inventory or (
        inventory_dir / "gpic_observed_action_inventory_stage35_workflow_manual_resolved.tsv"
    )
    attribute_canonical = config.attribute_canonical_inventory or (
        inventory_dir / "gpic_observed_attribute_inventory_stage35_workflow_manual_resolved_canonical.tsv"
    )
    action_canonical = config.action_canonical_inventory or (
        inventory_dir / "gpic_observed_action_inventory_stage35_workflow_manual_resolved_canonical.tsv"
    )
    return WorkflowPaths(
        inventory_dir=inventory_dir,
        attribute_inventory=attribute_inventory,
        attribute_inventory_progress=attribute_inventory.with_name(
            attribute_inventory.stem + "_progress.json"
        ),
        attribute_inventory_checkpoint=attribute_inventory.with_name(
            attribute_inventory.stem + "_checkpoint.json"
        ),
        attribute_needs_manual=inventory_dir
        / "gpic_observed_attribute_inventory_stage35_workflow_needs_manual.tsv",
        attribute_resolved_inventory=attribute_resolved,
        attribute_resolved_copy=attribute_resolved.with_name(
            attribute_resolved.stem + "_subset.tsv"
        ),
        attribute_manual_decisions_copy=attribute_resolved.with_name(
            attribute_resolved.stem + "_manual_decisions.tsv"
        ),
        attribute_canonical_inventory=attribute_canonical,
        attribute_canonical_ambiguous=attribute_canonical.with_name(
            attribute_canonical.stem + "_ambiguous.tsv"
        ),
        action_inventory=action_inventory,
        action_inventory_progress=action_inventory.with_name(
            action_inventory.stem + "_progress.json"
        ),
        action_inventory_checkpoint=action_inventory.with_name(
            action_inventory.stem + "_checkpoint.json"
        ),
        action_needs_manual=inventory_dir / "gpic_observed_action_inventory_stage35_workflow_needs_manual.tsv",
        action_resolved_inventory=action_resolved,
        action_resolved_output=action_resolved.with_name(action_resolved.stem + "_subset.tsv"),
        action_canonical_inventory=action_canonical,
        action_canonical_ambiguous=action_canonical.with_name(
            action_canonical.stem + "_ambiguous.tsv"
        ),
        lexicon_output_dir=config.lexicon_output_dir
        or (config.output_dir / "lexicons_after_stage35_workflow"),
        state_output=config.state_output or (config.output_dir / "stage35_workflow_state.json"),
    )


def _execute_decision(config: WorkflowConfig, decision: WorkflowDecision) -> None:
    paths = workflow_paths(config)
    def run_script(
        command: list[Path | str],
        *,
        nonzero_ok_output: Path | None = None,
    ) -> None:
        _run_script(
            command,
            nonzero_ok_output=nonzero_ok_output,
            timeout_seconds=config.subcommand_timeout_seconds,
        )

    if decision.action == "build_attribute_inventory":
        command = [
            script_path("build_gpic_observed_attribute_inventory.py"),
            "--input",
            config.stage3_records,
            "--object-inventory",
            config.object_inventory,
            "--preposition-mwe-lexicon",
            config.preposition_mwe_lexicon,
            "--output",
            paths.attribute_inventory,
            "--summary",
            paths.attribute_inventory.with_name(paths.attribute_inventory.stem + "_summary.json"),
            "--progress-output",
            paths.attribute_inventory_progress,
            "--checkpoint-output",
            paths.attribute_inventory_checkpoint,
            "--resume-checkpoint",
        ]
        if config.attribute_prior_inventory is not None:
            command.extend(["--attribute-inventory", config.attribute_prior_inventory])
        run_script(command)
        _write_needs_manual_subset(paths.attribute_inventory, paths.attribute_needs_manual)
        return

    if decision.action == "apply_attribute_manual_resolution":
        if config.attribute_manual_resolved is None:
            raise ValueError("attribute_manual_resolved is required")
        command = [
            script_path("apply_attribute_manual_resolution.py"),
            "--full-inventory",
            _current_inventory(
                resolved_path=paths.attribute_resolved_inventory,
                raw_path=paths.attribute_inventory,
            ),
            "--resolved-subset",
            config.attribute_manual_resolved,
            "--output",
            paths.attribute_resolved_inventory,
            "--resolved-copy",
            paths.attribute_resolved_copy,
            "--summary",
            paths.attribute_resolved_inventory.with_name(
                paths.attribute_resolved_inventory.stem + "_summary.json"
            ),
        ]
        if config.attribute_manual_decisions is not None:
            command.extend(
                [
                    "--manual-decisions",
                    config.attribute_manual_decisions,
                    "--manual-decisions-copy",
                    paths.attribute_manual_decisions_copy,
                ]
            )
        run_script(command)
        return

    if decision.action == "enrich_attribute_canonical":
        command = [
            script_path("enrich_gpic_attribute_inventory_canonical.py"),
            "--input",
            _current_inventory(
                resolved_path=paths.attribute_resolved_inventory,
                raw_path=paths.attribute_inventory,
            ),
            "--output",
            paths.attribute_canonical_inventory,
            "--ngram-evidence",
            config.ngram_evidence,
            "--ambiguous-output",
            paths.attribute_canonical_ambiguous,
            "--summary",
            paths.attribute_canonical_inventory.with_name(
                paths.attribute_canonical_inventory.stem + "_summary.json"
            ),
        ]
        run_script(command, nonzero_ok_output=paths.attribute_canonical_inventory)
        return

    if decision.action == "refresh_attribute_google_ngram_evidence":
        _refresh_google_ngram_evidence(
            config=config,
            canonical_inventory=paths.attribute_canonical_inventory,
            summary_path=paths.attribute_canonical_inventory.with_name(
                paths.attribute_canonical_inventory.stem + "_ngram_refresh_summary.json"
            ),
        )
        command = [
            script_path("enrich_gpic_attribute_inventory_canonical.py"),
            "--input",
            _current_inventory(
                resolved_path=paths.attribute_resolved_inventory,
                raw_path=paths.attribute_inventory,
            ),
            "--output",
            paths.attribute_canonical_inventory,
            "--ngram-evidence",
            config.ngram_evidence,
            "--ambiguous-output",
            paths.attribute_canonical_ambiguous,
            "--summary",
            paths.attribute_canonical_inventory.with_name(
                paths.attribute_canonical_inventory.stem + "_summary.json"
            ),
        ]
        run_script(command, nonzero_ok_output=paths.attribute_canonical_inventory)
        return

    if decision.action == "build_action_inventory":
        command = [
            script_path("build_gpic_observed_action_inventory.py"),
            "--input",
            config.stage3_records,
            "--output",
            paths.action_inventory,
            "--needs-manual-output",
            paths.action_needs_manual,
            "--summary",
            paths.action_inventory.with_name(paths.action_inventory.stem + "_summary.json"),
            "--progress-output",
            paths.action_inventory_progress,
            "--checkpoint-output",
            paths.action_inventory_checkpoint,
            "--resume-checkpoint",
            "--preposition-mwe-lexicon",
            config.preposition_mwe_lexicon,
        ]
        if config.action_prior_inventory is not None:
            command.extend(["--action-inventory", config.action_prior_inventory])
        run_script(command)
        return

    if decision.action == "apply_action_manual_resolution":
        if config.action_manual_decisions is None:
            raise ValueError("action_manual_decisions is required")
        command = [
            script_path("apply_action_manual_resolution.py"),
            "--full-inventory",
            _current_inventory(
                resolved_path=paths.action_resolved_inventory,
                raw_path=paths.action_inventory,
            ),
            "--manual-decisions",
            config.action_manual_decisions,
            "--output",
            paths.action_resolved_inventory,
            "--resolved-output",
            paths.action_resolved_output,
            "--summary",
            paths.action_resolved_inventory.with_name(
                paths.action_resolved_inventory.stem + "_summary.json"
            ),
        ]
        run_script(command)
        return

    if decision.action == "enrich_action_canonical":
        command = [
            script_path("enrich_gpic_action_inventory_canonical.py"),
            "--input",
            _current_inventory(
                resolved_path=paths.action_resolved_inventory,
                raw_path=paths.action_inventory,
            ),
            "--output",
            paths.action_canonical_inventory,
            "--ngram-evidence",
            config.ngram_evidence,
            "--ambiguous-output",
            paths.action_canonical_ambiguous,
            "--summary",
            paths.action_canonical_inventory.with_name(
                paths.action_canonical_inventory.stem + "_summary.json"
            ),
        ]
        run_script(command, nonzero_ok_output=paths.action_canonical_inventory)
        return

    if decision.action == "refresh_action_google_ngram_evidence":
        _refresh_google_ngram_evidence(
            config=config,
            canonical_inventory=paths.action_canonical_inventory,
            summary_path=paths.action_canonical_inventory.with_name(
                paths.action_canonical_inventory.stem + "_ngram_refresh_summary.json"
            ),
        )
        command = [
            script_path("enrich_gpic_action_inventory_canonical.py"),
            "--input",
            _current_inventory(
                resolved_path=paths.action_resolved_inventory,
                raw_path=paths.action_inventory,
            ),
            "--output",
            paths.action_canonical_inventory,
            "--ngram-evidence",
            config.ngram_evidence,
            "--ambiguous-output",
            paths.action_canonical_ambiguous,
            "--summary",
            paths.action_canonical_inventory.with_name(
                paths.action_canonical_inventory.stem + "_summary.json"
            ),
        ]
        run_script(command, nonzero_ok_output=paths.action_canonical_inventory)
        return

    if decision.action == "export_stage5_lexicon_bundle":
        command = [
            script_path("export_attribute_stage5_lexicons.py"),
            "--attribute-inventory",
            paths.attribute_canonical_inventory,
            "--action-canonical-inventory",
            paths.action_canonical_inventory,
            "--output-dir",
            paths.lexicon_output_dir,
            "--base-lexicon-dir",
            config.base_lexicon_dir,
            "--summary",
            paths.inventory_dir / "stage35_workflow_lexicon_export_summary.json",
        ]
        run_script(command)
        return

    raise ValueError(f"unsupported workflow action: {decision.action}")


def _refresh_google_ngram_evidence(
    *,
    config: WorkflowConfig,
    canonical_inventory: Path,
    summary_path: Path,
) -> None:
    command = [
        script_path("refresh_google_ngram_evidence_for_canonical_inventory.py"),
        "--canonical-inventory",
        canonical_inventory,
        "--ngram-evidence",
        config.ngram_evidence,
        "--summary",
        summary_path,
    ]
    _run_script(command, timeout_seconds=config.subcommand_timeout_seconds)


def script_path(name: str) -> Path:
    return ROOT / "scripts" / name


def _run_script(
    args: list[Path | str],
    *,
    nonzero_ok_output: Path | None = None,
    timeout_seconds: int = DEFAULT_SUBCOMMAND_TIMEOUT_SECONDS,
) -> None:
    command = [sys.executable, *(str(arg) for arg in args)]
    timeout = timeout_seconds if timeout_seconds > 0 else None
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            json.dumps(
                {
                    "status": "stage35_workflow_subcommand_timeout",
                    "command": command,
                    "timeout_seconds": timeout_seconds,
                    "stdout_tail": (exc.stdout or "")[-4000:],
                    "stderr_tail": (exc.stderr or "")[-4000:],
                },
                ensure_ascii=False,
                indent=2,
            )
        ) from exc
    if result.returncode == 0:
        return
    if nonzero_ok_output is not None and nonzero_ok_output.exists():
        return
    raise RuntimeError(
        json.dumps(
            {
                "status": "stage35_workflow_subcommand_failed",
                "command": command,
                "returncode": result.returncode,
                "stdout_tail": result.stdout[-4000:],
                "stderr_tail": result.stderr[-4000:],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _write_workflow_state(
    config: WorkflowConfig,
    decision: WorkflowDecision,
    executed_steps: list[str],
) -> None:
    paths = workflow_paths(config)
    bundle_path = paths.state_output.with_name("inventory_bundle.json")
    state = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage35_inventory_workflow",
        "stage": "3.5",
        "status": decision.status,
        "next_required_step": decision.next_required_step,
        "last_action": decision.action,
        "executed_steps": executed_steps,
        "stage3_records": str(config.stage3_records),
        "inventory_input_mode": config.inventory_input_mode,
        "source_inventory_bundle": str(config.source_inventory_bundle or ""),
        "object_inventory": str(config.object_inventory),
        "attribute_inventory": str(paths.attribute_inventory),
        "attribute_inventory_progress": str(paths.attribute_inventory_progress),
        "attribute_inventory_checkpoint": str(paths.attribute_inventory_checkpoint),
        "attribute_resolved_inventory": str(paths.attribute_resolved_inventory),
        "attribute_canonical_inventory": str(paths.attribute_canonical_inventory),
        "action_inventory": str(paths.action_inventory),
        "action_inventory_progress": str(paths.action_inventory_progress),
        "action_inventory_checkpoint": str(paths.action_inventory_checkpoint),
        "action_resolved_inventory": str(paths.action_resolved_inventory),
        "action_canonical_inventory": str(paths.action_canonical_inventory),
        "lexicon_output_dir": str(paths.lexicon_output_dir),
        "inventory_bundle": str(bundle_path),
        "publish_current": config.publish_current,
        "publish_target_dir": str(config.publish_target_dir),
        "snapshot_label": config.snapshot_label,
        "subcommand_timeout_seconds": config.subcommand_timeout_seconds,
        "details": decision.details,
    }
    paths.state_output.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(paths.state_output) as handle:
        handle.write(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
    if decision.status == "complete":
        write_inventory_bundle(
            bundle_path,
            build_inventory_bundle_state(
                object_inventory=config.object_inventory,
                attribute_inventory=paths.attribute_canonical_inventory,
                action_inventory=_current_inventory(
                    resolved_path=paths.action_resolved_inventory,
                    raw_path=paths.action_inventory,
                ),
                action_canonical_inventory=paths.action_canonical_inventory,
                lexicon_dir=paths.lexicon_output_dir,
                source_workflow_state=paths.state_output,
            ),
        )
        if config.publish_current:
            summary_path = config.publish_summary or (config.publish_target_dir / "publish_summary.json")
            publish_summary = publish_inventory_bundle(
                source_bundle=bundle_path,
                target_dir=config.publish_target_dir,
                snapshot_label=config.snapshot_label,
                source_stage3_records=str(config.stage3_records),
                summary_path=summary_path,
            )
            state["published_current_bundle"] = publish_summary["target_bundle"]
            state["publish_summary"] = publish_summary
            state["publish_summary_path"] = str(summary_path)
            with atomic_text_writer(paths.state_output) as handle:
                handle.write(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
                handle.write("\n")


def _write_blocker_subsets(config: WorkflowConfig, decision: WorkflowDecision) -> None:
    paths = workflow_paths(config)
    if decision.status == "blocked_attribute_needs_manual":
        _write_needs_manual_subset(
            _current_inventory(paths.attribute_resolved_inventory, paths.attribute_inventory),
            paths.attribute_needs_manual,
        )
    elif decision.status == "blocked_action_needs_manual":
        _write_needs_manual_subset(
            _current_inventory(paths.action_resolved_inventory, paths.action_inventory),
            paths.action_needs_manual,
        )


def _write_needs_manual_subset(source_path: Path, output_path: Path) -> None:
    rows, fieldnames = _read_tsv(source_path)
    needs_manual = [row for row in rows if row.get("decision_status", "") == "needs_manual"]
    if not needs_manual:
        return
    _write_tsv(output_path, needs_manual, fieldnames)


def _object_inventory_blockers(path: Path) -> list[dict[str, str]]:
    rows = _read_tsv_rows(path)
    return final_manual_resolution_blockers(
        rows,
        require_canonical_surface_for_selected_synset=True,
    )


def _attribute_canonical_blockers(path: Path) -> list[dict[str, str]]:
    rows = _read_tsv_rows(path)
    blockers: list[dict[str, str]] = []
    for row in rows:
        status = row.get("decision_status", "").strip()
        synset_id = row.get("selected_oewn_synset", "").strip()
        canonical = row.get("canonical_surface", "").strip()
        reason = ""
        if status == "needs_manual":
            reason = "pending_attribute_manual_decision"
        elif status == "chosen" and synset_id and not canonical:
            reason = "chosen_attribute_selected_synset_missing_canonical"
        if reason:
            blockers.append(_blocker_row(row, reason))
    return blockers


def _action_canonical_blockers(path: Path) -> list[dict[str, str]]:
    rows = _read_tsv_rows(path)
    blockers: list[dict[str, str]] = []
    for row in rows:
        status = row.get("decision_status", "").strip()
        synset_id = row.get("selected_oewn_synset", "").strip()
        canonical = row.get("canonical_surface", "").strip()
        reason = ""
        if status == "needs_manual":
            reason = "pending_action_manual_decision"
        elif status == "chosen" and (not synset_id or not canonical):
            reason = "chosen_action_missing_selected_synset_or_canonical"
        elif status == "raw_fallback" and synset_id:
            reason = "raw_fallback_action_must_not_have_selected_synset"
        if reason:
            blockers.append(_blocker_row(row, reason))
    return blockers


def _stage5_lexicon_bundle_ready(path: Path) -> bool:
    try:
        state = read_pipeline_state(output_dir_state_path(path))
    except PipelineStateError:
        return False
    return (
        state.get("artifact_type") == "stage5_lexicon_bundle"
        and state.get("preview_mode") is not True
        and state.get("action_canonical_exported") is True
    )


def _missing_ngram_evidence_pairs(
    blockers: list[dict[str, str]],
    evidence_path: Path,
) -> list[dict[str, str]]:
    missing_blockers = [
        blocker
        for blocker in blockers
        if "google_ngram_evidence_missing" in blocker.get("canonical_selection_tag", "")
    ]
    if not missing_blockers:
        return []
    existing_pairs = _existing_ngram_evidence_pairs(evidence_path)
    missing: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for blocker in missing_blockers:
        synset_id = blocker.get("selected_oewn_synset", "")
        if not synset_id:
            continue
        for surface in _split_pipe(blocker.get("google_ngram_candidate_surfaces", "")):
            surface_key = _surface_key(surface)
            pair = (synset_id, surface_key)
            if pair in existing_pairs or pair in seen:
                continue
            seen.add(pair)
            missing.append(
                {
                    "selected_oewn_synset": synset_id,
                    "surface": surface,
                    "surface_key": surface_key,
                    "span_key": blocker.get("span_key", ""),
                }
            )
    return missing


def _existing_ngram_evidence_pairs(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    return {
        (row.get("selected_oewn_synset", ""), row.get("surface_key", ""))
        for row in _read_tsv_rows(path)
        if row.get("selected_oewn_synset", "") and row.get("surface_key", "")
    }


def _current_inventory(resolved_path: Path, raw_path: Path) -> Path:
    return resolved_path if resolved_path.exists() else raw_path


def _blocked(status: str, next_required_step: str, **details: Any) -> WorkflowDecision:
    return WorkflowDecision(
        action="stop",
        status=status,
        next_required_step=next_required_step,
        details=details,
    )


def _run(action: str, next_required_step: str, **details: Any) -> WorkflowDecision:
    return WorkflowDecision(
        action=action,
        status="ready_to_run",
        next_required_step=next_required_step,
        details=details,
    )


def _read_tsv_rows(path: Path) -> list[dict[str, str]]:
    return _read_tsv(path)[0]


def _read_tsv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader), list(reader.fieldnames or [])


def _write_tsv(path: Path, rows: list[Mapping[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _rows_with_status(rows: list[Mapping[str, str]], status: str) -> list[Mapping[str, str]]:
    return [row for row in rows if row.get("decision_status", "").strip() == status]


def _count_by(rows: list[Mapping[str, str]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(row.get(field, "") for row in rows).items()))


def _surface_key(text: str) -> str:
    normalized = text.strip().lower()
    normalized = normalized.translate(
        str.maketrans(
            {
                "’": "'",
                "‘": "'",
                "‛": "'",
                "＇": "'",
                "‐": "-",
                "‑": "-",
                "‒": "-",
                "–": "-",
                "—": "-",
                "―": "-",
                "−": "-",
            }
        )
    )
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(char)
    )
    normalized = normalized.replace("_", " ")
    normalized = re.sub(r"(?<=\w)\s*-\s*(?=\w)", "-", normalized)
    normalized = re.sub(r"(?<=\w)\s*'\s*(?=\w)", "'", normalized)
    return " ".join(normalized.split())


def _split_pipe(value: str) -> list[str]:
    return [item.strip() for item in value.split("|") if item.strip()]


def _blocker_row(row: Mapping[str, str], reason: str) -> dict[str, str]:
    return {
        "blocker_reason": reason,
        "span_key": row.get("span_key", ""),
        "observed_surface": row.get("observed_surface", ""),
        "decision_status": row.get("decision_status", ""),
        "decision_reason": row.get("decision_reason", ""),
        "selected_query": row.get("selected_query", ""),
        "selected_oewn_synset": row.get("selected_oewn_synset", ""),
        "canonical_surface": row.get("canonical_surface", ""),
        "canonical_selection_tag": row.get("canonical_selection_tag", ""),
        "google_ngram_candidate_surfaces": row.get("google_ngram_candidate_surfaces", ""),
        "google_ngram_candidate_mean_frequencies": row.get(
            "google_ngram_candidate_mean_frequencies",
            "",
        ),
    }


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("stage35_inventory_workflow", main))
