"""Budgeted accumulator for caption-disjoint Stage 6 TSV merges."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Iterator

from gpic_concepts_v1.runtime_memory import (
    MemorySafetyConfig, ProgressWriter, current_rss_kib,
)


@dataclass(slots=True)
class MergedCountRow:
    fields: dict[str, str]
    count: int = 0
    caption_count: int = 0
    example_caption_ids: set[str] = field(default_factory=set)
    rule_ids: set[str] = field(default_factory=set)
    # Preserve distinct original pipe strings: one repeated literal pipe value
    # must not be normalized differently merely because a cache was spilled.
    pipe_field_values: dict[str, set[str]] = field(default_factory=dict)

    def merge(self, other: MergedCountRow, *, key: str, value_fields: tuple[str, ...]) -> None:
        for name in value_fields:
            if self.fields.get(name, "") != other.fields.get(name, ""):
                raise ValueError(f"count_key value field conflict: {key!r}, field={name!r}")
        self.count += other.count
        self.caption_count += other.caption_count
        self.example_caption_ids = set(sorted(self.example_caption_ids | other.example_caption_ids)[:5])
        self.rule_ids.update(other.rule_ids)
        for name, values in other.pipe_field_values.items():
            self.pipe_field_values.setdefault(name, set()).update(values)

    def encode(self) -> str:
        return json.dumps([
            self.fields, self.caption_count, sorted(self.example_caption_ids),
            sorted(self.rule_ids), {k: sorted(v) for k, v in self.pipe_field_values.items()},
        ], ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def decode(cls, count: int, payload: str) -> MergedCountRow:
        fields, caption_count, examples, rules, pipes = json.loads(payload)
        return cls(fields, count, caption_count, set(examples), set(rules),
                   {k: set(v) for k, v in pipes.items()})


class CountMergeStore:
    """Keep fitting tables in RAM; spill and sort on disk before the RSS guard."""

    def __init__(self, output_path: Path, *, value_fields: tuple[str, ...],
                 memory_config: MemorySafetyConfig) -> None:
        self.output_path = output_path
        self.value_fields = value_fields
        self.config = memory_config
        self.rows: dict[str, MergedCountRow] = {}
        self.input_rows = 0
        self.spills = 0
        self.max_cached_keys = 0
        self._temporary: tempfile.TemporaryDirectory | None = None
        self._conn: sqlite3.Connection | None = None
        self._last_check = float("-inf")
        self._last_progress = float("-inf")
        self.progress = ProgressWriter(
            output_path.with_suffix(".merge_progress.json"),
            stage_name="stage6_count_merge", memory_config=memory_config,
        )

    def __enter__(self) -> CountMergeStore:
        self.progress.check_memory(phase="start", force=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc is not None:
                self._progress("failed", str(exc))
        finally:
            if self._conn is not None:
                self._conn.close()
            if self._temporary is not None:
                self._temporary.cleanup()

    def row(self, key: str, fields: dict[str, str]) -> MergedCountRow:
        current = self.rows.get(key)
        if current is None:
            current = self.rows[key] = MergedCountRow(fields)
        return current

    def after_row(self) -> None:
        self.input_rows += 1
        self.max_cached_keys = max(self.max_cached_keys, len(self.rows))
        now = time.monotonic()
        if now - self._last_check < self.config.memory_check_min_interval_seconds:
            return
        self._last_check = now
        if self.should_spill():
            self.flush()
        self.progress.check_memory(phase="accumulate", force=True)
        if now - self._last_progress >= 30:
            self._progress("running", "accumulate")
            self._last_progress = now

    def should_spill(self) -> bool:
        budget = self.config.effective_max_rss_gib
        rss = current_rss_kib()
        # No reliable measurements means disk mode, never an unbounded fallback.
        return budget is None or rss is None or rss / 1024**2 >= budget * 0.8

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._temporary = tempfile.TemporaryDirectory(
                prefix=f".{self.output_path.stem}_spill_", dir=self.output_path.parent,
            )
            self._conn = sqlite3.connect(str(Path(self._temporary.name) / "counts.sqlite"))
            self._conn.execute("PRAGMA temp_store=FILE")
            self._conn.execute("PRAGMA mmap_size=0")
            self._conn.execute("CREATE TABLE counts (key TEXT PRIMARY KEY, n INTEGER, payload TEXT)")
        return self._conn

    def flush(self) -> None:
        if not self.rows:
            return
        conn = self._connect()
        self._progress("running", "spill")
        with conn:
            # Drain incrementally; do not duplicate the full cache during serialization.
            while self.rows:
                key, row = self.rows.popitem()
                existing = conn.execute("SELECT n, payload FROM counts WHERE key=?", (key,)).fetchone()
                if existing is not None:
                    old = MergedCountRow.decode(*existing)
                    old.merge(row, key=key, value_fields=self.value_fields)
                    row = old
                conn.execute("INSERT OR REPLACE INTO counts VALUES (?, ?, ?)",
                             (key, row.count, row.encode()))
                self.progress.check_memory(phase="spill")
                if time.monotonic() - self._last_progress >= 30:
                    self._progress("running", "spill")
                    self._last_progress = time.monotonic()
        self.rows.clear()
        self.spills += 1

    def sorted_rows(self) -> Iterator[tuple[str, MergedCountRow]]:
        if self._conn is not None or self.should_spill():
            self.flush()
            if self._conn is not None:
                self._progress("running", "disk_sort")
                for key, count, payload in self._conn.execute(
                    "SELECT key, n, payload FROM counts ORDER BY n DESC, key COLLATE BINARY"
                ):
                    self.progress.check_memory(phase="disk_sort")
                    yield key, MergedCountRow.decode(count, payload)
                return
        self._progress("running", "memory_sort")
        # The soft spill threshold leaves room for the sort index, not another
        # full copy of all rows/metadata.
        for key in sorted(self.rows, key=lambda key: (-self.rows[key].count, key)):
            self.progress.check_memory(phase="memory_sort")
            yield key, self.rows[key]

    def stats(self) -> dict:
        return {
            "backend": "sqlite_spill" if self._conn is not None else "memory",
            "spill_count": self.spills, "input_rows": self.input_rows,
            "max_cached_keys": self.max_cached_keys,
            "max_rss_gib": self.config.effective_max_rss_gib,
            "spill_rss_gib": (self.config.effective_max_rss_gib * 0.8
                              if self.config.effective_max_rss_gib is not None else None),
        }

    def _progress(self, status: str, phase: str) -> None:
        self.progress.write(status=status, phase=phase, note=phase,
                            metrics={**self.stats(), "cached_keys": len(self.rows)})

    def complete(self) -> dict:
        self._progress("completed", "complete")
        return self.stats()
