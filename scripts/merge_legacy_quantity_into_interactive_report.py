from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Any


INDEX_INSERT_SQL = (
    "INSERT OR IGNORE INTO report_caption_index "
    "(view_name, row_id, caption_id) VALUES (?, ?, ?)"
)
QUANTITY_FACT_MARKERS = (b'"quantity_exists"', b'"has_quantity"')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotently merge legacy Stage 6 quantity tables and their full "
            "caption index into an existing interactive report DB."
        ),
    )
    parser.add_argument("--report-db", required=True, type=Path)
    parser.add_argument("--quantity-counts-tsv", required=True, type=Path)
    parser.add_argument("--object-quantity-pair-counts-tsv", required=True, type=Path)
    parser.add_argument("--quantity-facts-jsonl", required=True, type=Path)
    parser.add_argument("--progress-json", type=Path)
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--progress-every", type=int, default=100_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = merge_legacy_quantity(
        report_db=args.report_db,
        quantity_counts_tsv=args.quantity_counts_tsv,
        object_quantity_pair_counts_tsv=args.object_quantity_pair_counts_tsv,
        quantity_facts_jsonl=args.quantity_facts_jsonl,
        progress_json=args.progress_json,
        batch_size=args.batch_size,
        progress_every=args.progress_every,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def merge_legacy_quantity(
    *,
    report_db: Path,
    quantity_counts_tsv: Path,
    object_quantity_pair_counts_tsv: Path,
    quantity_facts_jsonl: Path,
    progress_json: Path | None = None,
    batch_size: int = 100_000,
    progress_every: int = 100_000,
) -> dict[str, Any]:
    started_at = time.monotonic()
    quantity_rows = list(_iter_tsv(quantity_counts_tsv))
    pair_rows = list(_iter_tsv(object_quantity_pair_counts_tsv))
    if not quantity_rows:
        raise ValueError("quantity_counts.tsv contains no rows")
    if not pair_rows:
        raise ValueError("object_quantity_pair_counts.tsv contains no rows")

    with closing(sqlite3.connect(report_db)) as conn:
        conn.row_factory = sqlite3.Row
        _upgrade_legacy_report_schema(conn)
        _validate_report_schema(conn)
        quantity_row_ids = _upsert_quantity_rows(conn, quantity_rows)
        pair_row_ids = _upsert_quantity_pair_rows(conn, pair_rows)
        removed_index_rows = _delete_target_caption_index(
            conn,
            quantity_row_ids=quantity_row_ids,
            pair_row_ids=pair_row_ids,
        )
        _update_view_metadata(conn)
        _write_migration_metadata(
            conn,
            {
                "status": "indexing",
                "quantity_rows": len(quantity_row_ids),
                "quantity_pair_rows": len(pair_row_ids),
                "removed_index_rows": removed_index_rows,
            },
        )
        conn.commit()

        index_summary = _append_quantity_caption_index(
            conn,
            facts_path=quantity_facts_jsonl,
            quantity_row_ids=quantity_row_ids,
            pair_row_ids=pair_row_ids,
            progress_json=progress_json,
            batch_size=batch_size,
            progress_every=progress_every,
            started_at=started_at,
        )
        validation = _validate_caption_counts(
            conn,
            quantity_rows=quantity_rows,
            quantity_row_ids=quantity_row_ids,
            pair_rows=pair_rows,
            pair_row_ids=pair_row_ids,
        )
        summary = {
            "status": "complete",
            "report_db": str(report_db),
            "quantity_rows": len(quantity_row_ids),
            "quantity_pair_rows": len(pair_row_ids),
            "removed_index_rows": removed_index_rows,
            "index": index_summary,
            "validation": validation,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
        }
        _write_migration_metadata(conn, summary)
        conn.commit()

    if progress_json is not None:
        _write_json(progress_json, summary)
    return summary


def _delete_target_caption_index(
    conn: sqlite3.Connection,
    *,
    quantity_row_ids: dict[str, int],
    pair_row_ids: dict[tuple[str, str], int],
) -> int:
    removed = 0
    for view_name, row_ids in (
        ("attributes", list(quantity_row_ids.values())),
        ("attribute_object_pairs", list(pair_row_ids.values())),
    ):
        for start in range(0, len(row_ids), 500):
            batch = row_ids[start : start + 500]
            placeholders = ",".join("?" for _ in batch)
            cursor = conn.execute(
                "DELETE FROM report_caption_index "
                f"WHERE view_name = ? AND row_id IN ({placeholders})",
                [view_name, *batch],
            )
            removed += max(0, int(cursor.rowcount))
    return removed


def _upsert_quantity_rows(
    conn: sqlite3.Connection,
    rows: list[dict[str, str]],
) -> dict[str, int]:
    result: dict[str, int] = {}
    next_row_id = int(
        conn.execute("SELECT COALESCE(MAX(_row_id), 0) + 1 FROM attributes").fetchone()[0],
    )
    for row in rows:
        label = str(row.get("quantity") or "").strip()
        if not label:
            continue
        existing = _find_kind_rows(
            conn,
            table="attributes",
            label_column="canonical_attribute",
            label_values=(label,),
            kind="quantity",
        )
        if len(existing) > 1:
            raise ValueError(f"duplicate quantity attribute rows for {label!r}: {existing}")
        raw_surfaces = _pipe_union(row.get("raw_variants", ""), label)
        examples = _pipe_union(row.get("example_caption_ids", ""))
        values = (
            examples,
            label,
            "quantity",
            raw_surfaces,
            int(row.get("count") or 0),
            int(row.get("caption_count") or 0),
            examples,
        )
        if existing:
            row_id = existing[0]
            conn.execute(
                """
                UPDATE attributes
                SET _caption_ids = ?, canonical_attribute = ?,
                    attribute_kind = ?, attribute_raw_surfaces = ?,
                    count = ?, caption_count = ?, example_caption_ids = ?
                WHERE _row_id = ?
                """,
                (*values, row_id),
            )
        else:
            row_id = next_row_id
            next_row_id += 1
            conn.execute(
                """
                INSERT INTO attributes (
                    _row_id, _caption_ids, canonical_attribute, attribute_kind,
                    attribute_raw_surfaces, count, caption_count,
                    example_caption_ids
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row_id, *values),
            )
        result[label] = row_id
    return result


def _upsert_quantity_pair_rows(
    conn: sqlite3.Connection,
    rows: list[dict[str, str]],
) -> dict[tuple[str, str], int]:
    result: dict[tuple[str, str], int] = {}
    next_row_id = int(
        conn.execute(
            "SELECT COALESCE(MAX(_row_id), 0) + 1 FROM attribute_object_pairs",
        ).fetchone()[0],
    )
    object_metadata = {
        str(row["canonical_object"]): (
            str(row["object_raw_surfaces"] or ""),
            str(row["object_parent_concepts"] or ""),
        )
        for row in conn.execute(
            "SELECT canonical_object, object_raw_surfaces, object_parent_concepts "
            "FROM objects",
        )
    }
    quantity_raw_surfaces = {
        str(row["canonical_attribute"]): str(row["attribute_raw_surfaces"] or "")
        for row in conn.execute(
            "SELECT canonical_attribute, attribute_raw_surfaces FROM attributes "
            "WHERE instr('|' || attribute_kind || '|', '|quantity|') > 0",
        )
    }
    for row in rows:
        object_label = str(row.get("object") or "").strip()
        quantity = str(row.get("quantity") or "").strip()
        if not object_label or not quantity:
            continue
        existing = _find_kind_rows(
            conn,
            table="attribute_object_pairs",
            label_column="object, attribute",
            label_values=(object_label, quantity),
            kind="quantity",
        )
        if len(existing) > 1:
            raise ValueError(
                "duplicate quantity pair rows for "
                f"{(object_label, quantity)!r}: {existing}",
            )
        object_raw, object_parents = object_metadata.get(object_label, ("", ""))
        raw_surfaces = quantity_raw_surfaces.get(quantity, quantity)
        examples = _pipe_union(row.get("example_caption_ids", ""))
        values = (
            examples,
            object_label,
            object_raw,
            object_parents,
            quantity,
            "quantity",
            raw_surfaces,
            int(row.get("count") or 0),
            int(row.get("caption_count") or 0),
            examples,
        )
        if existing:
            row_id = existing[0]
            conn.execute(
                """
                UPDATE attribute_object_pairs
                SET _caption_ids = ?, object = ?, object_raw_surfaces = ?,
                    object_parent_concepts = ?, attribute = ?,
                    attribute_kind = ?, attribute_raw_surfaces = ?,
                    count = ?, caption_count = ?, example_caption_ids = ?
                WHERE _row_id = ?
                """,
                (*values, row_id),
            )
        else:
            row_id = next_row_id
            next_row_id += 1
            conn.execute(
                """
                INSERT INTO attribute_object_pairs (
                    _row_id, _caption_ids, object, object_raw_surfaces,
                    object_parent_concepts, attribute, attribute_kind,
                    attribute_raw_surfaces, count, caption_count,
                    example_caption_ids
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row_id, *values),
            )
        result[(object_label, quantity)] = row_id
    return result


def _find_kind_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    label_column: str,
    label_values: tuple[str, ...],
    kind: str,
) -> list[int]:
    columns = [column.strip() for column in label_column.split(",")]
    predicates = [f"{column} = ?" for column in columns]
    predicates.append("instr('|' || attribute_kind || '|', ?) > 0")
    rows = conn.execute(
        f"SELECT _row_id FROM {table} WHERE {' AND '.join(predicates)}",
        [*label_values, f"|{kind}|"],
    )
    return [int(row[0]) for row in rows]


def _append_quantity_caption_index(
    conn: sqlite3.Connection,
    *,
    facts_path: Path,
    quantity_row_ids: dict[str, int],
    pair_row_ids: dict[tuple[str, str], int],
    progress_json: Path | None,
    batch_size: int,
    progress_every: int,
    started_at: float,
) -> dict[str, Any]:
    pending: list[tuple[str, int, str]] = []
    lines_read = 0
    relevant_facts = 0
    attempted_rows = 0
    missing_attribute_keys = 0
    missing_pair_keys = 0

    def flush() -> None:
        if pending:
            conn.executemany(INDEX_INSERT_SQL, pending)
            conn.commit()
            pending.clear()

    with facts_path.open("rb") as handle:
        for raw_line in handle:
            lines_read += 1
            if not any(marker in raw_line for marker in QUANTITY_FACT_MARKERS):
                continue
            fact = json.loads(raw_line)
            values = fact.get("values")
            if not isinstance(values, dict):
                continue
            caption_id = str(fact.get("caption_id") or "")
            quantity = str(values.get("quantity") or "")
            if not caption_id or not quantity:
                continue
            relevant_facts += 1
            fact_type = str(fact.get("fact_type") or "")
            if fact_type == "quantity_exists":
                row_id = quantity_row_ids.get(quantity)
                if row_id is None:
                    missing_attribute_keys += 1
                    continue
                pending.append(("attributes", row_id, caption_id))
            elif fact_type == "has_quantity":
                object_label = str(values.get("object") or "")
                row_id = pair_row_ids.get((object_label, quantity))
                if row_id is None:
                    missing_pair_keys += 1
                    continue
                pending.append(("attribute_object_pairs", row_id, caption_id))
            else:
                continue
            attempted_rows += 1
            if len(pending) >= max(1, batch_size):
                flush()
            if progress_json is not None and lines_read % max(1, progress_every) == 0:
                progress = {
                    "status": "indexing",
                    "lines_read": lines_read,
                    "relevant_facts": relevant_facts,
                    "attempted_rows": attempted_rows,
                    "elapsed_seconds": round(time.monotonic() - started_at, 3),
                }
                _write_json(progress_json, progress)
                print(json.dumps(progress, ensure_ascii=False, sort_keys=True), flush=True)
    flush()
    return {
        "lines_read": lines_read,
        "relevant_facts": relevant_facts,
        "attempted_rows": attempted_rows,
        "missing_attribute_keys": missing_attribute_keys,
        "missing_pair_keys": missing_pair_keys,
    }


def _validate_caption_counts(
    conn: sqlite3.Connection,
    *,
    quantity_rows: list[dict[str, str]],
    quantity_row_ids: dict[str, int],
    pair_rows: list[dict[str, str]],
    pair_row_ids: dict[tuple[str, str], int],
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    for row in quantity_rows:
        label = str(row.get("quantity") or "")
        if label not in quantity_row_ids:
            continue
        expected = int(row.get("caption_count") or 0)
        actual = _indexed_caption_count(conn, "attributes", quantity_row_ids[label])
        if actual != expected:
            mismatches.append(
                {"view": "attributes", "key": label, "expected": expected, "actual": actual},
            )
    for row in pair_rows:
        key = (str(row.get("object") or ""), str(row.get("quantity") or ""))
        if key not in pair_row_ids:
            continue
        expected = int(row.get("caption_count") or 0)
        actual = _indexed_caption_count(
            conn,
            "attribute_object_pairs",
            pair_row_ids[key],
        )
        if actual != expected:
            mismatches.append(
                {
                    "view": "attribute_object_pairs",
                    "key": "\t".join(key),
                    "expected": expected,
                    "actual": actual,
                },
            )
    if mismatches:
        raise ValueError(
            "legacy quantity caption-index validation failed: "
            f"{len(mismatches)} mismatches; first={mismatches[:5]}",
        )
    return {
        "status": "ok",
        "validated_attribute_rows": len(quantity_row_ids),
        "validated_pair_rows": len(pair_row_ids),
        "mismatch_count": 0,
    }


def _indexed_caption_count(
    conn: sqlite3.Connection,
    view: str,
    row_id: int,
) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM report_caption_index "
            "WHERE view_name = ? AND row_id = ?",
            (view, row_id),
        ).fetchone()[0],
    )


def _update_view_metadata(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM metadata WHERE key = 'views'").fetchone()
    if row is None:
        return
    views = json.loads(str(row[0]))
    for view in views:
        name = str(view.get("name") or "")
        if name in {"attributes", "attribute_object_pairs"}:
            view["row_count"] = int(
                conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0],
            )
            columns = list(view.get("columns") or [])
            if columns and "attribute_kind" not in columns:
                anchor = (
                    "canonical_attribute"
                    if name == "attributes"
                    else "attribute"
                )
                insertion_index = (
                    columns.index(anchor) + 1
                    if anchor in columns
                    else len(columns)
                )
                columns.insert(insertion_index, "attribute_kind")
                view["columns"] = columns
    conn.execute(
        "UPDATE metadata SET value = ? WHERE key = 'views'",
        (json.dumps(views, ensure_ascii=False),),
    )


def _write_migration_metadata(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        (
            "legacy_quantity_migration",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )


def _validate_report_schema(conn: sqlite3.Connection) -> None:
    required = {
        "attributes": {
            "_row_id",
            "_caption_ids",
            "canonical_attribute",
            "attribute_kind",
            "attribute_raw_surfaces",
            "count",
            "caption_count",
            "example_caption_ids",
        },
        "attribute_object_pairs": {
            "_row_id",
            "_caption_ids",
            "object",
            "object_raw_surfaces",
            "object_parent_concepts",
            "attribute",
            "attribute_kind",
            "attribute_raw_surfaces",
            "count",
            "caption_count",
            "example_caption_ids",
        },
    }
    for table, columns in required.items():
        actual = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
        missing = columns - actual
        if missing:
            raise ValueError(f"{table} is missing required columns: {sorted(missing)}")
    if not conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'report_caption_index'",
    ).fetchone():
        raise ValueError("report_caption_index table is required")


def _upgrade_legacy_report_schema(conn: sqlite3.Connection) -> None:
    for table in ("attributes", "attribute_object_pairs"):
        columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if "attribute_kind" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN attribute_kind TEXT")
        conn.execute(
            f"UPDATE {table} SET attribute_kind = 'attribute' "
            "WHERE attribute_kind IS NULL OR trim(attribute_kind) = ''",
        )


def _iter_tsv(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle, delimiter="\t")


def _pipe_union(*values: str) -> str:
    parts = {
        item.strip()
        for value in values
        for item in str(value or "").split("|")
        if item.strip()
    }
    return "|".join(sorted(parts))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
