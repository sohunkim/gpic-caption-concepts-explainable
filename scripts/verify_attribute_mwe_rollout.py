from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.attribute_units import (
    ATTRIBUTE_MWE_RULE_VERSION,
    ATTRIBUTE_UNIT_MWE,
    normalize_attribute_surface,
)
from gpic_concepts_v1.io_jsonl import iter_jsonl
from incident_gate import guarded_entrypoint


UNCHANGED_STAGE6_TABLES = (
    "object_counts.tsv",
    "object_parent_counts.tsv",
    "action_counts.tsv",
    "agent_patient_pair_counts.tsv",
    "relation_triple_counts.tsv",
    "relation_component_counts.tsv",
    "ambiguous_relation_candidate_counts.tsv",
    "object_cooccurrence_pair_counts.tsv",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify one Attribute MWE prefix rollout against its no-MWE baseline."
    )
    parser.add_argument("--baseline-stage4-dir", required=True)
    parser.add_argument("--candidate-stage4-dir", required=True)
    parser.add_argument("--candidate-stage5-dir", required=True)
    parser.add_argument("--baseline-stage6-dir", required=True)
    parser.add_argument("--candidate-stage6-dir", required=True)
    parser.add_argument("--mwe-inventory", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = verify_attribute_mwe_rollout(
        baseline_stage4_dir=Path(args.baseline_stage4_dir),
        candidate_stage4_dir=Path(args.candidate_stage4_dir),
        candidate_stage5_dir=Path(args.candidate_stage5_dir),
        baseline_stage6_dir=Path(args.baseline_stage6_dir),
        candidate_stage6_dir=Path(args.candidate_stage6_dir),
        mwe_inventory_path=Path(args.mwe_inventory),
    )
    with atomic_text_writer(Path(args.output)) as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if summary["status"] != "ok":
        raise SystemExit(
            "Attribute MWE rollout verification failed: "
            + "; ".join(summary["failures"][:20])
        )


def verify_attribute_mwe_rollout(
    *,
    baseline_stage4_dir: Path,
    candidate_stage4_dir: Path,
    candidate_stage5_dir: Path,
    baseline_stage6_dir: Path,
    candidate_stage6_dir: Path,
    mwe_inventory_path: Path,
) -> dict[str, Any]:
    failures: list[str] = []
    print("verify_phase=stage6_non_attribute_tables", flush=True)
    unchanged_tables: dict[str, bool] = {}
    for name in UNCHANGED_STAGE6_TABLES:
        baseline = baseline_stage6_dir / name
        candidate = candidate_stage6_dir / name
        equal = _sha256(baseline) == _sha256(candidate)
        unchanged_tables[name] = equal
        if not equal:
            failures.append(f"non-attribute Stage 6 table changed: {name}")

    print("verify_phase=resolve_stage4_stage5_paths", flush=True)
    baseline_mention_paths = _stage_jsonl_paths(
        baseline_stage4_dir,
        stage_name="stage4",
        file_name="raw_mentions.jsonl",
    )
    candidate_mention_paths = _stage_jsonl_paths(
        candidate_stage4_dir,
        stage_name="stage4",
        file_name="raw_mentions.jsonl",
    )
    baseline_edge_paths = _stage_jsonl_paths(
        baseline_stage4_dir,
        stage_name="stage4",
        file_name="raw_edges.jsonl",
    )
    candidate_edge_paths = _stage_jsonl_paths(
        candidate_stage4_dir,
        stage_name="stage4",
        file_name="raw_edges.jsonl",
    )
    candidate_stage5_paths = _stage_jsonl_paths(
        candidate_stage5_dir,
        stage_name="stage5",
        file_name="canonical_mentions.jsonl",
    )

    print("verify_phase=scan_stage4_mentions", flush=True)
    baseline_mention_type_counts = _jsonl_type_counts(
        baseline_mention_paths,
        key="mention_type",
    )
    (
        candidate_mention_type_counts,
        mwe_mentions_by_key,
        actual_counts,
    ) = _collect_candidate_mwe_mentions(
        candidate_mention_paths,
        failures=failures,
    )
    _compare_stage4_type_counters(
        baseline_mention_type_counts,
        candidate_mention_type_counts,
        excluded_type="attribute",
        label="mention",
        failures=failures,
    )

    print("verify_phase=scan_stage4_edges", flush=True)
    baseline_edge_type_counts = _jsonl_type_counts(
        baseline_edge_paths,
        key="edge_type",
    )
    candidate_edge_type_counts, edge_counts_by_target = _collect_candidate_edges(
        candidate_edge_paths,
        mwe_keys=set(mwe_mentions_by_key),
    )
    _compare_stage4_type_counters(
        baseline_edge_type_counts,
        candidate_edge_type_counts,
        excluded_type="has_attribute",
        label="edge",
        failures=failures,
    )

    print("verify_phase=compare_quantity_tables", flush=True)
    quantity_tables_equal = {
        "attribute_counts.tsv": _normalized_quantity_rows(
            unified_path=baseline_stage6_dir / "attribute_counts.tsv",
            legacy_path=baseline_stage6_dir / "quantity_counts.tsv",
            pair=False,
        )
        == _normalized_quantity_rows(
            unified_path=candidate_stage6_dir / "attribute_counts.tsv",
            legacy_path=candidate_stage6_dir / "quantity_counts.tsv",
            pair=False,
        ),
        "object_attribute_pair_counts.tsv": _normalized_quantity_rows(
            unified_path=baseline_stage6_dir / "object_attribute_pair_counts.tsv",
            legacy_path=baseline_stage6_dir / "object_quantity_pair_counts.tsv",
            pair=True,
        )
        == _normalized_quantity_rows(
            unified_path=candidate_stage6_dir / "object_attribute_pair_counts.tsv",
            legacy_path=candidate_stage6_dir / "object_quantity_pair_counts.tsv",
            pair=True,
        ),
    }
    for name, equal in quantity_tables_equal.items():
        if not equal:
            failures.append(f"quantity rows changed in {name}")

    all_inventory_rows = [
        row
        for row in _read_tsv(mwe_inventory_path)
        if row.get("attribute_unit_type", "").strip() == ATTRIBUTE_UNIT_MWE
    ]
    inventory_rows = [
        row
        for row in all_inventory_rows
        if row.get("decision_status", "").strip() == "chosen"
    ]
    for row in all_inventory_rows:
        status = row.get("decision_status", "").strip()
        span = normalize_attribute_surface(row.get("span_key", ""))
        if status not in {"chosen", "excluded"}:
            failures.append(f"MWE inventory row is unresolved: {span}")
        if status == "excluded" and (
            row.get("selected_oewn_synset", "").strip()
            or row.get("canonical_surface", "").strip()
        ):
            failures.append(f"excluded MWE retains lexical selection: {span}")
    expected_counts = {
        normalize_attribute_surface(row.get("span_key", "")): int(
            row.get("count", "") or 0
        )
        for row in inventory_rows
    }
    canonical_by_span = {
        normalize_attribute_surface(row.get("span_key", "")): normalize_attribute_surface(
            row.get("canonical_surface", "")
        )
        for row in inventory_rows
    }
    for row in inventory_rows:
        span = normalize_attribute_surface(row.get("span_key", ""))
        if row.get("attribute_mwe_rule_version", "") != ATTRIBUTE_MWE_RULE_VERSION:
            failures.append(f"MWE inventory rule version mismatch: {span}")
        if not canonical_by_span[span]:
            failures.append(f"MWE inventory canonical is empty: {span}")

    if dict(actual_counts) != expected_counts:
        deltas = [
            (
                key,
                expected_counts.get(key, 0),
                actual_counts.get(key, 0),
                actual_counts.get(key, 0) - expected_counts.get(key, 0),
            )
            for key in sorted(set(expected_counts) | set(actual_counts))
            if expected_counts.get(key, 0) != actual_counts.get(key, 0)
        ]
        top_deltas = sorted(
            deltas,
            key=lambda item: (-abs(item[3]), item[0]),
        )[:20]
        failures.append(
            "MWE mention counts do not match prefix inventory: "
            f"delta_rows={len(deltas)} total_delta="
            f"{sum(item[3] for item in deltas)} top_deltas={top_deltas}"
        )

    for key, mention in mwe_mentions_by_key.items():
        detail = _source_detail(mention)
        unattached = detail.get("modifier_source") == "tag_list_unattached_attribute_mwe"
        expected_edges = 0 if unattached else 1
        if edge_counts_by_target[key] != expected_edges:
            failures.append(
                f"MWE edge count mismatch: key={key} "
                f"expected={expected_edges} actual={edge_counts_by_target[key]}"
            )

    print("verify_phase=scan_stage5_canonical_mentions", flush=True)
    _validate_candidate_mwe_canonical_mentions(
        candidate_stage5_paths,
        mwe_mentions_by_key=mwe_mentions_by_key,
        canonical_by_span=canonical_by_span,
        failures=failures,
    )

    return {
        "status": "ok" if not failures else "failed",
        "failures": failures,
        "mwe_inventory_rows": len(inventory_rows),
        "mwe_mention_counts": dict(sorted(actual_counts.items())),
        "unchanged_stage6_tables": unchanged_tables,
        "quantity_rows_unchanged": quantity_tables_equal,
        "baseline_stage4_mention_type_counts": dict(
            sorted(baseline_mention_type_counts.items())
        ),
        "candidate_stage4_mention_type_counts": dict(
            sorted(candidate_mention_type_counts.items())
        ),
        "baseline_stage4_edge_type_counts": dict(
            sorted(baseline_edge_type_counts.items())
        ),
        "candidate_stage4_edge_type_counts": dict(
            sorted(candidate_edge_type_counts.items())
        ),
    }


def _stage_jsonl_paths(
    root: Path,
    *,
    stage_name: str,
    file_name: str,
) -> tuple[Path, ...]:
    for direct in (root / file_name, root / stage_name / file_name):
        if direct.is_file():
            return (direct,)
    sharded = tuple(
        sorted((root / "shards").glob(f"shard_*/{stage_name}/{file_name}"))
    )
    if sharded:
        return sharded
    raise FileNotFoundError(
        f"missing {stage_name} {file_name} under direct or sharded root: {root}"
    )


def _jsonl_type_counts(paths: Iterable[Path], *, key: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in paths:
        for row in iter_jsonl(path):
            counts[str(row.get(key, ""))] += 1
    return counts


def _collect_candidate_mwe_mentions(
    paths: Iterable[Path],
    *,
    failures: list[str],
) -> tuple[
    Counter[str],
    dict[tuple[str, str], Mapping[str, Any]],
    Counter[str],
]:
    type_counts: Counter[str] = Counter()
    mwe_mentions_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    mwe_counts: Counter[str] = Counter()

    for path in paths:
        current_caption_id: str | None = None
        caption_attributes: list[Mapping[str, Any]] = []
        for row in iter_jsonl(path):
            type_counts[str(row.get("mention_type", ""))] += 1
            caption_id = str(row.get("caption_id", ""))
            if current_caption_id is not None and caption_id != current_caption_id:
                _check_caption_mwe_overlaps(
                    caption_attributes,
                    failures=failures,
                )
                caption_attributes = []
            current_caption_id = caption_id

            if row.get("mention_type") != "attribute":
                continue
            caption_attributes.append(row)
            if _source_detail(row).get("attribute_unit_type") != ATTRIBUTE_UNIT_MWE:
                continue
            key = (caption_id, str(row.get("mention_id", "")))
            if key in mwe_mentions_by_key:
                failures.append(f"duplicate MWE mention key: {key}")
                continue
            mwe_mentions_by_key[key] = row
            inventory_key = normalize_attribute_surface(
                str(_source_detail(row).get("inventory_span_key", ""))
            )
            if not inventory_key:
                failures.append(f"MWE mention is missing inventory_span_key: {key}")
                continue
            mwe_counts[inventory_key] += 1

        if caption_attributes:
            _check_caption_mwe_overlaps(
                caption_attributes,
                failures=failures,
            )

    return type_counts, mwe_mentions_by_key, mwe_counts


def _check_caption_mwe_overlaps(
    caption_attributes: list[Mapping[str, Any]],
    *,
    failures: list[str],
) -> None:
    for mention in caption_attributes:
        if _source_detail(mention).get("attribute_unit_type") != ATTRIBUTE_UNIT_MWE:
            continue
        _check_no_internal_single(
            mention,
            candidate_mentions=caption_attributes,
            failures=failures,
        )


def _collect_candidate_edges(
    paths: Iterable[Path],
    *,
    mwe_keys: set[tuple[str, str]],
) -> tuple[Counter[str], Counter[tuple[str, str]]]:
    type_counts: Counter[str] = Counter()
    edge_counts_by_target: Counter[tuple[str, str]] = Counter()
    for path in paths:
        for row in iter_jsonl(path):
            edge_type = str(row.get("edge_type", ""))
            type_counts[edge_type] += 1
            if edge_type != "has_attribute":
                continue
            key = (
                str(row.get("caption_id", "")),
                str(row.get("target_mention_id", "")),
            )
            if key in mwe_keys:
                edge_counts_by_target[key] += 1
    return type_counts, edge_counts_by_target


def _validate_candidate_mwe_canonical_mentions(
    paths: Iterable[Path],
    *,
    mwe_mentions_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    canonical_by_span: Mapping[str, str],
    failures: list[str],
) -> None:
    seen: set[tuple[str, str]] = set()
    for path in paths:
        for canonical in iter_jsonl(path):
            key = (
                str(canonical.get("caption_id", "")),
                str(canonical.get("mention_id", "")),
            )
            mention = mwe_mentions_by_key.get(key)
            if mention is None:
                continue
            seen.add(key)
            span = normalize_attribute_surface(
                str(_source_detail(mention).get("inventory_span_key", ""))
            )
            if canonical.get("canonical_source") != "lexicon":
                failures.append(f"MWE did not use Stage 5 lexicon: {span}")
            actual_canonical = normalize_attribute_surface(
                str(canonical.get("canonical", ""))
            )
            if actual_canonical != canonical_by_span.get(span, ""):
                failures.append(
                    f"MWE canonical mismatch: {span} -> {actual_canonical} / "
                    f"{canonical_by_span.get(span, '')}"
                )
    missing = set(mwe_mentions_by_key) - seen
    if missing:
        failures.append(f"MWE canonical mentions missing: {len(missing)}")


def _compare_stage4_type_counters(
    baseline: Counter[str],
    candidate: Counter[str],
    *,
    excluded_type: str,
    label: str,
    failures: list[str],
) -> None:
    baseline_without_excluded = Counter(
        {key: value for key, value in baseline.items() if key != excluded_type}
    )
    candidate_without_excluded = Counter(
        {key: value for key, value in candidate.items() if key != excluded_type}
    )
    if baseline_without_excluded != candidate_without_excluded:
        failures.append(
            f"non-attribute Stage 4 {label} counts changed: "
            f"baseline={dict(baseline_without_excluded)} "
            f"candidate={dict(candidate_without_excluded)}"
        )


def _compare_stage4_type_counts(
    baseline_rows: Iterable[Mapping[str, Any]],
    candidate_rows: Iterable[Mapping[str, Any]],
    *,
    excluded_type: str,
    key: str,
    label: str,
    failures: list[str],
) -> None:
    baseline = Counter(
        str(row.get(key, ""))
        for row in baseline_rows
        if row.get(key) != excluded_type
    )
    candidate = Counter(
        str(row.get(key, ""))
        for row in candidate_rows
        if row.get(key) != excluded_type
    )
    if baseline != candidate:
        failures.append(
            f"non-attribute Stage 4 {label} counts changed: "
            f"baseline={dict(baseline)} candidate={dict(candidate)}"
        )


def _check_no_internal_single(
    mwe: Mapping[str, Any],
    *,
    candidate_mentions: Iterable[Mapping[str, Any]],
    failures: list[str],
) -> None:
    caption_id = str(mwe.get("caption_id", ""))
    start = int(mwe.get("token_start") or 0)
    end = int(mwe.get("token_end") or 0)
    for other in candidate_mentions:
        if other is mwe or other.get("mention_type") != "attribute":
            continue
        if str(other.get("caption_id", "")) != caption_id:
            continue
        other_start = int(other.get("token_start") or 0)
        other_end = int(other.get("token_end") or 0)
        if max(start, other_start) < min(end, other_end):
            failures.append(
                "single attribute overlaps selected MWE: "
                f"caption={caption_id} mwe={mwe.get('text', '')!r} "
                f"other={other.get('text', '')!r}"
            )


def _normalized_quantity_rows(
    *,
    unified_path: Path,
    legacy_path: Path,
    pair: bool,
) -> list[tuple[tuple[str, str], ...]]:
    unified_rows = _read_tsv(unified_path)
    if unified_rows and "attribute_kind" in unified_rows[0]:
        rows = [
            _normalized_quantity_row(row, pair=pair, legacy=False)
            for row in unified_rows
            if row.get("attribute_kind", "") == "quantity"
        ]
    else:
        rows = [
            _normalized_quantity_row(row, pair=pair, legacy=True)
            for row in _read_tsv(legacy_path)
        ]
    return sorted(rows)


def _normalized_quantity_row(
    row: Mapping[str, str],
    *,
    pair: bool,
    legacy: bool,
) -> tuple[tuple[str, str], ...]:
    normalized = {
        "attribute": row.get("quantity", "") if legacy else row.get("attribute", ""),
        "count": row.get("count", ""),
        "caption_count": row.get("caption_count", ""),
        "example_caption_ids": row.get("example_caption_ids", ""),
        "raw_variants": row.get("raw_variants", ""),
        "rule_ids": row.get("rule_ids", ""),
    }
    if pair:
        normalized.update(
            {
                "object": row.get("object", ""),
                "object_parent_concepts": row.get("object_parent_concepts", ""),
                "object_parent_synset_ids": row.get("object_parent_synset_ids", ""),
            }
        )
    return tuple(sorted(normalized.items()))


def _source_detail(row: Mapping[str, Any]) -> Mapping[str, Any]:
    detail = row.get("source_detail")
    return detail if isinstance(detail, Mapping) else {}


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("verify_attribute_mwe_rollout", main))
