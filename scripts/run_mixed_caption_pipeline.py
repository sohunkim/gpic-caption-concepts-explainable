from __future__ import annotations

import argparse
import json
import shutil
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, TypeVar

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from incident_gate import guarded_entrypoint

from build_caption_concept_md import build_report, caption_id, group_by_caption
from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.inventory_bundle import load_inventory_bundle, merge_bundle_path
from gpic_concepts_v1.io_jsonl import iter_jsonl, open_text, write_jsonl
from gpic_concepts_v1.pipeline_state import (
    PipelineStateError,
    build_mixed_formal_pipeline_state,
    output_dir_state_path,
    require_stage5_lexicon_bundle_state,
    write_pipeline_state,
)
from gpic_concepts_v1.runtime_resources import (
    choose_mixed_pipeline_resource_plan,
    detect_hardware_resources,
)
from gpic_concepts_v1.stage1 import caption_id_from_gpic_row
from gpic_concepts_v1.stage1_loader import run_stage1_records
from gpic_concepts_v1.stage3_annotate import (
    DEFAULT_STAGE3_BATCH_SIZE,
    DEFAULT_STAGE3_DISABLED_COMPONENTS,
    DEFAULT_STAGE3_MODEL,
    iter_stage3_records_from_rows,
    iter_stage3_tag_list_records_from_rows,
    make_stage3_nlp,
    normalize_stage3_disabled_components,
)
from gpic_concepts_v1.stage4_extract_raw import (
    _load_object_lookup_runtime,
    load_gpic_action_inventory,
    load_gpic_attribute_mwe_inventory,
    load_gpic_object_inventory,
    load_preposition_mwe_lexicon,
    run_stage4_extract_raw,
)
from gpic_concepts_v1.stage5_canonicalize import run_stage5_canonicalize
from gpic_concepts_v1.stage6_export_counts import run_stage6_export_counts
from stage3_jsonl_utils import extract_stage3_caption_id_from_line

_T = TypeVar("_T")
from run_stage4_extract_raw import (
    _raise_if_action_inventory_not_ready,
    _raise_if_object_inventory_not_ready,
)
from run_stage5_canonicalize import _raise_if_attribute_inventory_not_ready
from run_stage456_sharded import run_stage456_sharded


DEFAULT_MAX_MONOLITHIC_STAGE456_CAPTIONS = 250_000
RESOURCE_OPTION_NAMES = {
    "--stage3-sentence-shards": "stage3_sentence_shards",
    "--stage3-tag-shards": "stage3_tag_shards",
    "--stage3-jobs": "stage3_jobs",
    "--stage3-gpu-devices": "stage3_gpu_devices",
    "--stage456-shards": "stage456_shards",
    "--stage456-jobs": "stage456_jobs",
    "--stage456-merge-jobs": "stage456_merge_jobs",
}


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description=(
            "Run the formal mixed sentence/tag-list v1 pipeline and export one "
            "shared Stage 6 count set."
        ),
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="Input GPIC .jsonl or .jsonl.gz caption rows. Can be repeated.",
    )
    parser.add_argument("--output-dir", required=True, help="Output directory.")
    parser.add_argument(
        "--inventory-bundle",
        help=(
            "Completed Stage 3.5 inventory bundle JSON. Supplies object, "
            "attribute, action, and Stage 5 lexicon paths together."
        ),
    )
    parser.add_argument(
        "--object-inventory",
        help="Resolved GPIC observed object inventory TSV.",
    )
    parser.add_argument(
        "--attribute-inventory",
        help="Resolved GPIC observed attribute inventory TSV.",
    )
    parser.add_argument(
        "--action-inventory",
        help=(
            "Resolved GPIC observed action inventory TSV. Required for "
            "formal Stage 4 extraction unless preview action lookup is "
            "explicitly enabled."
        ),
    )
    parser.add_argument(
        "--allow-runtime-action-lookup-preview",
        action="store_true",
        help=(
            "Preview/debug mode only: allow runtime OEWN verb lookup instead "
            "of a resolved action inventory."
        ),
    )
    parser.add_argument(
        "--preposition-mwe-lexicon",
        help=(
            "Optional active preposition MWE TSV. If omitted, Stage 4 uses "
            "resources/lexicons/preposition_mwes.tsv when it exists."
        ),
    )
    parser.add_argument(
        "--lexicon-dir",
        help="Directory containing Stage 5 TSV lexicons.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_STAGE3_MODEL,
        help=f"spaCy model name. Default: {DEFAULT_STAGE3_MODEL}",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_STAGE3_BATCH_SIZE,
        help=f"spaCy nlp.pipe batch size. Default: {DEFAULT_STAGE3_BATCH_SIZE}",
    )
    parser.add_argument(
        "--stage3-disable-components",
        default=",".join(DEFAULT_STAGE3_DISABLED_COMPONENTS),
        help=(
            "Comma-separated spaCy pipeline components to disable in Stage 3. "
            f"Default: {','.join(DEFAULT_STAGE3_DISABLED_COMPONENTS)}."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of Stage 1 input rows across all inputs.",
    )
    parser.add_argument(
        "--md-report",
        help=(
            "Optional Markdown report path. Defaults to no report; Stage 6 "
            "count files are always produced."
        ),
    )
    parser.add_argument(
        "--md-limit",
        type=int,
        default=100,
        help="Maximum captions to include when --md-report is passed.",
    )
    parser.add_argument(
        "--md-start",
        type=int,
        default=0,
        help="Start offset for --md-report.",
    )
    parser.add_argument(
        "--max-object-pairs-per-caption",
        type=int,
        default=40,
        help="Maximum object co-occurrence pair rows shown per caption in --md-report.",
    )
    parser.add_argument(
        "--progress-output",
        help="Optional JSON path updated while formal mixed pipeline stages run.",
    )
    parser.add_argument(
        "--progress-interval-records",
        type=int,
        default=5000,
        help="Record interval for progress JSON updates during Stage 3 writes.",
    )
    parser.add_argument(
        "--stage3-sentence-shards",
        type=int,
        default=1,
        help=(
            "Run sentence Stage 3 annotation in this many caption-row shards. "
            "Values greater than 1 use the Stage 3 sharded runner."
        ),
    )
    parser.add_argument(
        "--stage3-tag-shards",
        type=int,
        default=1,
        help=(
            "Run tag-list Stage 3 annotation in this many caption-row shards. "
            "Values greater than 1 use the Stage 3 sharded runner."
        ),
    )
    parser.add_argument(
        "--stage3-jobs",
        type=int,
        default=None,
        help=(
            "Parallel worker count for sharded Stage 3. Defaults to the number "
            "of non-empty Stage 3 shards."
        ),
    )
    parser.add_argument(
        "--stage3-gpu-devices",
        default="",
        help=(
            "Comma-separated CUDA_VISIBLE_DEVICES values assigned round-robin "
            "to sharded Stage 3 workers, for example: 0,1."
        ),
    )
    parser.add_argument(
        "--max-monolithic-stage456-captions",
        type=int,
        default=DEFAULT_MAX_MONOLITHIC_STAGE456_CAPTIONS,
        help=(
            "Fail before monolithic Stage 4/5/6 when the Stage 1 caption count "
            "exceeds this value. 0 disables the guard. Default: 250000."
        ),
    )
    parser.add_argument(
        "--stage456-shards",
        type=int,
        default=1,
        help=(
            "Run Stage 4/5/6 in this many caption-disjoint CPU shards. "
            "Use values greater than 1 only for count-only fixed-lexicon runs."
        ),
    )
    parser.add_argument(
        "--stage456-jobs",
        type=int,
        default=None,
        help=(
            "Parallel worker count for --stage456-shards. Defaults to the shard count."
        ),
    )
    parser.add_argument(
        "--stage456-merge-jobs",
        type=int,
        default=1,
        help=(
            "Parallel worker count for merging sharded Stage 6 count tables. "
            "Auto-resources fills this from detected CPU quota unless explicitly passed."
        ),
    )
    parser.add_argument(
        "--stage6-count-backend",
        choices=("sqlite", "memory"),
        default="sqlite",
        help=(
            "Stage 6 count accumulator backend. sqlite is the production-safe "
            "default; memory is for bounded speed experiments after RSS has "
            "been checked."
        ),
    )
    parser.add_argument(
        "--stage6-sqlite-cache-rows",
        type=int,
        default=None,
        help=(
            "Optional Stage 6 sqlite unique count-key cache hard cap. "
            "Defaults to adaptive RSS flushing."
        ),
    )
    parser.add_argument(
        "--stage6-facts-output-mode",
        choices=("write", "discard"),
        default="write",
        help=(
            "Whether Stage 6 should write facts.jsonl. Use discard only for "
            "fixed-lexicon count-speed experiments that do not need a markdown "
            "case report or fact-row handoff."
        ),
    )
    parser.add_argument(
        "--auto-resources",
        action="store_true",
        help=(
            "Detect cgroup CPU quota, process affinity, visible GPUs, and "
            "memory limit, then fill Stage 3/4/5/6 shard and job settings "
            "unless those settings were explicitly passed."
        ),
    )
    parser.add_argument(
        "--auto-resource-cpu-fraction",
        type=float,
        default=1.0,
        help=(
            "Fraction of detected CPU quota/affinity to use for auto Stage 4/5/6 "
            "jobs. Default: 1.0."
        ),
    )
    parser.add_argument(
        "--auto-resource-max-stage456-jobs",
        type=int,
        default=None,
        help="Optional hard cap for auto-selected Stage 4/5/6 jobs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the resolved hardware/resource plan and exit before reading "
            "inventories or running any pipeline stage."
        ),
    )
    gpu_group = parser.add_mutually_exclusive_group()
    gpu_group.add_argument(
        "--prefer-gpu",
        action="store_true",
        help="Use spaCy GPU if CuPy/CUDA is available; continue on CPU otherwise.",
    )
    gpu_group.add_argument(
        "--require-gpu",
        action="store_true",
        help="Require spaCy GPU; fail early if CuPy/CUDA is unavailable.",
    )
    args = parser.parse_args(raw_args)
    args._explicit_resource_options = _explicit_resource_options(raw_args)
    return args


def main() -> None:
    args = parse_args()
    progress_output = Path(args.progress_output) if args.progress_output else None
    gpu_mode, stage3_gpu_devices, runtime_resource_plan = apply_runtime_resource_plan(args)
    if args.dry_run:
        print(
            json.dumps(
                build_mixed_pipeline_dry_run_summary(
                    args,
                    gpu_mode=gpu_mode,
                    stage3_gpu_devices=stage3_gpu_devices,
                    runtime_resource_plan=runtime_resource_plan,
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    object_inventory, attribute_inventory, action_inventory, lexicon_dir = inventory_inputs_from_args(args)
    try:
        summary = run_mixed_caption_pipeline(
            input_paths=args.input,
            output_dir=Path(args.output_dir),
            object_inventory=object_inventory,
            attribute_inventory=attribute_inventory,
            action_inventory=action_inventory,
            allow_runtime_action_lookup_preview=args.allow_runtime_action_lookup_preview,
            preposition_mwe_lexicon=(
                Path(args.preposition_mwe_lexicon) if args.preposition_mwe_lexicon else None
            ),
            lexicon_dir=lexicon_dir,
            model=args.model,
            batch_size=args.batch_size,
            stage3_disabled_components=normalize_stage3_disabled_components(
                args.stage3_disable_components
            ),
            limit=args.limit,
            gpu_mode=gpu_mode,
            md_report=Path(args.md_report) if args.md_report else None,
            md_start=args.md_start,
            md_limit=args.md_limit,
            max_object_pairs_per_caption=args.max_object_pairs_per_caption,
            progress_output=progress_output,
            progress_interval_records=args.progress_interval_records,
            stage3_sentence_shards=args.stage3_sentence_shards,
            stage3_tag_shards=args.stage3_tag_shards,
            stage3_jobs=args.stage3_jobs,
            stage3_gpu_devices=stage3_gpu_devices,
            max_monolithic_stage456_captions=args.max_monolithic_stage456_captions,
            stage456_shards=args.stage456_shards,
            stage456_jobs=args.stage456_jobs,
            stage456_merge_jobs=args.stage456_merge_jobs,
            stage6_count_backend=args.stage6_count_backend,
            stage6_sqlite_cache_rows=args.stage6_sqlite_cache_rows,
            stage6_facts_output_mode=args.stage6_facts_output_mode,
            runtime_resource_plan=runtime_resource_plan,
        )
    except BaseException as exc:
        if progress_output is not None:
            MixedPipelineProgressWriter(progress_output).write(
                status="failed",
                phase="failed",
                error=repr(exc),
                output_dir=args.output_dir,
            )
        raise
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def apply_runtime_resource_plan(
    args: argparse.Namespace,
    *,
    hardware: Any | None = None,
) -> tuple[str, list[str], dict[str, Any] | None]:
    gpu_mode = _gpu_mode_from_args(args)
    stage3_gpu_devices = _parse_stage3_gpu_devices(args.stage3_gpu_devices)
    runtime_resource_plan: dict[str, Any] | None = None
    if args.auto_resources:
        resource_plan = choose_mixed_pipeline_resource_plan(
            hardware=hardware or detect_hardware_resources(),
            gpu_mode=gpu_mode,
            stage3_sentence_shards=args.stage3_sentence_shards,
            stage3_tag_shards=args.stage3_tag_shards,
            stage3_jobs=args.stage3_jobs,
            stage3_gpu_devices=stage3_gpu_devices,
            stage456_shards=args.stage456_shards,
            stage456_jobs=args.stage456_jobs,
            stage456_merge_jobs=args.stage456_merge_jobs,
            stage6_facts_output_mode=args.stage6_facts_output_mode,
            explicit_overrides=set(args._explicit_resource_options),
            cpu_fraction=args.auto_resource_cpu_fraction,
            max_stage456_jobs=args.auto_resource_max_stage456_jobs,
        )
        runtime_resource_plan = resource_plan.to_summary()
        chosen_resources = runtime_resource_plan["chosen"]
        args.stage3_sentence_shards = int(chosen_resources["stage3_sentence_shards"])
        args.stage3_tag_shards = int(chosen_resources["stage3_tag_shards"])
        args.stage3_jobs = chosen_resources["stage3_jobs"]
        stage3_gpu_devices = list(chosen_resources["stage3_gpu_devices"])
        args.stage456_shards = int(chosen_resources["stage456_shards"])
        args.stage456_jobs = chosen_resources["stage456_jobs"]
        args.stage456_merge_jobs = int(chosen_resources["stage456_merge_jobs"] or 1)
    return gpu_mode, stage3_gpu_devices, runtime_resource_plan


def build_mixed_pipeline_dry_run_summary(
    args: argparse.Namespace,
    *,
    gpu_mode: str,
    stage3_gpu_devices: list[str],
    runtime_resource_plan: Mapping[str, Any] | None,
    hardware: Any | None = None,
) -> dict[str, Any]:
    resource_plan = runtime_resource_plan
    if resource_plan is None:
        detected_hardware = hardware or detect_hardware_resources()
        resource_plan = {
            "auto_resources_enabled": False,
            "hardware": detected_hardware.to_summary(),
            "explicit_overrides": sorted(args._explicit_resource_options),
            "chosen": {
                "stage3_sentence_shards": args.stage3_sentence_shards,
                "stage3_tag_shards": args.stage3_tag_shards,
                "stage3_jobs": args.stage3_jobs,
                "stage3_gpu_devices": stage3_gpu_devices,
                "stage456_shards": args.stage456_shards,
                "stage456_jobs": args.stage456_jobs,
                "stage456_merge_jobs": args.stage456_merge_jobs,
            },
            "decisions": ["auto_resources_not_enabled"],
        }
    return {
        "artifact_type": "mixed_formal_pipeline_dry_run",
        "status": "dry_run",
        "input_paths": list(args.input),
        "output_dir": args.output_dir,
        "gpu_mode": gpu_mode,
        "model": args.model,
        "batch_size": args.batch_size,
        "limit": args.limit,
        "stage6_count_backend": args.stage6_count_backend,
        "stage6_facts_output_mode": args.stage6_facts_output_mode,
        "runtime_resource_plan": dict(resource_plan),
        "projected_args": {
            "stage3_sentence_shards": args.stage3_sentence_shards,
            "stage3_tag_shards": args.stage3_tag_shards,
            "stage3_jobs": args.stage3_jobs,
            "stage3_gpu_devices": stage3_gpu_devices,
            "stage456_shards": args.stage456_shards,
            "stage456_jobs": args.stage456_jobs,
            "stage456_merge_jobs": args.stage456_merge_jobs,
        },
        "validation_warnings": _resource_dry_run_warnings(args),
    }


def _gpu_mode_from_args(args: argparse.Namespace) -> str:
    return "require" if args.require_gpu else "prefer" if args.prefer_gpu else "none"


def _resource_dry_run_warnings(args: argparse.Namespace) -> list[str]:
    warnings: list[str] = []
    if args.stage456_shards > 1 and args.stage6_facts_output_mode != "discard":
        warnings.append("stage456_shards > 1 requires stage6_facts_output_mode='discard'")
    if args.md_report and args.stage6_facts_output_mode != "write":
        warnings.append("md_report requires stage6_facts_output_mode='write'")
    if args.batch_size < 1:
        warnings.append("batch_size must be greater than zero")
    if args.progress_interval_records < 1:
        warnings.append("progress_interval_records must be greater than zero")
    return warnings


def inventory_inputs_from_args(args: argparse.Namespace) -> tuple[Path, Path, Path | None, Path]:
    bundle = load_inventory_bundle(args.inventory_bundle) if args.inventory_bundle else None
    object_inventory = merge_bundle_path(
        field_name="object_inventory",
        explicit_path=args.object_inventory,
        bundled_path=bundle.object_inventory if bundle else None,
    )
    attribute_inventory = merge_bundle_path(
        field_name="attribute_inventory",
        explicit_path=args.attribute_inventory,
        bundled_path=bundle.attribute_inventory if bundle else None,
    )
    action_inventory = merge_bundle_path(
        field_name="action_inventory",
        explicit_path=args.action_inventory,
        bundled_path=bundle.action_inventory if bundle else None,
    )
    lexicon_dir = merge_bundle_path(
        field_name="lexicon_dir",
        explicit_path=args.lexicon_dir,
        bundled_path=bundle.lexicon_dir if bundle else None,
    )
    if object_inventory is None:
        raise ValueError("object_inventory is required unless --inventory-bundle is provided")
    if attribute_inventory is None:
        raise ValueError("attribute_inventory is required unless --inventory-bundle is provided")
    return object_inventory, attribute_inventory, action_inventory, lexicon_dir or Path("resources/lexicons")


class MixedPipelineProgressWriter:
    def __init__(self, path: Path, interval_records: int = 5000) -> None:
        self.path = Path(path)
        self.interval_records = interval_records
        self.interval_records = max(1, self.interval_records)
        self._started_at = time.perf_counter()
        self._last_stage_counts: dict[str, int] = {}

    def write(self, *, status: str, phase: str, **payload: Any) -> None:
        progress = {
            "schema_version": 1,
            "artifact_type": "mixed_formal_pipeline_progress",
            "status": status,
            "phase": phase,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.perf_counter() - self._started_at, 3),
            **payload,
        }
        with atomic_text_writer(self.path) as handle:
            handle.write(json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")

    def maybe_write_stage3(self, *, phase: str, total: int, **payload: Any) -> None:
        previous = self._last_stage_counts.get(phase, -self.interval_records)
        if total - previous < self.interval_records:
            return
        self._last_stage_counts[phase] = total
        self.write(
            status="running",
            phase=phase,
            stage3_records_written=total,
            **payload,
        )


def run_mixed_caption_pipeline(
    *,
    input_paths: Iterable[str | Path],
    output_dir: Path,
    object_inventory: Path,
    attribute_inventory: Path,
    action_inventory: Path | None = None,
    allow_runtime_action_lookup_preview: bool = False,
    preposition_mwe_lexicon: Path | None = None,
    lexicon_dir: Path = Path("resources/lexicons"),
    model: str = DEFAULT_STAGE3_MODEL,
    batch_size: int = DEFAULT_STAGE3_BATCH_SIZE,
    stage3_disabled_components: list[str] | tuple[str, ...] | None = None,
    limit: int | None = None,
    gpu_mode: str = "none",
    md_report: Path | None = None,
    md_start: int = 0,
    md_limit: int = 100,
    max_object_pairs_per_caption: int = 40,
    progress_output: Path | None = None,
    progress_interval_records: int = 5000,
    stage3_sentence_shards: int = 1,
    stage3_tag_shards: int = 1,
    stage3_jobs: int | None = None,
    stage3_gpu_devices: list[str] | None = None,
    max_monolithic_stage456_captions: int = DEFAULT_MAX_MONOLITHIC_STAGE456_CAPTIONS,
    stage456_shards: int = 1,
    stage456_jobs: int | None = None,
    stage456_merge_jobs: int = 1,
    stage6_count_backend: str = "sqlite",
    stage6_sqlite_cache_rows: int | None = None,
    stage6_facts_output_mode: str = "write",
    runtime_resource_plan: Mapping[str, Any] | None = None,
    memory_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total_start = time.perf_counter()
    timing_seconds: dict[str, float] = {}
    progress_writer = (
        MixedPipelineProgressWriter(
            progress_output,
            interval_records=progress_interval_records,
        )
        if progress_output is not None
        else None
    )

    def mark_timing(name: str, start: float) -> None:
        timing_seconds[name] = round(time.perf_counter() - start, 6)

    def write_progress(phase: str, *, status: str = "running", **payload: Any) -> None:
        if progress_writer is not None:
            progress_writer.write(
                status=status,
                phase=phase,
                output_dir=str(output_dir),
                timing_seconds=timing_seconds,
                **payload,
            )

    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")
    disabled_components = normalize_stage3_disabled_components(stage3_disabled_components)
    if max_monolithic_stage456_captions < 0:
        raise ValueError("max_monolithic_stage456_captions must be >= 0")
    if progress_interval_records < 1:
        raise ValueError("progress_interval_records must be greater than zero")
    if stage3_sentence_shards < 1:
        raise ValueError("stage3_sentence_shards must be greater than zero")
    if stage3_tag_shards < 1:
        raise ValueError("stage3_tag_shards must be greater than zero")
    if stage3_jobs is not None and stage3_jobs < 1:
        raise ValueError("stage3_jobs must be greater than zero")
    if stage456_shards < 1:
        raise ValueError("stage456_shards must be greater than zero")
    if stage456_jobs is not None and stage456_jobs < 1:
        raise ValueError("stage456_jobs must be greater than zero")
    if stage456_merge_jobs < 1:
        raise ValueError("stage456_merge_jobs must be greater than zero")
    if stage6_count_backend not in {"sqlite", "memory"}:
        raise ValueError("stage6_count_backend must be one of: sqlite, memory")
    if stage6_sqlite_cache_rows is not None and stage6_sqlite_cache_rows < 1:
        raise ValueError("stage6_sqlite_cache_rows must be greater than zero")
    if stage6_facts_output_mode not in {"write", "discard"}:
        raise ValueError("stage6_facts_output_mode must be one of: write, discard")
    if md_report is not None and stage6_facts_output_mode != "write":
        raise ValueError("md_report requires stage6_facts_output_mode='write'")
    if stage456_shards > 1:
        if stage6_facts_output_mode != "discard":
            raise ValueError(
                "stage456_shards > 1 currently requires stage6_facts_output_mode='discard'"
            )
        if md_report is not None:
            raise ValueError("md_report requires monolithic Stage 4/5/6 with facts.jsonl")
    if action_inventory is None and not allow_runtime_action_lookup_preview:
        raise ValueError(
            "action_inventory is required for formal mixed pipeline Stage 4; "
            "set allow_runtime_action_lookup_preview=True only for preview/debug runs"
        )
    runtime_action_lookup_preview = action_inventory is None and allow_runtime_action_lookup_preview
    _raise_if_object_inventory_not_ready(object_inventory)
    if action_inventory is not None:
        _raise_if_action_inventory_not_ready(action_inventory)
    if not runtime_action_lookup_preview:
        try:
            require_stage5_lexicon_bundle_state(lexicon_dir)
        except PipelineStateError as exc:
            raise ValueError(
                "lexicon_dir is not ready for formal mixed pipeline Stage 5: "
                f"{exc}"
            ) from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    resource_plan_summary: Mapping[str, Any] = runtime_resource_plan or {
        "auto_resources_enabled": False
    }
    write_progress(
        "start",
        input_paths=[str(path) for path in input_paths],
        runtime_resource_plan=resource_plan_summary,
    )

    stage1_dir = output_dir / "stage1"
    stage3_dir = output_dir / "stage3"
    stage4_dir = output_dir / "stage4"
    stage5_dir = output_dir / "stage5"
    stage6_dir = output_dir / "stage6"

    caption_records_path = stage1_dir / "caption_records.jsonl"
    sentence_rows_path = stage1_dir / "sentence_rows.jsonl"
    tag_rows_path = stage1_dir / "tag_rows.jsonl"
    mixed_caption_rows_path = stage1_dir / "caption_rows_mixed.jsonl"
    stage1_summary_path = stage1_dir / "summary.jsonl"
    stage_start = time.perf_counter()
    write_progress("stage1_records")
    stage1_summary = run_stage1_records(
        input_paths,
        caption_records_path=caption_records_path,
        sentence_rows_path=sentence_rows_path,
        tag_rows_path=tag_rows_path,
        summary_path=stage1_summary_path,
        limit=limit,
    )
    mark_timing("stage1_records", stage_start)
    write_progress("stage1_records_complete", stage1=stage1_summary)
    _raise_if_stage456_execution_not_safe(
        caption_total=int(stage1_summary.get("total", 0) or 0),
        max_monolithic_captions=max_monolithic_stage456_captions,
        stage456_shards=stage456_shards,
        output_dir=output_dir,
    )
    stage_start = time.perf_counter()
    write_progress("stage1_mixed_caption_rows")
    mixed_caption_rows_summary = combine_caption_rows_in_caption_order(
        caption_records_path=caption_records_path,
        sentence_rows_path=sentence_rows_path,
        tag_rows_path=tag_rows_path,
        output_path=mixed_caption_rows_path,
    )
    mark_timing("stage1_mixed_caption_rows", stage_start)
    write_progress("stage1_mixed_caption_rows_complete", stage1_mixed_caption_rows=mixed_caption_rows_summary)

    sentence_stage3_path = stage3_dir / "sentence_stage3_records.jsonl"
    tag_stage3_path = stage3_dir / "tag_list_stage3_records.jsonl"
    combined_stage3_path = stage3_dir / "stage3_records.jsonl"
    stage3_sharded_summary: dict[str, Any] | None = None
    if _stage3_sharding_enabled(
        sentence_shards=stage3_sentence_shards,
        tag_shards=stage3_tag_shards,
    ):
        stage_start = time.perf_counter()
        write_progress(
            "stage3_sharded",
            stage3_sentence_shards=stage3_sentence_shards,
            stage3_tag_shards=stage3_tag_shards,
            stage3_jobs=stage3_jobs,
            stage3_gpu_devices=stage3_gpu_devices or [],
            stage3_disabled_components=list(disabled_components),
        )
        from run_stage3_sharded import run_stage3_sharded

        stage3_sharded_summary = run_stage3_sharded(
            memory_kwargs=memory_kwargs,
            caption_records=caption_records_path,
            sentence_rows=sentence_rows_path,
            tag_rows=tag_rows_path,
            output_dir=output_dir / "stage3_sharded",
            model=model,
            batch_size=batch_size,
            sentence_shards=stage3_sentence_shards,
            tag_shards=stage3_tag_shards,
            gpu_devices=stage3_gpu_devices or [],
            jobs=stage3_jobs,
            progress_interval_records=progress_interval_records,
            overwrite=False,
            gpu_mode=gpu_mode,
            disabled_components=disabled_components,
        )
        (
            sentence_stage3_summary,
            tag_stage3_summary,
            combined_stage3_summary,
        ) = _standardize_sharded_stage3_outputs(
            stage3_sharded_summary,
            standard_stage3_dir=stage3_dir,
        )
        mark_timing("stage3_sharded", stage_start)
        write_progress(
            "stage3_sharded_complete",
            stage3_sentence=sentence_stage3_summary,
            stage3_tag_list=tag_stage3_summary,
            stage3_combined=combined_stage3_summary,
            stage3_sharded=stage3_sharded_summary,
        )
    else:
        stage_start = time.perf_counter()
        write_progress("stage3_model_load")
        nlp = make_stage3_nlp(
            model,
            gpu_mode=gpu_mode,
            disabled_components=disabled_components,
        )
        mark_timing("stage3_model_load", stage_start)
        write_progress(
            "stage3_model_load_complete",
            gpu_mode=nlp.meta.get("gpic_gpu_mode", ""),
            gpu_enabled=bool(nlp.meta.get("gpic_gpu_enabled", False)),
            stage3_disabled_components=list(disabled_components),
            stage3_enabled_components=list(nlp.meta.get("gpic_enabled_components", [])),
        )
        stage_start = time.perf_counter()
        write_progress("stage3_sentence")
        sentence_stage3_summary = _write_stage3_records(
            sentence_rows_path,
            output_path=sentence_stage3_path,
            nlp=nlp,
            caption_shape="sentence",
            model=model,
            batch_size=batch_size,
            progress_writer=progress_writer,
        )
        mark_timing("stage3_sentence", stage_start)
        write_progress("stage3_sentence_complete", stage3_sentence=sentence_stage3_summary)
        stage_start = time.perf_counter()
        write_progress("stage3_tag_list")
        tag_stage3_summary = _write_stage3_records(
            tag_rows_path,
            output_path=tag_stage3_path,
            nlp=nlp,
            caption_shape="tag_list",
            model=model,
            batch_size=batch_size,
            progress_writer=progress_writer,
        )
        mark_timing("stage3_tag_list", stage_start)
        write_progress("stage3_tag_list_complete", stage3_tag_list=tag_stage3_summary)

        stage_start = time.perf_counter()
        write_progress("stage3_combined")
        combined_stage3_summary = combine_stage3_records_in_caption_order(
            caption_records_path=caption_records_path,
            sentence_stage3_path=sentence_stage3_path,
            tag_stage3_path=tag_stage3_path,
            output_path=combined_stage3_path,
        )
        write_jsonl(stage3_dir / "summary.jsonl", [combined_stage3_summary])
        mark_timing("stage3_combined", stage_start)
        write_progress("stage3_combined_complete", stage3_combined=combined_stage3_summary)

    stage456_sharded_summary: dict[str, Any] | None = None
    if stage456_shards > 1:
        if runtime_action_lookup_preview:
            raise ValueError("sharded Stage 4/5/6 requires a resolved formal action inventory")
        if action_inventory is None:
            raise ValueError("sharded Stage 4/5/6 requires action_inventory")
        stage_start = time.perf_counter()
        write_progress(
            "stage456_sharded",
            stage456_shards=stage456_shards,
            stage456_jobs=stage456_jobs or stage456_shards,
            stage456_merge_jobs=stage456_merge_jobs,
        )
        stage456_sharded_summary = run_stage456_sharded(
            memory_kwargs=memory_kwargs,
            stage3_records=combined_stage3_path,
            output_dir=output_dir / "stage456_sharded",
            object_inventory=object_inventory,
            attribute_inventory=attribute_inventory,
            action_inventory=action_inventory,
            preposition_mwe_lexicon=preposition_mwe_lexicon,
            lexicon_dir=lexicon_dir,
            shards=stage456_shards,
            jobs=stage456_jobs or stage456_shards,
            merge_jobs=stage456_merge_jobs,
            overwrite=False,
            stage6_count_backend=stage6_count_backend,
            stage6_sqlite_cache_rows=stage6_sqlite_cache_rows,
            stage6_facts_output_mode=stage6_facts_output_mode,
        )
        stage4_summary = _aggregate_sharded_stage_summary(stage456_sharded_summary, "stage4")
        stage5_summary = _aggregate_sharded_stage_summary(stage456_sharded_summary, "stage5")
        stage5_summary["formal_attribute_inventory_gate"] = True
        stage6_summary = _standardize_sharded_stage6_summary(
            stage456_sharded_summary["stage6_merged"],
            standard_stage6_dir=stage6_dir,
        )
        _write_summary_jsonl(stage4_dir, stage4_summary)
        _write_summary_jsonl(stage5_dir, stage5_summary)
        _copy_sharded_stage6_counts(
            source_dir=Path(stage456_sharded_summary["stage6_merged"]["output_dir"]),
            standard_stage6_dir=stage6_dir,
            stage6_summary=stage6_summary,
        )
        mark_timing("stage456_sharded", stage_start)
        write_progress(
            "stage456_sharded_complete",
            stage4=stage4_summary,
            stage5=stage5_summary,
            stage6=stage6_summary,
            stage456_sharded=stage456_sharded_summary,
        )
    else:
        stage_start = time.perf_counter()
        write_progress("stage4_lookup_load")
        _raise_if_attribute_inventory_not_ready(attribute_inventory)
        object_lookup = load_gpic_object_inventory(object_inventory)
        attribute_mwe_lookup = load_gpic_attribute_mwe_inventory(
            attribute_inventory
        )
        if action_inventory is not None:
            action_lookup = load_gpic_action_inventory(action_inventory)
            runtime_action_lookup_preview = False
        elif allow_runtime_action_lookup_preview:
            action_lookup = _load_object_lookup_runtime()
            runtime_action_lookup_preview = True
        preposition_mwe_lookup = (
            load_preposition_mwe_lexicon(preposition_mwe_lexicon)
            if preposition_mwe_lexicon is not None
            else None
        )
        mark_timing("stage4_lookup_load", stage_start)
        write_progress("stage4_lookup_load_complete")
        stage_start = time.perf_counter()
        write_progress("stage4_extract_raw")
        stage4_summary = run_stage4_extract_raw(
            combined_stage3_path,
            raw_mentions_path=stage4_dir / "raw_mentions.jsonl",
            raw_edges_path=stage4_dir / "raw_edges.jsonl",
            summary_path=stage4_dir / "summary.jsonl",
            object_lookup=object_lookup,
            attribute_mwe_lookup=attribute_mwe_lookup,
            action_lookup=action_lookup,
            preposition_mwe_lookup=preposition_mwe_lookup,
            progress_path=stage4_dir / "progress.json",
            **(memory_kwargs or {}),
        )
        mark_timing("stage4_extract_raw", stage_start)
        write_progress("stage4_extract_raw_complete", stage4=stage4_summary)

        stage_start = time.perf_counter()
        write_progress("stage5_canonicalize")
        stage5_summary = run_stage5_canonicalize(
            stage4_dir / "raw_mentions.jsonl",
            stage4_dir / "raw_edges.jsonl",
            lexicon_dir=lexicon_dir,
            canonical_mentions_path=stage5_dir / "canonical_mentions.jsonl",
            canonical_edges_path=stage5_dir / "canonical_edges.jsonl",
            summary_path=None,
            progress_path=stage5_dir / "progress.json",
            **(memory_kwargs or {}),
        )
        stage5_summary["formal_attribute_inventory_gate"] = True
        write_jsonl(stage5_dir / "summary.jsonl", [stage5_summary])
        mark_timing("stage5_canonicalize", stage_start)
        write_progress("stage5_canonicalize_complete", stage5=stage5_summary)

        stage_start = time.perf_counter()
        write_progress("stage6_export_counts")
        stage6_summary = run_stage6_export_counts(
            stage5_dir / "canonical_mentions.jsonl",
            stage5_dir / "canonical_edges.jsonl",
            output_dir=stage6_dir,
            summary_path=stage6_dir / "summary.jsonl",
            progress_path=stage6_dir / "progress.json",
            count_backend=stage6_count_backend,
            sqlite_cache_rows=stage6_sqlite_cache_rows,
            facts_output_mode=stage6_facts_output_mode,
            **(memory_kwargs or {}),
        )
        mark_timing("stage6_export_counts", stage_start)
        write_progress("stage6_export_counts_complete", stage6=stage6_summary)

    md_summary: dict[str, Any] | None = None
    if md_report is not None:
        stage_start = time.perf_counter()
        write_progress("md_report")
        md_summary = build_mixed_markdown_report(
            caption_rows_path=mixed_caption_rows_path,
            stage3_records_path=combined_stage3_path,
            canonical_mentions_path=stage5_dir / "canonical_mentions.jsonl",
            canonical_edges_path=stage5_dir / "canonical_edges.jsonl",
            facts_path=stage6_dir / "facts.jsonl",
            output_path=md_report,
            start=md_start,
            limit=md_limit,
            max_object_pairs_per_caption=max_object_pairs_per_caption,
        )
        mark_timing("md_report", stage_start)
        write_progress("md_report_complete", md_report=md_summary)

    timing_seconds["total_pipeline"] = round(time.perf_counter() - total_start, 6)
    total_captions = int(stage1_summary.get("total", 0) or 0)
    timing_throughput = {
        "captions_per_second_total_pipeline": (
            round(total_captions / timing_seconds["total_pipeline"], 6)
            if timing_seconds["total_pipeline"] > 0
            else 0.0
        )
    }

    summary = {
        "status": "completed",
        "output_dir": str(output_dir),
        "stage1": stage1_summary,
        "stage1_mixed_caption_rows": mixed_caption_rows_summary,
        "stage3_sentence": sentence_stage3_summary,
        "stage3_tag_list": tag_stage3_summary,
        "stage3_combined": combined_stage3_summary,
        "stage4": stage4_summary,
        "stage5": stage5_summary,
        "stage6": stage6_summary,
        "stage3_sharded": stage3_sharded_summary,
        "stage456_sharded": stage456_sharded_summary,
        "md_report": md_summary,
        "runtime_action_lookup_preview": runtime_action_lookup_preview,
        "runtime_resource_plan": dict(resource_plan_summary),
        "stage3_component_config": {
            "disabled_components": list(disabled_components),
            "component_policy": "spacy_load_disable_components",
        },
        "timing_seconds": timing_seconds,
        "timing_throughput": timing_throughput,
    }
    pipeline_state_path = output_dir_state_path(output_dir)
    pipeline_state = build_mixed_formal_pipeline_state(
        output_dir=str(output_dir),
        object_inventory=str(object_inventory),
        attribute_inventory=str(attribute_inventory),
        action_inventory=str(action_inventory) if action_inventory is not None else None,
        lexicon_dir=str(lexicon_dir),
        runtime_action_lookup_preview=runtime_action_lookup_preview,
        stage_summaries=summary,
    )
    write_pipeline_state(pipeline_state_path, pipeline_state)
    summary["pipeline_state"] = str(pipeline_state_path)
    write_jsonl(output_dir / "mixed_pipeline_summary.jsonl", [summary])
    write_progress("complete", status="completed", summary=summary)
    return summary


def _aggregate_sharded_stage_summary(
    stage456_summary: Mapping[str, Any],
    stage_name: str,
) -> dict[str, Any]:
    shard_summaries = [
        shard.get(stage_name, {})
        for shard in stage456_summary.get("shards", [])
        if isinstance(shard, Mapping)
    ]
    return {
        "sharded": True,
        "stage": stage_name,
        "shard_count": len(shard_summaries),
        "stage456_sharded_output_dir": stage456_summary.get("output_dir", ""),
        "shard_summaries": shard_summaries,
    }


def _stage3_sharding_enabled(*, sentence_shards: int, tag_shards: int) -> bool:
    return sentence_shards > 1 or tag_shards > 1


def _parse_stage3_gpu_devices(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _explicit_resource_options(raw_args: Iterable[str]) -> set[str]:
    explicit: set[str] = set()
    for raw_arg in raw_args:
        option = raw_arg.split("=", 1)[0]
        mapped = RESOURCE_OPTION_NAMES.get(option)
        if mapped is not None:
            explicit.add(mapped)
    return explicit


def _standardize_sharded_stage3_outputs(
    stage3_summary: Mapping[str, Any],
    *,
    standard_stage3_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source_dir = Path(_required_text(stage3_summary, "output_dir"))
    standard_stage3_dir.mkdir(parents=True, exist_ok=True)
    sentence_output = standard_stage3_dir / "sentence_stage3_records.jsonl"
    tag_output = standard_stage3_dir / "tag_list_stage3_records.jsonl"
    combined_output = standard_stage3_dir / "stage3_records.jsonl"
    _copy_generated_file(source_dir / "sentence_stage3_records.jsonl", sentence_output)
    _copy_generated_file(source_dir / "tag_list_stage3_records.jsonl", tag_output)
    _copy_generated_file(source_dir / "stage3_records.jsonl", combined_output)
    summary_json = source_dir / "summary.json"
    if summary_json.exists():
        _copy_generated_file(summary_json, standard_stage3_dir / "sharded_source_summary.json")
    sentence_summary = _stage3_shape_summary_from_sharded(
        stage3_summary,
        caption_shape="sentence",
        output_path=sentence_output,
    )
    tag_summary = _stage3_shape_summary_from_sharded(
        stage3_summary,
        caption_shape="tag_list",
        output_path=tag_output,
    )
    combined_summary = dict(stage3_summary.get("stage3_combined", {}))
    combined_summary["output_path"] = str(combined_output)
    combined_summary["sharded"] = True
    combined_summary["stage3_sharded_output_dir"] = str(source_dir)
    write_jsonl(standard_stage3_dir / "summary.jsonl", [combined_summary])
    return sentence_summary, tag_summary, combined_summary


def _copy_generated_file(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"missing generated source file: {source}")
    if target.exists():
        raise FileExistsError(f"standardized target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _stage3_shape_summary_from_sharded(
    stage3_summary: Mapping[str, Any],
    *,
    caption_shape: str,
    output_path: Path,
) -> dict[str, Any]:
    shards = [
        shard
        for shard in stage3_summary.get("shards", [])
        if isinstance(shard, Mapping) and shard.get("caption_shape") == caption_shape
    ]
    worker_summaries = [
        shard.get("worker_summary", {})
        for shard in shards
        if isinstance(shard.get("worker_summary", {}), Mapping)
    ]
    span_counts: Counter[str] = Counter()
    for worker_summary in worker_summaries:
        protected_span_counts = worker_summary.get("protected_span_counts", {})
        if not isinstance(protected_span_counts, Mapping):
            continue
        for key, value in protected_span_counts.items():
            if isinstance(key, str):
                span_counts[key] += int(value or 0)
    return {
        "sharded": True,
        "total": _sum_summary_int(worker_summaries, "total"),
        "written": _sum_summary_int(worker_summaries, "written"),
        "model": stage3_summary.get("model", ""),
        "batch_size": stage3_summary.get("batch_size", ""),
        "caption_shape": caption_shape,
        "gpu_mode": stage3_summary.get("gpu_mode", ""),
        "gpu_enabled": any(bool(worker_summary.get("gpu_enabled", False)) for worker_summary in worker_summaries),
        "gpu_devices": list(stage3_summary.get("gpu_devices", [])),
        "disabled_components": list(stage3_summary.get("disabled_components", [])),
        "enabled_components": _unique_worker_component_list(worker_summaries, "enabled_components"),
        "output_path": str(output_path),
        "token_total": _sum_summary_int(worker_summaries, "token_total"),
        "noun_chunk_total": _sum_summary_int(worker_summaries, "noun_chunk_total"),
        "tag_segment_total": _sum_summary_int(worker_summaries, "tag_segment_total"),
        "protected_span_counts": dict(sorted(span_counts.items())),
        "shard_count": len(shards),
        "stage3_sharded_output_dir": stage3_summary.get("output_dir", ""),
        "shard_summaries": shards,
    }


def _unique_worker_component_list(
    summaries: Iterable[Mapping[str, Any]],
    key: str,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for summary in summaries:
        raw_values = summary.get(key, [])
        if not isinstance(raw_values, list):
            continue
        for value in raw_values:
            if not isinstance(value, str) or value in seen:
                continue
            values.append(value)
            seen.add(value)
    return values


def _sum_summary_int(summaries: Iterable[Mapping[str, Any]], key: str) -> int:
    total = 0
    for summary in summaries:
        total += int(summary.get(key, 0) or 0)
    return total


def _standardize_sharded_stage6_summary(
    merged_summary: Mapping[str, Any],
    *,
    standard_stage6_dir: Path,
) -> dict[str, Any]:
    summary = dict(merged_summary)
    source_output_dir = str(summary.get("output_dir", ""))
    summary["output_dir"] = str(standard_stage6_dir)
    summary["sharded_source_output_dir"] = source_output_dir
    table_paths = summary.get("table_paths", {})
    if isinstance(table_paths, Mapping):
        summary["table_paths"] = {
            file_name: str(standard_stage6_dir / file_name)
            for file_name in table_paths
        }
    return summary


def _write_summary_jsonl(directory: Path, summary: Mapping[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    write_jsonl(directory / "summary.jsonl", [summary])


def _copy_sharded_stage6_counts(
    *,
    source_dir: Path,
    standard_stage6_dir: Path,
    stage6_summary: Mapping[str, Any],
) -> None:
    if standard_stage6_dir.exists():
        raise FileExistsError(f"stage6 output directory already exists: {standard_stage6_dir}")
    standard_stage6_dir.mkdir(parents=True, exist_ok=True)
    for path in source_dir.glob("*.tsv"):
        shutil.copy2(path, standard_stage6_dir / path.name)
    summary_json = source_dir / "summary.json"
    if summary_json.exists():
        shutil.copy2(summary_json, standard_stage6_dir / "sharded_source_summary.json")
    write_jsonl(standard_stage6_dir / "summary.jsonl", [stage6_summary])


def _raise_if_monolithic_stage456_too_large(
    *,
    caption_total: int,
    max_captions: int,
    output_dir: Path,
) -> None:
    if max_captions == 0 or caption_total <= max_captions:
        return
    raise RuntimeError(
        "monolithic Stage 4/5/6 is not safe for this caption volume: "
        f"caption_total={caption_total}, max_monolithic_stage456_captions={max_captions}, "
        f"output_dir={output_dir}. Use a chunked/streaming Stage 4/5/6 runner instead."
    )


def _raise_if_stage456_execution_not_safe(
    *,
    caption_total: int,
    max_monolithic_captions: int,
    stage456_shards: int,
    output_dir: Path,
) -> None:
    if stage456_shards > 1:
        return
    _raise_if_monolithic_stage456_too_large(
        caption_total=caption_total,
        max_captions=max_monolithic_captions,
        output_dir=output_dir,
    )


def combine_caption_rows_in_caption_order(
    *,
    caption_records_path: Path,
    sentence_rows_path: Path,
    tag_rows_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    written = write_jsonl(
        output_path,
        _iter_caption_rows_in_caption_order(
            caption_records_path=caption_records_path,
            sentence_rows_path=sentence_rows_path,
            tag_rows_path=tag_rows_path,
        ),
        sort_keys=False,
    )
    shape_counts: Counter[str] = Counter()
    for row in iter_jsonl(output_path):
        caption_type = row.get("caption_type")
        if caption_type == "tag":
            shape_counts["tag_list"] += 1
        else:
            shape_counts["sentence"] += 1
    return {
        "total": written,
        "written": written,
        "output_path": str(output_path),
        "caption_shape_counts": dict(sorted(shape_counts.items())),
        "merge_policy": "caption_records_order",
    }


def _iter_caption_rows_in_caption_order(
    *,
    caption_records_path: Path,
    sentence_rows_path: Path,
    tag_rows_path: Path,
) -> Iterator[Mapping[str, Any]]:
    sentence_iter = iter(iter_jsonl(sentence_rows_path))
    tag_iter = iter(iter_jsonl(tag_rows_path))
    next_sentence = _next_or_none(sentence_iter)
    next_tag = _next_or_none(tag_iter)

    for caption_record in iter_jsonl(caption_records_path):
        if caption_record.get("skipped"):
            continue
        expected_id = _required_text(caption_record, "caption_id")
        shape = _required_text(caption_record, "caption_shape")
        if shape == "sentence":
            if next_sentence is None:
                raise ValueError(f"missing sentence row for {expected_id}")
            _raise_if_unexpected_row_id(next_sentence, expected_id, "sentence")
            yield next_sentence
            next_sentence = _next_or_none(sentence_iter)
        elif shape == "tag_list":
            if next_tag is None:
                raise ValueError(f"missing tag-list row for {expected_id}")
            _raise_if_unexpected_row_id(next_tag, expected_id, "tag_list")
            yield next_tag
            next_tag = _next_or_none(tag_iter)
        else:
            raise ValueError(f"unsupported caption_shape: {shape!r}")

    if next_sentence is not None:
        raise ValueError(
            f"extra sentence row after caption_records: {caption_id(next_sentence)}"
        )
    if next_tag is not None:
        raise ValueError(f"extra tag-list row after caption_records: {caption_id(next_tag)}")


def _write_stage3_records(
    input_path: Path,
    *,
    output_path: Path,
    nlp: Any,
    caption_shape: str,
    model: str,
    batch_size: int,
    progress_writer: MixedPipelineProgressWriter | None = None,
) -> dict[str, Any]:
    span_counts: Counter[str] = Counter()
    token_total = 0
    noun_chunk_total = 0
    tag_segment_total = 0
    total = 0

    def records() -> Iterator[Any]:
        nonlocal noun_chunk_total, tag_segment_total, token_total, total
        if caption_shape == "tag_list":
            iterator = iter_stage3_tag_list_records_from_rows(iter_jsonl(input_path), nlp=nlp)
        elif caption_shape == "sentence":
            iterator = iter_stage3_records_from_rows(
                iter_jsonl(input_path),
                nlp=nlp,
                batch_size=batch_size,
            )
        else:
            raise ValueError("caption_shape must be one of: sentence, tag_list")
        for record in iterator:
            total += 1
            token_total += len(record.tokens)
            noun_chunk_total += len(record.noun_chunks)
            tag_segment_total += len(record.tag_segments)
            for span in record.protected_spans:
                kind = span.get("kind")
                if isinstance(kind, str):
                    span_counts[kind] += 1
            if progress_writer is not None:
                progress_writer.maybe_write_stage3(
                    phase=f"stage3_{caption_shape}",
                    total=total,
                    caption_shape=caption_shape,
                    token_total=token_total,
                    noun_chunk_total=noun_chunk_total,
                    tag_segment_total=tag_segment_total,
                    output_path=str(output_path),
                )
            yield record

    written = write_jsonl(output_path, records(), sort_keys=False)
    return {
        "total": total,
        "written": written,
        "model": model,
        "batch_size": batch_size,
        "caption_shape": caption_shape,
        "gpu_mode": nlp.meta.get("gpic_gpu_mode", ""),
        "gpu_enabled": bool(nlp.meta.get("gpic_gpu_enabled", False)),
        "disabled_components": list(nlp.meta.get("gpic_disabled_components", [])),
        "enabled_components": list(nlp.meta.get("gpic_enabled_components", [])),
        "output_path": str(output_path),
        "token_total": token_total,
        "noun_chunk_total": noun_chunk_total,
        "tag_segment_total": tag_segment_total,
        "protected_span_counts": dict(sorted(span_counts.items())),
    }


def combine_stage3_records_in_caption_order(
    *,
    caption_records_path: Path,
    sentence_stage3_path: Path,
    tag_stage3_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    shape_counts: Counter[str] = Counter()
    written = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sentence_iter = iter(_iter_raw_jsonl_records(sentence_stage3_path))
    tag_iter = iter(_iter_raw_jsonl_records(tag_stage3_path))
    next_sentence = _next_or_none(sentence_iter)
    next_tag = _next_or_none(tag_iter)
    with open_text(output_path, "wt") as output_handle:
        for caption_record in iter_jsonl(caption_records_path):
            if caption_record.get("skipped"):
                continue
            expected_id = _required_text(caption_record, "caption_id")
            shape = _required_text(caption_record, "caption_shape")
            if shape == "sentence":
                if next_sentence is None:
                    raise ValueError(f"missing sentence Stage 3 record for {expected_id}")
                _raise_if_unexpected_raw_caption_id(next_sentence.caption_id, expected_id, "sentence")
                output_handle.write(next_sentence.raw_line)
                output_handle.write("\n")
                next_sentence = _next_or_none(sentence_iter)
            elif shape == "tag_list":
                if next_tag is None:
                    raise ValueError(f"missing tag-list Stage 3 record for {expected_id}")
                _raise_if_unexpected_raw_caption_id(next_tag.caption_id, expected_id, "tag_list")
                output_handle.write(next_tag.raw_line)
                output_handle.write("\n")
                next_tag = _next_or_none(tag_iter)
            else:
                raise ValueError(f"unsupported caption_shape: {shape!r}")
            shape_counts[shape] += 1
            written += 1

    if next_sentence is not None:
        raise ValueError(
            f"extra sentence Stage 3 record after caption_records: "
            f"{next_sentence.caption_id}"
        )
    if next_tag is not None:
        raise ValueError(
            f"extra tag-list Stage 3 record after caption_records: "
            f"{next_tag.caption_id}"
        )
    return {
        "total": written,
        "written": written,
        "output_path": str(output_path),
        "caption_shape_counts": dict(sorted(shape_counts.items())),
        "merge_policy": "caption_records_order",
    }


def _iter_stage3_records_in_caption_order(
    *,
    caption_records_path: Path,
    sentence_stage3_path: Path,
    tag_stage3_path: Path,
) -> Iterator[Mapping[str, Any]]:
    sentence_iter = iter(iter_jsonl(sentence_stage3_path))
    tag_iter = iter(iter_jsonl(tag_stage3_path))
    next_sentence = _next_or_none(sentence_iter)
    next_tag = _next_or_none(tag_iter)

    for caption_record in iter_jsonl(caption_records_path):
        if caption_record.get("skipped"):
            continue
        expected_id = _required_text(caption_record, "caption_id")
        shape = _required_text(caption_record, "caption_shape")
        if shape == "sentence":
            if next_sentence is None:
                raise ValueError(f"missing sentence Stage 3 record for {expected_id}")
            _raise_if_unexpected_caption_id(next_sentence, expected_id, "sentence")
            yield next_sentence
            next_sentence = _next_or_none(sentence_iter)
        elif shape == "tag_list":
            if next_tag is None:
                raise ValueError(f"missing tag-list Stage 3 record for {expected_id}")
            _raise_if_unexpected_caption_id(next_tag, expected_id, "tag_list")
            yield next_tag
            next_tag = _next_or_none(tag_iter)
        else:
            raise ValueError(f"unsupported caption_shape: {shape!r}")

    if next_sentence is not None:
        raise ValueError(
            f"extra sentence Stage 3 record after caption_records: "
            f"{next_sentence.get('caption_id')}"
        )
    if next_tag is not None:
        raise ValueError(
            f"extra tag-list Stage 3 record after caption_records: "
            f"{next_tag.get('caption_id')}"
        )


class _RawJsonlRecord:
    __slots__ = ("caption_id", "raw_line")

    def __init__(self, *, raw_line: str, caption_id: str) -> None:
        self.raw_line = raw_line
        self.caption_id = caption_id


def _iter_raw_jsonl_records(path: Path) -> Iterator[_RawJsonlRecord]:
    with open_text(path, "rt") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            caption_id_value = extract_stage3_caption_id_from_line(
                stripped,
                record_index=line_number - 1,
            )
            yield _RawJsonlRecord(
                raw_line=line.rstrip("\n"),
                caption_id=caption_id_value,
            )


def _next_or_none(iterator: Iterator[_T]) -> _T | None:
    try:
        return next(iterator)
    except StopIteration:
        return None


def _raise_if_unexpected_caption_id(
    stage3_record: Mapping[str, Any],
    expected_id: str,
    shape: str,
) -> None:
    actual_id = _required_text(stage3_record, "caption_id")
    if actual_id != expected_id:
        raise ValueError(
            f"{shape} Stage 3 order mismatch: expected {expected_id}, got {actual_id}"
        )


def _raise_if_unexpected_raw_caption_id(
    actual_id: str,
    expected_id: str,
    shape: str,
) -> None:
    if actual_id != expected_id:
        raise ValueError(
            f"{shape} Stage 3 order mismatch: expected {expected_id}, got {actual_id}"
        )


def _raise_if_unexpected_row_id(
    row: Mapping[str, Any],
    expected_id: str,
    shape: str,
) -> None:
    actual_id = caption_id_from_gpic_row(row)
    if actual_id != expected_id:
        raise ValueError(f"{shape} row order mismatch: expected {expected_id}, got {actual_id}")


def _stage3_caption_shape(stage3_record: Mapping[str, Any]) -> str:
    meta = stage3_record.get("meta")
    if isinstance(meta, Mapping):
        value = meta.get("caption_shape")
        if isinstance(value, str) and value:
            return value
    value = stage3_record.get("caption_shape")
    if isinstance(value, str) and value:
        return value
    return "sentence"


def _required_text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def build_mixed_markdown_report(
    *,
    caption_rows_path: Path,
    stage3_records_path: Path,
    canonical_mentions_path: Path,
    canonical_edges_path: Path,
    facts_path: Path,
    output_path: Path,
    start: int,
    limit: int,
    max_object_pairs_per_caption: int,
) -> dict[str, Any]:
    captions = list(iter_jsonl(caption_rows_path))[start : start + limit]
    caption_ids = [caption_id(row) for row in captions]
    caption_id_set = set(caption_ids)
    stage3_records = group_by_caption(
        row for row in iter_jsonl(stage3_records_path) if row["caption_id"] in caption_id_set
    )
    mentions = group_by_caption(
        row for row in iter_jsonl(canonical_mentions_path) if row["caption_id"] in caption_id_set
    )
    edges = group_by_caption(
        row for row in iter_jsonl(canonical_edges_path) if row["caption_id"] in caption_id_set
    )
    facts = group_by_caption(
        row for row in iter_jsonl(facts_path) if row["caption_id"] in caption_id_set
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_report(
            captions,
            stage3_records,
            mentions,
            edges,
            facts,
            start=start,
            max_object_pairs_per_caption=max_object_pairs_per_caption,
        ),
        encoding="utf-8",
    )
    return {
        "output": str(output_path),
        "caption_count": len(captions),
        "start": start,
        "limit": limit,
        "max_object_pairs_per_caption": max_object_pairs_per_caption,
        "stage3_records_included": True,
    }


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("mixed_caption_pipeline", main))
