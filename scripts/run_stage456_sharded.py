from __future__ import annotations

import argparse
import csv
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import heapq
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any
import zlib

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from incident_gate import guarded_entrypoint
from stage3_jsonl_utils import extract_stage3_caption_id_from_line
from run_stage4_extract_raw import (  # noqa: E402
    _raise_if_action_inventory_not_ready,
    _raise_if_object_inventory_not_ready,
)
from run_stage5_canonicalize import _raise_if_attribute_inventory_not_ready  # noqa: E402

from gpic_concepts_v1.atomic_io import atomic_text_writer  # noqa: E402
from gpic_concepts_v1.cli_memory import add_memory_safety_args, memory_safety_kwargs  # noqa: E402
from gpic_concepts_v1.io_jsonl import iter_jsonl, open_text  # noqa: E402
from gpic_concepts_v1.stage4_extract_raw import (  # noqa: E402
    load_gpic_action_inventory,
    load_gpic_attribute_mwe_inventory,
    load_gpic_object_inventory,
    load_preposition_mwe_lexicon,
    run_stage4_extract_raw,
)
from gpic_concepts_v1.stage5_canonicalize import run_stage5_canonicalize  # noqa: E402
from gpic_concepts_v1.stage6_export_counts import (  # noqa: E402
    COUNT_TABLE_SPECS,
    CountTableSpec,
    run_stage6_export_counts,
)


COMMON_COUNT_COLUMNS = {
    "count_key",
    "count",
    "caption_count",
    "example_caption_ids",
    "raw_variants",
    "rule_ids",
}

DEFAULT_PARTITIONED_MERGE_TABLES = frozenset({"object_cooccurrence_pair_counts.tsv"})
COUNT_TABLE_IO_BUFFER_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ShardInput:
    shard_index: int
    stage3_path: str
    output_dir: str
    object_inventory: str
    attribute_inventory: str
    action_inventory: str
    preposition_mwe_lexicon: str | None
    lexicon_dir: str
    count_backend: str
    facts_output_mode: str
    sqlite_cache_rows: int | None
    memory_kwargs: dict[str, Any]


@dataclass(slots=True)
class MergedCountRow:
    fields: dict[str, str]
    count: int = 0
    caption_count: int = 0
    example_caption_ids: set[str] | None = None
    rule_ids: set[str] | None = None
    pipe_field_values: dict[str, list[str]] | None = None

    def __post_init__(self) -> None:
        if self.example_caption_ids is None:
            self.example_caption_ids = set()
        if self.rule_ids is None:
            self.rule_ids = set()
        if self.pipe_field_values is None:
            self.pipe_field_values = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Stage 4/5/6 over Stage 3 records in caption-disjoint CPU shards "
            "and merge Stage 6 count TSVs."
        ),
    )
    parser.add_argument("--stage3-records", required=True, help="Input Stage 3 JSONL file.")
    parser.add_argument("--output-dir", required=True, help="Output directory.")
    parser.add_argument("--object-inventory", required=True, help="Resolved object inventory TSV.")
    parser.add_argument("--attribute-inventory", required=True, help="Resolved attribute inventory TSV.")
    parser.add_argument("--action-inventory", required=True, help="Resolved action inventory TSV.")
    parser.add_argument(
        "--preposition-mwe-lexicon",
        default=None,
        help="Optional active preposition MWE TSV.",
    )
    parser.add_argument(
        "--lexicon-dir",
        default="resources/lexicons",
        help="Stage 5 lexicon directory.",
    )
    parser.add_argument("--shards", type=int, default=4, help="Number of caption shards.")
    parser.add_argument("--jobs", type=int, default=4, help="Parallel worker process count.")
    parser.add_argument(
        "--merge-jobs",
        type=int,
        default=1,
        help="Parallel count-table merge worker count. Defaults to 1 for deterministic low-load runs.",
    )
    parser.add_argument(
        "--partitioned-merge-tables",
        default="object_cooccurrence_pair_counts.tsv",
        help=(
            "Comma-separated Stage 6 count table file names to merge by stable count_key "
            "hash partitions. Use an empty value to disable."
        ),
    )
    parser.add_argument(
        "--partitioned-merge-partitions",
        type=int,
        default=0,
        help=(
            "Hash partition count for partitioned table merge. "
            "0 uses the requested --merge-jobs value."
        ),
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional input record limit.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Remove an existing output directory before writing this run.",
    )
    parser.add_argument(
        "--stage6-count-backend",
        choices=("sqlite", "memory"),
        default="sqlite",
        help="Per-shard Stage 6 count backend.",
    )
    parser.add_argument(
        "--stage6-sqlite-cache-rows",
        type=int,
        default=None,
        help="Optional per-shard SQLite cache row cap.",
    )
    parser.add_argument(
        "--stage6-facts-output-mode",
        choices=("discard",),
        default="discard",
        help="Sharded runner currently supports count-only Stage 6 output.",
    )
    parser.add_argument(
        "--compare-stage6-dir",
        default=None,
        help="Optional baseline Stage 6 directory. Merged TSVs must match exactly.",
    )
    add_memory_safety_args(parser, stage_name="Stage 4/5/6 shard")
    return parser.parse_args()


def _parse_partitioned_merge_tables(value: str | None) -> set[str]:
    if value is None:
        return set(DEFAULT_PARTITIONED_MERGE_TABLES)
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "off", "false", "0"}:
        return set()
    return {item.strip() for item in cleaned.split(",") if item.strip()}


def main() -> None:
    args = parse_args()
    summary = run_stage456_sharded(
        stage3_records=Path(args.stage3_records),
        output_dir=Path(args.output_dir),
        object_inventory=Path(args.object_inventory),
        attribute_inventory=Path(args.attribute_inventory),
        action_inventory=Path(args.action_inventory),
        preposition_mwe_lexicon=(
            Path(args.preposition_mwe_lexicon) if args.preposition_mwe_lexicon else None
        ),
        lexicon_dir=Path(args.lexicon_dir),
        shards=args.shards,
        jobs=args.jobs,
        merge_jobs=args.merge_jobs,
        partitioned_merge_tables=_parse_partitioned_merge_tables(args.partitioned_merge_tables),
        partitioned_merge_partitions=args.partitioned_merge_partitions,
        limit=args.limit,
        overwrite=args.overwrite,
        stage6_count_backend=args.stage6_count_backend,
        stage6_sqlite_cache_rows=args.stage6_sqlite_cache_rows,
        stage6_facts_output_mode=args.stage6_facts_output_mode,
        compare_stage6_dir=Path(args.compare_stage6_dir) if args.compare_stage6_dir else None,
        memory_kwargs=stage_function_memory_kwargs(memory_safety_kwargs(args)),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def run_stage456_sharded(
    *,
    stage3_records: Path,
    output_dir: Path,
    object_inventory: Path,
    attribute_inventory: Path,
    action_inventory: Path,
    preposition_mwe_lexicon: Path | None = None,
    lexicon_dir: Path = Path("resources/lexicons"),
    shards: int = 4,
    jobs: int = 4,
    merge_jobs: int = 1,
    partitioned_merge_tables: set[str] | None = None,
    partitioned_merge_partitions: int = 0,
    limit: int | None = None,
    overwrite: bool = False,
    stage6_count_backend: str = "sqlite",
    stage6_sqlite_cache_rows: int | None = None,
    stage6_facts_output_mode: str = "discard",
    compare_stage6_dir: Path | None = None,
    memory_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if shards < 1:
        raise ValueError("--shards must be greater than zero")
    if jobs < 1:
        raise ValueError("--jobs must be greater than zero")
    if merge_jobs < 1:
        raise ValueError("--merge-jobs must be greater than zero")
    if partitioned_merge_partitions < 0:
        raise ValueError("--partitioned-merge-partitions must be zero or greater")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be greater than zero")
    if stage6_facts_output_mode != "discard":
        raise ValueError("sharded Stage 4/5/6 currently requires facts_output_mode='discard'")

    _raise_if_object_inventory_not_ready(object_inventory)
    _raise_if_action_inventory_not_ready(action_inventory)
    _raise_if_attribute_inventory_not_ready(attribute_inventory)

    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"output directory already exists: {output_dir}. "
                "Use --overwrite only when replacing that exact run directory is intended."
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.json"
    _write_progress(
        progress_path,
        status="running",
        phase="split_stage3_records",
        output_dir=str(output_dir),
    )
    started = time.perf_counter()
    shard_records_dir = output_dir / "stage3_shards"
    split_started = time.perf_counter()
    split_summary = split_stage3_records(
        stage3_records,
        shard_records_dir=shard_records_dir,
        shard_count=shards,
        limit=limit,
    )
    split_seconds = time.perf_counter() - split_started
    _write_json(output_dir / "split_summary.json", split_summary)

    _write_progress(
        progress_path,
        status="running",
        phase="run_shards",
        output_dir=str(output_dir),
        split_summary=split_summary,
    )
    shard_inputs = [
        ShardInput(
            shard_index=index,
            stage3_path=str(shard_records_dir / f"stage3_shard_{index:04d}.jsonl"),
            output_dir=str(output_dir / "shards" / f"shard_{index:04d}"),
            object_inventory=str(object_inventory),
            attribute_inventory=str(attribute_inventory),
            action_inventory=str(action_inventory),
            preposition_mwe_lexicon=(
                str(preposition_mwe_lexicon) if preposition_mwe_lexicon is not None else None
            ),
            lexicon_dir=str(lexicon_dir),
            count_backend=stage6_count_backend,
            facts_output_mode=stage6_facts_output_mode,
            sqlite_cache_rows=stage6_sqlite_cache_rows,
            memory_kwargs=stage_function_memory_kwargs(memory_kwargs or {}),
        )
        for index in range(shards)
    ]
    run_shards_started = time.perf_counter()
    shard_summaries = run_shards(
        shard_inputs,
        jobs=min(jobs, shards),
        progress_path=progress_path,
    )
    run_shards_wall_seconds = time.perf_counter() - run_shards_started

    _write_progress(
        progress_path,
        status="running",
        phase="merge_stage6_counts",
        output_dir=str(output_dir),
        completed_shards=len(shard_summaries),
    )
    merged_stage6_dir = output_dir / "stage6_merged"
    merge_started = time.perf_counter()
    merge_summary = merge_stage6_count_dirs(
        [Path(summary["stage6"]["output_dir"]) for summary in shard_summaries],
        merged_stage6_dir,
        merge_jobs=merge_jobs,
        partitioned_merge_tables=partitioned_merge_tables,
        partitioned_merge_partitions=partitioned_merge_partitions,
    )
    merge_seconds = time.perf_counter() - merge_started

    compare_summary: dict[str, Any] | None = None
    if compare_stage6_dir:
        compare_summary = compare_stage6_dirs(
            expected_dir=compare_stage6_dir,
            actual_dir=merged_stage6_dir,
        )

    summary = {
        "status": "completed",
        "stage3_records": str(stage3_records),
        "output_dir": str(output_dir),
        "split": split_summary,
        "merge_jobs": merge_jobs,
        "shards": sorted(shard_summaries, key=lambda item: int(item["shard_index"])),
        "stage6_merged": merge_summary,
        "compare_stage6": compare_summary,
        "timing_seconds": {
            "total": round(time.perf_counter() - started, 6),
            "split_stage3_records": round(split_seconds, 6),
            "run_shards_wall": round(run_shards_wall_seconds, 6),
            "merge_stage6_counts": round(merge_seconds, 6),
            "shards_total_max": round(
                max((float(item["timing_seconds"]["total"]) for item in shard_summaries), default=0.0),
                6,
            ),
            "shards_total_sum": round(
                sum(float(item["timing_seconds"]["total"]) for item in shard_summaries),
                6,
            ),
        },
    }
    _write_json(output_dir / "summary.json", summary)
    _write_progress(
        progress_path,
        status="complete",
        phase="complete",
        output_dir=str(output_dir),
        summary=summary,
    )
    return summary


def split_stage3_records(
    input_path: Path,
    *,
    shard_records_dir: Path,
    shard_count: int,
    limit: int | None = None,
) -> dict[str, Any]:
    if shard_records_dir.exists():
        raise FileExistsError(f"shard records directory already exists: {shard_records_dir}")
    shard_records_dir.mkdir(parents=True, exist_ok=True)
    handles = [
        open_text(shard_records_dir / f"stage3_shard_{index:04d}.jsonl", "wt")
        for index in range(shard_count)
    ]
    entered_handles = [handle.__enter__() for handle in handles]
    shard_counts = [0 for _ in range(shard_count)]
    caption_ids: set[str] = set()
    duplicate_caption_ids: list[str] = []
    caption_digest = hashlib.sha256()
    total = 0
    try:
        with open_text(input_path, "rt") as input_handle:
            record_index = 0
            for raw_line in input_handle:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if limit is not None and record_index >= limit:
                    break
                caption_id = extract_stage3_caption_id_from_line(
                    stripped,
                    record_index=record_index,
                )
                if not caption_id:
                    raise ValueError(f"missing_caption_id_at_stage3_record_index={record_index}")
                if caption_id in caption_ids and len(duplicate_caption_ids) < 20:
                    duplicate_caption_ids.append(caption_id)
                caption_ids.add(caption_id)
                caption_digest.update(caption_id.encode("utf-8"))
                caption_digest.update(b"\n")
                shard_index = record_index % shard_count
                entered_handles[shard_index].write(stripped)
                entered_handles[shard_index].write("\n")
                shard_counts[shard_index] += 1
                total += 1
                record_index += 1
    finally:
        for manager in reversed(handles):
            manager.__exit__(None, None, None)
    if duplicate_caption_ids:
        raise ValueError(
            "sharded Stage 6 merge requires globally unique caption_id values; "
            + json.dumps(
                {
                    "duplicate_caption_ids": duplicate_caption_ids,
                    "input_path": str(input_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return {
        "input_path": str(input_path),
        "shard_records_dir": str(shard_records_dir),
        "shard_count": shard_count,
        "record_count": total,
        "unique_caption_count": len(caption_ids),
        "caption_id_sha256": caption_digest.hexdigest(),
        "shard_record_counts": {
            f"stage3_shard_{index:04d}.jsonl": count
            for index, count in enumerate(shard_counts)
        },
    }


def run_shards(
    shard_inputs: list[ShardInput],
    *,
    jobs: int,
    progress_path: Path,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(run_one_shard, shard_input): shard_input
            for shard_input in shard_inputs
        }
        for future in as_completed(futures):
            shard_input = futures[future]
            summary = future.result()
            summaries.append(summary)
            _write_progress(
                progress_path,
                status="running",
                phase="run_shards",
                completed_shards=len(summaries),
                shard_count=len(shard_inputs),
                latest_shard_index=shard_input.shard_index,
                latest_shard_summary=summary,
            )
    return summaries


def run_one_shard(shard_input: ShardInput) -> dict[str, Any]:
    shard_started = time.perf_counter()
    timing_seconds: dict[str, float] = {}
    shard_output = Path(shard_input.output_dir)
    shard_output.mkdir(parents=True, exist_ok=True)

    stage4_dir = shard_output / "stage4"
    stage5_dir = shard_output / "stage5"
    stage6_dir = shard_output / "stage6"

    start = time.perf_counter()
    object_lookup = load_gpic_object_inventory(Path(shard_input.object_inventory))
    attribute_mwe_lookup = load_gpic_attribute_mwe_inventory(
        Path(shard_input.attribute_inventory)
    )
    action_lookup = load_gpic_action_inventory(Path(shard_input.action_inventory))
    preposition_mwe_lookup = (
        load_preposition_mwe_lexicon(Path(shard_input.preposition_mwe_lexicon))
        if shard_input.preposition_mwe_lexicon
        else None
    )
    timing_seconds["stage4_lookup_load"] = round(time.perf_counter() - start, 6)

    start = time.perf_counter()
    stage4_summary = run_stage4_extract_raw(
        Path(shard_input.stage3_path),
        raw_mentions_path=stage4_dir / "raw_mentions.jsonl",
        raw_edges_path=stage4_dir / "raw_edges.jsonl",
        summary_path=stage4_dir / "summary.jsonl",
        object_lookup=object_lookup,
        attribute_mwe_lookup=attribute_mwe_lookup,
        action_lookup=action_lookup,
        preposition_mwe_lookup=preposition_mwe_lookup,
        progress_path=stage4_dir / "progress.json",
        **shard_input.memory_kwargs,
    )
    timing_seconds["stage4_extract_raw"] = round(time.perf_counter() - start, 6)

    start = time.perf_counter()
    stage5_summary = run_stage5_canonicalize(
        stage4_dir / "raw_mentions.jsonl",
        stage4_dir / "raw_edges.jsonl",
        lexicon_dir=Path(shard_input.lexicon_dir),
        canonical_mentions_path=stage5_dir / "canonical_mentions.jsonl",
        canonical_edges_path=stage5_dir / "canonical_edges.jsonl",
        summary_path=stage5_dir / "summary.jsonl",
        progress_path=stage5_dir / "progress.json",
        **shard_input.memory_kwargs,
    )
    timing_seconds["stage5_canonicalize"] = round(time.perf_counter() - start, 6)

    start = time.perf_counter()
    stage6_summary = run_stage6_export_counts(
        stage5_dir / "canonical_mentions.jsonl",
        stage5_dir / "canonical_edges.jsonl",
        output_dir=stage6_dir,
        summary_path=stage6_dir / "summary.jsonl",
        progress_path=stage6_dir / "progress.json",
        count_backend=shard_input.count_backend,
        sqlite_cache_rows=shard_input.sqlite_cache_rows,
        facts_output_mode=shard_input.facts_output_mode,
        **shard_input.memory_kwargs,
    )
    timing_seconds["stage6_export_counts"] = round(time.perf_counter() - start, 6)
    timing_seconds["total"] = round(time.perf_counter() - shard_started, 6)

    summary = {
        "shard_index": shard_input.shard_index,
        "stage3_path": shard_input.stage3_path,
        "output_dir": str(shard_output),
        "stage4": stage4_summary,
        "stage5": stage5_summary,
        "stage6": stage6_summary,
        "timing_seconds": timing_seconds,
    }
    _write_json(shard_output / "summary.json", summary)
    return summary


def stage_function_memory_kwargs(cli_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return memory kwargs safe to combine with per-stage progress paths."""
    return {
        key: value
        for key, value in cli_kwargs.items()
        if key != "progress_path"
    }


def merge_stage6_count_dirs(
    shard_stage6_dirs: list[Path],
    output_dir: Path,
    *,
    merge_jobs: int = 1,
    partitioned_merge_tables: set[str] | None = None,
    partitioned_merge_partitions: int = 0,
) -> dict[str, Any]:
    if merge_jobs < 1:
        raise ValueError("merge_jobs must be greater than zero")
    if partitioned_merge_partitions < 0:
        raise ValueError("partitioned_merge_partitions must be zero or greater")
    output_dir.mkdir(parents=True, exist_ok=True)
    shard_summaries = [_read_stage6_summary(path) for path in shard_stage6_dirs]
    fact_type_counts = _sum_counters(summary.get("fact_type_counts", {}) for summary in shard_summaries)
    fact_total = sum(int(summary.get("fact_total", 0) or 0) for summary in shard_summaries)
    table_row_counts: dict[str, int] = {}
    table_count_sums: dict[str, int] = {}
    table_paths: dict[str, str] = {}
    table_merge_seconds: dict[str, float] = {}
    table_merge_strategies: dict[str, str] = {}
    table_merge_details: dict[str, dict[str, Any]] = {}

    requested_merge_jobs = merge_jobs
    merge_jobs = min(merge_jobs, len(COUNT_TABLE_SPECS))
    partitioned_merge_tables = (
        set(DEFAULT_PARTITIONED_MERGE_TABLES)
        if partitioned_merge_tables is None
        else set(partitioned_merge_tables)
    )
    partitioned_merge_partitions = (
        requested_merge_jobs
        if partitioned_merge_partitions == 0
        else partitioned_merge_partitions
    )
    merge_requests = [
        (
            spec,
            [path / spec.file_name for path in shard_stage6_dirs],
            output_dir / spec.file_name,
        )
        for spec in COUNT_TABLE_SPECS
    ]
    partitioned_requests = [
        request
        for request in merge_requests
        if request[0].file_name in partitioned_merge_tables and partitioned_merge_partitions > 1
    ]
    standard_requests = [
        request
        for request in merge_requests
        if request not in partitioned_requests
    ]
    merge_results: list[dict[str, Any]] = []
    if not standard_requests:
        pass
    elif merge_jobs == 1 or len(standard_requests) == 1:
        merge_results = [
            _merge_count_table_shards_timed(spec, shard_paths, output_path)
            for spec, shard_paths, output_path in standard_requests
        ]
    else:
        with ProcessPoolExecutor(max_workers=min(merge_jobs, len(standard_requests))) as executor:
            futures = [
                executor.submit(_merge_count_table_shards_timed, spec, shard_paths, output_path)
                for spec, shard_paths, output_path in standard_requests
            ]
            for future in as_completed(futures):
                merge_results.append(future.result())
    for spec, shard_paths, output_path in partitioned_requests:
        merge_results.append(
            _merge_count_table_shards_partitioned_timed(
                spec,
                shard_paths,
                output_path,
                partition_count=partitioned_merge_partitions,
                jobs=requested_merge_jobs,
            )
        )

    for merge_result in sorted(merge_results, key=lambda item: str(item["file_name"])):
        file_name = str(merge_result["file_name"])
        output_path = Path(str(merge_result["output_path"]))
        table_paths[file_name] = str(output_path)
        table_row_counts[file_name] = int(merge_result["row_count"])
        table_count_sums[file_name] = int(merge_result["count_sum"])
        table_merge_seconds[file_name] = float(merge_result["timing_seconds"])
        table_merge_strategies[file_name] = str(merge_result.get("merge_strategy", "single_pass"))
        details = merge_result.get("details")
        if isinstance(details, dict):
            table_merge_details[file_name] = details

    count_integrity = {
        "status": "ok",
        "fact_total": fact_total,
        "fact_type_counts": dict(sorted(fact_type_counts.items())),
        "table_count_sums": dict(sorted(table_count_sums.items())),
    }
    mismatches: list[dict[str, Any]] = []
    for spec in COUNT_TABLE_SPECS:
        expected = fact_type_counts.get(spec.fact_type, 0)
        actual = table_count_sums.get(spec.file_name, 0)
        if expected != actual:
            mismatches.append(
                {
                    "file_name": spec.file_name,
                    "fact_type": spec.fact_type,
                    "expected_count_sum": expected,
                    "actual_count_sum": actual,
                }
            )
    if mismatches:
        count_integrity["status"] = "failed"
        count_integrity["mismatches"] = mismatches
        raise ValueError("merged count integrity failed: " + json.dumps(count_integrity, sort_keys=True))

    summary = {
        "output_dir": str(output_dir),
        "shard_stage6_dirs": [str(path) for path in shard_stage6_dirs],
        "facts_output_mode": "discard",
        "requested_merge_jobs": requested_merge_jobs,
        "merge_jobs": merge_jobs,
        "partitioned_merge_tables": sorted(partitioned_merge_tables),
        "partitioned_merge_partitions": partitioned_merge_partitions,
        "fact_total": fact_total,
        "fact_type_counts": dict(sorted(fact_type_counts.items())),
        "table_paths": table_paths,
        "table_row_counts": dict(sorted(table_row_counts.items())),
        "table_merge_seconds": dict(sorted(table_merge_seconds.items())),
        "table_merge_strategies": dict(sorted(table_merge_strategies.items())),
        "table_merge_details": dict(sorted(table_merge_details.items())),
        "count_integrity": count_integrity,
    }
    _write_json(output_dir / "summary.json", summary)
    return summary


def _merge_count_table_shards_timed(
    spec: CountTableSpec,
    shard_paths: list[Path],
    output_path: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = merge_count_table_shards(spec, shard_paths, output_path)
    return {
        "file_name": spec.file_name,
        "output_path": str(output_path),
        "row_count": int(result["row_count"]),
        "count_sum": int(result["count_sum"]),
        "timing_seconds": round(time.perf_counter() - started, 6),
        "merge_strategy": "single_pass",
    }


def _merge_count_table_shards_partitioned_timed(
    spec: CountTableSpec,
    shard_paths: list[Path],
    output_path: Path,
    *,
    partition_count: int,
    jobs: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = merge_count_table_shards_partitioned(
        spec,
        shard_paths,
        output_path,
        partition_count=partition_count,
        jobs=jobs,
    )
    details = {
        key: value
        for key, value in result.items()
        if key not in {"row_count", "count_sum"}
    }
    return {
        "file_name": spec.file_name,
        "output_path": str(output_path),
        "row_count": int(result["row_count"]),
        "count_sum": int(result["count_sum"]),
        "timing_seconds": round(time.perf_counter() - started, 6),
        "merge_strategy": "hash_partition",
        "details": details,
    }


def merge_count_table_shards(
    spec: CountTableSpec,
    shard_paths: list[Path],
    output_path: Path,
) -> dict[str, int]:
    fieldnames = _count_table_fieldnames(spec)
    value_fields = set(spec.value_fields)
    merge_fields = set(spec.extra_value_fields)
    rows_by_key: dict[str, MergedCountRow] = {}
    count_sum = 0
    for shard_path in shard_paths:
        if not shard_path.exists():
            raise FileNotFoundError(f"missing shard count table: {shard_path}")
        with shard_path.open(
            "r",
            encoding="utf-8",
            newline="",
            buffering=COUNT_TABLE_IO_BUFFER_SIZE,
        ) as handle:
            reader = csv.reader(handle, delimiter="\t")
            header = next(reader, None)
            if header is None:
                raise ValueError(f"missing TSV header: {shard_path}")
            unexpected = set(header) - set(fieldnames)
            missing = set(fieldnames) - set(header)
            if unexpected or missing:
                raise ValueError(
                    "count table schema mismatch: "
                    + json.dumps(
                        {
                            "path": str(shard_path),
                            "unexpected": sorted(unexpected),
                            "missing": sorted(missing),
                        },
                        sort_keys=True,
                    )
                )
            index = {field: header_index for header_index, field in enumerate(header)}
            count_key_index = index["count_key"]
            count_index = index["count"]
            caption_count_index = index["caption_count"]
            example_caption_ids_index = index["example_caption_ids"]
            raw_variants_index = index["raw_variants"]
            rule_ids_index = index["rule_ids"]
            value_indices = {field: index[field] for field in value_fields}
            merge_indices = {field: index[field] for field in merge_fields}
            stored_field_indices = {
                field: index[field]
                for field in (*spec.value_fields, *spec.extra_value_fields)
            }
            for row_number, row in enumerate(reader, start=2):
                if len(row) != len(header):
                    raise ValueError(
                        "count table row width mismatch: "
                        + json.dumps(
                            {
                                "path": str(shard_path),
                                "line": row_number,
                                "expected_width": len(header),
                                "actual_width": len(row),
                            },
                            sort_keys=True,
                        )
                    )
                count_key = row[count_key_index]
                if not count_key:
                    raise ValueError(f"blank count_key in {shard_path}")
                current = rows_by_key.get(count_key)
                if current is None:
                    current = MergedCountRow(
                        fields={
                            field: row[field_index]
                            for field, field_index in stored_field_indices.items()
                        }
                    )
                    rows_by_key[count_key] = current
                for field, field_index in value_indices.items():
                    if current.fields.get(field, "") != row[field_index]:
                        raise ValueError(
                            "count_key value field conflict: "
                            + json.dumps(
                                {
                                    "count_key": count_key,
                                    "field": field,
                                    "old": current.fields.get(field, ""),
                                    "new": row[field_index],
                                },
                                sort_keys=True,
                            )
                        )
                for field, field_index in merge_indices.items():
                    current.pipe_field_values.setdefault(field, []).append(row[field_index])
                row_count = int(row[count_index] or 0)
                current.count += row_count
                count_sum += row_count
                current.caption_count += int(row[caption_count_index] or 0)
                current.example_caption_ids.update(_split_pipe(row[example_caption_ids_index]))
                current.pipe_field_values.setdefault("raw_variants", []).append(
                    row[raw_variants_index],
                )
                current.rule_ids.update(_split_pipe(row[rule_ids_index]))

    sorted_items = sorted(rows_by_key.items(), key=lambda item: (-item[1].count, item[0]))
    with atomic_text_writer(output_path, newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(fieldnames)
        for count_key, row in sorted_items:
            writer.writerow(
                [
                    count_key,
                    *(row.fields.get(field, "") for field in spec.value_fields),
                    *(
                        _merged_pipe_field(
                            (row.pipe_field_values or {}).get(field, []),
                        )
                        for field in spec.extra_value_fields
                    ),
                    row.count,
                    row.caption_count,
                    "|".join(sorted(row.example_caption_ids or set())[:5]),
                    _merged_pipe_field(
                        (row.pipe_field_values or {}).get("raw_variants", []),
                    ),
                    "|".join(sorted(row.rule_ids or set())),
                ]
            )
    return {
        "row_count": len(sorted_items),
        "count_sum": count_sum,
    }


def _count_table_fieldnames(spec: CountTableSpec) -> list[str]:
    return [
        "count_key",
        *spec.value_fields,
        *spec.extra_value_fields,
        "count",
        "caption_count",
        "example_caption_ids",
        "raw_variants",
        "rule_ids",
    ]


def _validate_count_table_header(
    *,
    path: Path,
    header: list[str] | None,
    fieldnames: list[str],
) -> dict[str, int]:
    if header is None:
        raise ValueError(f"missing TSV header: {path}")
    unexpected = set(header) - set(fieldnames)
    missing = set(fieldnames) - set(header)
    if unexpected or missing:
        raise ValueError(
            "count table schema mismatch: "
            + json.dumps(
                {
                    "path": str(path),
                    "unexpected": sorted(unexpected),
                    "missing": sorted(missing),
                },
                sort_keys=True,
            )
        )
    return {field: header_index for header_index, field in enumerate(header)}


def _stable_count_key_partition(count_key: str, partition_count: int) -> int:
    return zlib.crc32(count_key.encode("utf-8")) % partition_count


def merge_count_table_shards_partitioned(
    spec: CountTableSpec,
    shard_paths: list[Path],
    output_path: Path,
    *,
    partition_count: int,
    jobs: int,
) -> dict[str, Any]:
    if partition_count < 1:
        raise ValueError("partition_count must be greater than zero")
    if jobs < 1:
        raise ValueError("jobs must be greater than zero")
    if partition_count == 1:
        result = merge_count_table_shards(spec, shard_paths, output_path)
        row_count = int(result["row_count"])
        return {
            **result,
            "partition_count": 1,
            "partition_jobs": 1,
            "partition_input_rows": [row_count],
            "partition_write_seconds": 0.0,
            "partition_merge_seconds": 0.0,
            "final_kway_merge_seconds": 0.0,
            "max_partition_input_rows": row_count,
            "min_partition_input_rows": row_count,
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = _count_table_fieldnames(spec)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_path.stem}_hash_merge_",
        dir=output_path.parent,
    ) as temporary_dir_name:
        temporary_dir = Path(temporary_dir_name)
        partition_inputs = [
            temporary_dir / f"partition_{index:04d}.input.tsv"
            for index in range(partition_count)
        ]
        partition_outputs = [
            temporary_dir / f"partition_{index:04d}.merged.tsv"
            for index in range(partition_count)
        ]
        partition_input_rows = [0 for _ in range(partition_count)]

        write_started = time.perf_counter()
        handles = [
            path.open(
                "w",
                encoding="utf-8",
                newline="",
                buffering=COUNT_TABLE_IO_BUFFER_SIZE,
            )
            for path in partition_inputs
        ]
        try:
            writers = [csv.writer(handle, delimiter="\t") for handle in handles]
            for writer in writers:
                writer.writerow(fieldnames)
            for shard_path in shard_paths:
                if not shard_path.exists():
                    raise FileNotFoundError(f"missing shard count table: {shard_path}")
                with shard_path.open(
                    "r",
                    encoding="utf-8",
                    newline="",
                    buffering=COUNT_TABLE_IO_BUFFER_SIZE,
                ) as handle:
                    raw_header = handle.readline()
                    header = _parse_tsv_header_line(raw_header)
                    index = _validate_count_table_header(
                        path=shard_path,
                        header=header,
                        fieldnames=fieldnames,
                    )
                    count_key_index = index["count_key"]
                    ordered_indices = [index[field] for field in fieldnames]
                    header_is_canonical = header == fieldnames
                    if header_is_canonical and count_key_index == 0:
                        for row_number, raw_line in enumerate(handle, start=2):
                            count_key = _fast_count_key_from_canonical_tsv_line(
                                raw_line,
                                path=shard_path,
                                line_number=row_number,
                                field_count=len(fieldnames),
                            )
                            partition_index = _stable_count_key_partition(
                                count_key,
                                partition_count,
                            )
                            handles[partition_index].write(raw_line)
                            partition_input_rows[partition_index] += 1
                    else:
                        reader = csv.reader(handle, delimiter="\t")
                        for row_number, row in enumerate(reader, start=2):
                            if len(row) != len(header or ()):
                                raise ValueError(
                                    "count table row width mismatch: "
                                    + json.dumps(
                                        {
                                            "path": str(shard_path),
                                            "line": row_number,
                                            "expected_width": len(header or ()),
                                            "actual_width": len(row),
                                        },
                                        sort_keys=True,
                                    )
                                )
                            count_key = row[count_key_index]
                            if not count_key:
                                raise ValueError(f"blank count_key in {shard_path}")
                            partition_index = _stable_count_key_partition(
                                count_key,
                                partition_count,
                            )
                            writers[partition_index].writerow(
                                [row[field_index] for field_index in ordered_indices],
                            )
                            partition_input_rows[partition_index] += 1
        finally:
            for handle in reversed(handles):
                handle.close()
        partition_write_seconds = time.perf_counter() - write_started

        merge_started = time.perf_counter()
        partition_jobs = min(jobs, partition_count)
        if partition_jobs == 1:
            partition_results = [
                _merge_one_count_table_partition(
                    spec,
                    partition_inputs[index],
                    partition_outputs[index],
                    index,
                )
                for index in range(partition_count)
            ]
        else:
            partition_results = []
            with ProcessPoolExecutor(max_workers=partition_jobs) as executor:
                futures = [
                    executor.submit(
                        _merge_one_count_table_partition,
                        spec,
                        partition_inputs[index],
                        partition_outputs[index],
                        index,
                    )
                    for index in range(partition_count)
                ]
                for future in as_completed(futures):
                    partition_results.append(future.result())
        partition_merge_seconds = time.perf_counter() - merge_started

        row_count = sum(int(result["row_count"]) for result in partition_results)
        count_sum = sum(int(result["count_sum"]) for result in partition_results)

        final_started = time.perf_counter()
        _kway_merge_sorted_count_partitions(
            fieldnames,
            partition_outputs,
            output_path,
        )
        final_kway_merge_seconds = time.perf_counter() - final_started

    return {
        "row_count": row_count,
        "count_sum": count_sum,
        "partition_count": partition_count,
        "partition_jobs": partition_jobs,
        "nonempty_partition_count": sum(1 for count in partition_input_rows if count),
        "partition_input_rows": partition_input_rows,
        "partition_write_seconds": round(partition_write_seconds, 6),
        "partition_merge_seconds": round(partition_merge_seconds, 6),
        "final_kway_merge_seconds": round(final_kway_merge_seconds, 6),
        "max_partition_input_rows": max(partition_input_rows, default=0),
        "min_partition_input_rows": min(partition_input_rows, default=0),
    }


def _merge_one_count_table_partition(
    spec: CountTableSpec,
    input_path: Path,
    output_path: Path,
    partition_index: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = merge_count_table_shards(spec, [input_path], output_path)
    return {
        "partition_index": partition_index,
        "row_count": int(result["row_count"]),
        "count_sum": int(result["count_sum"]),
        "timing_seconds": round(time.perf_counter() - started, 6),
    }


def _kway_merge_sorted_count_partitions(
    fieldnames: list[str],
    partition_outputs: list[Path],
    output_path: Path,
) -> None:
    count_key_index = fieldnames.index("count_key")
    count_index = fieldnames.index("count")
    handles = [
        path.open(
            "r",
            encoding="utf-8",
            newline="",
            buffering=COUNT_TABLE_IO_BUFFER_SIZE,
        )
        for path in partition_outputs
    ]
    try:
        heap: list[tuple[int, str, int, str, Any, Path]] = []
        sequence = 0
        for index, handle in enumerate(handles):
            raw_header = handle.readline()
            header = _parse_tsv_header_line(raw_header)
            if header != fieldnames:
                raise ValueError(
                    "partition output schema mismatch: "
                    + json.dumps(
                        {
                            "path": str(partition_outputs[index]),
                            "expected": fieldnames,
                            "actual": header,
                        },
                        sort_keys=True,
                    )
                )
            entry = _read_next_raw_count_row_entry(
                handle,
                path=partition_outputs[index],
                fieldnames=fieldnames,
                count_key_index=count_key_index,
                count_index=count_index,
            )
            if entry is None:
                continue
            count_key, count, raw_line = entry
            heapq.heappush(
                heap,
                (-count, count_key, sequence, raw_line, handle, partition_outputs[index]),
            )
            sequence += 1

        with atomic_text_writer(output_path, newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(fieldnames)
            while heap:
                _, _, _, raw_line, input_handle, input_path = heapq.heappop(heap)
                handle.write(raw_line)
                next_entry = _read_next_raw_count_row_entry(
                    input_handle,
                    path=input_path,
                    fieldnames=fieldnames,
                    count_key_index=count_key_index,
                    count_index=count_index,
                )
                if next_entry is not None:
                    next_count_key, next_count, next_raw_line = next_entry
                    heapq.heappush(
                        heap,
                        (
                            -next_count,
                            next_count_key,
                            sequence,
                            next_raw_line,
                            input_handle,
                            input_path,
                        ),
                    )
                    sequence += 1
    finally:
        for handle in reversed(handles):
            handle.close()


def _parse_tsv_header_line(raw_header: str) -> list[str] | None:
    if raw_header == "":
        return None
    return next(csv.reader([raw_header], delimiter="\t"), None)


def _fast_count_key_from_canonical_tsv_line(
    raw_line: str,
    *,
    path: Path,
    line_number: int,
    field_count: int,
) -> str:
    if not raw_line:
        raise ValueError(
            "empty count table row: "
            + json.dumps({"path": str(path), "line": line_number}, sort_keys=True)
        )
    if raw_line.startswith('"') or raw_line.count("\t") < field_count - 1:
        row = _parse_single_tsv_data_line(raw_line, path=path, line_number=line_number)
        if len(row) != field_count:
            raise ValueError(
                "count table row width mismatch: "
                + json.dumps(
                    {
                        "path": str(path),
                        "line": line_number,
                        "expected_width": field_count,
                        "actual_width": len(row),
                    },
                    sort_keys=True,
                )
            )
        count_key = row[0]
    else:
        count_key = raw_line.split("\t", 1)[0]
    if not count_key:
        raise ValueError(f"blank count_key in {path}")
    return count_key


def _read_next_raw_count_row_entry(
    handle: Any,
    *,
    path: Path,
    fieldnames: list[str],
    count_key_index: int,
    count_index: int,
) -> tuple[str, int, str] | None:
    raw_line = handle.readline()
    if raw_line == "":
        return None
    count_key, count = _raw_count_row_sort_key(
        raw_line,
        path=path,
        fieldnames=fieldnames,
        count_key_index=count_key_index,
        count_index=count_index,
    )
    return count_key, count, raw_line


def _raw_count_row_sort_key(
    raw_line: str,
    *,
    path: Path,
    fieldnames: list[str],
    count_key_index: int,
    count_index: int,
) -> tuple[str, int]:
    if count_key_index == 0 and not raw_line.startswith('"'):
        parts = raw_line.rstrip("\r\n").split("\t", count_index + 1)
        if len(parts) > count_index:
            count_key = parts[count_key_index]
            if count_key:
                try:
                    return count_key, int(parts[count_index] or 0)
                except ValueError:
                    pass
    row = _parse_single_tsv_data_line(raw_line, path=path, line_number=None)
    if len(row) != len(fieldnames):
        raise ValueError(
            "count table row width mismatch: "
            + json.dumps(
                {
                    "path": str(path),
                    "expected_width": len(fieldnames),
                    "actual_width": len(row),
                },
                sort_keys=True,
            )
        )
    count_key = row[count_key_index]
    if not count_key:
        raise ValueError(f"blank count_key in {path}")
    return count_key, int(row[count_index] or 0)


def _parse_single_tsv_data_line(
    raw_line: str,
    *,
    path: Path,
    line_number: int | None,
) -> list[str]:
    rows = list(csv.reader([raw_line], delimiter="\t"))
    if len(rows) != 1:
        details: dict[str, Any] = {"path": str(path)}
        if line_number is not None:
            details["line"] = line_number
        raise ValueError("invalid TSV row: " + json.dumps(details, sort_keys=True))
    return rows[0]


def compare_stage6_dirs(*, expected_dir: Path, actual_dir: Path) -> dict[str, Any]:
    mismatches: list[dict[str, str]] = []
    for spec in COUNT_TABLE_SPECS:
        expected_path = expected_dir / spec.file_name
        actual_path = actual_dir / spec.file_name
        if not expected_path.exists() or not actual_path.exists():
            mismatches.append(
                {
                    "file_name": spec.file_name,
                    "reason": "missing_file",
                    "expected_path": str(expected_path),
                    "actual_path": str(actual_path),
                }
            )
            continue
        if expected_path.read_bytes() != actual_path.read_bytes():
            mismatches.append(
                {
                    "file_name": spec.file_name,
                    "reason": "content_mismatch",
                    "expected_path": str(expected_path),
                    "actual_path": str(actual_path),
                }
            )
    summary = {
        "expected_dir": str(expected_dir),
        "actual_dir": str(actual_dir),
        "compared_files": [spec.file_name for spec in COUNT_TABLE_SPECS],
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
    if mismatches:
        raise ValueError("Stage 6 merged TSVs differ from baseline: " + json.dumps(summary, sort_keys=True))
    return summary


def _read_stage6_summary(stage6_dir: Path) -> dict[str, Any]:
    path = stage6_dir / "summary.jsonl"
    records = list(iter_jsonl(path))
    if len(records) != 1:
        raise ValueError(f"expected exactly one Stage 6 summary row: {path}")
    return records[0]


def _sum_counters(items: Any) -> Counter[str]:
    counter: Counter[str] = Counter()
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            counter[str(key)] += int(value or 0)
    return counter


def _split_pipe(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part for part in str(value).split("|") if part}


def _join_pipe_values(*values: set[str]) -> str:
    merged: set[str] = set()
    for value in values:
        merged.update(value)
    return "|".join(sorted(merged))


def _merged_pipe_field(values: list[str]) -> str:
    nonempty = [value for value in values if value]
    if not nonempty:
        return ""
    if len(set(nonempty)) == 1:
        return nonempty[0]
    merged: set[str] = set()
    for value in nonempty:
        merged.update(_split_pipe(value))
    return "|".join(sorted(merged))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with atomic_text_writer(path) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")


def _write_progress(path: Path, *, status: str, phase: str, **payload: Any) -> None:
    _write_json(
        path,
        {
            "schema_version": 1,
            "artifact_type": "stage456_sharded_progress",
            "status": status,
            "phase": phase,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            **payload,
        },
    )


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("stage456_sharded", main))
