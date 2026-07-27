from __future__ import annotations

import argparse
from contextlib import closing
import csv
import json
import sqlite3
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit conjunct-attribute synonym mappings in an interactive report DB.",
    )
    parser.add_argument("--report-db", required=True, type=Path)
    parser.add_argument("--added-synonyms", required=True, type=Path)
    parser.add_argument("--quantity-sample", action="append", default=[])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mappings = _load_mappings(args.added_synonyms)
    with closing(sqlite3.connect(args.report_db)) as conn:
        conn.row_factory = sqlite3.Row
        rows_by_canonical: dict[str, list[sqlite3.Row]] = {}
        for row in conn.execute(
            "SELECT _row_id, canonical_attribute, attribute_kind, "
            "attribute_raw_surfaces, count, caption_count FROM attributes",
        ):
            rows_by_canonical.setdefault(str(row["canonical_attribute"]), []).append(row)

        missing_mappings: list[dict[str, str]] = []
        affected_row_ids: set[int] = set()
        for raw, canonical in mappings:
            candidates = rows_by_canonical.get(canonical, [])
            matches = [
                row
                for row in candidates
                if _contains_raw_surface(str(row["attribute_raw_surfaces"] or ""), raw)
            ]
            if not matches:
                missing_mappings.append({"raw": raw, "canonical": canonical})
                continue
            affected_row_ids.update(int(row["_row_id"]) for row in matches)

        caption_index_mismatches: list[dict[str, Any]] = []
        for row_id in sorted(affected_row_ids):
            row = conn.execute(
                "SELECT canonical_attribute, attribute_kind, caption_count "
                "FROM attributes WHERE _row_id = ?",
                [row_id],
            ).fetchone()
            index_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM report_caption_index "
                    "WHERE view_name = 'attributes' AND row_id = ?",
                    [row_id],
                ).fetchone()[0],
            )
            if int(row["caption_count"]) != index_count:
                caption_index_mismatches.append(
                    {
                        "row_id": row_id,
                        "canonical_attribute": row["canonical_attribute"],
                        "attribute_kind": row["attribute_kind"],
                        "caption_count": int(row["caption_count"]),
                        "index_count": index_count,
                    },
                )

        quantity_samples = []
        for label in args.quantity_sample:
            rows = rows_by_canonical.get(label, [])
            quantity_rows = [
                row
                for row in rows
                if "quantity" in str(row["attribute_kind"] or "").split("|")
            ]
            quantity_samples.append(
                {
                    "label": label,
                    "row_count": len(quantity_rows),
                    "rows": [
                        {
                            "attribute_kind": row["attribute_kind"],
                            "count": int(row["count"]),
                            "caption_count": int(row["caption_count"]),
                        }
                        for row in quantity_rows
                    ],
                },
            )

    result = {
        "status": (
            "complete"
            if not missing_mappings and not caption_index_mismatches
            else "failed"
        ),
        "mapping_count": len(mappings),
        "affected_attribute_rows": len(affected_row_ids),
        "missing_mapping_count": len(missing_mappings),
        "missing_mapping_examples": missing_mappings[:20],
        "caption_index_mismatch_count": len(caption_index_mismatches),
        "caption_index_mismatch_examples": caption_index_mismatches[:20],
        "quantity_samples": quantity_samples,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] != "complete":
        raise SystemExit(1)


def _load_mappings(path: Path) -> list[tuple[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    mappings = sorted(
        {
            (str(row.get("raw", "")), str(row.get("canonical", "")))
            for row in rows
            if row.get("raw") and row.get("canonical")
        },
    )
    if not mappings:
        raise ValueError(f"no synonym mappings found: {path}")
    return mappings


def _contains_raw_surface(serialized: str, raw: str) -> bool:
    return serialized == raw or raw in serialized.split("|")


if __name__ == "__main__":
    main()
