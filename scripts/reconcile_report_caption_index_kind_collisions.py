from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from itertools import groupby
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterable


PATCH_VERSION = 1
COLLISION_VIEWS = {
    "attributes": (
        "canonical_attribute",
        ("canonical_attribute",),
    ),
    "attribute_object_pairs": (
        "object, attribute",
        ("object", "attribute"),
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export or apply an exact caption-index repair for report rows whose "
            "semantic key is shared by multiple attribute_kind values."
        )
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    export = subparsers.add_parser("export")
    export.add_argument("--source-db", required=True, type=Path)
    export.add_argument("--output-jsonl", required=True, type=Path)
    export.add_argument("--progress-every", type=int, default=100_000)

    apply = subparsers.add_parser("apply")
    apply.add_argument("--report-db", required=True, type=Path)
    apply.add_argument("--patch-jsonl", required=True, type=Path)
    apply.add_argument("--progress-every", type=int, default=100_000)
    apply.add_argument("--batch-size", type=int, default=50_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.progress_every < 1:
        raise SystemExit("--progress-every must be positive")
    if args.action == "export":
        summary = export_collision_patch(
            source_db=args.source_db,
            output_jsonl=args.output_jsonl,
            progress_every=args.progress_every,
        )
    else:
        if args.batch_size < 1:
            raise SystemExit("--batch-size must be positive")
        summary = apply_collision_patch(
            report_db=args.report_db,
            patch_jsonl=args.patch_jsonl,
            progress_every=args.progress_every,
            batch_size=args.batch_size,
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


def export_collision_patch(
    *,
    source_db: Path,
    output_jsonl: Path,
    progress_every: int = 100_000,
) -> dict[str, Any]:
    started = time.monotonic()
    with closing(sqlite3.connect(source_db)) as conn:
        conn.row_factory = sqlite3.Row
        _validate_schema(conn)
        targets = _collision_targets(conn)
        expected_caption_records = sum(
            _index_count(conn, target["view"], target["source_row_id"])
            for target in targets
        )
        for target in targets:
            indexed = _index_count(
                conn,
                target["view"],
                target["source_row_id"],
            )
            target["expected_index_count"] = indexed
            if indexed != target["caption_count"]:
                raise ValueError(
                    "source caption index is incomplete for "
                    f"{target['patch_id']}: caption_count={target['caption_count']} "
                    f"index_count={indexed}"
                )

        output_jsonl.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_jsonl.with_suffix(output_jsonl.suffix + ".tmp")
        caption_records = 0
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            _write_record(
                handle,
                {
                    "record_type": "manifest",
                    "patch_version": PATCH_VERSION,
                    "created_at_utc": _utc_now(),
                    "source_db": str(source_db),
                    "source_db_size": source_db.stat().st_size,
                    "target_count": len(targets),
                    "expected_caption_records": expected_caption_records,
                },
            )
            for target in targets:
                _write_record(
                    handle,
                    {
                        key: value
                        for key, value in target.items()
                        if key != "source_row_id"
                    },
                )
            for target in targets:
                rows = conn.execute(
                    "SELECT caption_id FROM report_caption_index "
                    "WHERE view_name = ? AND row_id = ? ORDER BY caption_id",
                    (target["view"], target["source_row_id"]),
                )
                for row in rows:
                    _write_record(
                        handle,
                        {
                            "record_type": "caption",
                            "patch_id": target["patch_id"],
                            "caption_id": str(row[0]),
                        },
                    )
                    caption_records += 1
                    if caption_records % progress_every == 0:
                        print(
                            json.dumps(
                                {
                                    "status": "exporting",
                                    "caption_records": caption_records,
                                    "expected_caption_records": expected_caption_records,
                                },
                                sort_keys=True,
                            ),
                            flush=True,
                        )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_jsonl)

    return {
        "status": "complete",
        "action": "export",
        "source_db": str(source_db),
        "output_jsonl": str(output_jsonl),
        "target_count": len(targets),
        "caption_records": caption_records,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def apply_collision_patch(
    *,
    report_db: Path,
    patch_jsonl: Path,
    progress_every: int = 100_000,
    batch_size: int = 50_000,
) -> dict[str, Any]:
    started = time.monotonic()
    manifest, targets, patch_caption_counts = _read_patch_headers(patch_jsonl)
    if manifest.get("patch_version") != PATCH_VERSION:
        raise ValueError(f"unsupported patch version: {manifest.get('patch_version')}")
    if int(manifest.get("target_count", -1)) != len(targets):
        raise ValueError("patch target_count does not match target records")
    expected_total = sum(int(target["expected_index_count"]) for target in targets)
    if int(manifest.get("expected_caption_records", -1)) != expected_total:
        raise ValueError("patch expected_caption_records does not match targets")
    for target in targets:
        patch_id = str(target["patch_id"])
        if patch_caption_counts[patch_id] != int(target["expected_index_count"]):
            raise ValueError(
                f"patch caption count mismatch for {patch_id}: "
                f"{patch_caption_counts[patch_id]} != "
                f"{target['expected_index_count']}"
            )

    with closing(sqlite3.connect(report_db)) as conn:
        conn.row_factory = sqlite3.Row
        _validate_schema(conn)
        targets_by_id = {
            str(target["patch_id"]): target
            for target in targets
        }
        resolved = {
            str(target["patch_id"]): _resolve_remote_target(conn, target)
            for target in targets
        }
        conn.execute("BEGIN IMMEDIATE")
        try:
            removed = 0
            for patch_id, row_id in resolved.items():
                target = targets_by_id[patch_id]
                cursor = conn.execute(
                    "DELETE FROM report_caption_index "
                    "WHERE view_name = ? AND row_id = ?",
                    (target["view"], row_id),
                )
                removed += max(0, int(cursor.rowcount))

            inserted = 0
            batch: list[tuple[str, int, str]] = []
            for record in _iter_patch_records(patch_jsonl):
                if record.get("record_type") != "caption":
                    continue
                patch_id = str(record["patch_id"])
                target = targets_by_id[patch_id]
                batch.append(
                    (
                        str(target["view"]),
                        resolved[patch_id],
                        str(record["caption_id"]),
                    )
                )
                if len(batch) >= batch_size:
                    conn.executemany(
                        "INSERT INTO report_caption_index "
                        "(view_name, row_id, caption_id) VALUES (?, ?, ?)",
                        batch,
                    )
                    inserted += len(batch)
                    batch.clear()
                    if inserted % progress_every < batch_size:
                        print(
                            json.dumps(
                                {
                                    "status": "applying",
                                    "inserted": inserted,
                                    "expected": expected_total,
                                },
                                sort_keys=True,
                            ),
                            flush=True,
                        )
            if batch:
                conn.executemany(
                    "INSERT INTO report_caption_index "
                    "(view_name, row_id, caption_id) VALUES (?, ?, ?)",
                    batch,
                )
                inserted += len(batch)

            for target in targets:
                patch_id = str(target["patch_id"])
                actual = _index_count(
                    conn,
                    str(target["view"]),
                    resolved[patch_id],
                )
                expected = int(target["expected_index_count"])
                if actual != expected:
                    raise ValueError(
                        f"applied index count mismatch for {patch_id}: "
                        f"{actual} != {expected}"
                    )
            summary = {
                "status": "complete",
                "action": "apply",
                "report_db": str(report_db),
                "patch_jsonl": str(patch_jsonl),
                "target_count": len(targets),
                "removed_index_rows": removed,
                "inserted_index_rows": inserted,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (
                    "caption_index_kind_collision_reconciliation",
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                ),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return summary


def _collision_targets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for view, (select_key, key_columns) in COLLISION_VIEWS.items():
        rows = conn.execute(
            f"SELECT _row_id, {select_key}, attribute_kind, caption_count "
            f"FROM {view} ORDER BY {select_key}, attribute_kind, _row_id"
        )
        for key_values, grouped in groupby(
            rows,
            key=lambda row: tuple(str(row[column] or "") for column in key_columns),
        ):
            group_rows = list(grouped)
            kinds = [_normalized_kind(row["attribute_kind"]) for row in group_rows]
            if len(set(kinds)) < 2:
                continue
            if len(set(kinds)) != len(kinds):
                raise ValueError(
                    f"duplicate {view} rows for key={key_values!r} kinds={kinds!r}"
                )
            for row, kind in zip(group_rows, kinds):
                key = dict(zip(key_columns, key_values))
                patch_id = _patch_id(view, key_values, kind)
                targets.append(
                    {
                        "record_type": "target",
                        "patch_id": patch_id,
                        "view": view,
                        "key": key,
                        "attribute_kind": kind,
                        "caption_count": int(row["caption_count"] or 0),
                        "source_row_id": int(row["_row_id"]),
                    }
                )
    return targets


def _read_patch_headers(
    patch_jsonl: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], Counter[str]]:
    manifest: dict[str, Any] | None = None
    targets: list[dict[str, Any]] = []
    caption_counts: Counter[str] = Counter()
    target_ids: set[str] = set()
    for record in _iter_patch_records(patch_jsonl):
        record_type = record.get("record_type")
        if record_type == "manifest":
            if manifest is not None:
                raise ValueError("patch contains multiple manifests")
            manifest = record
        elif record_type == "target":
            patch_id = str(record.get("patch_id") or "")
            if not patch_id or patch_id in target_ids:
                raise ValueError(f"duplicate or empty patch_id: {patch_id!r}")
            target_ids.add(patch_id)
            targets.append(record)
        elif record_type == "caption":
            patch_id = str(record.get("patch_id") or "")
            if patch_id not in target_ids:
                raise ValueError(f"caption references unknown patch_id: {patch_id!r}")
            caption_counts[patch_id] += 1
        else:
            raise ValueError(f"unknown patch record_type: {record_type!r}")
    if manifest is None:
        raise ValueError("patch manifest is missing")
    return manifest, targets, caption_counts


def _resolve_remote_target(
    conn: sqlite3.Connection,
    target: dict[str, Any],
) -> int:
    view = str(target["view"])
    key = dict(target["key"])
    if view not in COLLISION_VIEWS:
        raise ValueError(f"unsupported target view: {view}")
    key_columns = COLLISION_VIEWS[view][1]
    if set(key) != set(key_columns):
        raise ValueError(f"invalid key columns for {view}: {sorted(key)!r}")
    predicates = [f"{column} = ?" for column in key_columns]
    rows = conn.execute(
        f"SELECT _row_id, attribute_kind, caption_count FROM {view} "
        f"WHERE {' AND '.join(predicates)}",
        [str(key[column]) for column in key_columns],
    ).fetchall()
    matching = [
        row
        for row in rows
        if _normalized_kind(row["attribute_kind"]) == target["attribute_kind"]
    ]
    if len(matching) != 1:
        raise ValueError(
            f"remote target is not unique for {target['patch_id']}: {len(matching)}"
        )
    row = matching[0]
    remote_caption_count = int(row["caption_count"] or 0)
    source_caption_count = int(target["caption_count"])
    if remote_caption_count != source_caption_count:
        raise ValueError(
            f"remote caption_count mismatch for {target['patch_id']}: "
            f"{remote_caption_count} != {source_caption_count}"
        )
    return int(row["_row_id"])


def _validate_schema(conn: sqlite3.Connection) -> None:
    required = {
        "attributes",
        "attribute_object_pairs",
        "report_caption_index",
        "metadata",
    }
    present = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = sorted(required - present)
    if missing:
        raise ValueError(f"report DB is missing tables: {missing}")
    for view in ("attributes", "attribute_object_pairs"):
        columns = {
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({view})")
        }
        if "attribute_kind" not in columns:
            raise ValueError(f"{view} is missing attribute_kind")


def _index_count(conn: sqlite3.Connection, view: str, row_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM report_caption_index "
            "WHERE view_name = ? AND row_id = ?",
            (view, row_id),
        ).fetchone()[0]
    )


def _normalized_kind(value: Any) -> str:
    return str(value or "").strip() or "attribute"


def _patch_id(view: str, key_values: tuple[str, ...], kind: str) -> str:
    return json.dumps(
        [view, *key_values, kind],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _write_record(handle: Any, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
    handle.write("\n")


def _iter_patch_records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON at {path}:{line_number}: {exc}"
                ) from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
