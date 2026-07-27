from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SEPARATOR = "\x1f"

VIEW_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "objects": ("canonical_object",),
    "attributes": ("canonical_attribute", "attribute_kind"),
    "actions": ("canonical_action",),
    "relations": ("source_object", "relation", "target_object"),
    "object_cooccurrence": ("source_object", "target_object"),
    "attribute_object_pairs": ("object", "attribute", "attribute_kind"),
    "patient_action_pairs": ("patient_object", "action"),
    "agent_action_pairs": ("agent_object", "action"),
    "patient_action_agent_triples": ("patient_object", "action", "agent_object"),
    "relation_components": ("relation", "component_index", "component"),
}

INSERT_SQL = (
    "INSERT OR IGNORE INTO report_caption_index "
    "(view_name, row_id, caption_id) VALUES (?, ?, ?)"
)


@dataclass(slots=True)
class RoleFact:
    caption_id: str
    action_mention_id: str
    action: str
    role: str
    target: str


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Attach a full row-to-caption index to an interactive report DB by "
            "streaming Stage 6 facts.jsonl. This restores full paginated caption "
            "drill-down for reports built from aggregate Stage 6 TSVs."
        ),
    )
    parser.add_argument("--report-db", required=True, type=Path)
    parser.add_argument("--facts-jsonl", required=True, type=Path)
    parser.add_argument("--progress-json", type=Path)
    parser.add_argument("--progress-every", type=int, default=1_000_000)
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--max-facts", type=int)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--views",
        nargs="*",
        choices=tuple(VIEW_KEY_COLUMNS),
        default=tuple(VIEW_KEY_COLUMNS),
        help="Optional subset of views to index. Defaults to every report view.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    selected_views = set(args.views)
    if not args.report_db.exists():
        raise SystemExit(f"missing report DB: {args.report_db}")
    if not args.facts_jsonl.exists():
        raise SystemExit(f"missing facts JSONL: {args.facts_jsonl}")

    started_at = time.time()
    with sqlite3.connect(args.report_db) as conn:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        _validate_view_key_schema(conn, selected_views)
        _prepare_index_table(conn, overwrite=args.overwrite)
        row_maps = _load_row_maps(conn, selected_views)
        summary = _stream_facts_into_index(
            conn,
            facts_jsonl=args.facts_jsonl,
            row_maps=row_maps,
            selected_views=selected_views,
            progress_json=args.progress_json,
            progress_every=args.progress_every,
            batch_size=args.batch_size,
            max_facts=args.max_facts,
            started_at=started_at,
        )
        conn.commit()
        summary["index_rows"] = int(
            conn.execute("SELECT COUNT(*) FROM report_caption_index").fetchone()[0],
        )
        summary["view_index_rows"] = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                "SELECT view_name, COUNT(*) FROM report_caption_index "
                "GROUP BY view_name ORDER BY view_name",
            ).fetchall()
        }
        _write_index_metadata(conn, summary)
        conn.commit()

    summary["elapsed_seconds"] = round(time.time() - started_at, 3)
    if args.progress_json:
        _write_progress(args.progress_json, {"phase": "complete", **summary})
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def _prepare_index_table(conn: sqlite3.Connection, *, overwrite: bool) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = "
        "'report_caption_index'",
    ).fetchone()
    if exists and not overwrite:
        raise SystemExit(
            "report_caption_index already exists; pass --overwrite to rebuild it",
        )
    if exists:
        conn.execute("DROP TABLE report_caption_index")
    conn.execute(
        "CREATE TABLE report_caption_index ("
        "view_name TEXT NOT NULL, "
        "row_id INTEGER NOT NULL, "
        "caption_id TEXT NOT NULL, "
        "PRIMARY KEY (view_name, row_id, caption_id)"
        ") WITHOUT ROWID",
    )


def _load_row_maps(
    conn: sqlite3.Connection,
    selected_views: set[str],
) -> dict[str, dict[str, int]]:
    row_maps: dict[str, dict[str, int]] = {}
    for view, columns in VIEW_KEY_COLUMNS.items():
        if view not in selected_views:
            continue
        if not _table_exists(conn, view):
            continue
        select_columns = ", ".join(["_row_id", *[_q(column) for column in columns]])
        mapping: dict[str, int] = {}
        for row in conn.execute(f"SELECT {select_columns} FROM {_q(view)}"):
            row_id = int(row[0])
            values = tuple(str(value or "") for value in row[1:])
            for key in _row_keys(view, values):
                existing_row_id = mapping.get(key)
                if existing_row_id is not None and existing_row_id != row_id:
                    raise ValueError(
                        "duplicate report caption-index key: "
                        f"view={view!r}, key={key!r}, "
                        f"row_ids=({existing_row_id}, {row_id})",
                    )
                mapping[key] = row_id
        row_maps[view] = mapping
    return row_maps


def _validate_view_key_schema(
    conn: sqlite3.Connection,
    selected_views: set[str],
) -> None:
    for view, required_columns in VIEW_KEY_COLUMNS.items():
        if view not in selected_views or not _table_exists(conn, view):
            continue
        actual_columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({_q(view)})").fetchall()
        }
        missing = [
            column for column in required_columns if column not in actual_columns
        ]
        if missing:
            raise ValueError(
                "report caption-index key schema is stale: "
                f"view={view!r}, missing_columns={missing!r}. "
                "Rebuild the base report with the same code snapshot as the "
                "caption-index builder before indexing.",
            )


def _row_keys(view: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if view not in {"attributes", "attribute_object_pairs"}:
        return (_key(*values),)
    if not values:
        return ()
    kinds = tuple(
        sorted(
            {
                kind.strip()
                for kind in values[-1].split("|")
                if kind.strip()
            },
        ),
    ) or ("attribute",)
    return tuple(_key(*values[:-1], kind) for kind in kinds)


def _stream_facts_into_index(
    conn: sqlite3.Connection,
    *,
    facts_jsonl: Path,
    row_maps: Mapping[str, Mapping[str, int]],
    selected_views: set[str],
    progress_json: Path | None,
    progress_every: int,
    batch_size: int,
    max_facts: int | None,
    started_at: float,
) -> dict[str, Any]:
    pending: list[tuple[str, int, str]] = []
    rows_read = 0
    attempted_index_rows = 0
    fact_type_counts: dict[str, int] = defaultdict(int)
    missing_row_keys: dict[str, int] = defaultdict(int)
    current_caption_id: str | None = None
    current_roles_by_action: dict[str, dict[str, list[RoleFact]]] = defaultdict(
        lambda: defaultdict(list),
    )

    def add(view: str, key: str, caption_id: str) -> None:
        nonlocal attempted_index_rows
        if view not in selected_views:
            return
        row_id = row_maps.get(view, {}).get(key)
        if row_id is None:
            missing_row_keys[view] += 1
            return
        pending.append((view, row_id, caption_id))
        attempted_index_rows += 1
        if len(pending) >= max(1, batch_size):
            _flush_pending(conn, pending)

    def flush_roles() -> None:
        if not current_roles_by_action:
            return
        _add_triple_index_rows(current_roles_by_action, add)
        current_roles_by_action.clear()

    with facts_jsonl.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows_read += 1
            if max_facts is not None and rows_read > max_facts:
                break
            fact = json.loads(line)
            fact_type = str(fact.get("fact_type") or "")
            fact_type_counts[fact_type] += 1
            caption_id = str(fact.get("caption_id") or "")
            if not caption_id:
                continue

            if fact_type == "event_role":
                role_fact = _role_fact_from_event_role(fact)
                if role_fact is not None:
                    if current_caption_id is None:
                        current_caption_id = role_fact.caption_id
                    if role_fact.caption_id != current_caption_id:
                        flush_roles()
                        current_caption_id = role_fact.caption_id
                    current_roles_by_action[role_fact.action_mention_id][
                        role_fact.role
                    ].append(role_fact)
                _add_event_role_pair_index(fact, add)
            else:
                _add_simple_fact_index(fact, add)

            if progress_json and rows_read % max(1, progress_every) == 0:
                _flush_pending(conn, pending)
                _write_progress(
                    progress_json,
                    {
                        "phase": "streaming_facts",
                        "rows_read": rows_read,
                        "attempted_index_rows": attempted_index_rows,
                        "fact_type_counts": dict(sorted(fact_type_counts.items())),
                        "missing_row_keys": dict(sorted(missing_row_keys.items())),
                        "elapsed_seconds": round(time.time() - started_at, 3),
                    },
                )

    flush_roles()
    _flush_pending(conn, pending)
    return {
        "facts_jsonl": str(facts_jsonl),
        "rows_read": rows_read,
        "attempted_index_rows": attempted_index_rows,
        "fact_type_counts": dict(sorted(fact_type_counts.items())),
        "missing_row_keys": dict(sorted(missing_row_keys.items())),
    }


def _add_simple_fact_index(fact: Mapping[str, Any], add: Any) -> None:
    values = fact.get("values")
    if not isinstance(values, Mapping):
        return
    caption_id = str(fact.get("caption_id") or "")
    fact_type = str(fact.get("fact_type") or "")
    if fact_type == "entity_exists":
        add("objects", _key(values.get("object")), caption_id)
    elif fact_type == "attribute_exists":
        add(
            "attributes",
            _key(
                values.get("attribute"),
                values.get("attribute_kind") or "attribute",
            ),
            caption_id,
        )
    elif fact_type == "quantity_exists":
        add("attributes", _key(values.get("quantity"), "quantity"), caption_id)
    elif fact_type == "action_event":
        add("actions", _key(values.get("action")), caption_id)
    elif fact_type == "relation":
        add(
            "relations",
            _key(values.get("source"), values.get("relation"), values.get("target")),
            caption_id,
        )
    elif fact_type == "object_pair_in_caption":
        add(
            "object_cooccurrence",
            _key(values.get("source_object"), values.get("target_object")),
            caption_id,
        )
    elif fact_type == "has_attribute":
        add(
            "attribute_object_pairs",
            _key(
                values.get("object"),
                values.get("attribute"),
                values.get("attribute_kind") or "attribute",
            ),
            caption_id,
        )
    elif fact_type == "has_quantity":
        add(
            "attribute_object_pairs",
            _key(values.get("object"), values.get("quantity"), "quantity"),
            caption_id,
        )
    elif fact_type == "relation_component":
        add(
            "relation_components",
            _key(
                values.get("relation"),
                values.get("component_index"),
                values.get("component"),
            ),
            caption_id,
        )


def _add_event_role_pair_index(fact: Mapping[str, Any], add: Any) -> None:
    values = fact.get("values")
    if not isinstance(values, Mapping):
        return
    caption_id = str(fact.get("caption_id") or "")
    role = str(values.get("role") or "")
    action = values.get("action")
    target = values.get("target")
    if role == "agent":
        add("agent_action_pairs", _key(target, action), caption_id)
    elif role == "patient":
        add("patient_action_pairs", _key(target, action), caption_id)


def _role_fact_from_event_role(fact: Mapping[str, Any]) -> RoleFact | None:
    values = fact.get("values")
    if not isinstance(values, Mapping):
        return None
    caption_id = str(fact.get("caption_id") or "")
    action_mention_id = _action_mention_id(fact)
    action = str(values.get("action") or "")
    role = str(values.get("role") or "")
    target = str(values.get("target") or "")
    if not caption_id or not action_mention_id or not action:
        return None
    if role not in {"agent", "patient"} or not target:
        return None
    return RoleFact(
        caption_id=caption_id,
        action_mention_id=action_mention_id,
        action=action,
        role=role,
        target=target,
    )


def _action_mention_id(fact: Mapping[str, Any]) -> str:
    source_mention_ids = fact.get("source_mention_ids")
    if isinstance(source_mention_ids, list) and source_mention_ids:
        return str(source_mention_ids[0])
    return ""


def _add_triple_index_rows(
    roles_by_action: Mapping[str, Mapping[str, list[RoleFact]]],
    add: Any,
) -> None:
    for roles in roles_by_action.values():
        agents = roles.get("agent", [])
        patients = roles.get("patient", [])
        if not agents or not patients:
            continue
        for patient in patients:
            for agent in agents:
                add(
                    "patient_action_agent_triples",
                    _key(patient.target, patient.action, agent.target),
                    patient.caption_id,
                )


def _flush_pending(
    conn: sqlite3.Connection,
    pending: list[tuple[str, int, str]],
) -> None:
    if not pending:
        return
    conn.executemany(INSERT_SQL, pending)
    pending.clear()


def _write_index_metadata(conn: sqlite3.Connection, summary: Mapping[str, Any]) -> None:
    payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ["report_caption_index_summary", payload],
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            [table],
        ).fetchone()
        is not None
    )


def _key(*parts: Any) -> str:
    return SEPARATOR.join(str(part or "") for part in parts)


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _write_progress(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


if __name__ == "__main__":
    raise SystemExit(main())
