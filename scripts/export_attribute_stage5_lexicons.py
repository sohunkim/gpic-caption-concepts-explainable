from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.attribute_units import (
    ATTRIBUTE_UNIT_MWE,
    separator_equivalent_key,
)
from gpic_concepts_v1.pipeline_state import (
    build_stage5_lexicon_bundle_state,
    output_dir_state_path,
    write_pipeline_state,
)


LEXICON_FILES = (
    "object_synonyms.tsv",
    "object_parents.tsv",
    "attribute_synonyms.tsv",
    "attribute_types.tsv",
    "action_synonyms.tsv",
    "action_types.tsv",
)

LEXICON_HEADERS = {
    "object_synonyms.tsv": ("raw", "canonical", "source", "notes"),
    "object_parents.tsv": ("canonical", "parent", "source", "notes"),
    "attribute_synonyms.tsv": ("raw", "canonical", "source", "notes"),
    "attribute_types.tsv": ("canonical", "attribute_type", "source", "notes"),
    "action_synonyms.tsv": ("raw", "canonical", "source", "notes"),
    "action_types.tsv": ("canonical", "action_type", "source", "notes"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export Stage 5 lexicon TSVs from a resolved GPIC observed "
            "attribute inventory."
        ),
    )
    parser.add_argument("--attribute-inventory", required=True)
    parser.add_argument("--action-canonical-inventory")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--base-lexicon-dir",
        default="resources/lexicons",
        help="Existing Stage 5 lexicon directory to copy/merge from.",
    )
    parser.add_argument("--summary", help="Optional summary JSON path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = export_attribute_stage5_lexicons(
        attribute_inventory_path=Path(args.attribute_inventory),
        action_canonical_inventory_path=(
            Path(args.action_canonical_inventory)
            if args.action_canonical_inventory
            else None
        ),
        output_dir=Path(args.output_dir),
        base_lexicon_dir=Path(args.base_lexicon_dir),
    )
    if args.summary:
        with atomic_text_writer(Path(args.summary)) as handle:
            handle.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def export_attribute_stage5_lexicons(
    *,
    attribute_inventory_path: Path,
    output_dir: Path,
    base_lexicon_dir: Path | None = None,
    action_canonical_inventory_path: Path | None = None,
) -> dict[str, object]:
    rows = _read_tsv(attribute_inventory_path)
    base_rows = _read_base_lexicons(base_lexicon_dir)

    authoritative_mwe_raw_keys = {
        raw_key
        for row in rows
        if row.get("attribute_unit_type", "").strip() == "mwe"
        for raw_key in _observed_surface_keys(row)
    }
    synonym_rows: list[dict[str, str]] = []
    replaced_stale_attribute_mwe_synonym_rows = 0
    for row in base_rows["attribute_synonyms.tsv"]:
        raw_key = _key(row.get("raw", ""))
        if (
            raw_key in authoritative_mwe_raw_keys
            and row.get("source", "").strip()
            == "gpic_observed_attribute_inventory"
        ):
            replaced_stale_attribute_mwe_synonym_rows += 1
            continue
        synonym_rows.append(row)
    type_rows: list[dict[str, str]] = []
    action_synonym_rows = list(base_rows["action_synonyms.tsv"])
    synonym_canonical_by_key: dict[str, str] = {}
    for synonym_row in synonym_rows:
        raw_key = _key(synonym_row.get("raw", ""))
        canonical_key = _key(synonym_row.get("canonical", ""))
        if not raw_key:
            continue
        existing = synonym_canonical_by_key.get(raw_key)
        if existing is not None and existing != canonical_key:
            raise ValueError(
                f"conflicting base attribute synonym: {raw_key} -> {existing} / {canonical_key}"
            )
        synonym_canonical_by_key[raw_key] = canonical_key
    action_synonym_keys = {
        _key(row["raw"]) for row in action_synonym_rows if row.get("raw")
    }

    ignored_excluded_canonical_rows = 0
    attribute_type_rows_deferred = 0
    legacy_no_synset_status_rows = 0
    chosen_synonym_rows = 0
    chosen_missing_canonical_rows: list[str] = []
    action_synonym_rows_added = 0
    action_raw_fallback_rows_skipped = 0

    for row in rows:
        status = row.get("decision_status", "").strip()
        if status == "no_synset":
            status = "chosen"
            legacy_no_synset_status_rows += 1
        raw_keys = _observed_surface_keys(row)
        if not raw_keys:
            continue
        canonical_surface = row.get("canonical_surface", "").strip()
        if row.get("attribute_type", "").strip():
            attribute_type_rows_deferred += 1
        selected_synset = row.get("selected_oewn_synset", "").strip()

        if status == "chosen":
            if not canonical_surface:
                if selected_synset:
                    chosen_missing_canonical_rows.append(raw_keys[0])
                continue
            else:
                for raw_key in raw_keys:
                    canonical_key = _key(canonical_surface)
                    existing = synonym_canonical_by_key.get(raw_key)
                    if existing is not None:
                        if existing != canonical_key:
                            raise ValueError(
                                "conflicting attribute synonym mapping: "
                                f"{raw_key} -> {existing} / {canonical_key}"
                            )
                        continue
                    synonym_rows.append(
                        {
                            "raw": raw_key,
                            "canonical": canonical_surface,
                            "source": "gpic_observed_attribute_inventory",
                            "notes": _notes(row, "chosen_canonical_synonym"),
                        }
                    )
                    synonym_canonical_by_key[raw_key] = canonical_key
                    chosen_synonym_rows += 1
        else:
            if status == "excluded" and canonical_surface:
                ignored_excluded_canonical_rows += 1

    if action_canonical_inventory_path is not None:
        action_rows = _read_tsv(action_canonical_inventory_path)
        action_synonym_rows_added, action_raw_fallback_rows_skipped = (
            _append_action_synonym_rows(
                action_rows,
                action_synonym_rows,
                action_synonym_keys,
                action_canonical_inventory_path,
            )
        )
    else:
        action_rows = []

    if chosen_missing_canonical_rows:
        raise ValueError(
            "chosen attribute rows are missing canonical_surface: "
            + ", ".join(chosen_missing_canonical_rows[:20])
        )

    merged_rows = dict(base_rows)
    merged_rows["attribute_synonyms.tsv"] = synonym_rows
    merged_rows["attribute_types.tsv"] = type_rows
    merged_rows["action_synonyms.tsv"] = action_synonym_rows
    _write_lexicon_bundle(output_dir, merged_rows)

    summary = {
        "attribute_inventory": str(attribute_inventory_path),
        "action_canonical_inventory": (
            str(action_canonical_inventory_path)
            if action_canonical_inventory_path is not None
            else None
        ),
        "output_dir": str(output_dir),
        "inventory_rows": len(rows),
        "action_inventory_rows": len(action_rows),
        "attribute_synonym_rows": len(synonym_rows),
        "action_synonym_rows": len(action_synonym_rows),
        "attribute_type_rows": len(type_rows),
        "chosen_synonym_rows_added": chosen_synonym_rows,
        "action_synonym_rows_added": action_synonym_rows_added,
        "action_raw_fallback_rows_skipped": action_raw_fallback_rows_skipped,
        "attribute_type_rows_deferred": attribute_type_rows_deferred,
        "legacy_no_synset_status_rows": legacy_no_synset_status_rows,
        "ignored_excluded_canonical_rows": ignored_excluded_canonical_rows,
        "replaced_stale_attribute_mwe_synonym_rows": (
            replaced_stale_attribute_mwe_synonym_rows
        ),
    }
    write_pipeline_state(
        output_dir_state_path(output_dir),
        build_stage5_lexicon_bundle_state(
            attribute_inventory_path=str(attribute_inventory_path),
            action_canonical_inventory_path=(
                str(action_canonical_inventory_path)
                if action_canonical_inventory_path is not None
                else None
            ),
            output_dir=str(output_dir),
            summary=summary,
        ),
    )
    return summary


def _append_action_synonym_rows(
    action_rows: list[dict[str, str]],
    action_synonym_rows: list[dict[str, str]],
    action_synonym_keys: set[str],
    source_path: Path,
) -> tuple[int, int]:
    added = 0
    raw_fallback_skipped = 0
    blocked_rows: list[str] = []

    for row in action_rows:
        status = row.get("decision_status", "").strip()
        raw_keys = _observed_surface_keys(row)
        if not raw_keys:
            continue
        selected_synset = row.get("selected_oewn_synset", "").strip()
        canonical_surface = row.get("canonical_surface", "").strip()

        if status == "raw_fallback":
            raw_fallback_skipped += 1
            continue
        if status != "chosen":
            blocked_rows.append(f"{raw_keys[0]}: status={status or '<empty>'}")
            continue
        if not selected_synset or not canonical_surface:
            blocked_rows.append(f"{raw_keys[0]}: missing selected synset or canonical")
            continue

        for raw_key in raw_keys:
            if raw_key in action_synonym_keys:
                continue
            action_synonym_rows.append(
                {
                    "raw": raw_key,
                    "canonical": canonical_surface,
                    "source": "gpic_observed_action_inventory",
                    "notes": _notes(row, "chosen_action_canonical_synonym"),
                }
            )
            action_synonym_keys.add(raw_key)
            added += 1

    if blocked_rows:
        raise ValueError(
            f"{source_path} has action rows not ready for Stage 5 export: "
            + ", ".join(blocked_rows[:20])
        )
    return added, raw_fallback_skipped


def _read_base_lexicons(base_lexicon_dir: Path | None) -> dict[str, list[dict[str, str]]]:
    output: dict[str, list[dict[str, str]]] = {}
    for file_name in LEXICON_FILES:
        path = base_lexicon_dir / file_name if base_lexicon_dir is not None else None
        if path is not None and path.exists():
            output[file_name] = _read_tsv(path)
        else:
            output[file_name] = []
    return output


def _write_lexicon_bundle(
    output_dir: Path,
    rows_by_file: Mapping[str, Iterable[Mapping[str, str]]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for file_name in LEXICON_FILES:
        path = output_dir / file_name
        fieldnames = LEXICON_HEADERS[file_name]
        with atomic_text_writer(path, newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            for row in rows_by_file[file_name]:
                writer.writerow({field: row.get(field, "") for field in fieldnames})


def _notes(row: Mapping[str, str], tag: str) -> str:
    parts = [
        f"export_tag={tag}",
        f"decision_status={row.get('decision_status', '').strip()}",
    ]
    if row.get("canonical_selection_tag", "").strip():
        parts.append(f"canonical_selection_tag={row['canonical_selection_tag'].strip()}")
    if row.get("decision_reason", "").strip():
        parts.append(f"decision_reason={row['decision_reason'].strip()}")
    return "; ".join(parts)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            return []
        return [dict(row) for row in reader]


def _key(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _observed_surface_keys(row: Mapping[str, str]) -> tuple[str, ...]:
    keys: list[str] = []
    seen: set[str] = set()
    unit_type = row.get("attribute_unit_type", "").strip()
    for raw in (
        row.get("span_key", ""),
        row.get("observed_surface", ""),
        *_split_pipe(row.get("example_surfaces", "")),
        *_split_pipe(row.get("lookup_forms", "")),
    ):
        candidates = [_key(raw)]
        if unit_type == ATTRIBUTE_UNIT_MWE:
            candidates.append(separator_equivalent_key(raw))
        for key in candidates:
            if key and key not in seen:
                keys.append(key)
                seen.add(key)
    return tuple(keys)


def _split_pipe(value: str) -> list[str]:
    return [part for part in value.split("|") if part]


if __name__ == "__main__":
    main()
