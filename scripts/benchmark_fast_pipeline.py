from __future__ import annotations

import argparse
import csv
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
import subprocess
from time import perf_counter
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gpic_concepts_v1.io_jsonl import iter_jsonl
from gpic_concepts_v1.stage3_annotate import (
    DEFAULT_STAGE3_BATCH_SIZE,
    DEFAULT_STAGE3_MODEL,
    Stage3Timing,
    iter_annotated_docs_from_rows,
    iter_stage3_records_from_rows,
    make_stage3_nlp,
)
from gpic_concepts_v1.stage4_extract_raw import (
    extract_raw_concepts_from_doc,
    extract_raw_concepts_from_stage3_record,
    load_gpic_attribute_mwe_inventory,
    load_gpic_object_inventory,
)
from gpic_concepts_v1.stage5_canonicalize import (
    canonicalize_raw_graph,
    load_stage5_lexicons,
)
from gpic_concepts_v1.stage6_export_counts import export_count_facts


_NVIDIA_SMI_QUERY_FIELDS = (
    "name",
    "driver_version",
    "pstate",
    "power.draw",
    "power.limit",
    "utilization.gpu",
    "memory.used",
    "memory.total",
    "temperature.gpu",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark the v1 sentence pipeline in one process without writing "
            "intermediate Stage 3-5 JSONL files."
        ),
    )
    parser.add_argument("--input", required=True, help="Stage 1 sentence_rows.jsonl")
    parser.add_argument(
        "--lexicon-dir",
        default="resources/lexicons",
        help="Directory containing Stage 5 TSV lexicons.",
    )
    parser.add_argument(
        "--summary",
        help="Optional path for one JSON summary file.",
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
        "--limit",
        type=int,
        help="Optional maximum number of sentence rows to process.",
    )
    parser.add_argument(
        "--length-bucket-size",
        type=int,
        default=0,
        help=(
            "If greater than 0, sort rows by caption length within this many-row "
            "buffer before Stage 3 batching. This only changes benchmark scheduling."
        ),
    )
    parser.add_argument(
        "--raw-extraction-mode",
        choices=("stage3-record", "doc-direct"),
        default="stage3-record",
        help=(
            "stage3-record preserves the original Stage 3 evidence-table round trip. "
            "doc-direct applies the same Stage 4 rules directly to annotated spaCy Docs."
        ),
    )
    parser.add_argument(
        "--object-inventory",
        help=(
            "GPIC-observed object span inventory TSV for Stage 4. "
            "Do not pass COCO/LVIS/Objects365/OpenImages/Visual Genome source-label files here."
        ),
    )
    parser.add_argument(
        "--attribute-inventory",
        help="Resolved GPIC-observed attribute inventory TSV for Stage 4 MWE matching.",
    )
    parser.add_argument(
        "--allow-runtime-oewn-lookup",
        action="store_true",
        help="Probe/debug mode only: run Stage 4 with live OEWN lookup instead of GPIC inventory.",
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
    return parser.parse_args()


def _caption_length(row: Mapping[str, object]) -> int:
    caption = row.get("caption", "")
    return len(caption) if isinstance(caption, str) else 0


def _iter_length_bucketed_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    bucket_size: int,
    limit: int | None = None,
) -> Iterator[Mapping[str, object]]:
    if bucket_size < 1:
        raise ValueError("bucket_size must be greater than zero")

    bucket: list[Mapping[str, object]] = []
    accepted = 0
    for row in rows:
        if limit is not None and accepted >= limit:
            break
        bucket.append(row)
        accepted += 1
        if len(bucket) >= bucket_size:
            yield from sorted(bucket, key=_caption_length)
            bucket.clear()

    if bucket:
        yield from sorted(bucket, key=_caption_length)


def _parse_optional_float(value: str) -> float | None:
    text = value.strip()
    if not text or text.upper() in {"N/A", "[N/A]"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_nvidia_smi_csv(stdout: str) -> dict[str, object]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {}
    values = next(csv.reader([lines[0]]))
    if len(values) < len(_NVIDIA_SMI_QUERY_FIELDS):
        return {}
    row = {field: values[index].strip() for index, field in enumerate(_NVIDIA_SMI_QUERY_FIELDS)}
    return {
        "gpu_name": row["name"],
        "driver_version": row["driver_version"],
        "pstate": row["pstate"],
        "power_draw_w": _parse_optional_float(row["power.draw"]),
        "power_limit_w": _parse_optional_float(row["power.limit"]),
        "utilization_gpu_percent": _parse_optional_float(row["utilization.gpu"]),
        "memory_used_mib": _parse_optional_float(row["memory.used"]),
        "memory_total_mib": _parse_optional_float(row["memory.total"]),
        "temperature_gpu_c": _parse_optional_float(row["temperature.gpu"]),
    }


def _read_nvidia_smi_metadata() -> dict[str, object]:
    metadata: dict[str, object] = {
        "available": False,
        "query_fields": list(_NVIDIA_SMI_QUERY_FIELDS),
    }
    query_command = [
        "nvidia-smi",
        f"--query-gpu={','.join(_NVIDIA_SMI_QUERY_FIELDS)}",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            query_command,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        return metadata

    metadata["query_returncode"] = completed.returncode
    if completed.returncode == 0:
        parsed = _parse_nvidia_smi_csv(completed.stdout)
        if parsed:
            metadata.update(parsed)
            metadata["available"] = True
    else:
        metadata["stderr"] = completed.stderr.strip()

    try:
        table = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return metadata

    if table.returncode != 0:
        return metadata
    metadata["available"] = True
    cuda_match = re.search(r"CUDA Version:\s*([0-9.]+)", table.stdout)
    if cuda_match:
        metadata["cuda_version"] = cuda_match.group(1)
    if metadata.get("power_limit_w") is None:
        power_match = re.search(r"([0-9.]+)W\s*/\s*([0-9.]+)W", table.stdout)
        if power_match:
            metadata["power_draw_w"] = float(power_match.group(1))
            metadata["power_limit_w"] = float(power_match.group(2))
            metadata["power_limit_source"] = "nvidia-smi-table-fallback"
    return metadata


def main() -> None:
    args = parse_args()
    if args.length_bucket_size < 0:
        raise ValueError("--length-bucket-size must be zero or greater")
    gpu_mode = "require" if args.require_gpu else "prefer" if args.prefer_gpu else "none"

    setup_start = perf_counter()
    nlp = make_stage3_nlp(args.model, gpu_mode=gpu_mode)
    lexicons = load_stage5_lexicons(args.lexicon_dir)
    if args.object_inventory:
        object_lookup = load_gpic_object_inventory(args.object_inventory)
        object_lookup_mode = "gpic_observed_inventory"
    elif args.allow_runtime_oewn_lookup:
        object_lookup = None
        object_lookup_mode = "runtime_oewn_probe"
    else:
        raise SystemExit(
            "--object-inventory is required for benchmark Stage 4 extraction. "
            "Use --allow-runtime-oewn-lookup only for OEWN probe/debug runs."
        )
    if not args.attribute_inventory:
        raise SystemExit("--attribute-inventory is required for benchmark Stage 4 extraction.")
    attribute_mwe_lookup = load_gpic_attribute_mwe_inventory(
        args.attribute_inventory
    )
    setup_seconds = perf_counter() - setup_start

    gpu_metadata_start = perf_counter()
    nvidia_smi = _read_nvidia_smi_metadata()
    nvidia_smi_seconds = perf_counter() - gpu_metadata_start

    stage2_to_stage4_start = perf_counter()
    stage3_timing = Stage3Timing()
    stage4_seconds = 0.0
    raw_mentions = []
    raw_edges = []
    sentence_count = 0
    rows = iter_jsonl(args.input)
    stage3_limit = args.limit
    if args.length_bucket_size:
        rows = _iter_length_bucketed_rows(
            rows,
            bucket_size=args.length_bucket_size,
            limit=args.limit,
        )
        stage3_limit = None
    if args.raw_extraction_mode == "doc-direct":
        for annotated in iter_annotated_docs_from_rows(
            rows,
            nlp=nlp,
            batch_size=args.batch_size,
            limit=stage3_limit,
            timing=stage3_timing,
        ):
            sentence_count += 1
            stage4_start = perf_counter()
            result = extract_raw_concepts_from_doc(
                annotated.caption_id,
                annotated.doc,
                object_lookup=object_lookup,
                attribute_mwe_lookup=attribute_mwe_lookup,
            )
            stage4_seconds += perf_counter() - stage4_start
            raw_mentions.extend(result.raw_mentions)
            raw_edges.extend(result.raw_edges)
    else:
        for stage3_record in iter_stage3_records_from_rows(
            rows,
            nlp=nlp,
            batch_size=args.batch_size,
            limit=stage3_limit,
            timing=stage3_timing,
        ):
            sentence_count += 1
            stage4_start = perf_counter()
            result = extract_raw_concepts_from_stage3_record(
                stage3_record.to_dict(),
                object_lookup=object_lookup,
                attribute_mwe_lookup=attribute_mwe_lookup,
            )
            stage4_seconds += perf_counter() - stage4_start
            raw_mentions.extend(result.raw_mentions)
            raw_edges.extend(result.raw_edges)
    stage2_to_stage4_seconds = perf_counter() - stage2_to_stage4_start
    stage2_seconds = stage3_timing.stage2_seconds
    stage3_seconds = stage3_timing.stage3_seconds
    stage2_stage3_seconds = stage2_seconds + stage3_seconds
    stage2_to_stage4_overhead_seconds = (
        stage2_to_stage4_seconds - stage2_stage3_seconds - stage4_seconds
    )

    stage5_start = perf_counter()
    canonical = canonicalize_raw_graph(raw_mentions, raw_edges, lexicons=lexicons)
    stage5_seconds = perf_counter() - stage5_start

    stage6_start = perf_counter()
    counts = export_count_facts(canonical.canonical_mentions, canonical.canonical_edges)
    stage6_seconds = perf_counter() - stage6_start

    processing_seconds = stage2_to_stage4_seconds + stage5_seconds + stage6_seconds
    total_seconds = setup_seconds + processing_seconds
    table_row_counts = {
        file_name: len(rows)
        for file_name, rows in sorted(counts.count_tables.items())
    }
    fact_type_counts: dict[str, int] = {}
    for fact in counts.facts:
        fact_type_counts[fact.fact_type] = fact_type_counts.get(fact.fact_type, 0) + 1

    summary = {
        "input_path": args.input,
        "sentence_count": sentence_count,
        "model": args.model,
        "batch_size": args.batch_size,
        "length_bucket_size": args.length_bucket_size,
        "length_bucketing_enabled": bool(args.length_bucket_size),
        "raw_extraction_mode": args.raw_extraction_mode,
        "object_lookup_mode": object_lookup_mode,
        "object_inventory": args.object_inventory,
        "gpu_mode": nlp.meta.get("gpic_gpu_mode", gpu_mode),
        "gpu_enabled": bool(nlp.meta.get("gpic_gpu_enabled", False)),
        "gpu_name": nvidia_smi.get("gpu_name"),
        "gpu_driver_version": nvidia_smi.get("driver_version"),
        "gpu_cuda_version": nvidia_smi.get("cuda_version"),
        "gpu_power_limit_w": nvidia_smi.get("power_limit_w"),
        "gpu_power_draw_w": nvidia_smi.get("power_draw_w"),
        "gpu_pstate": nvidia_smi.get("pstate"),
        "nvidia_smi_seconds": nvidia_smi_seconds,
        "nvidia_smi": nvidia_smi,
        "raw_mention_total": len(raw_mentions),
        "raw_edge_total": len(raw_edges),
        "canonical_mention_total": len(canonical.canonical_mentions),
        "canonical_edge_total": len(canonical.canonical_edges),
        "fact_total": len(counts.facts),
        "fact_type_counts": dict(sorted(fact_type_counts.items())),
        "table_row_counts": table_row_counts,
        "setup_seconds": setup_seconds,
        "stage2_seconds": stage2_seconds,
        "stage3_seconds": stage3_seconds,
        "stage2_stage3_seconds": stage2_stage3_seconds,
        "stage4_seconds": stage4_seconds,
        "stage2_to_stage4_seconds": stage2_to_stage4_seconds,
        "stage2_to_stage4_overhead_seconds": stage2_to_stage4_overhead_seconds,
        "stage2_doc_count": stage3_timing.stage2_doc_count,
        "stage3_doc_count": stage3_timing.stage3_doc_count,
        "stage3_batch_count": stage3_timing.stage3_batch_count,
        "stage5_seconds": stage5_seconds,
        "stage6_seconds": stage6_seconds,
        "processing_seconds": processing_seconds,
        "total_seconds": total_seconds,
        "processing_captions_per_second": (
            sentence_count / processing_seconds if processing_seconds else 0.0
        ),
        "total_captions_per_second": (
            sentence_count / total_seconds if total_seconds else 0.0
        ),
    }

    if args.summary:
        summary_path = Path(args.summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
