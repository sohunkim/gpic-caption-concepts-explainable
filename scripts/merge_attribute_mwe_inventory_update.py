from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import csv
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from build_gpic_observed_attribute_inventory import FIELDNAMES
from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.attribute_units import (
    ATTRIBUTE_MWE_RULE_VERSION,
    ATTRIBUTE_UNIT_MWE,
    ATTRIBUTE_UNIT_SINGLE_TOKEN,
    inventory_attribute_key,
    inventory_attribute_unit_type,
    normalize_attribute_surface,
)
from incident_gate import guarded_entrypoint


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Upsert validated attribute MWE rows into a cumulative attribute "
            "inventory while preserving every existing single-token row."
        )
    )
    parser.add_argument("--base-inventory", required=True)
    parser.add_argument(
        "--mwe-update",
        help=(
            "Validated MWE update TSV. Omit it to migrate only the base "
            "inventory to the current Attribute MWE schema for a baseline run."
        ),
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_rows = _read_tsv(Path(args.base_inventory))
    update_rows = _read_tsv(Path(args.mwe_update)) if args.mwe_update else []
    merged, summary = merge_attribute_mwe_inventory_rows(base_rows, update_rows)
    _write_tsv(Path(args.output), merged)
    summary.update(
        {
            "base_inventory": args.base_inventory,
            "mwe_update": args.mwe_update or None,
            "output": args.output,
        }
    )
    if args.summary:
        with atomic_text_writer(Path(args.summary)) as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def merge_attribute_mwe_inventory_rows(
    base_rows: Sequence[Mapping[str, str]],
    update_rows: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    normalized_base = [_normalize_inventory_row(row) for row in base_rows]
    normalized_input_updates = [_normalize_inventory_row(row) for row in update_rows]
    for row in normalized_input_updates:
        key = inventory_attribute_key(row)
        if key[0] != ATTRIBUTE_UNIT_MWE:
            raise ValueError(f"MWE update contains non-MWE row: {key}")
    unsupported_statuses = sorted(
        {
            row.get("decision_status", "").strip()
            for row in normalized_input_updates
            if row.get("decision_status", "").strip() not in {"chosen", "excluded"}
        }
    )
    if unsupported_statuses:
        raise ValueError(
            "MWE update contains unresolved or unsupported statuses: "
            + ", ".join(repr(status) for status in unsupported_statuses)
        )
    normalized_updates = normalized_input_updates
    base_by_key = _unique_rows(normalized_base, "base inventory")
    update_by_key = _unique_rows(normalized_updates, "MWE update")

    for key, row in update_by_key.items():
        if key[0] != ATTRIBUTE_UNIT_MWE:
            raise ValueError(f"MWE update contains non-MWE row: {key}")
        _validate_publishable_mwe(row)

    merged: list[dict[str, str]] = []
    replaced_keys: set[tuple[str, str]] = set()
    for row in normalized_base:
        key = inventory_attribute_key(row)
        replacement = update_by_key.get(key)
        if replacement is None:
            merged.append(row)
            continue
        merged.append(replacement)
        replaced_keys.add(key)

    additions = [
        row
        for key, row in update_by_key.items()
        if key not in base_by_key
    ]
    additions.sort(key=lambda row: (-int(row.get("count", "0") or 0), row["span_key"]))
    merged.extend(additions)

    single_token_rows_preserved = sum(
        inventory_attribute_unit_type(row) == ATTRIBUTE_UNIT_SINGLE_TOKEN
        for row in merged
    )
    if single_token_rows_preserved != sum(
        inventory_attribute_unit_type(row) == ATTRIBUTE_UNIT_SINGLE_TOKEN
        for row in normalized_base
    ):
        raise ValueError("attribute MWE merge changed the single-token row count")
    _assert_single_token_field_values_preserved(
        original_base_rows=base_rows,
        merged_rows=merged,
    )

    return merged, {
        "status": "complete",
        "base_rows": len(base_rows),
        "input_update_rows": len(update_rows),
        "update_rows": len(normalized_updates),
        "chosen_update_rows": sum(
            row.get("decision_status", "").strip() == "chosen"
            for row in normalized_updates
        ),
        "excluded_update_rows": sum(
            row.get("decision_status", "").strip() == "excluded"
            for row in normalized_updates
        ),
        "added_mwe_rows": len(additions),
        "replaced_mwe_rows": len(replaced_keys),
        "preserved_existing_rows": len(base_rows) - len(replaced_keys),
        "merged_rows": len(merged),
        "single_token_rows_preserved": single_token_rows_preserved,
        "single_token_field_values_preserved": True,
        "mwe_rows_after_merge": sum(
            inventory_attribute_unit_type(row) == ATTRIBUTE_UNIT_MWE
            for row in merged
        ),
        "count_semantics": "prefix_rescan_replacement_not_addition",
    }


def _normalize_inventory_row(row: Mapping[str, str]) -> dict[str, str]:
    normalized = {field: str(row.get(field, "")) for field in FIELDNAMES}
    unit_type = inventory_attribute_unit_type(row)
    normalized["attribute_unit_type"] = unit_type
    normalized["span_key"] = (
        normalized.get("span_key", "")
        or normalize_attribute_surface(normalized.get("observed_surface", ""))
    )
    if unit_type == ATTRIBUTE_UNIT_SINGLE_TOKEN:
        normalized["span_token_count"] = normalized["span_token_count"] or "1"
        normalized["anchor_token_offset"] = normalized["anchor_token_offset"] or "0"
        normalized["lookup_forms"] = normalized["lookup_forms"] or normalized["span_key"]
    normalized["attribute_mwe_rule_version"] = ATTRIBUTE_MWE_RULE_VERSION
    return normalized


def _assert_single_token_field_values_preserved(
    *,
    original_base_rows: Sequence[Mapping[str, str]],
    merged_rows: Sequence[Mapping[str, str]],
) -> None:
    merged_by_key = {
        inventory_attribute_key(row): row
        for row in merged_rows
    }
    structural_fields = {
        "attribute_unit_type",
        "span_token_count",
        "anchor_token_offset",
        "lookup_forms",
        "attribute_mwe_rule_version",
    }
    for source in original_base_rows:
        if inventory_attribute_unit_type(source) != ATTRIBUTE_UNIT_SINGLE_TOKEN:
            continue
        key = inventory_attribute_key(source)
        merged = merged_by_key.get(key)
        if merged is None:
            raise ValueError(f"attribute MWE merge removed a single-token row: {key}")
        for field, value in source.items():
            if field in structural_fields:
                continue
            if str(merged.get(field, "")) != str(value):
                raise ValueError(
                    "attribute MWE merge changed a single-token field: "
                    f"key={key} field={field!r} "
                    f"before={value!r} after={merged.get(field, '')!r}"
                )


def _unique_rows(
    rows: Sequence[Mapping[str, str]],
    label: str,
) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    for source in rows:
        row = dict(source)
        key = inventory_attribute_key(row)
        if not key[1]:
            raise ValueError(f"{label} contains an empty attribute key")
        if key in result:
            raise ValueError(f"{label} contains duplicate attribute key: {key}")
        result[key] = row
    return result


def _validate_publishable_mwe(row: Mapping[str, str]) -> None:
    key = inventory_attribute_key(row)
    status = row.get("decision_status", "").strip()
    if status == "excluded":
        if row.get("selected_oewn_synset", "").strip():
            raise ValueError(f"excluded attribute MWE retains a selected synset: {key}")
        if row.get("canonical_surface", "").strip():
            raise ValueError(f"excluded attribute MWE retains a canonical surface: {key}")
        return
    if status != "chosen":
        raise ValueError(f"attribute MWE is not final: {key}")
    if not row.get("selected_oewn_synset", "").strip():
        raise ValueError(f"attribute MWE is missing selected synset: {key}")
    if not row.get("canonical_surface", "").strip():
        raise ValueError(f"attribute MWE is missing canonical surface: {key}")
    if row.get("canonical_selection_tag", "").startswith("ambiguous"):
        raise ValueError(f"attribute MWE canonical is ambiguous: {key}")
    if row.get("attribute_mwe_rule_version", "") != ATTRIBUTE_MWE_RULE_VERSION:
        raise ValueError(f"attribute MWE rule version mismatch: {key}")
    if int(row.get("span_token_count", "") or 0) < 2:
        raise ValueError(f"attribute MWE token count is invalid: {key}")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def _write_tsv(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=FIELDNAMES,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(
            {field: str(row.get(field, "")) for field in FIELDNAMES}
            for row in rows
        )


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("merge_attribute_mwe_inventory_update", main))
