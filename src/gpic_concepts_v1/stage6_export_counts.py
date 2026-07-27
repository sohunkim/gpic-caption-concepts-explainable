"""Stage 6 count export.

Stage 6 consumes Stage 5 canonical mentions and edges. It creates fact rows and
flat count tables only. It does not repair, infer, or rewrite graph structure.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
import csv
import json
from pathlib import Path
import sqlite3
from typing import Any

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.io_jsonl import iter_jsonl, open_text, to_jsonable, write_jsonl
from gpic_concepts_v1.runtime_memory import MemorySafetyConfig, ProgressWriter, current_rss_kib
from gpic_concepts_v1.schema import (
    CanonicalEdge,
    CanonicalMention,
    CountRow,
    FactRow,
    JsonObject,
    MISSING_SOURCE_MENTION_ID,
    MISSING_TARGET_MENTION_ID,
)


COUNT_RULE_ID = "R25"

RAW_MENTION_RULE_BY_TYPE = {
    "object": "R12",
    "attribute": "R13",
    "quantity": "R14",
    "action": "R15",
}


@dataclass(slots=True)
class CountExportResult:
    facts: list[FactRow]
    count_tables: dict[str, list[CountRow]]


@dataclass(frozen=True, slots=True)
class CountTableSpec:
    file_name: str
    fact_type: str
    table_key_prefix: str
    value_fields: tuple[str, ...]
    extra_value_fields: tuple[str, ...] = ()


@dataclass(slots=True)
class CountAccumulator:
    count_key: str
    values: dict[str, str]
    count: int = 0
    caption_count: int = 0
    first_caption_id: str | None = None
    last_caption_id: str | None = None
    example_caption_ids: list[str] | None = None
    raw_variants: set[str] | None = None
    rule_ids: set[str] | None = None

    def __post_init__(self) -> None:
        if self.example_caption_ids is None:
            self.example_caption_ids = []
        if self.raw_variants is None:
            self.raw_variants = set()
        if self.rule_ids is None:
            self.rule_ids = set()


COUNT_TABLE_SPECS = (
    CountTableSpec(
        "object_counts.tsv",
        "entity_exists",
        "object",
        ("object",),
        ("parent_concepts", "parent_synset_ids"),
    ),
    CountTableSpec(
        "attribute_counts.tsv",
        "attribute_exists",
        "attribute",
        ("attribute", "attribute_kind"),
    ),
    CountTableSpec(
        "object_parent_counts.tsv",
        "object_parent",
        "object_parent",
        ("object", "parent"),
        ("parent_synset_id",),
    ),
    CountTableSpec(
        "object_attribute_pair_counts.tsv",
        "has_attribute",
        "object_attribute_pair",
        ("object", "attribute", "attribute_kind"),
        ("object_parent_concepts", "object_parent_synset_ids"),
    ),
    CountTableSpec("action_counts.tsv", "action_event", "action", ("action",)),
    CountTableSpec(
        "agent_patient_pair_counts.tsv",
        "event_role",
        "event_role",
        ("action", "role", "target"),
        (
            "target_parent_concepts",
            "target_parent_synset_ids",
            "raw_role",
            "voice_normalization",
        ),
    ),
    CountTableSpec(
        "relation_triple_counts.tsv",
        "relation",
        "relation",
        ("source", "relation", "target"),
        (
            "source_parent_concepts",
            "source_parent_synset_ids",
            "target_parent_concepts",
            "target_parent_synset_ids",
        ),
    ),
    CountTableSpec(
        "relation_component_counts.tsv",
        "relation_component",
        "relation_component",
        ("relation", "component_index", "component"),
    ),
    CountTableSpec(
        "ambiguous_relation_candidate_counts.tsv",
        "ambiguous_relation_candidate",
        "ambiguous_relation_candidate",
        ("source_status", "relation", "target_status"),
        ("candidate_sources", "candidate_targets", "candidate_pair_count"),
    ),
    CountTableSpec(
        "object_cooccurrence_pair_counts.tsv",
        "object_pair_in_caption",
        "object_pair_in_caption",
        ("source_object", "target_object"),
        (
            "source_parent_concepts",
            "source_parent_synset_ids",
            "target_parent_concepts",
            "target_parent_synset_ids",
        ),
    ),
)

COUNT_TABLE_SPEC_BY_FACT_TYPE = {
    spec.fact_type: spec
    for spec in COUNT_TABLE_SPECS
}


def export_count_facts(
    canonical_mentions: Sequence[Mapping[str, Any] | CanonicalMention],
    canonical_edges: Sequence[Mapping[str, Any] | CanonicalEdge],
) -> CountExportResult:
    """Create Stage 6 facts and count tables from Stage 5 records."""
    mentions = [_coerce_canonical_mention(record) for record in canonical_mentions]
    edges = [_coerce_canonical_edge(record) for record in canonical_edges]
    mention_by_key = {
        (mention.caption_id, mention.mention_id): mention
        for mention in mentions
    }

    facts: list[FactRow] = []
    facts.extend(_entity_exists_facts(mentions, start_index=len(facts)))
    facts.extend(_attribute_family_exists_facts(mentions, start_index=len(facts)))
    facts.extend(_object_parent_facts(mentions, start_index=len(facts)))
    facts.extend(_action_event_facts(mentions, start_index=len(facts)))
    facts.extend(_edge_facts(edges, mention_by_key, start_index=len(facts)))
    facts.extend(_ambiguous_relation_candidate_facts(edges, mention_by_key, start_index=len(facts)))
    facts.extend(_relation_component_facts(edges, start_index=len(facts)))
    facts.extend(_object_pair_facts(mentions, start_index=len(facts)))

    count_tables = {
        "object_counts.tsv": _aggregate_facts(
            facts,
            fact_type="entity_exists",
            table_key_prefix="object",
            value_fields=("object",),
            extra_value_fields=("parent_concepts", "parent_synset_ids"),
        ),
        "attribute_counts.tsv": _aggregate_facts(
            facts,
            fact_type="attribute_exists",
            table_key_prefix="attribute",
            value_fields=("attribute", "attribute_kind"),
        ),
        "object_parent_counts.tsv": _aggregate_facts(
            facts,
            fact_type="object_parent",
            table_key_prefix="object_parent",
            value_fields=("object", "parent"),
            extra_value_fields=("parent_synset_id",),
        ),
        "object_attribute_pair_counts.tsv": _aggregate_facts(
            facts,
            fact_type="has_attribute",
            table_key_prefix="object_attribute_pair",
            value_fields=("object", "attribute", "attribute_kind"),
            extra_value_fields=(
                "object_parent_concepts",
                "object_parent_synset_ids",
            ),
        ),
        "action_counts.tsv": _aggregate_facts(
            facts,
            fact_type="action_event",
            table_key_prefix="action",
            value_fields=("action",),
        ),
        "agent_patient_pair_counts.tsv": _aggregate_facts(
            facts,
            fact_type="event_role",
            table_key_prefix="event_role",
            value_fields=("action", "role", "target"),
            extra_value_fields=(
                "target_parent_concepts",
                "target_parent_synset_ids",
                "raw_role",
                "voice_normalization",
            ),
        ),
        "relation_triple_counts.tsv": _aggregate_facts(
            facts,
            fact_type="relation",
            table_key_prefix="relation",
            value_fields=("source", "relation", "target"),
            extra_value_fields=(
                "source_parent_concepts",
                "source_parent_synset_ids",
                "target_parent_concepts",
                "target_parent_synset_ids",
            ),
        ),
        "relation_component_counts.tsv": _aggregate_facts(
            facts,
            fact_type="relation_component",
            table_key_prefix="relation_component",
            value_fields=("relation", "component_index", "component"),
        ),
        "ambiguous_relation_candidate_counts.tsv": _aggregate_facts(
            facts,
            fact_type="ambiguous_relation_candidate",
            table_key_prefix="ambiguous_relation_candidate",
            value_fields=("source_status", "relation", "target_status"),
            extra_value_fields=(
                "candidate_sources",
                "candidate_targets",
                "candidate_pair_count",
            ),
        ),
        "object_cooccurrence_pair_counts.tsv": _aggregate_facts(
            facts,
            fact_type="object_pair_in_caption",
            table_key_prefix="object_pair_in_caption",
            value_fields=("source_object", "target_object"),
            extra_value_fields=(
                "source_parent_concepts",
                "source_parent_synset_ids",
                "target_parent_concepts",
                "target_parent_synset_ids",
            ),
        ),
    }
    return CountExportResult(facts=facts, count_tables=count_tables)


def run_stage6_export_counts(
    canonical_mentions_path: str | Path,
    canonical_edges_path: str | Path,
    *,
    output_dir: str | Path,
    summary_path: str | Path | None = None,
    max_rss_gib: float | None = None,
    memory_limit_gib: float | None = None,
    rss_limit_fraction: float = 0.75,
    rss_reserve_gib: float = 16.0,
    progress_path: str | Path | None = None,
    count_backend: str = "sqlite",
    sqlite_db_path: str | Path | None = None,
    sqlite_cache_rows: int | None = None,
    facts_output_mode: str = "write",
) -> dict[str, Any]:
    """Run Stage 6 and write facts plus TSV count tables.

    This path is deliberately streaming. The in-memory helper above is useful
    for tests and small samples, but 1M-caption exports can create hundreds of
    millions of fact rows and must not materialize them as Python objects.
    """
    if facts_output_mode not in {"write", "discard"}:
        raise ValueError("facts_output_mode must be one of: write, discard")
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    facts_path = output_root / "facts.jsonl"

    fact_type_counts: dict[str, int] = defaultdict(int)
    fact_total = 0
    caption_group_total = 0
    memory_config = MemorySafetyConfig(
        max_rss_gib=max_rss_gib,
        memory_limit_gib=memory_limit_gib,
        rss_limit_fraction=rss_limit_fraction,
        rss_reserve_gib=rss_reserve_gib,
    )
    count_store = _make_count_store(
        backend=count_backend,
        output_root=output_root,
        sqlite_db_path=sqlite_db_path,
        sqlite_cache_rows=sqlite_cache_rows,
        memory_config=memory_config,
    )
    progress = ProgressWriter(
        progress_path,
        stage_name="stage6",
        memory_config=memory_config,
    )
    progress.write(
        status="running",
        phase="stage6_streaming",
        note="started",
        metrics={
            "caption_groups_processed": caption_group_total,
            "fact_total": fact_total,
            "fact_type_counts": dict(fact_type_counts),
        },
        outputs={"facts": facts_path, "output_dir": output_root},
    )
    mention_groups = _iter_caption_groups(canonical_mentions_path, _coerce_canonical_mention)
    edge_groups = _CaptionGroupReader(canonical_edges_path, _coerce_canonical_edge)

    try:
        facts_handle = open_text(facts_path, "wt") if facts_output_mode == "write" else nullcontext()
        with facts_handle as handle:
            for caption_id, mentions in mention_groups:
                progress.check_memory(
                    phase="stage6_streaming",
                    metrics={"caption_groups_processed": caption_group_total},
                )
                edges = edge_groups.take_if_caption(caption_id)
                facts = _caption_facts(
                    mentions,
                    edges,
                    start_index=fact_total,
                )
                for fact in facts:
                    if handle is not None:
                        handle.write(
                            json.dumps(to_jsonable(fact), ensure_ascii=False, sort_keys=False),
                        )
                        handle.write("\n")
                    count_store.accumulate(fact)
                    fact_type_counts[fact.fact_type] += 1
                    fact_total += 1
                caption_group_total += 1
                if caption_group_total % 1000 == 0:
                    progress.write(
                        status="running",
                        phase="stage6_streaming",
                        note="streaming_facts",
                        metrics={
                            "caption_groups_processed": caption_group_total,
                            "fact_total": fact_total,
                            "fact_type_counts": dict(fact_type_counts),
                        },
                        outputs={"facts": facts_path, "output_dir": output_root},
                    )

            leftover_caption = edge_groups.peek_caption()
            if leftover_caption is not None:
                raise ValueError(
                    "canonical_edges contains a caption with no matching canonical_mentions "
                    f"group or the files are not in the same caption order: {leftover_caption!r}",
                )
    except Exception as exc:
        count_store.close()
        progress.write(
            status="failed",
            phase="stage6_streaming",
            note=f"{type(exc).__name__}: {exc}",
            metrics={
                "caption_groups_processed": caption_group_total,
                "fact_total": fact_total,
                "fact_type_counts": dict(fact_type_counts),
            },
            outputs={"facts": facts_path, "output_dir": output_root},
        )
        raise

    progress.write(
        status="running",
        phase="stage6_writing_tables",
        note="facts_complete",
        metrics={
            "caption_groups_processed": caption_group_total,
            "fact_total": fact_total,
            "fact_type_counts": dict(fact_type_counts),
        },
        outputs={"facts": facts_path, "output_dir": output_root},
    )

    table_paths: dict[str, str] = {}
    table_row_counts: dict[str, int] = {}
    count_integrity: dict[str, Any] = {}
    try:
        table_paths, table_row_counts = count_store.write_tables(output_root)
        count_integrity = count_store.integrity_report(fact_type_counts, fact_total)
    finally:
        count_store.close()

    summary = {
        "canonical_mentions_path": str(canonical_mentions_path),
        "canonical_edges_path": str(canonical_edges_path),
        "output_dir": str(output_root),
        "facts_path": str(facts_path),
        "facts_output_mode": facts_output_mode,
        "fact_total": fact_total,
        "fact_type_counts": dict(sorted(fact_type_counts.items())),
        "table_paths": table_paths,
        "table_row_counts": dict(sorted(table_row_counts.items())),
        "count_integrity": count_integrity,
        "export_mode": "streaming_count_accumulator",
        "count_backend": count_store.backend_name,
        "sqlite_db_path": count_store.db_path_for_summary,
        "sqlite_cache_rows": sqlite_cache_rows if count_store.backend_name == "sqlite" else None,
        "sqlite_cache_policy": count_store.cache_policy_for_summary,
        "sqlite_cache_flush_rss_gib": count_store.cache_flush_rss_gib_for_summary,
        "memory_limit_gib": memory_config.resolved_memory_limit_gib,
        "memory_limit_source": memory_config.memory_limit_source,
        "rss_limit_fraction": rss_limit_fraction,
        "rss_reserve_gib": rss_reserve_gib,
        "max_rss_gib": memory_config.effective_max_rss_gib,
    }
    if summary_path is not None:
        write_jsonl(summary_path, [summary])
    progress.write(
        status="complete",
        phase="stage6_complete",
        note="complete",
        metrics={
            "caption_groups_processed": caption_group_total,
            "fact_total": fact_total,
            "fact_type_counts": dict(fact_type_counts),
        },
        outputs={"facts": facts_path, "output_dir": output_root},
        summary=summary,
    )
    return summary


def _caption_facts(
    mentions: Sequence[CanonicalMention],
    edges: Sequence[CanonicalEdge],
    *,
    start_index: int,
) -> list[FactRow]:
    mention_by_key = {
        (mention.caption_id, mention.mention_id): mention
        for mention in mentions
    }
    facts: list[FactRow] = []
    facts.extend(_entity_exists_facts(mentions, start_index=start_index + len(facts)))
    facts.extend(_attribute_family_exists_facts(mentions, start_index=start_index + len(facts)))
    facts.extend(_object_parent_facts(mentions, start_index=start_index + len(facts)))
    facts.extend(_action_event_facts(mentions, start_index=start_index + len(facts)))
    facts.extend(_edge_facts(edges, mention_by_key, start_index=start_index + len(facts)))
    facts.extend(
        _ambiguous_relation_candidate_facts(
            edges,
            mention_by_key,
            start_index=start_index + len(facts),
        ),
    )
    facts.extend(_relation_component_facts(edges, start_index=start_index + len(facts)))
    facts.extend(_object_pair_facts(mentions, start_index=start_index + len(facts)))
    return facts


def _iter_caption_groups(
    path: str | Path,
    coerce_record: Any,
) -> Iterable[tuple[str, list[Any]]]:
    current_caption_id: str | None = None
    group: list[Any] = []
    for record in iter_jsonl(path):
        item = coerce_record(record)
        caption_id = item.caption_id
        if current_caption_id is None:
            current_caption_id = caption_id
        if caption_id != current_caption_id:
            yield current_caption_id, group
            current_caption_id = caption_id
            group = []
        group.append(item)
    if current_caption_id is not None:
        yield current_caption_id, group


class _CaptionGroupReader:
    def __init__(self, path: str | Path, coerce_record: Any) -> None:
        self._groups = iter(_iter_caption_groups(path, coerce_record))
        self._next: tuple[str, list[Any]] | None = None
        self._advance()

    def peek_caption(self) -> str | None:
        if self._next is None:
            return None
        return self._next[0]

    def take_if_caption(self, caption_id: str) -> list[Any]:
        if self._next is None:
            return []
        next_caption_id, group = self._next
        if next_caption_id != caption_id:
            return []
        self._advance()
        return group

    def _advance(self) -> None:
        self._next = next(self._groups, None)


class _MemoryCountStore:
    backend_name = "memory"
    db_path_for_summary: str | None = None
    cache_policy_for_summary = "memory_unbounded"
    cache_flush_rss_gib_for_summary: float | None = None

    def __init__(self) -> None:
        self._table_buckets = _new_count_table_buckets()

    def accumulate(self, fact: FactRow) -> None:
        _accumulate_count_fact(self._table_buckets, fact)

    def write_tables(self, output_root: Path) -> tuple[dict[str, str], dict[str, int]]:
        table_paths: dict[str, str] = {}
        table_row_counts: dict[str, int] = {}
        for spec in COUNT_TABLE_SPECS:
            rows = _count_rows_from_buckets(self._table_buckets[spec.file_name])
            path = output_root / spec.file_name
            _write_count_table_tsv(
                path,
                rows,
                value_fields=(*spec.value_fields, *spec.extra_value_fields),
            )
            table_paths[spec.file_name] = str(path)
            table_row_counts[spec.file_name] = len(rows)
        return table_paths, table_row_counts

    def integrity_report(
        self,
        expected_fact_type_counts: Mapping[str, int],
        fact_total: int,
    ) -> dict[str, Any]:
        table_count_sums = {
            spec.file_name: sum(
                accumulator.count
                for accumulator in self._table_buckets[spec.file_name].values()
            )
            for spec in COUNT_TABLE_SPECS
        }
        table_row_counts = {
            spec.file_name: len(self._table_buckets[spec.file_name])
            for spec in COUNT_TABLE_SPECS
        }
        return _count_integrity_report(
            expected_fact_type_counts=expected_fact_type_counts,
            fact_total=fact_total,
            table_count_sums=table_count_sums,
            table_row_counts=table_row_counts,
        )

    def close(self) -> None:
        return


class _SqliteCountStore:
    backend_name = "sqlite"

    def __init__(
        self,
        db_path: str | Path,
        *,
        cache_rows: int | None,
        memory_config: MemorySafetyConfig,
    ) -> None:
        if cache_rows is not None and cache_rows < 1:
            raise ValueError("--sqlite-cache-rows must be greater than zero")
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        for path in (
            self._db_path,
            self._db_path.with_name(self._db_path.name + "-wal"),
            self._db_path.with_name(self._db_path.name + "-shm"),
        ):
            path.unlink(missing_ok=True)
        self.db_path_for_summary = str(self._db_path)
        self._cache_rows_limit = cache_rows
        self._cache_flush_rss_gib = memory_config.effective_max_rss_gib
        self.cache_flush_rss_gib_for_summary = self._cache_flush_rss_gib
        if self._cache_flush_rss_gib is not None and cache_rows is not None:
            self.cache_policy_for_summary = "rss_adaptive_with_row_hard_cap"
        elif self._cache_flush_rss_gib is not None:
            self.cache_policy_for_summary = "rss_adaptive"
        elif cache_rows is not None:
            self.cache_policy_for_summary = "row_hard_cap_only"
        else:
            self.cache_policy_for_summary = "unbounded_no_memory_limit_detected"
        self._cache = _new_count_table_buckets()
        self._cache_row_count = 0
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=FILE")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS count_accumulators (
                table_name TEXT NOT NULL,
                key_json TEXT NOT NULL,
                count_key TEXT NOT NULL,
                values_json TEXT NOT NULL,
                count INTEGER NOT NULL,
                caption_count INTEGER NOT NULL,
                last_caption_id TEXT,
                example_caption_ids_json TEXT NOT NULL,
                raw_variants_json TEXT NOT NULL,
                rule_ids_json TEXT NOT NULL,
                PRIMARY KEY (table_name, key_json)
            )
            """,
        )
        self._conn.commit()

    def accumulate(self, fact: FactRow) -> None:
        spec = COUNT_TABLE_SPEC_BY_FACT_TYPE.get(fact.fact_type)
        if spec is None:
            return
        key_values = tuple(str(fact.values[field]) for field in spec.value_fields)
        bucket = self._cache[spec.file_name]
        before = len(bucket)
        _accumulate_count_fact(self._cache, fact)
        if len(bucket) != before:
            self._cache_row_count += 1
            if self._should_flush_cache():
                self.flush()

    def _should_flush_cache(self) -> bool:
        if self._cache_row_count == 0:
            return False
        if self._cache_rows_limit is not None and self._cache_row_count >= self._cache_rows_limit:
            return True
        if self._cache_flush_rss_gib is None:
            return False
        rss_kib = current_rss_kib()
        if rss_kib is None:
            return False
        return rss_kib / 1024 / 1024 >= self._cache_flush_rss_gib

    def flush(self) -> None:
        if self._cache_row_count == 0:
            return
        with self._conn:
            for table_name, bucket in self._cache.items():
                for key_values, accumulator in bucket.items():
                    self._merge_accumulator(table_name, key_values, accumulator)
        self._cache = _new_count_table_buckets()
        self._cache_row_count = 0

    def write_tables(self, output_root: Path) -> tuple[dict[str, str], dict[str, int]]:
        self.flush()
        table_paths: dict[str, str] = {}
        table_row_counts: dict[str, int] = {}
        for spec in COUNT_TABLE_SPECS:
            path = output_root / spec.file_name
            row_count = _write_count_table_tsv_from_iter(
                path,
                value_fields=(*spec.value_fields, *spec.extra_value_fields),
                rows=self._iter_count_rows(spec.file_name),
            )
            table_paths[spec.file_name] = str(path)
            table_row_counts[spec.file_name] = row_count
        return table_paths, table_row_counts

    def integrity_report(
        self,
        expected_fact_type_counts: Mapping[str, int],
        fact_total: int,
    ) -> dict[str, Any]:
        self.flush()
        table_count_sums: dict[str, int] = {}
        table_row_counts: dict[str, int] = {}
        for spec in COUNT_TABLE_SPECS:
            count_sum, row_count = self._conn.execute(
                """
                SELECT COALESCE(SUM(count), 0), COUNT(*)
                FROM count_accumulators
                WHERE table_name = ?
                """,
                (spec.file_name,),
            ).fetchone()
            table_count_sums[spec.file_name] = int(count_sum)
            table_row_counts[spec.file_name] = int(row_count)
        return _count_integrity_report(
            expected_fact_type_counts=expected_fact_type_counts,
            fact_total=fact_total,
            table_count_sums=table_count_sums,
            table_row_counts=table_row_counts,
        )

    def close(self) -> None:
        self._conn.close()

    def _merge_accumulator(
        self,
        table_name: str,
        key_values: tuple[str, ...],
        accumulator: CountAccumulator,
    ) -> None:
        key_json = _json_dumps_list(key_values)
        row = self._conn.execute(
            """
            SELECT count_key, values_json, count, caption_count, last_caption_id,
                   example_caption_ids_json, raw_variants_json, rule_ids_json
            FROM count_accumulators
            WHERE table_name = ? AND key_json = ?
            """,
            (table_name, key_json),
        ).fetchone()
        if row is None:
            self._conn.execute(
                """
                INSERT INTO count_accumulators (
                    table_name, key_json, count_key, values_json, count,
                    caption_count, last_caption_id, example_caption_ids_json,
                    raw_variants_json, rule_ids_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    table_name,
                    key_json,
                    accumulator.count_key,
                    _json_dumps_mapping(accumulator.values),
                    accumulator.count,
                    accumulator.caption_count,
                    accumulator.last_caption_id,
                    _json_dumps_list(accumulator.example_caption_ids or []),
                    _json_dumps_list(sorted(accumulator.raw_variants or set())),
                    _json_dumps_list(sorted(accumulator.rule_ids or set())),
                ),
            )
            return

        (
            count_key,
            values_json,
            old_count,
            old_caption_count,
            old_last_caption_id,
            example_caption_ids_json,
            raw_variants_json,
            rule_ids_json,
        ) = row
        merged_caption_count = int(old_caption_count) + accumulator.caption_count
        if (
            accumulator.first_caption_id is not None
            and old_last_caption_id == accumulator.first_caption_id
        ):
            merged_caption_count -= 1
        self._conn.execute(
            """
            UPDATE count_accumulators
            SET values_json = ?,
                count = ?,
                caption_count = ?,
                last_caption_id = ?,
                example_caption_ids_json = ?,
                raw_variants_json = ?,
                rule_ids_json = ?
            WHERE table_name = ? AND key_json = ?
            """,
            (
                _json_dumps_mapping(
                    _merge_values(
                        _json_loads_mapping(values_json),
                        accumulator.values,
                    ),
                ),
                int(old_count) + accumulator.count,
                merged_caption_count,
                accumulator.last_caption_id or old_last_caption_id,
                _json_dumps_list(
                    _limited_sorted_union(
                        _json_loads_list(example_caption_ids_json),
                        accumulator.example_caption_ids or [],
                        limit=5,
                    ),
                ),
                _json_dumps_list(
                    sorted(
                        set(_json_loads_list(raw_variants_json))
                        | set(accumulator.raw_variants or set()),
                    ),
                ),
                _json_dumps_list(
                    sorted(
                        set(_json_loads_list(rule_ids_json))
                        | set(accumulator.rule_ids or set()),
                    ),
                ),
                table_name,
                key_json,
            ),
        )

    def _iter_count_rows(self, table_name: str) -> Iterable[CountRow]:
        for row in self._conn.execute(
            """
            SELECT count_key, values_json, count, caption_count,
                   example_caption_ids_json, raw_variants_json, rule_ids_json
            FROM count_accumulators
            WHERE table_name = ?
            ORDER BY count DESC, count_key ASC
            """,
            (table_name,),
        ):
            (
                count_key,
                values_json,
                count,
                caption_count,
                example_caption_ids_json,
                raw_variants_json,
                rule_ids_json,
            ) = row
            yield CountRow(
                count_key=count_key,
                count=int(count),
                caption_count=int(caption_count),
                example_caption_ids=_json_loads_list(example_caption_ids_json),
                raw_variants=_json_loads_list(raw_variants_json),
                rule_ids=_json_loads_list(rule_ids_json),
                values=_json_loads_mapping(values_json),
            )


def _make_count_store(
    *,
    backend: str,
    output_root: Path,
    sqlite_db_path: str | Path | None,
    sqlite_cache_rows: int | None,
    memory_config: MemorySafetyConfig,
) -> _MemoryCountStore | _SqliteCountStore:
    if backend == "memory":
        return _MemoryCountStore()
    if backend == "sqlite":
        return _SqliteCountStore(
            sqlite_db_path or output_root / "stage6_count_accumulators.sqlite3",
            cache_rows=sqlite_cache_rows,
            memory_config=memory_config,
        )
    raise ValueError("--count-backend must be one of: memory, sqlite")


def _count_integrity_report(
    *,
    expected_fact_type_counts: Mapping[str, int],
    fact_total: int,
    table_count_sums: Mapping[str, int],
    table_row_counts: Mapping[str, int],
) -> dict[str, Any]:
    supported_fact_types = {spec.fact_type for spec in COUNT_TABLE_SPECS}
    unexpected_fact_types = sorted(set(expected_fact_type_counts) - supported_fact_types)
    if unexpected_fact_types:
        raise ValueError(
            "Stage 6 generated facts with no count table spec: "
            + ", ".join(unexpected_fact_types),
        )

    actual_by_fact_type = {
        spec.fact_type: int(table_count_sums.get(spec.file_name, 0))
        for spec in COUNT_TABLE_SPECS
    }
    expected_by_fact_type = {
        spec.fact_type: int(expected_fact_type_counts.get(spec.fact_type, 0))
        for spec in COUNT_TABLE_SPECS
    }
    deltas = {
        fact_type: actual_by_fact_type[fact_type] - expected_count
        for fact_type, expected_count in expected_by_fact_type.items()
        if actual_by_fact_type[fact_type] != expected_count
    }
    total_counted_facts = sum(actual_by_fact_type.values())
    if total_counted_facts != fact_total:
        deltas["__total__"] = total_counted_facts - fact_total

    report = {
        "status": "ok" if not deltas else "failed",
        "expected_fact_type_counts": dict(sorted(expected_by_fact_type.items())),
        "actual_fact_type_count_sums": dict(sorted(actual_by_fact_type.items())),
        "table_count_sums": dict(sorted((str(key), int(value)) for key, value in table_count_sums.items())),
        "table_row_counts": dict(sorted((str(key), int(value)) for key, value in table_row_counts.items())),
        "fact_total": int(fact_total),
        "total_counted_facts": int(total_counted_facts),
        "deltas": dict(sorted(deltas.items())),
    }
    if deltas:
        raise ValueError(
            "Stage 6 count integrity check failed: "
            + json.dumps(report, ensure_ascii=False, sort_keys=True),
        )
    return report


def _new_count_table_buckets() -> dict[str, dict[tuple[str, ...], CountAccumulator]]:
    return {
        spec.file_name: {}
        for spec in COUNT_TABLE_SPECS
    }


def _accumulate_count_fact(
    table_buckets: dict[str, dict[tuple[str, ...], CountAccumulator]],
    fact: FactRow,
) -> None:
    spec = COUNT_TABLE_SPEC_BY_FACT_TYPE.get(fact.fact_type)
    if spec is None:
        return
    key_values = tuple(str(fact.values[field]) for field in spec.value_fields)
    bucket = table_buckets[spec.file_name]
    accumulator = bucket.get(key_values)
    if accumulator is None:
        values = {
            field: value
            for field, value in zip(spec.value_fields, key_values, strict=True)
        }
        for field in spec.extra_value_fields:
            values[field] = ""
        accumulator = CountAccumulator(
            count_key=":".join((spec.table_key_prefix, *key_values)),
            values=values,
        )
        bucket[key_values] = accumulator

    accumulator.count += 1
    if accumulator.last_caption_id != fact.caption_id:
        if accumulator.first_caption_id is None:
            accumulator.first_caption_id = fact.caption_id
        accumulator.caption_count += 1
        accumulator.last_caption_id = fact.caption_id
        if accumulator.example_caption_ids is not None:
            _accumulate_example_caption_id(accumulator.example_caption_ids, fact.caption_id)

    if accumulator.raw_variants is not None:
        value = fact.values.get("raw_variants")
        if isinstance(value, list):
            accumulator.raw_variants.update(str(item) for item in value if str(item))
    if accumulator.rule_ids is not None:
        accumulator.rule_ids.update(fact.rule_ids)
        accumulator.rule_ids.add(COUNT_RULE_ID)
    for field in spec.extra_value_fields:
        _accumulate_extra_value(accumulator, field, fact.values.get(field))


def _accumulate_extra_value(
    accumulator: CountAccumulator,
    field: str,
    value: Any,
) -> None:
    existing = accumulator.values.get(field, "")
    values = set(existing.split("|")) if existing else set()
    if isinstance(value, list):
        values.update(str(item) for item in value if str(item))
    elif value is not None:
        text = str(value)
        if text:
            values.add(text)
    accumulator.values[field] = "|".join(sorted(values))


def _accumulate_example_caption_id(
    example_caption_ids: list[str],
    caption_id: str,
) -> None:
    if caption_id in example_caption_ids:
        return
    example_caption_ids.append(caption_id)
    example_caption_ids.sort()
    del example_caption_ids[5:]


def _count_rows_from_buckets(
    bucket: Mapping[tuple[str, ...], CountAccumulator],
) -> list[CountRow]:
    rows = [
        CountRow(
            count_key=accumulator.count_key,
            count=accumulator.count,
            caption_count=accumulator.caption_count,
            example_caption_ids=list(accumulator.example_caption_ids or []),
            raw_variants=sorted(accumulator.raw_variants or set()),
            rule_ids=sorted(accumulator.rule_ids or set()),
            values=dict(accumulator.values),
        )
        for accumulator in bucket.values()
    ]
    rows.sort(key=lambda row: (-row.count, row.count_key))
    return rows


def _json_dumps_list(values: Sequence[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _json_dumps_mapping(values: Mapping[str, str]) -> str:
    return json.dumps(dict(values), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads_list(value: str) -> list[str]:
    loaded = json.loads(value)
    if not isinstance(loaded, list):
        raise ValueError("expected JSON list")
    return [str(item) for item in loaded if str(item)]


def _json_loads_mapping(value: str) -> dict[str, str]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("expected JSON object")
    return {str(key): str(item) for key, item in loaded.items()}


def _limited_sorted_union(
    existing: Sequence[str],
    incoming: Sequence[str],
    *,
    limit: int,
) -> list[str]:
    return sorted(set(existing) | set(incoming))[:limit]


def _merge_values(old_values: Mapping[str, str], new_values: Mapping[str, str]) -> dict[str, str]:
    values = dict(old_values)
    for field, new_value in new_values.items():
        old_value = values.get(field, "")
        if not old_value:
            values[field] = new_value
        elif new_value and old_value != new_value:
            merged = sorted(set(old_value.split("|")) | set(new_value.split("|")))
            values[field] = "|".join(item for item in merged if item)
    return values


def _make_fact_row(
    *,
    caption_id: str,
    fact_index: int,
    fact_type: str,
    count_key: str,
    rule_ids: list[str],
    source_mention_ids: list[str],
    source_edge_ids: list[str],
    values: JsonObject,
) -> FactRow:
    # Stage 6 builds these fields from already validated records and constants.
    fact = object.__new__(FactRow)
    fact.caption_id = caption_id
    fact.fact_id = f"f{fact_index}"
    fact.fact_type = fact_type  # type: ignore[assignment]
    fact.count_key = count_key
    fact.rule_ids = rule_ids
    fact.source_mention_ids = source_mention_ids
    fact.source_edge_ids = source_edge_ids
    fact.values = values
    return fact


def _entity_exists_facts(
    mentions: Sequence[CanonicalMention],
    *,
    start_index: int,
) -> list[FactRow]:
    facts: list[FactRow] = []
    for mention in mentions:
        if mention.mention_type != "object":
            continue
        facts.append(
            _make_fact_row(
                caption_id=mention.caption_id,
                fact_index=start_index + len(facts),
                fact_type="entity_exists",
                count_key=f"entity_exists:{mention.canonical}",
                rule_ids=_mention_rule_ids(mention),
                source_mention_ids=[mention.mention_id],
                source_edge_ids=[],
                values={
                    "object": mention.canonical,
                    "parent_concepts": mention.parent_concepts,
                    "parent_synset_ids": _parent_synset_ids(mention),
                    "raw_variants": [_raw_variant(mention)],
                },
            ),
        )
    return facts


def _action_event_facts(
    mentions: Sequence[CanonicalMention],
    *,
    start_index: int,
) -> list[FactRow]:
    facts: list[FactRow] = []
    for mention in mentions:
        if mention.mention_type != "action":
            continue
        facts.append(
            _make_fact_row(
                caption_id=mention.caption_id,
                fact_index=start_index + len(facts),
                fact_type="action_event",
                count_key=f"action_event:{mention.canonical}",
                rule_ids=_mention_rule_ids(mention),
                source_mention_ids=[mention.mention_id],
                source_edge_ids=[],
                values={
                    "action": mention.canonical,
                    "raw_variants": [_raw_variant(mention)],
                },
            ),
        )
    return facts


def _attribute_family_exists_facts(
    mentions: Sequence[CanonicalMention],
    *,
    start_index: int,
) -> list[FactRow]:
    facts: list[FactRow] = []
    for mention in mentions:
        if mention.mention_type not in {"attribute", "quantity"}:
            continue
        attribute_kind = "quantity" if mention.mention_type == "quantity" else "attribute"
        facts.append(
            _make_fact_row(
                caption_id=mention.caption_id,
                fact_index=start_index + len(facts),
                fact_type="attribute_exists",
                count_key=(
                    f"attribute_exists:{mention.canonical}:{attribute_kind}"
                ),
                rule_ids=_mention_rule_ids(mention),
                source_mention_ids=[mention.mention_id],
                source_edge_ids=[],
                values={
                    "attribute": mention.canonical,
                    "attribute_kind": attribute_kind,
                    "raw_variants": [_raw_variant(mention)],
                },
            ),
        )
    return facts


def _quantity_exists_facts(
    mentions: Sequence[CanonicalMention],
    *,
    start_index: int,
) -> list[FactRow]:
    facts: list[FactRow] = []
    for mention in mentions:
        if mention.mention_type != "quantity":
            continue
        facts.append(
            _make_fact_row(
                caption_id=mention.caption_id,
                fact_index=start_index + len(facts),
                fact_type="quantity_exists",
                count_key=f"quantity_exists:{mention.canonical}",
                rule_ids=_mention_rule_ids(mention),
                source_mention_ids=[mention.mention_id],
                source_edge_ids=[],
                values={
                    "quantity": mention.canonical,
                    "raw_variants": [_raw_variant(mention)],
                },
            ),
        )
    return facts


def _object_parent_facts(
    mentions: Sequence[CanonicalMention],
    *,
    start_index: int,
) -> list[FactRow]:
    facts: list[FactRow] = []
    for mention in mentions:
        if mention.mention_type != "object" or not mention.parent_concepts:
            continue
        parent_synset_ids = _parent_synset_ids(mention)
        for index, parent in enumerate(mention.parent_concepts):
            parent_synset_id = (
                parent_synset_ids[index] if index < len(parent_synset_ids) else ""
            )
            facts.append(
                _make_fact_row(
                    caption_id=mention.caption_id,
                    fact_index=start_index + len(facts),
                    fact_type="object_parent",
                    count_key=f"object_parent:{mention.canonical}:{parent}",
                    rule_ids=_mention_rule_ids(mention),
                    source_mention_ids=[mention.mention_id],
                    source_edge_ids=[],
                    values={
                        "object": mention.canonical,
                        "parent": parent,
                        "parent_synset_id": parent_synset_id,
                        "raw_variants": [_raw_variant(mention)],
                    },
                ),
            )
    return facts


def _edge_facts(
    edges: Sequence[CanonicalEdge],
    mention_by_key: Mapping[tuple[str, str], CanonicalMention],
    *,
    start_index: int,
) -> list[FactRow]:
    facts: list[FactRow] = []
    for edge in edges:
        if edge.edge_type == "ambiguous_relation_candidate":
            continue
        source = _require_mention(mention_by_key, edge.caption_id, edge.source_mention_id)
        target = _require_mention(mention_by_key, edge.caption_id, edge.target_mention_id)
        if edge.edge_type == "has_attribute":
            fact_type = "has_attribute"
            values = {
                "object": source.canonical,
                "attribute": target.canonical,
                "attribute_kind": "attribute",
                "object_parent_concepts": source.parent_concepts,
                "object_parent_synset_ids": _parent_synset_ids(source),
            }
            count_key = (
                f"has_attribute:{source.canonical}:{target.canonical}:attribute"
            )
        elif edge.edge_type == "has_quantity":
            fact_type = "has_attribute"
            values = {
                "object": source.canonical,
                "attribute": target.canonical,
                "attribute_kind": "quantity",
                "object_parent_concepts": source.parent_concepts,
                "object_parent_synset_ids": _parent_synset_ids(source),
            }
            count_key = (
                f"has_attribute:{source.canonical}:{target.canonical}:quantity"
            )
        elif edge.edge_type == "event_role":
            fact_type = edge.edge_type
            raw_role = edge.canonical_detail.get("raw_role")
            voice_normalization = edge.canonical_detail.get("voice_normalization")
            values = {
                "action": source.canonical,
                "role": edge.canonical_label,
                "target": target.canonical,
                "target_parent_concepts": target.parent_concepts,
                "target_parent_synset_ids": _parent_synset_ids(target),
                "raw_role": raw_role if isinstance(raw_role, str) and raw_role else edge.canonical_label,
                "voice_normalization": (
                    voice_normalization
                    if isinstance(voice_normalization, str) and voice_normalization
                    else "none"
                ),
            }
            count_key = f"event_role:{source.canonical}:{edge.canonical_label}:{target.canonical}"
        elif edge.edge_type == "relation":
            fact_type = edge.edge_type
            values = {
                "source": source.canonical,
                "relation": edge.canonical_label,
                "target": target.canonical,
                "source_parent_concepts": source.parent_concepts,
                "source_parent_synset_ids": _parent_synset_ids(source),
                "target_parent_concepts": target.parent_concepts,
                "target_parent_synset_ids": _parent_synset_ids(target),
            }
            count_key = f"relation:{source.canonical}:{edge.canonical_label}:{target.canonical}"
        else:
            raise ValueError(f"unsupported edge type: {edge.edge_type}")

        facts.append(
            _make_fact_row(
                caption_id=edge.caption_id,
                fact_index=start_index + len(facts),
                fact_type=fact_type,
                count_key=count_key,
                rule_ids=_edge_rule_ids(edge, source, target),
                source_mention_ids=[edge.source_mention_id, edge.target_mention_id],
                source_edge_ids=[edge.edge_id],
                values=values,
            ),
        )
    return facts


def _ambiguous_relation_candidate_facts(
    edges: Sequence[CanonicalEdge],
    mention_by_key: Mapping[tuple[str, str], CanonicalMention],
    *,
    start_index: int,
) -> list[FactRow]:
    grouped_edges: dict[tuple[str, str, tuple[str, ...]], list[CanonicalEdge]] = defaultdict(list)
    for edge in edges:
        if edge.edge_type != "ambiguous_relation_candidate":
            continue
        matched_token_indices = _matched_token_indices_key(edge)
        key = (edge.caption_id, edge.canonical_label, matched_token_indices)
        grouped_edges[key].append(edge)

    facts: list[FactRow] = []
    for (caption_id, relation, _matched_token_indices), group in sorted(grouped_edges.items()):
        candidate_sources: set[str] = set()
        candidate_targets: set[str] = set()
        source_mention_ids: set[str] = set()
        rule_ids: set[str] = set()
        source_missing = False
        target_missing = False
        for edge in group:
            rule_ids.update(_edge_only_rule_ids(edge))
            if edge.source_mention_id == MISSING_SOURCE_MENTION_ID:
                source_missing = True
                candidate_sources.add("source_missing")
            else:
                source = _require_mention(mention_by_key, edge.caption_id, edge.source_mention_id)
                candidate_sources.add(source.canonical)
                source_mention_ids.add(edge.source_mention_id)
                rule_ids.update(source_mention_rule_ids_without_parent(source))
                if source.parent_rule_id is not None:
                    rule_ids.add(source.parent_rule_id)
            if edge.target_mention_id == MISSING_TARGET_MENTION_ID:
                target_missing = True
                candidate_targets.add("target_missing")
            else:
                target = _require_mention(mention_by_key, edge.caption_id, edge.target_mention_id)
                candidate_targets.add(target.canonical)
                source_mention_ids.add(edge.target_mention_id)
                rule_ids.update(source_mention_rule_ids_without_parent(target))
                if target.parent_rule_id is not None:
                    rule_ids.add(target.parent_rule_id)

        source_status = _relation_candidate_status(
            candidate_sources,
            missing=source_missing,
            endpoint_name="source",
        )
        target_status = _relation_candidate_status(
            candidate_targets,
            missing=target_missing,
            endpoint_name="target",
        )
        facts.append(
            _make_fact_row(
                caption_id=caption_id,
                fact_index=start_index + len(facts),
                fact_type="ambiguous_relation_candidate",
                count_key=(
                    "ambiguous_relation_candidate:"
                    f"{source_status}:{relation}:{target_status}"
                ),
                rule_ids=sorted(rule_ids),
                source_mention_ids=sorted(source_mention_ids),
                source_edge_ids=sorted(edge.edge_id for edge in group),
                values={
                    "source_status": source_status,
                    "relation": relation,
                    "target_status": target_status,
                    "candidate_sources": sorted(candidate_sources),
                    "candidate_targets": sorted(candidate_targets),
                    "candidate_pair_count": str(len(group)),
                    "raw_variants": sorted({_edge_raw_variant(edge) for edge in group}),
                },
            ),
        )
    return facts


def _relation_candidate_status(
    candidates: set[str],
    *,
    missing: bool,
    endpoint_name: str,
) -> str:
    if missing:
        return f"{endpoint_name}_missing"
    if len(candidates) > 1:
        return f"{endpoint_name}_ambiguous"
    return f"{endpoint_name}_resolved"


def _matched_token_indices_key(edge: CanonicalEdge) -> tuple[str, ...]:
    value = edge.canonical_detail.get("matched_token_indices")
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, tuple):
        return tuple(str(item) for item in value)
    return (edge.edge_id,)


def _relation_component_facts(
    edges: Sequence[CanonicalEdge],
    *,
    start_index: int,
) -> list[FactRow]:
    facts: list[FactRow] = []
    for edge in edges:
        if edge.edge_type != "relation":
            continue
        components = _relation_components(edge)
        raw_variant = _edge_raw_variant(edge)
        for index, component in enumerate(components):
            facts.append(
                _make_fact_row(
                    caption_id=edge.caption_id,
                    fact_index=start_index + len(facts),
                    fact_type="relation_component",
                    count_key=(
                        "relation_component:"
                        f"{edge.canonical_label}:{index}:{component}"
                    ),
                    rule_ids=_edge_only_rule_ids(edge),
                    source_mention_ids=[edge.source_mention_id, edge.target_mention_id],
                    source_edge_ids=[edge.edge_id],
                    values={
                        "relation": edge.canonical_label,
                        "component_index": str(index),
                        "component": component,
                        "raw_variants": [raw_variant],
                    },
                ),
            )
    return facts


def _relation_components(edge: CanonicalEdge) -> list[str]:
    components = edge.canonical_detail.get("relation_components")
    if isinstance(components, list):
        return [
            str(component).strip().lower()
            for component in components
            if str(component).strip()
        ]
    return [
        component
        for component in edge.canonical_label.strip().lower().split()
        if component
    ]


def _object_pair_facts(
    mentions: Sequence[CanonicalMention],
    *,
    start_index: int,
) -> list[FactRow]:
    objects_by_caption: dict[str, dict[str, list[CanonicalMention]]] = defaultdict(dict)
    for mention in mentions:
        if mention.mention_type != "object":
            continue
        objects_by_caption[mention.caption_id].setdefault(mention.canonical, []).append(mention)

    facts: list[FactRow] = []
    for caption_id in sorted(objects_by_caption):
        object_map = objects_by_caption[caption_id]
        canonical_objects = sorted(object_map)
        mention_ids_by_object = {
            canonical: [mention.mention_id for mention in object_mentions]
            for canonical, object_mentions in object_map.items()
        }
        rule_ids_by_object = {
            canonical: {
                rule_id
                for mention in object_mentions
                for rule_id in _mention_rule_ids(mention)
            }
            for canonical, object_mentions in object_map.items()
        }
        parent_concepts_by_object = {
            canonical: sorted(
                {
                    parent
                    for mention in object_mentions
                    for parent in mention.parent_concepts
                },
            )
            for canonical, object_mentions in object_map.items()
        }
        parent_synset_ids_by_object = {
            canonical: sorted(
                {
                    parent_synset_id
                    for mention in object_mentions
                    for parent_synset_id in _parent_synset_ids(mention)
                },
            )
            for canonical, object_mentions in object_map.items()
        }
        for source_object in canonical_objects:
            for target_object in canonical_objects:
                if source_object == target_object:
                    continue
                rule_ids = sorted(
                    rule_ids_by_object[source_object]
                    | rule_ids_by_object[target_object]
                    | {COUNT_RULE_ID},
                )
                facts.append(
                    _make_fact_row(
                        caption_id=caption_id,
                        fact_index=start_index + len(facts),
                        fact_type="object_pair_in_caption",
                        count_key=(
                            "object_pair_in_caption:"
                            f"{source_object}:{target_object}"
                        ),
                        rule_ids=rule_ids,
                        source_mention_ids=(
                            mention_ids_by_object[source_object]
                            + mention_ids_by_object[target_object]
                        ),
                        source_edge_ids=[],
                        values={
                            "source_object": source_object,
                            "target_object": target_object,
                            "source_parent_concepts": parent_concepts_by_object[source_object],
                            "source_parent_synset_ids": parent_synset_ids_by_object[source_object],
                            "target_parent_concepts": parent_concepts_by_object[target_object],
                            "target_parent_synset_ids": parent_synset_ids_by_object[target_object],
                        },
                    ),
                )
    return facts


def _aggregate_facts(
    facts: Sequence[FactRow],
    *,
    fact_type: str,
    table_key_prefix: str,
    value_fields: Sequence[str],
    extra_value_fields: Sequence[str] = (),
) -> list[CountRow]:
    buckets: dict[tuple[str, ...], list[FactRow]] = defaultdict(list)
    for fact in facts:
        if fact.fact_type != fact_type:
            continue
        key_values = tuple(str(fact.values[field]) for field in value_fields)
        buckets[key_values].append(fact)

    rows: list[CountRow] = []
    for key_values in sorted(buckets):
        bucket = buckets[key_values]
        values = {
            field: value
            for field, value in zip(value_fields, key_values, strict=True)
        }
        for field in extra_value_fields:
            values[field] = _collect_value_field(bucket, field)
        count_key = ":".join((table_key_prefix, *key_values))
        rows.append(
            CountRow(
                count_key=count_key,
                count=len(bucket),
                caption_count=len({fact.caption_id for fact in bucket}),
                example_caption_ids=sorted({fact.caption_id for fact in bucket})[:5],
                raw_variants=_collect_raw_variants(bucket),
                rule_ids=_collect_rule_ids(bucket),
                values=values,
            ),
        )
    rows.sort(key=lambda row: (-row.count, row.count_key))
    return rows


def _collect_value_field(facts: Iterable[FactRow], field: str) -> str:
    values: set[str] = set()
    for fact in facts:
        value = fact.values.get(field)
        if isinstance(value, list):
            values.update(str(item) for item in value if str(item))
        elif value is not None:
            text = str(value)
            if text:
                values.add(text)
    return "|".join(sorted(values))


def _write_count_table_tsv(
    path: Path,
    rows: Sequence[CountRow],
    *,
    value_fields: Sequence[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value_fields = tuple(value_fields) if value_fields is not None else tuple(_value_fields(rows))
    fieldnames = [
        "count_key",
        *value_fields,
        "count",
        "caption_count",
        "example_caption_ids",
        "raw_variants",
        "rule_ids",
    ]
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "count_key": row.count_key,
                    **{field: row.values.get(field, "") for field in value_fields},
                    "count": row.count,
                    "caption_count": row.caption_count,
                    "example_caption_ids": "|".join(row.example_caption_ids),
                    "raw_variants": "|".join(row.raw_variants),
                    "rule_ids": "|".join(row.rule_ids),
                },
            )


def _write_count_table_tsv_from_iter(
    path: Path,
    *,
    value_fields: Sequence[str],
    rows: Iterable[CountRow],
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "count_key",
        *value_fields,
        "count",
        "caption_count",
        "example_caption_ids",
        "raw_variants",
        "rule_ids",
    ]
    count = 0
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "count_key": row.count_key,
                    **{field: row.values.get(field, "") for field in value_fields},
                    "count": row.count,
                    "caption_count": row.caption_count,
                    "example_caption_ids": "|".join(row.example_caption_ids),
                    "raw_variants": "|".join(row.raw_variants),
                    "rule_ids": "|".join(row.rule_ids),
                },
            )
            count += 1
    return count


def _value_fields(rows: Sequence[CountRow]) -> list[str]:
    fields: list[str] = []
    for row in rows:
        for field in row.values:
            if field not in fields:
                fields.append(field)
    return fields


def _mention_rule_ids(mention: CanonicalMention) -> list[str]:
    rule_ids = [
        RAW_MENTION_RULE_BY_TYPE[mention.mention_type],
        mention.canonical_rule_id,
    ]
    if mention.parent_rule_id is not None:
        rule_ids.append(mention.parent_rule_id)
    return _unique_sorted(rule_ids)


def _edge_rule_ids(
    edge: CanonicalEdge,
    source: CanonicalMention,
    target: CanonicalMention,
) -> list[str]:
    rule_ids = [
        edge.rule_id,
        *source_mention_rule_ids_without_parent(source),
        *source_mention_rule_ids_without_parent(target),
    ]
    if edge.canonical_rule_id is not None:
        rule_ids.append(edge.canonical_rule_id)
    if source.parent_rule_id is not None:
        rule_ids.append(source.parent_rule_id)
    if target.parent_rule_id is not None:
        rule_ids.append(target.parent_rule_id)
    return _unique_sorted(rule_ids)


def _edge_only_rule_ids(edge: CanonicalEdge) -> list[str]:
    rule_ids = [edge.rule_id]
    if edge.canonical_rule_id is not None:
        rule_ids.append(edge.canonical_rule_id)
    return _unique_sorted(rule_ids)


def source_mention_rule_ids_without_parent(mention: CanonicalMention) -> list[str]:
    return [
        RAW_MENTION_RULE_BY_TYPE[mention.mention_type],
        mention.canonical_rule_id,
    ]


def _collect_raw_variants(facts: Iterable[FactRow]) -> list[str]:
    variants: set[str] = set()
    for fact in facts:
        value = fact.values.get("raw_variants")
        if isinstance(value, list):
            variants.update(str(item) for item in value if str(item))
    return sorted(variants)


def _collect_rule_ids(facts: Iterable[FactRow]) -> list[str]:
    rule_ids: set[str] = set()
    for fact in facts:
        rule_ids.update(fact.rule_ids)
        rule_ids.add(COUNT_RULE_ID)
    return sorted(rule_ids)


def _raw_variant(mention: CanonicalMention) -> str:
    raw_text = mention.raw_text.strip().lower()
    if raw_text:
        return raw_text
    return mention.raw_lemma.strip().lower()


def _edge_raw_variant(edge: CanonicalEdge) -> str:
    raw_span = edge.canonical_detail.get("raw_span_surface")
    if isinstance(raw_span, str) and raw_span.strip():
        return raw_span.strip().lower()
    return edge.label.strip().lower()


def _parent_synset_ids(mention: CanonicalMention) -> list[str]:
    value = mention.canonical_detail.get("parent_oewn_synsets")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    if isinstance(value, tuple):
        return [item for item in value if isinstance(item, str) and item]
    if isinstance(value, str) and value:
        return [item for item in value.split("|") if item]
    return []


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def _coerce_canonical_mention(
    record: Mapping[str, Any] | CanonicalMention,
) -> CanonicalMention:
    if isinstance(record, CanonicalMention):
        return record
    return CanonicalMention(**dict(record))


def _coerce_canonical_edge(
    record: Mapping[str, Any] | CanonicalEdge,
) -> CanonicalEdge:
    if isinstance(record, CanonicalEdge):
        return record
    return CanonicalEdge(**dict(record))


def _require_mention(
    mention_by_key: Mapping[tuple[str, str], CanonicalMention],
    caption_id: str,
    mention_id: str,
) -> CanonicalMention:
    mention = mention_by_key.get((caption_id, mention_id))
    if mention is None:
        raise ValueError(
            f"edge endpoint {(caption_id, mention_id)!r} has no canonical mention",
        )
    return mention
