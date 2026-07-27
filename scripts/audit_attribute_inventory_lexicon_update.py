from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gpic_concepts_v1.atomic_io import atomic_text_writer


UNCHANGED_LEXICON_FILES = (
    "action_synonyms.tsv",
    "action_types.tsv",
    "attribute_types.tsv",
    "object_parents.tsv",
    "object_synonyms.tsv",
)

ALLOWED_EXISTING_INVENTORY_REFRESH_FIELDS = frozenset(
    (
        "google_ngram_candidate_surfaces",
        "google_ngram_candidate_mean_frequencies",
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail unless an attribute inventory update preserves every existing "
            "inventory row and synonym mapping and changes no unrelated lexicon."
        )
    )
    parser.add_argument("--base-inventory", required=True)
    parser.add_argument("--updated-inventory", required=True)
    parser.add_argument("--base-lexicon-dir", required=True)
    parser.add_argument("--updated-lexicon-dir", required=True)
    parser.add_argument("--expected-added-keys")
    parser.add_argument("--summary", required=True)
    parser.add_argument("--added-inventory-rows", required=True)
    parser.add_argument("--added-synonym-rows", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_inventory, inventory_fields = _read_tsv(Path(args.base_inventory))
    updated_inventory, updated_inventory_fields = _read_tsv(
        Path(args.updated_inventory)
    )
    if updated_inventory_fields != inventory_fields:
        raise ValueError("updated attribute inventory schema differs from base")

    expected_added_keys: set[str] | None = None
    if args.expected_added_keys:
        expected_rows, _ = _read_tsv(Path(args.expected_added_keys))
        expected_added_keys = {
            _required(row, "span_key")
            for row in expected_rows
        }

    summary, added_inventory = audit_inventory_rows(
        base_inventory,
        updated_inventory,
        expected_added_keys=expected_added_keys,
    )

    base_lexicon_dir = Path(args.base_lexicon_dir)
    updated_lexicon_dir = Path(args.updated_lexicon_dir)
    base_synonyms, synonym_fields = _read_tsv(
        base_lexicon_dir / "attribute_synonyms.tsv"
    )
    updated_synonyms, updated_synonym_fields = _read_tsv(
        updated_lexicon_dir / "attribute_synonyms.tsv"
    )
    if updated_synonym_fields != synonym_fields:
        raise ValueError("updated attribute synonym schema differs from base")
    synonym_summary, added_synonyms = audit_synonym_rows(
        base_synonyms,
        updated_synonyms,
    )
    summary.update(synonym_summary)

    unrelated_hashes: dict[str, dict[str, str]] = {}
    for filename in UNCHANGED_LEXICON_FILES:
        base_hash = _sha256(base_lexicon_dir / filename)
        updated_hash = _sha256(updated_lexicon_dir / filename)
        unrelated_hashes[filename] = {
            "base_sha256": base_hash,
            "updated_sha256": updated_hash,
        }
        if base_hash != updated_hash:
            raise ValueError(f"unrelated lexicon changed: {filename}")
    summary["unchanged_lexicon_hashes"] = unrelated_hashes
    summary["status"] = "complete"

    _write_tsv(
        Path(args.added_inventory_rows),
        added_inventory,
        inventory_fields,
    )
    _write_tsv(
        Path(args.added_synonym_rows),
        added_synonyms,
        synonym_fields,
    )
    with atomic_text_writer(Path(args.summary)) as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def audit_inventory_rows(
    base_rows: Sequence[Mapping[str, str]],
    updated_rows: Sequence[Mapping[str, str]],
    *,
    expected_added_keys: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    base_by_key = _unique_rows(base_rows, "span_key", "base inventory")
    updated_by_key = _unique_rows(updated_rows, "span_key", "updated inventory")
    removed = sorted(set(base_by_key) - set(updated_by_key))
    if removed:
        raise ValueError(f"existing inventory keys removed: {removed[:20]}")
    semantic_changes: list[str] = []
    evidence_refreshes: dict[str, list[str]] = {}
    for key, base_row in base_by_key.items():
        updated_row = updated_by_key[key]
        changed_fields = sorted(
            field
            for field in set(base_row) | set(updated_row)
            if base_row.get(field, "") != updated_row.get(field, "")
        )
        if not changed_fields:
            continue
        disallowed_fields = sorted(
            set(changed_fields) - ALLOWED_EXISTING_INVENTORY_REFRESH_FIELDS
        )
        if disallowed_fields:
            semantic_changes.append(
                f"{key}:{','.join(disallowed_fields)}"
            )
        else:
            evidence_refreshes[key] = changed_fields
    if semantic_changes:
        raise ValueError(
            f"existing inventory semantic rows modified: {semantic_changes[:20]}"
        )
    added_keys = set(updated_by_key) - set(base_by_key)
    if expected_added_keys is not None and added_keys != expected_added_keys:
        raise ValueError(
            json.dumps(
                {
                    "status": "added_inventory_key_mismatch",
                    "missing": sorted(expected_added_keys - added_keys)[:20],
                    "unexpected": sorted(added_keys - expected_added_keys)[:20],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    added_rows = [
        updated_by_key[key]
        for key in sorted(added_keys)
    ]
    return (
        {
            "base_inventory_rows": len(base_rows),
            "updated_inventory_rows": len(updated_rows),
            "added_inventory_rows": len(added_rows),
            "removed_inventory_rows": 0,
            "modified_existing_inventory_semantic_rows": 0,
            "refreshed_existing_inventory_evidence_rows": len(evidence_refreshes),
            "refreshed_existing_inventory_evidence": evidence_refreshes,
        },
        added_rows,
    )


def audit_synonym_rows(
    base_rows: Sequence[Mapping[str, str]],
    updated_rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    base_by_raw = _unique_rows(base_rows, "raw", "base attribute synonyms")
    updated_by_raw = _unique_rows(updated_rows, "raw", "updated attribute synonyms")
    removed = sorted(set(base_by_raw) - set(updated_by_raw))
    if removed:
        raise ValueError(f"existing attribute synonyms removed: {removed[:20]}")
    changed = sorted(
        key
        for key, base_row in base_by_raw.items()
        if updated_by_raw[key] != base_row
    )
    if changed:
        raise ValueError(f"existing attribute synonyms changed: {changed[:20]}")
    added_keys = set(updated_by_raw) - set(base_by_raw)
    added_rows = [
        updated_by_raw[key]
        for key in sorted(added_keys)
    ]
    return (
        {
            "base_attribute_synonym_rows": len(base_rows),
            "updated_attribute_synonym_rows": len(updated_rows),
            "added_attribute_synonym_rows": len(added_rows),
            "removed_attribute_synonym_rows": 0,
            "changed_existing_attribute_synonym_rows": 0,
        },
        added_rows,
    )


def _read_tsv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def _write_tsv(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    fieldnames: Sequence[str],
) -> None:
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


def _unique_rows(
    rows: Sequence[Mapping[str, str]],
    key_field: str,
    label: str,
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = _required(row, key_field)
        if key in result:
            raise ValueError(f"duplicate {label} key: {key}")
        result[key] = {field: str(value) for field, value in row.items()}
    return result


def _required(row: Mapping[str, str], field: str) -> str:
    value = str(row.get(field, "")).strip()
    if not value:
        raise ValueError(f"missing required field {field}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
