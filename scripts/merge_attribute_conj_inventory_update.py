from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
import csv
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from build_gpic_observed_attribute_inventory import (
    AttributeLookupResult,
    FIELDNAMES as ATTRIBUTE_INVENTORY_FIELDS,
    _attribute_gate_for_lexfile,
    _load_attribute_lookup_runtime,
)
from gpic_concepts_v1.atomic_io import atomic_text_writer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append audited conjunct-only attribute decisions to the current "
            "attribute inventory without modifying existing rows."
        )
    )
    parser.add_argument("--base-inventory", required=True)
    parser.add_argument("--auto-chosen", required=True)
    parser.add_argument("--manual-resolved", required=True)
    parser.add_argument("--expected-oewn-hits", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--manual-resolved-copy")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_rows, fieldnames = _read_tsv(Path(args.base_inventory))
    auto_rows, _ = _read_tsv(Path(args.auto_chosen))
    manual_rows, _ = _read_tsv(Path(args.manual_resolved))
    expected_rows, _ = _read_tsv(Path(args.expected_oewn_hits))
    runtime_lookup = _load_attribute_lookup_runtime()
    merged_rows, summary = merge_attribute_conj_inventory_rows(
        base_rows,
        auto_rows=auto_rows,
        manual_rows=manual_rows,
        expected_rows=expected_rows,
        runtime_lookup=runtime_lookup,
    )
    output = Path(args.output)
    _write_tsv(output, merged_rows, fieldnames)
    if args.manual_resolved_copy:
        _atomic_copy(Path(args.manual_resolved), Path(args.manual_resolved_copy))
    summary.update(
        {
            "base_inventory": args.base_inventory,
            "auto_chosen": args.auto_chosen,
            "manual_resolved": args.manual_resolved,
            "expected_oewn_hits": args.expected_oewn_hits,
            "output": args.output,
        }
    )
    with atomic_text_writer(Path(args.summary)) as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def merge_attribute_conj_inventory_rows(
    base_rows: Sequence[Mapping[str, str]],
    *,
    auto_rows: Sequence[Mapping[str, str]],
    manual_rows: Sequence[Mapping[str, str]],
    expected_rows: Sequence[Mapping[str, str]],
    runtime_lookup: Callable[..., AttributeLookupResult],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    base_by_key = _unique_by_key(base_rows, "base inventory")
    auto_by_key = _unique_by_key(auto_rows, "auto chosen")
    manual_by_key = _unique_by_key(manual_rows, "manual resolved")
    expected_by_key = _unique_by_key(expected_rows, "expected OEWN hits")
    overlap = set(auto_by_key) & set(manual_by_key)
    if overlap:
        raise ValueError(f"auto/manual overlap: {sorted(overlap)[:20]}")
    update_keys = set(auto_by_key) | set(manual_by_key)
    if update_keys != set(expected_by_key):
        raise ValueError(
            json.dumps(
                {
                    "status": "conj_update_key_mismatch",
                    "missing_update_keys": sorted(set(expected_by_key) - update_keys)[:20],
                    "unexpected_update_keys": sorted(update_keys - set(expected_by_key))[:20],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    existing_conflicts = sorted(update_keys & set(base_by_key))
    if existing_conflicts:
        raise ValueError(
            "conj update would overwrite existing inventory rows: "
            + ", ".join(existing_conflicts[:20])
        )

    additions: list[dict[str, str]] = []
    for key in sorted(
        update_keys,
        key=lambda item: (-int(expected_by_key[item]["mention_count"]), item),
    ):
        source = manual_by_key.get(key) or auto_by_key[key]
        is_manual = key in manual_by_key
        additions.append(
            _build_inventory_row(
                source,
                runtime_lookup=runtime_lookup,
                is_manual=is_manual,
            )
        )

    merged_rows = [
        {field: str(row.get(field, "")) for field in ATTRIBUTE_INVENTORY_FIELDS}
        for row in base_rows
    ]
    merged_rows.extend(additions)
    return merged_rows, {
        "status": "complete",
        "base_rows": len(base_rows),
        "added_rows": len(additions),
        "added_auto_rows": len(auto_rows),
        "added_manual_rows": len(manual_rows),
        "merged_rows": len(merged_rows),
        "existing_rows_modified": 0,
        "added_attribute_gate_counts": dict(
            sorted(Counter(row["attribute_gate"] for row in additions).items())
        ),
        "added_lookup_case_counts": dict(
            sorted(Counter(row["selected_lookup_case"] for row in additions).items())
        ),
    }


def _build_inventory_row(
    source: Mapping[str, str],
    *,
    runtime_lookup: Callable[..., AttributeLookupResult],
    is_manual: bool,
) -> dict[str, str]:
    key = _required(source, "span_key")
    tags = set(_split_pipe(source.get("tag_values", "")))
    lookup = runtime_lookup(
        key,
        require_surface_query_conflict_check=bool({"NNS", "NNPS"} & tags),
    )
    expected_ids = _split_pipe(_required(source, "oewn_synset_ids"))
    runtime_ids = [str(synset.id) for synset in lookup.synsets]
    if runtime_ids != expected_ids:
        raise ValueError(
            f"OEWN candidate drift for {key}: expected={expected_ids} runtime={runtime_ids}"
        )
    selected_id = _required(source, "selected_oewn_synset")
    selected = next(
        (synset for synset in lookup.synsets if str(synset.id) == selected_id),
        None,
    )
    if selected is None:
        raise ValueError(f"selected synset is not an OEWN candidate for {key}: {selected_id}")
    if source.get("decision_status", "").strip() != "chosen":
        raise ValueError(f"conj update row is not chosen: {key}")
    if not is_manual and (
        lookup.decision_status != "chosen"
        or lookup.selected_synset is None
        or str(lookup.selected_synset.id) != selected_id
    ):
        raise ValueError(f"auto chosen decision drift for {key}")

    observed_surfaces = _split_pipe(source.get("observed_surfaces", ""))
    observed_surface = observed_surfaces[0] if observed_surfaces else key
    gate = _attribute_gate_for_lexfile(str(selected.lexfile()))
    return {
        "span_key": key,
        "observed_surface": observed_surface,
        "decision_status": "chosen",
        "decision_reason": (
            "manual_attribute_synset_selected"
            if is_manual
            else lookup.decision_reason
        ),
        "count": _required(source, "mention_count"),
        "caption_count": _required(source, "caption_count"),
        "example_caption_ids": source.get("example_caption_ids", ""),
        "example_surfaces": "|".join(observed_surfaces[:5]),
        "selected_lookup_case": lookup.lookup_case,
        "selected_query": lookup.query,
        "has_oewn_attribute_synset": "true",
        "oewn_synset_count": str(len(lookup.synsets)),
        "selected_oewn_synset": selected_id,
        "selected_oewn_lexfile": str(selected.lexfile()),
        "attribute_gate": gate,
        "synset_lemmas": "|".join(str(lemma) for lemma in selected.lemmas()),
        "canonical_surface": "",
        "canonical_label_key": "",
        "canonical_selection_tag": "",
        "canonical_candidate_lemmas": "",
        "canonical_candidate_lemma_counts": "",
        "google_ngram_candidate_surfaces": "",
        "google_ngram_candidate_mean_frequencies": "",
        "attribute_parent": "",
        "attribute_parent_selection_tag": "",
        "all_oewn_synsets": "|".join(runtime_ids),
        "all_oewn_lexfiles": "|".join(
            str(synset.lexfile()) for synset in lookup.synsets
        ),
        "synset_selection_tag": (
            "manual_attribute_synset_selected"
            if is_manual
            else lookup.synset_selection_tag
        ),
        "wn30_lemma_counts": lookup.wn30_lemma_counts,
        "decision_basis": "gpic_observed_attribute_inventory_conj_repair",
    }


def _read_tsv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def _write_tsv(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    fieldnames: Sequence[str],
) -> None:
    if list(fieldnames) != list(ATTRIBUTE_INVENTORY_FIELDS):
        raise ValueError("base attribute inventory schema does not match current builder")
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(
            {
                field: str(row.get(field, ""))
                for field in fieldnames
            }
            for row in rows
        )


def _unique_by_key(
    rows: Sequence[Mapping[str, str]],
    label: str,
) -> dict[str, Mapping[str, str]]:
    result: dict[str, Mapping[str, str]] = {}
    for row in rows:
        key = _required(row, "span_key")
        if key in result:
            raise ValueError(f"duplicate {label} span_key: {key}")
        result[key] = row
    return result


def _required(row: Mapping[str, str], field: str) -> str:
    value = str(row.get(field, "")).strip()
    if not value:
        raise ValueError(f"missing required field {field}: {row.get('span_key', '')}")
    return value


def _split_pipe(value: str) -> list[str]:
    return [item for item in str(value).split("|") if item]


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + f".{os.getpid()}.tmp")
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
