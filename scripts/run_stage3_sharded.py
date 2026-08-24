from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from incident_gate import guarded_entrypoint  # noqa: E402

from gpic_concepts_v1.atomic_io import atomic_text_writer  # noqa: E402
from gpic_concepts_v1.io_jsonl import iter_jsonl, open_text  # noqa: E402
from gpic_concepts_v1.stage3_annotate import (  # noqa: E402
    DEFAULT_STAGE3_BATCH_SIZE,
    DEFAULT_STAGE3_DISABLED_COMPONENTS,
    DEFAULT_STAGE3_MODEL,
    normalize_stage3_disabled_components,
)
from run_mixed_caption_pipeline import combine_stage3_records_in_caption_order  # noqa: E402


@dataclass(frozen=True, slots=True)
class Stage3Shard:
    caption_shape: str
    shard_index: int
    input_path: Path
    output_path: Path
    summary_path: Path
    progress_path: Path
    stdout_path: Path
    stderr_path: Path
    row_count: int
    gpu_device: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Stage 3 annotation in order-preserving row shards. This runner "
            "does not change Stage 3 semantics; each shard calls run_stage3_annotate.py."
        ),
    )
    parser.add_argument("--caption-records", required=True, help="Stage 1 caption_records.jsonl.")
    parser.add_argument("--sentence-rows", required=True, help="Stage 1 sentence_rows.jsonl.")
    parser.add_argument("--tag-rows", required=True, help="Stage 1 tag_rows.jsonl.")
    parser.add_argument("--output-dir", required=True, help="Output directory.")
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
        "--disable-components",
        default=",".join(DEFAULT_STAGE3_DISABLED_COMPONENTS),
        help=(
            "Comma-separated spaCy pipeline components to disable in every "
            f"Stage 3 worker. Default: {','.join(DEFAULT_STAGE3_DISABLED_COMPONENTS)}."
        ),
    )
    parser.add_argument(
        "--sentence-shards",
        type=int,
        default=2,
        help="Number of contiguous sentence-row shards.",
    )
    parser.add_argument(
        "--tag-shards",
        type=int,
        default=1,
        help="Number of contiguous tag-list-row shards.",
    )
    parser.add_argument(
        "--gpu-devices",
        default="",
        help=(
            "Comma-separated physical GPU ids assigned round-robin to non-empty "
            "shards, for example 0,1. Empty means do not set CUDA_VISIBLE_DEVICES."
        ),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Parallel Stage 3 subprocess count. Defaults to number of non-empty shards.",
    )
    parser.add_argument(
        "--progress-interval-records",
        type=int,
        default=5000,
        help="Record interval forwarded to each Stage 3 shard progress JSON.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Remove an existing output directory before writing this run.",
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
        help="Require spaCy GPU in every non-empty shard.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_stage3_sharded(
        caption_records=Path(args.caption_records),
        sentence_rows=Path(args.sentence_rows),
        tag_rows=Path(args.tag_rows),
        output_dir=Path(args.output_dir),
        model=args.model,
        batch_size=args.batch_size,
        sentence_shards=args.sentence_shards,
        tag_shards=args.tag_shards,
        gpu_devices=parse_gpu_devices(args.gpu_devices),
        jobs=args.jobs,
        progress_interval_records=args.progress_interval_records,
        overwrite=args.overwrite,
        gpu_mode="require" if args.require_gpu else "prefer" if args.prefer_gpu else "none",
        disabled_components=normalize_stage3_disabled_components(args.disable_components),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def run_stage3_sharded(
    *,
    caption_records: Path,
    sentence_rows: Path,
    tag_rows: Path,
    output_dir: Path,
    model: str = DEFAULT_STAGE3_MODEL,
    batch_size: int = DEFAULT_STAGE3_BATCH_SIZE,
    sentence_shards: int = 2,
    tag_shards: int = 1,
    gpu_devices: list[str] | None = None,
    jobs: int | None = None,
    progress_interval_records: int = 5000,
    overwrite: bool = False,
    gpu_mode: str = "none",
    disabled_components: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if sentence_shards < 1:
        raise ValueError("--sentence-shards must be greater than zero")
    if tag_shards < 1:
        raise ValueError("--tag-shards must be greater than zero")
    if batch_size < 1:
        raise ValueError("--batch-size must be greater than zero")
    if progress_interval_records < 1:
        raise ValueError("--progress-interval-records must be greater than zero")
    if gpu_mode not in {"none", "prefer", "require"}:
        raise ValueError("gpu_mode must be one of: none, prefer, require")
    gpu_devices = gpu_devices or []
    disabled = normalize_stage3_disabled_components(disabled_components)

    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"output directory already exists: {output_dir}. "
                "Use --overwrite only when replacing that exact run directory is intended."
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.json"
    started = time.perf_counter()
    timing_seconds: dict[str, float] = {}
    write_progress(progress_path, status="running", phase="split_rows", output_dir=str(output_dir))

    step_started = time.perf_counter()
    sentence_split = split_jsonl_contiguous(
        sentence_rows,
        output_dir / "sentence_row_shards",
        shard_count=sentence_shards,
        file_prefix="sentence_rows_shard",
    )
    timing_seconds["split_sentence_rows"] = round(time.perf_counter() - step_started, 6)
    step_started = time.perf_counter()
    tag_split = split_jsonl_contiguous(
        tag_rows,
        output_dir / "tag_list_row_shards",
        shard_count=tag_shards,
        file_prefix="tag_list_rows_shard",
    )
    timing_seconds["split_tag_rows"] = round(time.perf_counter() - step_started, 6)

    step_started = time.perf_counter()
    shards = build_stage3_shards(
        output_dir=output_dir,
        sentence_split=sentence_split,
        tag_split=tag_split,
        gpu_devices=gpu_devices,
    )
    timing_seconds["build_shards"] = round(time.perf_counter() - step_started, 6)
    non_empty_shards = [shard for shard in shards if shard.row_count > 0]
    empty_shard_summaries = [
        materialize_empty_stage3_shard(shard)
        for shard in shards
        if shard.row_count == 0
    ]
    requested_jobs = jobs if jobs is not None else max(1, len(non_empty_shards))
    if requested_jobs < 1:
        raise ValueError("--jobs must be greater than zero")
    effective_jobs = min(requested_jobs, max(1, len(non_empty_shards)))
    write_progress(
        progress_path,
        status="running",
        phase="run_shards",
        output_dir=str(output_dir),
        non_empty_shards=len(non_empty_shards),
        jobs=effective_jobs,
        gpu_devices=gpu_devices,
    )
    step_started = time.perf_counter()
    shard_summaries = empty_shard_summaries + run_stage3_shards(
        non_empty_shards,
        model=model,
        batch_size=batch_size,
        gpu_mode=gpu_mode,
        jobs=effective_jobs,
        progress_interval_records=progress_interval_records,
        disabled_components=disabled,
        progress_path=progress_path,
    )
    timing_seconds["run_shards_wall"] = round(time.perf_counter() - step_started, 6)

    write_progress(progress_path, status="running", phase="merge_shape_outputs", output_dir=str(output_dir))
    sentence_stage3_path = output_dir / "sentence_stage3_records.jsonl"
    tag_stage3_path = output_dir / "tag_list_stage3_records.jsonl"
    step_started = time.perf_counter()
    sentence_merge = merge_shard_jsonl_outputs(
        [shard.output_path for shard in shards if shard.caption_shape == "sentence"],
        sentence_stage3_path,
    )
    timing_seconds["merge_sentence_stage3_records"] = round(time.perf_counter() - step_started, 6)
    step_started = time.perf_counter()
    tag_merge = merge_shard_jsonl_outputs(
        [shard.output_path for shard in shards if shard.caption_shape == "tag_list"],
        tag_stage3_path,
    )
    timing_seconds["merge_tag_stage3_records"] = round(time.perf_counter() - step_started, 6)

    write_progress(progress_path, status="running", phase="combine_caption_order", output_dir=str(output_dir))
    combined_stage3_path = output_dir / "stage3_records.jsonl"
    step_started = time.perf_counter()
    combined_summary = combine_stage3_records_in_caption_order(
        caption_records_path=caption_records,
        sentence_stage3_path=sentence_stage3_path,
        tag_stage3_path=tag_stage3_path,
        output_path=combined_stage3_path,
    )
    timing_seconds["combine_caption_order"] = round(time.perf_counter() - step_started, 6)
    total_seconds = round(time.perf_counter() - started, 6)
    timing_seconds["total"] = total_seconds
    timing_seconds["shards_total_max"] = round(
        max((float(item["timing_seconds"]["total"]) for item in shard_summaries), default=0.0),
        6,
    )
    timing_seconds["shards_total_sum"] = round(
        sum(float(item["timing_seconds"]["total"]) for item in shard_summaries),
        6,
    )
    summary = {
        "status": "completed",
        "caption_records": str(caption_records),
        "sentence_rows": str(sentence_rows),
        "tag_rows": str(tag_rows),
        "output_dir": str(output_dir),
        "model": model,
        "batch_size": batch_size,
        "gpu_mode": gpu_mode,
        "gpu_devices": gpu_devices,
        "disabled_components": list(disabled),
        "gpu_metadata": collect_nvidia_smi_metadata(),
        "jobs": effective_jobs,
        "sentence_split": sentence_split.to_summary(),
        "tag_split": tag_split.to_summary(),
        "shards": sorted(shard_summaries, key=lambda item: (item["caption_shape"], int(item["shard_index"]))),
        "sentence_merge": sentence_merge,
        "tag_merge": tag_merge,
        "stage3_combined": combined_summary,
        "timing_seconds": timing_seconds,
    }
    write_json(output_dir / "summary.json", summary)
    write_progress(progress_path, status="complete", phase="complete", output_dir=str(output_dir), summary=summary)
    return summary


@dataclass(frozen=True, slots=True)
class SplitSummary:
    input_path: Path
    output_dir: Path
    file_prefix: str
    shard_count: int
    row_count: int
    shard_row_counts: list[int]

    def to_summary(self) -> dict[str, Any]:
        return {
            "input_path": str(self.input_path),
            "output_dir": str(self.output_dir),
            "file_prefix": self.file_prefix,
            "shard_count": self.shard_count,
            "row_count": self.row_count,
            "shard_row_counts": {
                f"{self.file_prefix}_{index:04d}.jsonl": count
                for index, count in enumerate(self.shard_row_counts)
            },
            "split_policy": "contiguous_preserve_input_order",
        }


def split_jsonl_contiguous(
    input_path: Path,
    output_dir: Path,
    *,
    shard_count: int,
    file_prefix: str,
) -> SplitSummary:
    if shard_count < 1:
        raise ValueError("shard_count must be greater than zero")
    if output_dir.exists():
        raise FileExistsError(f"row shard directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    total = count_jsonl_rows(input_path)
    shard_row_counts = contiguous_shard_sizes(total, shard_count)
    handles = [
        open_text(output_dir / f"{file_prefix}_{index:04d}.jsonl", "wt")
        for index in range(shard_count)
    ]
    entered_handles = [handle.__enter__() for handle in handles]
    try:
        shard_index = 0
        written_in_shard = 0
        written_total = 0
        with open_text(input_path, "rt") as input_handle:
            for line in input_handle:
                if not line.strip():
                    continue
                while shard_index < shard_count - 1 and written_in_shard >= shard_row_counts[shard_index]:
                    shard_index += 1
                    written_in_shard = 0
                entered_handles[shard_index].write(line.rstrip("\n"))
                entered_handles[shard_index].write("\n")
                written_in_shard += 1
                written_total += 1
        if written_total != total:
            raise RuntimeError("jsonl row count changed while splitting")
    finally:
        for manager in reversed(handles):
            manager.__exit__(None, None, None)
    return SplitSummary(
        input_path=input_path,
        output_dir=output_dir,
        file_prefix=file_prefix,
        shard_count=shard_count,
        row_count=total,
        shard_row_counts=shard_row_counts,
    )


def split_jsonl_contiguous_parsed_reference(
    input_path: Path,
    output_dir: Path,
    *,
    shard_count: int,
    file_prefix: str,
) -> SplitSummary:
    """Slow reference splitter kept for regression tests and incident diagnosis."""
    if shard_count < 1:
        raise ValueError("shard_count must be greater than zero")
    if output_dir.exists():
        raise FileExistsError(f"row shard directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    total = sum(1 for _ in iter_jsonl(input_path))
    shard_row_counts = contiguous_shard_sizes(total, shard_count)
    handles = [
        open_text(output_dir / f"{file_prefix}_{index:04d}.jsonl", "wt")
        for index in range(shard_count)
    ]
    entered_handles = [handle.__enter__() for handle in handles]
    try:
        shard_index = 0
        written_in_shard = 0
        for row_index, row in enumerate(iter_jsonl(input_path)):
            while shard_index < shard_count - 1 and written_in_shard >= shard_row_counts[shard_index]:
                shard_index += 1
                written_in_shard = 0
            entered_handles[shard_index].write(json.dumps(row, ensure_ascii=False, sort_keys=False))
            entered_handles[shard_index].write("\n")
            written_in_shard += 1
            if row_index + 1 > total:
                raise RuntimeError("jsonl row count changed while splitting")
    finally:
        for manager in reversed(handles):
            manager.__exit__(None, None, None)
    return SplitSummary(
        input_path=input_path,
        output_dir=output_dir,
        file_prefix=file_prefix,
        shard_count=shard_count,
        row_count=total,
        shard_row_counts=shard_row_counts,
    )


def contiguous_shard_sizes(total: int, shard_count: int) -> list[int]:
    if total < 0:
        raise ValueError("total must be non-negative")
    if shard_count < 1:
        raise ValueError("shard_count must be greater than zero")
    base, remainder = divmod(total, shard_count)
    return [base + (1 if index < remainder else 0) for index in range(shard_count)]


def count_jsonl_rows(path: Path) -> int:
    with open_text(path, "rt") as handle:
        return sum(1 for line in handle if line.strip())


def build_stage3_shards(
    *,
    output_dir: Path,
    sentence_split: SplitSummary,
    tag_split: SplitSummary,
    gpu_devices: list[str],
) -> list[Stage3Shard]:
    shards: list[Stage3Shard] = []
    stage3_shard_dir = output_dir / "stage3_shards"
    logs_dir = output_dir / "logs"
    progress_dir = output_dir / "shard_progress"
    summaries_dir = output_dir / "shard_summaries"
    for directory in (stage3_shard_dir, logs_dir, progress_dir, summaries_dir):
        directory.mkdir(parents=True, exist_ok=True)

    gpu_assignment_index = 0
    for caption_shape, split in (("sentence", sentence_split), ("tag_list", tag_split)):
        for shard_index, row_count in enumerate(split.shard_row_counts):
            name = f"{caption_shape}_shard_{shard_index:04d}"
            gpu_device = None
            if row_count > 0 and gpu_devices:
                gpu_device = gpu_devices[gpu_assignment_index % len(gpu_devices)]
                gpu_assignment_index += 1
            shards.append(
                Stage3Shard(
                    caption_shape=caption_shape,
                    shard_index=shard_index,
                    input_path=split.output_dir / f"{split.file_prefix}_{shard_index:04d}.jsonl",
                    output_path=stage3_shard_dir / f"{name}.jsonl",
                    summary_path=summaries_dir / f"{name}.jsonl",
                    progress_path=progress_dir / f"{name}.json",
                    stdout_path=logs_dir / f"{name}.stdout.log",
                    stderr_path=logs_dir / f"{name}.stderr.log",
                    row_count=row_count,
                    gpu_device=gpu_device,
                )
            )
    return shards


def materialize_empty_stage3_shard(shard: Stage3Shard) -> dict[str, Any]:
    """Write the complete Stage 3 artifact contract for a zero-row shard."""
    if shard.row_count != 0:
        raise ValueError("only zero-row Stage 3 shards may be materialized as empty")
    for path in (
        shard.output_path,
        shard.summary_path,
        shard.progress_path,
        shard.stdout_path,
        shard.stderr_path,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)

    shard.output_path.write_text("", encoding="utf-8")
    worker_summary = {
        "total": 0,
        "written": 0,
        "model": "",
        "batch_size": 0,
        "caption_shape": shard.caption_shape,
        "gpu_mode": "none",
        "gpu_enabled": False,
        "output_path": str(shard.output_path),
        "empty_shard": True,
    }
    shard.summary_path.write_text(
        json.dumps(worker_summary, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_json(
        shard.progress_path,
        {
            "status": "complete",
            "phase": "empty_shard",
            "caption_shape": shard.caption_shape,
            "total": 0,
            "written": 0,
            "output_path": str(shard.output_path),
        },
    )
    shard.stdout_path.write_text("", encoding="utf-8")
    shard.stderr_path.write_text("", encoding="utf-8")
    return {
        "caption_shape": shard.caption_shape,
        "shard_index": shard.shard_index,
        "row_count": 0,
        "gpu_device": None,
        "input_path": str(shard.input_path),
        "output_path": str(shard.output_path),
        "summary_path": str(shard.summary_path),
        "progress_path": str(shard.progress_path),
        "stdout_path": str(shard.stdout_path),
        "stderr_path": str(shard.stderr_path),
        "worker_summary": worker_summary,
        "timing_seconds": {"total": 0.0},
    }


def run_stage3_shards(
    shards: list[Stage3Shard],
    *,
    model: str,
    batch_size: int,
    gpu_mode: str,
    jobs: int,
    progress_interval_records: int,
    disabled_components: tuple[str, ...],
    progress_path: Path,
) -> list[dict[str, Any]]:
    if not shards:
        return []
    summaries: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(
                run_one_stage3_shard,
                shard,
                model=model,
                batch_size=batch_size,
                gpu_mode=gpu_mode,
                progress_interval_records=progress_interval_records,
                disabled_components=disabled_components,
            ): shard
            for shard in shards
        }
        for future in as_completed(futures):
            shard = futures[future]
            summary = future.result()
            summaries.append(summary)
            write_progress(
                progress_path,
                status="running",
                phase="run_shards",
                completed_shards=len(summaries),
                non_empty_shards=len(shards),
                latest_shard={
                    "caption_shape": shard.caption_shape,
                    "shard_index": shard.shard_index,
                    "row_count": shard.row_count,
                    "gpu_device": shard.gpu_device,
                    "timing_seconds": summary.get("timing_seconds", {}),
                },
            )
    return summaries


def run_one_stage3_shard(
    shard: Stage3Shard,
    *,
    model: str,
    batch_size: int,
    gpu_mode: str,
    progress_interval_records: int,
    disabled_components: tuple[str, ...],
) -> dict[str, Any]:
    started = time.perf_counter()
    command = build_stage3_worker_command(
        shard,
        model=model,
        batch_size=batch_size,
        gpu_mode=gpu_mode,
        progress_interval_records=progress_interval_records,
        disabled_components=disabled_components,
    )
    env = os.environ.copy()
    if shard.gpu_device is not None:
        env["CUDA_VISIBLE_DEVICES"] = shard.gpu_device
    shard.output_path.parent.mkdir(parents=True, exist_ok=True)
    shard.summary_path.parent.mkdir(parents=True, exist_ok=True)
    shard.progress_path.parent.mkdir(parents=True, exist_ok=True)
    shard.stdout_path.parent.mkdir(parents=True, exist_ok=True)
    shard.stderr_path.parent.mkdir(parents=True, exist_ok=True)
    with shard.stdout_path.open("w", encoding="utf-8") as stdout_handle:
        with shard.stderr_path.open("w", encoding="utf-8") as stderr_handle:
            completed = subprocess.run(
                command,
                cwd=str(ROOT),
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                check=False,
            )
    if completed.returncode != 0:
        raise RuntimeError(
            "Stage 3 shard failed: "
            + json.dumps(
                {
                    "caption_shape": shard.caption_shape,
                    "shard_index": shard.shard_index,
                    "returncode": completed.returncode,
                    "input_path": str(shard.input_path),
                    "stdout_path": str(shard.stdout_path),
                    "stderr_path": str(shard.stderr_path),
                    "stderr_tail": tail_text(shard.stderr_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    summary_records = list(iter_jsonl(shard.summary_path))
    if len(summary_records) != 1:
        raise ValueError(f"expected exactly one shard summary row: {shard.summary_path}")
    worker_summary = summary_records[0]
    expected_total = int(shard.row_count)
    actual_total = int(worker_summary.get("total", 0) or 0)
    if actual_total != expected_total:
        raise ValueError(
            "Stage 3 shard total mismatch: "
            + json.dumps(
                {
                    "caption_shape": shard.caption_shape,
                    "shard_index": shard.shard_index,
                    "expected_total": expected_total,
                    "actual_total": actual_total,
                },
                sort_keys=True,
            )
        )
    return {
        "caption_shape": shard.caption_shape,
        "shard_index": shard.shard_index,
        "row_count": shard.row_count,
        "gpu_device": shard.gpu_device,
        "input_path": str(shard.input_path),
        "output_path": str(shard.output_path),
        "summary_path": str(shard.summary_path),
        "progress_path": str(shard.progress_path),
        "stdout_path": str(shard.stdout_path),
        "stderr_path": str(shard.stderr_path),
        "worker_summary": worker_summary,
        "timing_seconds": {
            "total": round(time.perf_counter() - started, 6),
        },
    }


def build_stage3_worker_command(
    shard: Stage3Shard,
    *,
    model: str,
    batch_size: int,
    gpu_mode: str,
    progress_interval_records: int,
    disabled_components: tuple[str, ...] = DEFAULT_STAGE3_DISABLED_COMPONENTS,
) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "run_stage3_annotate.py"),
        "--input",
        str(shard.input_path),
        "--output",
        str(shard.output_path),
        "--summary",
        str(shard.summary_path),
        "--model",
        model,
        "--batch-size",
        str(batch_size),
        "--caption-shape",
        shard.caption_shape,
        "--progress-output",
        str(shard.progress_path),
        "--progress-interval-records",
        str(progress_interval_records),
        "--disable-components",
        ",".join(disabled_components),
    ]
    if gpu_mode == "require":
        command.append("--require-gpu")
    elif gpu_mode == "prefer":
        command.append("--prefer-gpu")
    return command


def merge_shard_jsonl_outputs(shard_paths: list[Path], output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open_text(output_path, "wt") as output_handle:
        for shard_path in shard_paths:
            if not shard_path.exists():
                raise FileNotFoundError(f"missing Stage 3 shard output: {shard_path}")
            with open_text(shard_path, "rt") as input_handle:
                for line in input_handle:
                    if not line.strip():
                        continue
                    output_handle.write(line.rstrip("\n"))
                    output_handle.write("\n")
                    total += 1
    return {
        "output_path": str(output_path),
        "input_paths": [str(path) for path in shard_paths],
        "total": total,
        "merge_policy": "contiguous_shard_order",
    }


def parse_gpu_devices(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def collect_nvidia_smi_metadata() -> list[dict[str, str]]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,driver_version,pstate,power.draw,power.limit,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if completed.returncode != 0:
        return []
    fields = [
        "index",
        "name",
        "driver_version",
        "pstate",
        "power_draw_w",
        "power_limit_w",
        "memory_used_mib",
        "memory_total_mib",
    ]
    rows: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != len(fields):
            continue
        rows.append(dict(zip(fields, parts, strict=True)))
    return rows


def tail_text(path: Path, *, max_lines: int = 30) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def write_json(path: Path, payload: dict[str, Any]) -> None:
    with atomic_text_writer(path) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")


def write_progress(path: Path, *, status: str, phase: str, **payload: Any) -> None:
    write_json(
        path,
        {
            "schema_version": 1,
            "artifact_type": "stage3_sharded_progress",
            "status": status,
            "phase": phase,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            **payload,
        },
    )


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("stage3_sharded", main))
