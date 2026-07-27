from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.attribute_units import inventory_attribute_key


CANONICAL_FIELDS = frozenset(
    (
        "canonical_surface",
        "canonical_label_key",
        "canonical_selection_tag",
        "canonical_candidate_lemmas",
        "canonical_candidate_lemma_counts",
        "google_ngram_candidate_surfaces",
        "google_ngram_candidate_mean_frequencies",
    )
)
SELECTED_SYNSET_FIELDS = frozenset(
    (
        "selected_oewn_synset",
        "selected_oewn_lexfile",
        "synset_lemmas",
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Overlay resolved attribute manual decisions onto a full GPIC attribute inventory.",
    )
    parser.add_argument("--full-inventory", required=True)
    parser.add_argument("--resolved-subset", required=True)
    parser.add_argument("--manual-decisions")
    parser.add_argument("--output", required=True)
    parser.add_argument("--resolved-copy")
    parser.add_argument("--manual-decisions-copy")
    parser.add_argument("--summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = apply_attribute_manual_resolution(
        full_inventory_path=Path(args.full_inventory),
        resolved_subset_path=Path(args.resolved_subset),
        output_path=Path(args.output),
        manual_decisions_path=Path(args.manual_decisions) if args.manual_decisions else None,
        resolved_copy_path=Path(args.resolved_copy) if args.resolved_copy else None,
        manual_decisions_copy_path=Path(args.manual_decisions_copy)
        if args.manual_decisions_copy
        else None,
    )
    if args.summary:
        with atomic_text_writer(Path(args.summary)) as handle:
            handle.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def apply_attribute_manual_resolution(
    *,
    full_inventory_path: Path,
    resolved_subset_path: Path,
    output_path: Path,
    manual_decisions_path: Path | None = None,
    resolved_copy_path: Path | None = None,
    manual_decisions_copy_path: Path | None = None,
) -> dict[str, object]:
    full_rows, fieldnames = _read_tsv(full_inventory_path)
    resolved_rows, _ = _read_tsv(resolved_subset_path)
    if not fieldnames:
        raise ValueError(f"full inventory has no header: {full_inventory_path}")

    full_by_key = _unique_by_inventory_key(full_rows, "full inventory")
    resolved_by_key = _unique_by_inventory_key(resolved_rows, "resolved subset")
    missing = sorted(key for key in resolved_by_key if key not in full_by_key)
    if missing:
        raise ValueError(
            "resolved rows not found in full inventory: " + ", ".join(missing[:20])
        )

    needs_manual_keys = {
        inventory_attribute_key(row)
        for row in full_rows
        if row.get("decision_status", "").strip() == "needs_manual"
    }
    resolved_keys = set(resolved_by_key)
    if needs_manual_keys != resolved_keys:
        raise ValueError(
            json.dumps(
                {
                    "status": "manual_resolution_key_mismatch",
                    "extra_resolved_keys": sorted(resolved_keys - needs_manual_keys)[:20],
                    "missing_needs_manual_keys": sorted(needs_manual_keys - resolved_keys)[:20],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    fieldnames = _merged_fieldnames(fieldnames, list(resolved_rows[0].keys()) if resolved_rows else [])
    merged_rows: list[dict[str, str]] = []
    for row in full_rows:
        key = inventory_attribute_key(row)
        if key not in resolved_by_key:
            merged_rows.append({field: row.get(field, "") for field in fieldnames})
            continue
        replacement = {field: row.get(field, "") for field in fieldnames}
        resolved = resolved_by_key[key]
        resolved_status = _normalize_resolved_status(resolved.get("decision_status", ""))
        for field in fieldnames:
            if field in CANONICAL_FIELDS:
                replacement[field] = ""
                continue
            if resolved_status == "excluded" and field in SELECTED_SYNSET_FIELDS:
                replacement[field] = ""
                continue
            if field == "decision_status":
                replacement[field] = resolved_status
                continue
            replacement[field] = resolved.get(field, row.get(field, ""))
        merged_rows.append(replacement)

    _write_tsv(output_path, merged_rows, fieldnames)
    if resolved_copy_path is not None:
        _atomic_copy(resolved_subset_path, resolved_copy_path)
    if manual_decisions_path is not None and manual_decisions_copy_path is not None:
        _atomic_copy(manual_decisions_path, manual_decisions_copy_path)

    manual_decision_rows = 0
    if manual_decisions_path is not None:
        manual_decision_rows = len(_read_tsv(manual_decisions_path)[0])

    return {
        "full_inventory": str(full_inventory_path),
        "resolved_subset": str(resolved_subset_path),
        "manual_decisions": str(manual_decisions_path) if manual_decisions_path else "",
        "output": str(output_path),
        "resolved_copy": str(resolved_copy_path) if resolved_copy_path else "",
        "manual_decisions_copy": str(manual_decisions_copy_path)
        if manual_decisions_copy_path
        else "",
        "full_rows": len(full_rows),
        "resolved_rows": len(resolved_rows),
        "manual_decision_rows": manual_decision_rows,
        "overlaid_rows": len(resolved_rows),
        "original_decision_status_counts": _count_by(full_rows, "decision_status"),
        "resolved_decision_status_counts": _count_by(resolved_rows, "decision_status"),
        "merged_decision_status_counts": _count_by(merged_rows, "decision_status"),
        "merged_empty_canonical_surface_rows": sum(
            1 for row in merged_rows if not row.get("canonical_surface", "").strip()
        ),
        "merged_selected_synset_rows": sum(
            1 for row in merged_rows if row.get("selected_oewn_synset", "").strip()
        ),
    }


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


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(target.name + ".tmp")
    shutil.copyfile(source, temp_path)
    os.replace(temp_path, target)


def _normalize_resolved_status(value: str) -> str:
    status = value.strip()
    if status in {"accepted", "chosen", "selected"}:
        return "chosen"
    if status in {"excluded", "rejected"}:
        return "excluded"
    if status == "needs_manual":
        return status
    raise ValueError(f"unsupported resolved attribute decision_status: {value!r}")


def _unique_by_inventory_key(
    rows: list[dict[str, str]],
    label: str,
) -> dict[tuple[str, str], dict[str, str]]:
    by_key: dict[tuple[str, str], dict[str, str]] = {}
    duplicates: set[tuple[str, str]] = set()
    for row in rows:
        if not row.get("span_key", ""):
            raise ValueError(f"{label} has a row without span_key")
        key = inventory_attribute_key(row)
        if key in by_key:
            duplicates.add(key)
        by_key[key] = row
    if duplicates:
        raise ValueError(
            f"{label} has duplicate attribute inventory keys: {sorted(duplicates)[:20]}"
        )
    return by_key


def _merged_fieldnames(
    full_fieldnames: list[str],
    resolved_fieldnames: list[str],
) -> list[str]:
    fieldnames = list(full_fieldnames)
    for field in resolved_fieldnames:
        if field not in fieldnames:
            fieldnames.append(field)
    return fieldnames


def _count_by(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(row.get(field, "") for row in rows).items()))


if __name__ == "__main__":
    main()
