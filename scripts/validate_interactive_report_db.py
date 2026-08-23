from __future__ import annotations

import argparse
from contextlib import closing
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any


QUOTE_PREFIX_CHARS = ('"', "'", "“", "”", "‘", "’")


QUOTE_FREE_LABEL_FIELDS = {
    "objects": ["canonical_object", "object_raw_surfaces"],
    "attributes": ["canonical_attribute", "attribute_raw_surfaces"],
    "actions": ["canonical_action", "action_raw_surfaces"],
    "relations": [
        "source_object",
        "source_object_raw_surfaces",
        "target_object",
        "target_object_raw_surfaces",
    ],
    "object_cooccurrence": [
        "source_object",
        "source_object_raw_surfaces",
        "target_object",
        "target_object_raw_surfaces",
    ],
    "attribute_object_pairs": [
        "object",
        "object_raw_surfaces",
        "attribute",
        "attribute_raw_surfaces",
    ],
    "patient_action_pairs": [
        "patient_object",
        "patient_object_raw_surfaces",
        "action",
        "action_raw_surfaces",
    ],
    "agent_action_pairs": [
        "agent_object",
        "agent_object_raw_surfaces",
        "action",
        "action_raw_surfaces",
    ],
    "patient_action_agent_triples": [
        "patient_object",
        "patient_object_raw_surfaces",
        "action",
        "action_raw_surfaces",
        "agent_object",
        "agent_object_raw_surfaces",
    ],
}


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate structural invariants of an interactive count report DB.",
    )
    parser.add_argument("--report-db", required=True, type=Path)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--require-caption-index", action="store_true")
    parser.add_argument("--check-top-caption-counts", type=int, default=0)
    parser.add_argument("--check-all-caption-counts", action="store_true")
    parser.add_argument("--min-patient-action-agent-triples", type=int, default=0)
    parser.add_argument(
        "--forbid-leading-quoted-labels",
        action="store_true",
        help=(
            "Fail if extracted concept label columns still contain rows that "
            "start with a quote character. Use this for quote-free report builds."
        ),
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.report_db.exists():
        raise SystemExit(f"missing report DB: {args.report_db}")
    errors: list[str] = []
    with closing(sqlite3.connect(args.report_db)) as conn:
        conn.row_factory = sqlite3.Row
        views = _load_views(conn)
        for view in views:
            name = str(view["name"])
            expected = int(view.get("row_count") or 0)
            if not _table_exists(conn, name):
                errors.append(f"missing view table: {name}")
                continue
            actual = int(
                conn.execute(f"SELECT COUNT(*) FROM {_q(name)}").fetchone()[0],
            )
            if actual != expected:
                errors.append(f"{name}: metadata row_count={expected}, actual={actual}")

        if args.summary_json and args.summary_json.exists():
            summary = json.loads(args.summary_json.read_text(encoding="utf-8"))
            summary_counts = summary.get("view_row_counts", {})
            for name, expected in sorted(summary_counts.items()):
                if not _table_exists(conn, str(name)):
                    errors.append(f"summary lists missing table: {name}")
                    continue
                actual = int(
                    conn.execute(f"SELECT COUNT(*) FROM {_q(str(name))}").fetchone()[0],
                )
                if actual != int(expected):
                    errors.append(
                        f"{name}: summary row_count={expected}, actual={actual}",
                    )

        has_caption_index = _table_exists(conn, "report_caption_index")
        if args.require_caption_index and not has_caption_index:
            errors.append("report_caption_index is required but missing")
        if args.require_caption_index and has_caption_index:
            caption_index_metadata = conn.execute(
                "SELECT value FROM metadata "
                "WHERE key = 'report_caption_index_summary'",
            ).fetchone()
            if caption_index_metadata is None:
                errors.append(
                    "metadata key 'report_caption_index_summary' is required but missing",
                )

        if args.min_patient_action_agent_triples:
            if not _table_exists(conn, "patient_action_agent_triples"):
                errors.append("patient_action_agent_triples table is missing")
            else:
                triple_rows = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM patient_action_agent_triples",
                    ).fetchone()[0],
                )
                if triple_rows < args.min_patient_action_agent_triples:
                    errors.append(
                        "patient_action_agent_triples row_count="
                        f"{triple_rows}, expected >= "
                        f"{args.min_patient_action_agent_triples}",
                    )

        caption_mismatches: list[dict[str, Any]] = []
        if has_caption_index and args.check_all_caption_counts:
            caption_mismatches = _check_all_caption_counts(conn, views=views)
        elif has_caption_index and args.check_top_caption_counts > 0:
            caption_mismatches = _check_top_caption_counts(
                conn,
                views=views,
                limit=args.check_top_caption_counts,
            )
        for mismatch in caption_mismatches:
            errors.append(
                "{view} row_id={row_id}: caption_count={caption_count}, "
                "index_count={index_count}".format(**mismatch),
            )

        quote_label_hits: list[dict[str, Any]] = []
        if args.forbid_leading_quoted_labels:
            quote_label_hits = _find_leading_quoted_labels(conn)
            for hit in quote_label_hits[:50]:
                errors.append(
                    "{view} row_id={row_id}: leading quoted label remains in "
                    "{field}={value!r}".format(**hit),
                )
            if len(quote_label_hits) > 50:
                errors.append(
                    f"{len(quote_label_hits) - 50} additional leading quoted labels",
                )

        result = {
            "report_db": str(args.report_db),
            "view_count": len(views),
            "has_caption_index": has_caption_index,
            "caption_mismatch_count": len(caption_mismatches),
            "leading_quoted_label_count": len(quote_label_hits),
            "errors": errors,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if errors:
        raise SystemExit(1)
    return 0


def _load_views(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    row = conn.execute("SELECT value FROM metadata WHERE key = 'views'").fetchone()
    if row is None:
        raise SystemExit("metadata key 'views' is missing")
    payload = json.loads(str(row[0]))
    if not isinstance(payload, list):
        raise SystemExit("metadata key 'views' is not a list")
    return [dict(item) for item in payload if isinstance(item, dict)]


def _check_top_caption_counts(
    conn: sqlite3.Connection,
    *,
    views: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for view in views:
        name = str(view["name"])
        if not _table_exists(conn, name):
            continue
        rows = conn.execute(
            f"SELECT _row_id, caption_count FROM {_q(name)} "
            "ORDER BY caption_count DESC, _row_id ASC LIMIT ?",
            [limit],
        ).fetchall()
        for row in rows:
            row_id = int(row["_row_id"])
            caption_count = int(row["caption_count"] or 0)
            index_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM report_caption_index "
                    "WHERE view_name = ? AND row_id = ?",
                    [name, row_id],
                ).fetchone()[0],
            )
            if caption_count != index_count:
                mismatches.append(
                    {
                        "view": name,
                        "row_id": row_id,
                        "caption_count": caption_count,
                        "index_count": index_count,
                    },
                )
    return mismatches


def _check_all_caption_counts(
    conn: sqlite3.Connection,
    *,
    views: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for view in views:
        name = str(view["name"])
        if not _table_exists(conn, name):
            continue
        rows = conn.execute(
            f"SELECT source._row_id, source.caption_count, "
            "COALESCE(captions.index_count, 0) AS index_count "
            f"FROM {_q(name)} AS source "
            "LEFT JOIN ("
            "SELECT row_id, COUNT(*) AS index_count "
            "FROM report_caption_index WHERE view_name = ? GROUP BY row_id"
            ") AS captions ON captions.row_id = source._row_id "
            "WHERE CAST(COALESCE(source.caption_count, 0) AS INTEGER) "
            "!= COALESCE(captions.index_count, 0) "
            "ORDER BY source._row_id",
            [name],
        ).fetchall()
        mismatches.extend(
            {
                "view": name,
                "row_id": int(row["_row_id"]),
                "caption_count": int(row["caption_count"] or 0),
                "index_count": int(row["index_count"] or 0),
            }
            for row in rows
        )
    return mismatches


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            [table],
        ).fetchone()
        is not None
    )


def _find_leading_quoted_labels(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for table, fields in QUOTE_FREE_LABEL_FIELDS.items():
        if not _table_exists(conn, table):
            continue
        existing_fields = _table_columns(conn, table)
        selected_fields = [field for field in fields if field in existing_fields]
        if not selected_fields:
            continue
        select_list = ", ".join(["_row_id", *(quote_identifier(f) for f in selected_fields)])
        for row in conn.execute(f"SELECT {select_list} FROM {_q(table)}"):
            row_id = int(row[0])
            for index, field in enumerate(selected_fields, start=1):
                value = row[index]
                if isinstance(value, str) and value.startswith(QUOTE_PREFIX_CHARS):
                    hits.append(
                        {
                            "view": table,
                            "row_id": row_id,
                            "field": field,
                            "value": value,
                        },
                    )
    return hits


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({_q(table)})")}


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


if __name__ == "__main__":
    raise SystemExit(main())
