from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import inspect
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.attribute_units import (
    ATTRIBUTE_MWE_RULE_VERSION,
    ATTRIBUTE_UNIT_MWE,
    ATTRIBUTE_UNIT_SINGLE_TOKEN,
    AttributeAnchor,
    AttributeMweCandidate,
    AttributeTokenView,
    collect_attribute_anchors,
    inventory_attribute_key,
    normalize_attribute_surface,
    select_attribute_mwes,
    separator_equivalent_key,
    separator_variants,
)
from gpic_concepts_v1.io_jsonl import iter_jsonl
from gpic_concepts_v1.stage4_extract_raw import (
    ATTRIBUTE_MODIFIER_DEPS,
    NLTK_DATA_DIR,
    OEWN_SPEC,
    WN_DATA_DIR,
    _build_children_by_head,
    _chunk_tokens,
    _conjunct_attribute_modifiers,
    _is_allowed_token_record_span_start,
    _is_plural_common_noun_token,
    _is_quantity_modifier,
    _normalize_query,
    _object_core_token_indices_from_token_records,
    _probe_object_surface,
    _require_int,
    _select_by_wn30_lemma_count,
    _find_preposition_mwe_matches_in_token_records,
    _token_record_span_lookup_surfaces,
    _token_record_span_text,
    _token_text,
    load_gpic_object_inventory,
    load_preposition_mwe_lexicon,
    nltk,
    wn,
)

try:  # pragma: no cover - exercised when runtime OEWN dependencies exist.
    from wn.morphy import Morphy
except ModuleNotFoundError:  # pragma: no cover - keeps lightweight tests importable.
    Morphy = Any  # type: ignore[misc,assignment]


ATTRIBUTE_COMPATIBLE_LEXFILES = frozenset(
    (
        "adj.all",
        "adj.pert",
        "adj.ppl",
        "noun.attribute",
        "noun.shape",
        "noun.state",
        "noun.substance",
    )
)
CONDITIONAL_ATTRIBUTE_LEXFILES = frozenset(
    (
        "noun.Tops",
        "noun.act",
        "noun.animal",
        "noun.artifact",
        "noun.body",
        "noun.cognition",
        "noun.communication",
        "noun.event",
        "noun.food",
        "noun.group",
        "noun.location",
        "noun.object",
        "noun.person",
        "noun.phenomenon",
        "noun.plant",
        "noun.possession",
        "noun.process",
        "noun.quantity",
        "noun.relation",
        "noun.time",
        "verb.body",
        "verb.change",
        "verb.cognition",
        "verb.communication",
        "verb.competition",
        "verb.consumption",
        "verb.contact",
        "verb.creation",
        "verb.emotion",
        "verb.motion",
        "verb.perception",
        "verb.possession",
        "verb.social",
        "verb.stative",
        "verb.weather",
    )
)
HARD_CONFLICT_ATTRIBUTE_LEXFILES = frozenset(("adv.all", "noun.feeling", "noun.motive"))

FIELDNAMES = [
    "span_key",
    "attribute_unit_type",
    "span_token_count",
    "anchor_token_offset",
    "lookup_forms",
    "attribute_mwe_rule_version",
    "observed_surface",
    "decision_status",
    "decision_reason",
    "count",
    "caption_count",
    "example_caption_ids",
    "example_surfaces",
    "selected_lookup_case",
    "selected_query",
    "has_oewn_attribute_synset",
    "oewn_synset_count",
    "selected_oewn_synset",
    "selected_oewn_lexfile",
    "attribute_gate",
    "synset_lemmas",
    "canonical_surface",
    "canonical_label_key",
    "canonical_selection_tag",
    "canonical_candidate_lemmas",
    "canonical_candidate_lemma_counts",
    "google_ngram_candidate_surfaces",
    "google_ngram_candidate_mean_frequencies",
    "attribute_parent",
    "attribute_parent_selection_tag",
    "all_oewn_synsets",
    "all_oewn_lexfiles",
    "synset_selection_tag",
    "wn30_lemma_counts",
    "decision_basis",
]

ATTRIBUTE_MORPHY_POS = ("a", "n", "v", "r")


@dataclass(frozen=True, slots=True)
class AttributeLookupResult:
    lookup_case: str
    query: str
    synsets: tuple[Any, ...]
    selected_synset: Any | None
    synset_selection_tag: str
    wn30_lemma_counts: str
    attribute_gate: str
    decision_status: str
    decision_reason: str
    source_row: Mapping[str, str] | None = None
    lookup_forms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _InventorySynset:
    id: str
    _lexfile: str
    _lemmas: tuple[str, ...]

    def lexfile(self) -> str:
        return self._lexfile

    def lemmas(self) -> list[str]:
        return list(self._lemmas)


@dataclass(slots=True)
class AttributeAccumulator:
    span_key: str
    attribute_unit_type: str = ATTRIBUTE_UNIT_SINGLE_TOKEN
    span_token_count: int = 1
    anchor_token_offset: int = 0
    lookup_forms: tuple[str, ...] = ()
    count: int = 0
    caption_ids: set[str] = field(default_factory=set)
    surfaces: Counter[str] = field(default_factory=Counter)
    lookup: AttributeLookupResult | None = None


@dataclass(frozen=True, slots=True)
class AttributeInventoryCheckpointState:
    caption_total: int
    noun_chunk_total: int
    attribute_candidate_total: int
    inventory: dict[tuple[str, str], AttributeAccumulator]


@dataclass(slots=True)
class AttributeInventoryCheckpointWriter:
    path: Path
    metadata: Mapping[str, str]
    interval_records: int = 10000
    _last_caption_total: int = 0

    def __post_init__(self) -> None:
        if self.interval_records < 1:
            raise ValueError("checkpoint_interval_records must be greater than zero")

    def maybe_write(
        self,
        *,
        caption_total: int,
        noun_chunk_total: int,
        attribute_candidate_total: int,
        inventory: Mapping[tuple[str, str], AttributeAccumulator],
    ) -> None:
        if caption_total - self._last_caption_total < self.interval_records:
            return
        self._last_caption_total = caption_total
        self.write(
            status="running",
            caption_total=caption_total,
            noun_chunk_total=noun_chunk_total,
            attribute_candidate_total=attribute_candidate_total,
            inventory=inventory,
        )

    def write_completed(self, *, summary: Mapping[str, Any]) -> None:
        self.write(
            status="completed",
            caption_total=int(summary.get("caption_total") or 0),
            noun_chunk_total=int(summary.get("noun_chunk_total") or 0),
            attribute_candidate_total=int(summary.get("attribute_candidate_total") or 0),
            inventory={},
            summary=summary,
        )

    def write(
        self,
        *,
        status: str,
        caption_total: int,
        noun_chunk_total: int,
        attribute_candidate_total: int,
        inventory: Mapping[tuple[str, str], AttributeAccumulator],
        summary: Mapping[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "schema_version": 2,
            "artifact_type": "gpic_observed_attribute_inventory_checkpoint",
            "status": status,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "metadata": dict(self.metadata),
            "caption_total": caption_total,
            "noun_chunk_total": noun_chunk_total,
            "attribute_candidate_total": attribute_candidate_total,
            "inventory": [_accumulator_checkpoint_row(acc) for acc in inventory.values()],
        }
        if summary is not None:
            payload["summary"] = dict(summary)
        with atomic_text_writer(self.path) as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


class GpicAttributeInventoryLookup:
    """Lookup prior attribute rows by unit type and normalized observed surface."""

    def __init__(
        self,
        rows_by_key: Mapping[Any, Mapping[str, str]],
    ) -> None:
        normalized_rows: dict[tuple[str, str], Mapping[str, str]] = {}
        for key, row in rows_by_key.items():
            composite_key = (
                key
                if isinstance(key, tuple) and len(key) == 2
                else inventory_attribute_key(row)
            )
            normalized_rows[(str(composite_key[0]), str(composite_key[1]))] = row
        self._rows_by_key = normalized_rows

    @classmethod
    def from_tsv(cls, path: str | Path) -> "GpicAttributeInventoryLookup":
        rows_by_key: dict[tuple[str, str], Mapping[str, str]] = {}
        with Path(path).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                row_dict = dict(row)
                if _is_automatic_surface_changed_prior_row(row_dict):
                    continue
                key = inventory_attribute_key(row_dict)
                if key[1] and key not in rows_by_key:
                    rows_by_key[key] = row_dict
        return cls(rows_by_key)

    def __call__(
        self,
        surface: str,
        *,
        require_surface_query_conflict_check: bool = False,
        attribute_unit_type: str = ATTRIBUTE_UNIT_SINGLE_TOKEN,
    ) -> AttributeLookupResult | None:
        row = self._rows_by_key.get(
            (attribute_unit_type, normalize_attribute_surface(surface))
        )
        if row is None:
            return None
        return _attribute_lookup_result_from_inventory_row(row, surface)

def _attribute_lookup_result_from_inventory_row(
    row: Mapping[str, str],
    surface: str,
    *,
    preserve_source_row: bool = True,
) -> AttributeLookupResult:
    synsets = _inventory_synsets(row)
    selected = _inventory_selected_synset(row, synsets)
    attribute_gate = row.get("attribute_gate", "")
    if selected is not None and not attribute_gate:
        attribute_gate = _attribute_gate_for_lexfile(selected.lexfile())
    decision_status = row.get("decision_status", "")
    if decision_status == "no_synset":
        decision_status = "chosen"
    if not decision_status:
        decision_status = _attribute_decision_status(
            selected_synset=selected,
            synsets=synsets,
            attribute_gate=attribute_gate,
        )
    decision_reason = row.get("decision_reason", "")
    if not decision_reason:
        decision_reason = _attribute_decision_reason(
            selected_synset=selected,
            synsets=synsets,
            attribute_gate=attribute_gate,
        )
    return AttributeLookupResult(
        row.get("selected_lookup_case", "inventory"),
        row.get("selected_query", surface),
        tuple(synsets),
        selected,
        row.get("synset_selection_tag", "inventory_selected_synset"),
        row.get("wn30_lemma_counts", ""),
        attribute_gate,
        decision_status,
        decision_reason,
        dict(row) if preserve_source_row else None,
        tuple(_split_pipe(row.get("lookup_forms", ""))),
    )


class AttributeLookup(Protocol):
    def __call__(
        self,
        surface: str,
        *,
        require_surface_query_conflict_check: bool = False,
        attribute_unit_type: str = ATTRIBUTE_UNIT_SINGLE_TOKEN,
    ) -> AttributeLookupResult | None:
        ...


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a GPIC-observed attribute inventory from Stage 3 records.",
    )
    parser.add_argument("--input", required=True, help="Input stage3_records.jsonl")
    parser.add_argument(
        "--object-inventory",
        required=True,
        help="Resolved GPIC observed object inventory TSV used to find consumed object spans",
    )
    parser.add_argument(
        "--attribute-inventory",
        help="Optional prior attribute inventory TSV. Selected synsets in this file are reused.",
    )
    parser.add_argument(
        "--preposition-mwe-lexicon",
        help="Optional active preposition MWE TSV used as an attribute-span boundary.",
    )
    parser.add_argument(
        "--attribute-unit-mode",
        choices=("all", "mwe_only", "single_token_only"),
        default="all",
        help="Select which attribute units to emit. Default: all.",
    )
    parser.add_argument("--output", required=True, help="Output observed attribute inventory TSV")
    parser.add_argument("--summary", help="Optional summary JSON path")
    parser.add_argument(
        "--needs-manual-output",
        help="Optional TSV containing only decision_status=needs_manual rows.",
    )
    parser.add_argument("--limit", type=int, help="Optional maximum Stage 3 records to scan")
    parser.add_argument(
        "--progress-output",
        help="Optional progress JSON path updated while scanning Stage 3 records.",
    )
    parser.add_argument(
        "--progress-interval-records",
        type=int,
        default=10000,
        help="Caption interval for progress JSON updates. Default: 10000.",
    )
    parser.add_argument(
        "--checkpoint-output",
        help=(
            "Optional JSON checkpoint written during long scans. If used with "
            "--resume-checkpoint, an interrupted run resumes from this file."
        ),
    )
    parser.add_argument(
        "--resume-checkpoint",
        action="store_true",
        help="Resume from --checkpoint-output when it exists and metadata matches.",
    )
    parser.add_argument(
        "--checkpoint-interval-records",
        type=int,
        default=10000,
        help="Caption interval for checkpoint JSON updates. Default: 10000.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    object_lookup = load_gpic_object_inventory(args.object_inventory)
    attribute_lookup = _build_attribute_lookup(args.attribute_inventory)
    preposition_mwe_lookup = (
        load_preposition_mwe_lexicon(Path(args.preposition_mwe_lexicon))
        if args.preposition_mwe_lexicon
        else None
    )
    checkpoint_metadata = _checkpoint_metadata(args)
    checkpoint_state = (
        _load_checkpoint(Path(args.checkpoint_output), checkpoint_metadata)
        if args.resume_checkpoint and args.checkpoint_output
        else None
    )
    checkpoint_writer = (
        AttributeInventoryCheckpointWriter(
            Path(args.checkpoint_output),
            metadata=checkpoint_metadata,
            interval_records=args.checkpoint_interval_records,
        )
        if args.checkpoint_output
        else None
    )
    resume_caption_total = checkpoint_state.caption_total if checkpoint_state else 0

    records = _resume_records(
        iter_jsonl(args.input),
        resume_caption_total=resume_caption_total,
        limit=args.limit,
    )
    rows, summary = build_attribute_inventory_rows(
        records,
        object_lookup=object_lookup,
        attribute_lookup=attribute_lookup,
        preposition_mwe_lookup=preposition_mwe_lookup,
        attribute_unit_mode=args.attribute_unit_mode,
        progress_output=Path(args.progress_output) if args.progress_output else None,
        progress_interval_records=args.progress_interval_records,
        checkpoint_writer=checkpoint_writer,
        initial_inventory=checkpoint_state.inventory if checkpoint_state else None,
        initial_caption_total=resume_caption_total,
        initial_noun_chunk_total=checkpoint_state.noun_chunk_total if checkpoint_state else 0,
        initial_attribute_candidate_total=checkpoint_state.attribute_candidate_total
        if checkpoint_state
        else 0,
    )
    _write_tsv(Path(args.output), rows)
    if args.needs_manual_output:
        _write_tsv(
            Path(args.needs_manual_output),
            [row for row in rows if row.get("decision_status") == "needs_manual"],
        )
    summary.update({"input": args.input, "output": args.output})
    if args.summary:
        with atomic_text_writer(Path(args.summary)) as handle:
            handle.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
    if checkpoint_writer is not None:
        checkpoint_writer.write_completed(summary=summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def build_attribute_inventory_rows(
    records: Iterable[Mapping[str, Any]],
    *,
    object_lookup: Any,
    attribute_lookup: AttributeLookup,
    preposition_mwe_lookup: Any | None = None,
    attribute_unit_mode: str = "all",
    progress_output: Path | None = None,
    progress_interval_records: int = 10000,
    checkpoint_writer: AttributeInventoryCheckpointWriter | None = None,
    initial_inventory: Mapping[tuple[str, str], AttributeAccumulator] | None = None,
    initial_caption_total: int = 0,
    initial_noun_chunk_total: int = 0,
    initial_attribute_candidate_total: int = 0,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if progress_interval_records < 1:
        raise ValueError("progress_interval_records must be greater than zero")
    if attribute_unit_mode not in {"all", "mwe_only", "single_token_only"}:
        raise ValueError(f"unsupported attribute_unit_mode: {attribute_unit_mode}")
    inventory: dict[tuple[str, str], AttributeAccumulator] = dict(initial_inventory or {})
    caption_total = initial_caption_total
    noun_chunk_total = initial_noun_chunk_total
    attribute_candidate_total = initial_attribute_candidate_total
    started = perf_counter()
    _write_progress(
        progress_output,
        status="running",
        phase="scan_stage3_records",
        caption_total=caption_total,
        noun_chunk_total=noun_chunk_total,
        attribute_candidate_total=attribute_candidate_total,
        inventory_rows=len(inventory),
        elapsed_seconds=round(perf_counter() - started, 3),
    )

    for record in records:
        caption_total += 1
        caption_id = str(record.get("caption_id", ""))
        tokens = list(record.get("tokens", []))
        token_by_i = {_require_int(token, "i"): token for token in tokens}
        children_by_head = _build_children_by_head(tokens)
        relation_mwe_consumed_tokens: set[int] = set()
        selected_object_segment_ids: set[str] = set()
        if preposition_mwe_lookup is not None:
            relation_mwe_consumed_tokens.update(
                token_i
                for match in _find_preposition_mwe_matches_in_token_records(
                    tokens,
                    preposition_mwe_lookup,
                )
                for token_i in match.token_indices
            )
        for chunk in record.get("noun_chunks", []):
            noun_chunk_total += 1
            consumed = _selected_object_token_indices(chunk, token_by_i, object_lookup)
            if not consumed:
                continue
            segment_id = str(chunk.get("segment_id", "")).strip()
            if segment_id:
                selected_object_segment_ids.add(segment_id)
            chunk_tokens = _chunk_tokens(chunk, token_by_i)
            excluded = set(consumed) | relation_mwe_consumed_tokens
            token_views = _attribute_token_views(chunk_tokens)
            children_views = _attribute_children_views(children_by_head)
            anchors = collect_attribute_anchors(
                token_views,
                children_by_head=children_views,
                excluded_token_indices=excluded,
            )
            selected_mwes = ()
            if attribute_unit_mode != "single_token_only":
                excluded_mwe_candidates: dict[
                    tuple[int, ...],
                    tuple[AttributeMweCandidate, AttributeLookupResult],
                ] = {}

                def lookup_mwe_for_selection(
                    candidate: AttributeMweCandidate,
                ) -> AttributeLookupResult | None:
                    result = _lookup_attribute_mwe_candidate(
                        candidate,
                        attribute_lookup=attribute_lookup,
                    )
                    if result is not None and result.decision_status == "excluded":
                        excluded_mwe_candidates.setdefault(
                            candidate.token_indices,
                            (candidate, result),
                        )
                        return None
                    return result

                selected_mwes = select_attribute_mwes(
                    token_views,
                    anchors=anchors,
                    excluded_token_indices=excluded,
                    lookup=lookup_mwe_for_selection,
                )
                for candidate, lookup in excluded_mwe_candidates.values():
                    attribute_candidate_total += 1
                    _accumulate_attribute_unit(
                        inventory,
                        caption_id=caption_id,
                        surface=candidate.surface,
                        attribute_unit_type=ATTRIBUTE_UNIT_MWE,
                        span_token_count=len(candidate.tokens),
                        anchor_token_offset=candidate.anchor_token_offset,
                        lookup=lookup,
                    )
                for selected in selected_mwes:
                    attribute_candidate_total += 1
                    _accumulate_attribute_unit(
                        inventory,
                        caption_id=caption_id,
                        surface=selected.candidate.surface,
                        attribute_unit_type=ATTRIBUTE_UNIT_MWE,
                        span_token_count=len(selected.candidate.tokens),
                        anchor_token_offset=selected.candidate.anchor_token_offset,
                        lookup=selected.lookup,
                    )

            if attribute_unit_mode == "mwe_only":
                continue
            mwe_consumed_tokens = {
                token_i
                for selected in selected_mwes
                for token_i in selected.candidate.token_indices
            }
            token_by_view_i = {token.i: token for token in token_views}
            for anchor in anchors:
                if anchor.token_i in mwe_consumed_tokens:
                    continue
                token = token_by_view_i[anchor.token_i]
                surface = token.text
                lookup = _call_attribute_lookup(
                    attribute_lookup,
                    surface,
                    require_surface_query_conflict_check=_is_plural_common_noun_token(
                        token_by_i[token.i]
                    ),
                    attribute_unit_type=ATTRIBUTE_UNIT_SINGLE_TOKEN,
                )
                attribute_candidate_total += 1
                _accumulate_attribute_unit(
                    inventory,
                    caption_id=caption_id,
                    surface=surface,
                    attribute_unit_type=ATTRIBUTE_UNIT_SINGLE_TOKEN,
                    span_token_count=1,
                    anchor_token_offset=0,
                    lookup=lookup,
                )
        if attribute_unit_mode != "single_token_only":
            attribute_candidate_total += _accumulate_objectless_tag_list_mwes(
                inventory,
                record=record,
                caption_id=caption_id,
                selected_object_segment_ids=selected_object_segment_ids,
                relation_mwe_consumed_tokens=relation_mwe_consumed_tokens,
                attribute_lookup=attribute_lookup,
            )
        if caption_total == 1 or caption_total % progress_interval_records == 0:
            _write_progress(
                progress_output,
                status="running",
                phase="scan_stage3_records",
                caption_total=caption_total,
                noun_chunk_total=noun_chunk_total,
                attribute_candidate_total=attribute_candidate_total,
                inventory_rows=len(inventory),
                elapsed_seconds=round(perf_counter() - started, 3),
            )
        if checkpoint_writer is not None:
            checkpoint_writer.maybe_write(
                caption_total=caption_total,
                noun_chunk_total=noun_chunk_total,
                attribute_candidate_total=attribute_candidate_total,
                inventory=inventory,
            )

    rows = [_inventory_row(acc) for acc in inventory.values()]
    rows.sort(key=lambda row: (-int(row["count"]), row["span_key"]))
    summary = {
        "caption_total": caption_total,
        "noun_chunk_total": noun_chunk_total,
        "attribute_candidate_total": attribute_candidate_total,
        "inventory_rows": len(rows),
        "attribute_unit_mode": attribute_unit_mode,
        "attribute_unit_type_counts": dict(
            Counter(row["attribute_unit_type"] for row in rows)
        ),
        "decision_status_counts": dict(Counter(row["decision_status"] for row in rows)),
        "decision_reason_counts": dict(Counter(row["decision_reason"] for row in rows)),
        "attribute_gate_counts": dict(Counter(row["attribute_gate"] for row in rows)),
        "prior_reused_rows": sum(
            1
            for acc in inventory.values()
            if acc.lookup is not None and acc.lookup.source_row is not None
        ),
        "prior_selected_synset_reused_rows": sum(
            1
            for acc in inventory.values()
            if acc.lookup is not None
            and acc.lookup.source_row is not None
            and acc.lookup.selected_synset is not None
        ),
        "prior_selected_query_reused_rows": 0,
        "prior_canonical_reused_rows": sum(
            1
            for acc in inventory.values()
            if acc.lookup is not None
            and _preserved_prior_value(acc.lookup, "canonical_surface")
        ),
    }
    _write_progress(
        progress_output,
        status="complete",
        phase="complete",
        caption_total=caption_total,
        noun_chunk_total=noun_chunk_total,
        attribute_candidate_total=attribute_candidate_total,
        inventory_rows=len(rows),
        decision_status_counts=summary["decision_status_counts"],
        decision_reason_counts=summary["decision_reason_counts"],
        attribute_gate_counts=summary["attribute_gate_counts"],
        elapsed_seconds=round(perf_counter() - started, 3),
    )
    return rows, summary


def _accumulate_objectless_tag_list_mwes(
    inventory: dict[tuple[str, str], AttributeAccumulator],
    *,
    record: Mapping[str, Any],
    caption_id: str,
    selected_object_segment_ids: set[str],
    relation_mwe_consumed_tokens: set[int],
    attribute_lookup: AttributeLookup,
) -> int:
    meta = record.get("meta")
    caption_shape = (
        str(meta.get("caption_shape", "")).strip()
        if isinstance(meta, Mapping)
        else str(record.get("caption_shape", "")).strip()
    )
    tag_segments = list(record.get("tag_segments", []))
    if caption_shape != "tag_list" and not tag_segments:
        return 0

    added = 0
    for segment in tag_segments:
        segment_id = str(segment.get("segment_id", "")).strip()
        if segment_id and segment_id in selected_object_segment_ids:
            continue
        content_tokens = [
            token
            for token in segment.get("tokens", [])
            if str(token.get("pos", "")).strip() not in {"PUNCT", "SPACE"}
        ]
        if len(content_tokens) < 2:
            continue
        token_views = _attribute_token_views(content_tokens)
        if any(
            token.i in relation_mwe_consumed_tokens or token.is_quantity
            for token in token_views
        ):
            continue
        candidate = AttributeMweCandidate(
            tokens=token_views,
            anchor=AttributeAnchor(token_i=token_views[-1].i),
            surface=" ".join(token.text for token in token_views),
        )
        lookup = _lookup_attribute_mwe_candidate(
            candidate,
            attribute_lookup=attribute_lookup,
        )
        if lookup is None:
            continue
        _accumulate_attribute_unit(
            inventory,
            caption_id=caption_id,
            surface=candidate.surface,
            attribute_unit_type=ATTRIBUTE_UNIT_MWE,
            span_token_count=len(candidate.tokens),
            anchor_token_offset=candidate.anchor_token_offset,
            lookup=lookup,
        )
        added += 1
    return added


def _checkpoint_metadata(args: argparse.Namespace) -> dict[str, str]:
    return {
        "attribute_candidate_rule_version": ATTRIBUTE_MWE_RULE_VERSION,
        "input": str(Path(args.input)),
        "output": str(Path(args.output)),
        "limit": "" if args.limit is None else str(args.limit),
        "object_inventory": str(Path(args.object_inventory)),
        "attribute_inventory": args.attribute_inventory or "",
        "preposition_mwe_lexicon": args.preposition_mwe_lexicon or "",
        "attribute_unit_mode": args.attribute_unit_mode,
    }


def _load_checkpoint(
    path: Path,
    expected_metadata: Mapping[str, str],
) -> AttributeInventoryCheckpointState | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("artifact_type") != "gpic_observed_attribute_inventory_checkpoint":
        raise SystemExit(f"invalid attribute inventory checkpoint: {path}")
    if int(payload.get("schema_version") or 0) != 2:
        raise SystemExit(
            "attribute inventory checkpoint schema mismatch; remove the stale "
            f"single-token checkpoint: {path}"
        )
    metadata = payload.get("metadata") or {}
    if dict(metadata) != dict(expected_metadata):
        raise SystemExit(
            "attribute inventory checkpoint metadata mismatch; remove the stale "
            f"checkpoint or use a matching command: {path}"
        )
    if payload.get("status") == "completed":
        return None
    return AttributeInventoryCheckpointState(
        caption_total=int(payload.get("caption_total") or 0),
        noun_chunk_total=int(payload.get("noun_chunk_total") or 0),
        attribute_candidate_total=int(payload.get("attribute_candidate_total") or 0),
        inventory={
            (acc.attribute_unit_type, acc.span_key): acc
            for acc in (
                _accumulator_from_checkpoint_row(row)
                for row in payload.get("inventory", [])
            )
        },
    )


def _accumulator_checkpoint_row(acc: AttributeAccumulator) -> dict[str, Any]:
    return {
        "span_key": acc.span_key,
        "attribute_unit_type": acc.attribute_unit_type,
        "span_token_count": acc.span_token_count,
        "anchor_token_offset": acc.anchor_token_offset,
        "lookup_forms": list(acc.lookup_forms),
        "count": acc.count,
        "caption_ids": sorted(acc.caption_ids),
        "surfaces": dict(acc.surfaces),
        "lookup_has_source_row": bool(acc.lookup is not None and acc.lookup.source_row is not None),
        "lookup_row": _inventory_row(acc),
    }


def _accumulator_from_checkpoint_row(row: Mapping[str, Any]) -> AttributeAccumulator:
    lookup_row = {
        field: str((row.get("lookup_row") or {}).get(field, ""))
        for field in FIELDNAMES
    }
    lookup = _attribute_lookup_result_from_inventory_row(
        lookup_row,
        lookup_row.get("observed_surface", "") or lookup_row.get("span_key", ""),
        preserve_source_row=bool(row.get("lookup_has_source_row")),
    )
    return AttributeAccumulator(
        span_key=str(row.get("span_key", "")),
        attribute_unit_type=str(
            row.get("attribute_unit_type", ATTRIBUTE_UNIT_SINGLE_TOKEN)
        ),
        span_token_count=int(row.get("span_token_count") or 1),
        anchor_token_offset=int(row.get("anchor_token_offset") or 0),
        lookup_forms=tuple(str(item) for item in row.get("lookup_forms", [])),
        count=int(row.get("count") or 0),
        caption_ids=set(str(item) for item in row.get("caption_ids", [])),
        surfaces=Counter({str(key): int(value) for key, value in row.get("surfaces", {}).items()}),
        lookup=lookup,
    )


def _write_progress(path: Path | None, *, status: str, phase: str, **payload: Any) -> None:
    if path is None:
        return
    progress = {
        "schema_version": 1,
        "artifact_type": "gpic_observed_attribute_inventory_progress",
        "status": status,
        "phase": phase,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    with atomic_text_writer(path) as handle:
        handle.write(json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")


def _selected_object_token_indices(
    chunk: Mapping[str, Any],
    token_by_i: Mapping[int, Mapping[str, Any]],
    object_lookup: Any,
) -> set[int]:
    tokens = _chunk_tokens(chunk, token_by_i)
    if not tokens:
        return set()
    root_i = _require_int(chunk, "root_i")
    root_pos = next(
        (index for index, token in enumerate(tokens) if _require_int(token, "i") == root_i),
        None,
    )
    if root_pos is None:
        return set()
    for start_pos in range(0, root_pos + 1):
        span_tokens = tokens[start_pos : root_pos + 1]
        if len(span_tokens) > 1 and not _is_allowed_token_record_span_start(span_tokens[0]):
            continue
        lookup = _probe_object_surface(
            _token_record_span_lookup_surfaces(span_tokens),
            object_lookup,
            require_manual_on_any_surface_changed_hit=_is_plural_common_noun_token(
                span_tokens[-1]
            ),
        )
        if lookup is not None:
            return set(_object_core_token_indices_from_token_records(span_tokens, lookup))
    return set()


def _attribute_candidate_tokens(
    chunk_tokens: Sequence[Mapping[str, Any]],
    *,
    consumed_token_indices: set[int],
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> list[Mapping[str, Any]]:
    chunk_token_indices = {_require_int(token, "i") for token in chunk_tokens}
    candidates: list[Mapping[str, Any]] = []
    emitted_token_indices: set[int] = set()

    def append_candidate(token: Mapping[str, Any]) -> None:
        token_i = _require_int(token, "i")
        if token_i in emitted_token_indices:
            return
        emitted_token_indices.add(token_i)
        candidates.append(token)

    for token in chunk_tokens:
        token_i = _require_int(token, "i")
        if token_i in consumed_token_indices:
            continue
        if _is_quantity_modifier(token):
            continue
        if token.get("dep") not in ATTRIBUTE_MODIFIER_DEPS:
            continue
        append_candidate(token)
        for conj_token, _ in _conjunct_attribute_modifiers(
            token,
            children_by_head=children_by_head,
            chunk_token_indices=chunk_token_indices,
            excluded_token_indices=consumed_token_indices,
        ):
            append_candidate(conj_token)
    return candidates


def _attribute_token_views(
    tokens: Sequence[Mapping[str, Any]],
) -> tuple[AttributeTokenView, ...]:
    return tuple(
        AttributeTokenView(
            i=_require_int(token, "i"),
            text=_token_text(token),
            lemma=str(token.get("lemma", "")),
            dep=str(token.get("dep", "")),
            pos=str(token.get("pos", "")),
            tag=str(token.get("tag", "")),
            char_start=int(token["char_start"]) if token.get("char_start") is not None else None,
            char_end=int(token["char_end"]) if token.get("char_end") is not None else None,
            is_quantity=_is_quantity_modifier(token),
        )
        for token in tokens
    )


def _attribute_children_views(
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> dict[int, tuple[AttributeTokenView, ...]]:
    return {
        head_i: _attribute_token_views(children)
        for head_i, children in children_by_head.items()
    }


def _lookup_attribute_mwe_candidate(
    candidate: AttributeMweCandidate,
    *,
    attribute_lookup: AttributeLookup,
) -> AttributeLookupResult | None:
    lookup = _call_attribute_lookup(
        attribute_lookup,
        candidate.surface,
        attribute_unit_type=ATTRIBUTE_UNIT_MWE,
    )
    if lookup is None or not lookup.synsets:
        return None
    return lookup


def _call_attribute_lookup(
    attribute_lookup: AttributeLookup,
    surface: str,
    *,
    require_surface_query_conflict_check: bool = False,
    attribute_unit_type: str,
) -> AttributeLookupResult | None:
    try:
        parameters = inspect.signature(attribute_lookup).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_unit_type = any(
        parameter.name == "attribute_unit_type"
        or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if not accepts_unit_type:
        if attribute_unit_type == ATTRIBUTE_UNIT_MWE:
            return None
        return attribute_lookup(
            surface,
            require_surface_query_conflict_check=require_surface_query_conflict_check,
        )
    return attribute_lookup(
        surface,
        require_surface_query_conflict_check=require_surface_query_conflict_check,
        attribute_unit_type=attribute_unit_type,
    )


def _accumulate_attribute_unit(
    inventory: dict[tuple[str, str], AttributeAccumulator],
    *,
    caption_id: str,
    surface: str,
    attribute_unit_type: str,
    span_token_count: int,
    anchor_token_offset: int,
    lookup: AttributeLookupResult | None,
) -> None:
    span_key = normalize_attribute_surface(surface)
    if not span_key:
        return
    key = (attribute_unit_type, span_key)
    lookup_forms = lookup.lookup_forms if lookup is not None else ()
    acc = inventory.setdefault(
        key,
        AttributeAccumulator(
            span_key=span_key,
            attribute_unit_type=attribute_unit_type,
            span_token_count=span_token_count,
            anchor_token_offset=anchor_token_offset,
            lookup_forms=lookup_forms,
        ),
    )
    if (
        acc.span_token_count != span_token_count
        or acc.anchor_token_offset != anchor_token_offset
    ):
        raise ValueError(f"inconsistent attribute unit structure for {key}")
    acc.count += 1
    if caption_id:
        acc.caption_ids.add(caption_id)
    acc.surfaces[surface] += 1
    if lookup_forms and not acc.lookup_forms:
        acc.lookup_forms = lookup_forms
    if acc.lookup is None or _lookup_rank(lookup) > _lookup_rank(acc.lookup):
        acc.lookup = lookup


def _build_attribute_lookup(attribute_inventory_path: str | None) -> AttributeLookup:
    existing_lookup = (
        GpicAttributeInventoryLookup.from_tsv(attribute_inventory_path)
        if attribute_inventory_path
        else None
    )
    runtime_lookup = _load_attribute_lookup_runtime()

    def lookup(
        surface: str,
        *,
        require_surface_query_conflict_check: bool = False,
        attribute_unit_type: str = ATTRIBUTE_UNIT_SINGLE_TOKEN,
    ) -> AttributeLookupResult | None:
        existing = (
            existing_lookup(surface, attribute_unit_type=attribute_unit_type)
            if existing_lookup is not None
            else None
        )
        if existing is not None and existing.decision_status in {"chosen", "excluded"}:
            return existing
        runtime = runtime_lookup(
            surface,
            require_surface_query_conflict_check=require_surface_query_conflict_check,
            attribute_unit_type=attribute_unit_type,
        )
        return runtime

    return lookup


def _load_attribute_lookup_runtime() -> AttributeLookup:
    if wn is None:
        raise RuntimeError("OEWN runtime lookup is unavailable; cannot build GPIC attribute inventory")
    wn.config.data_directory = str(WN_DATA_DIR)
    if nltk is not None:
        nltk.data.path.insert(0, str(NLTK_DATA_DIR))
    oewn = wn.Wordnet(OEWN_SPEC, expand="")
    morphy = Morphy(oewn)

    def lookup(
        surface: str,
        *,
        require_surface_query_conflict_check: bool = False,
        attribute_unit_type: str = ATTRIBUTE_UNIT_SINGLE_TOKEN,
    ) -> AttributeLookupResult | None:
        if attribute_unit_type == ATTRIBUTE_UNIT_MWE:
            return _lookup_attribute_mwe_surface(
                surface,
                oewn=oewn,
                morphy=morphy,
            )
        return _lookup_attribute_surface(
            surface,
            oewn=oewn,
            morphy=morphy,
            require_surface_query_conflict_check=require_surface_query_conflict_check,
        )

    return lookup


def _lookup_attribute_surface(
    surface: str,
    *,
    oewn: Any,
    morphy: Any,
    require_surface_query_conflict_check: bool = False,
) -> AttributeLookupResult:
    exact = _normalize_query(surface)
    exact_result: AttributeLookupResult | None = None
    if exact:
        synsets = tuple(oewn.synsets(exact))
        if synsets:
            exact_result = _with_selected_attribute_synset("exact", exact, synsets)
    morphy_hits: list[AttributeLookupResult] = []
    if exact_result is None or require_surface_query_conflict_check:
        for case, query in _morphy_attribute_queries(exact, morphy):
            synsets = tuple(oewn.synsets(query))
            if synsets:
                morphy_hits.append(_with_selected_attribute_synset(case, query, synsets))
    if exact_result is not None:
        if require_surface_query_conflict_check:
            conflict = _attribute_surface_query_conflict_result(exact_result, morphy_hits)
            if conflict is not None:
                return conflict
        return exact_result
    if morphy_hits:
        return morphy_hits[0]
    return AttributeLookupResult(
        "unresolved",
        exact,
        (),
        None,
        "unresolved_no_oewn_attribute_synset",
        "",
        "",
        "chosen",
        "no_oewn_attribute_synset",
    )


def _lookup_attribute_mwe_surface(
    surface: str,
    *,
    oewn: Any,
    morphy: Any,
) -> AttributeLookupResult:
    words = normalize_attribute_surface(surface).split()
    if len(words) < 2:
        return AttributeLookupResult(
            "unresolved",
            normalize_attribute_surface(surface),
            (),
            None,
            "unresolved_not_multi_token_attribute",
            "",
            "",
            "chosen",
            "no_oewn_attribute_synset",
        )

    attempted: list[str] = []
    for index, query in enumerate(separator_variants(words)):
        attempted.append(query)
        synsets = _matching_attribute_mwe_synsets(oewn, query)
        if synsets:
            case = ("mwe_exact", "mwe_hyphen_variant", "mwe_underscore_variant")[index]
            return replace(
                _with_selected_attribute_synset(case, query, synsets),
                lookup_forms=tuple(attempted),
            )

    anchor_lemmas: list[str] = []
    seen_anchor_lemmas: set[str] = set()
    for pos in ATTRIBUTE_MORPHY_POS:
        result = morphy(words[-1], pos)
        lemmas = result.get(pos, set()) if result else set()
        for lemma in sorted(lemmas):
            normalized = normalize_attribute_surface(str(lemma))
            if normalized and normalized != words[-1] and normalized not in seen_anchor_lemmas:
                seen_anchor_lemmas.add(normalized)
                anchor_lemmas.append(normalized)
    for anchor_lemma in anchor_lemmas:
        for index, query in enumerate(separator_variants([*words[:-1], anchor_lemma])):
            attempted.append(query)
            synsets = _matching_attribute_mwe_synsets(oewn, query)
            if synsets:
                suffix = ("space", "hyphen", "underscore")[index]
                return replace(
                    _with_selected_attribute_synset(
                        f"mwe_anchor_morphy_{suffix}",
                        query,
                        synsets,
                    ),
                    lookup_forms=tuple(_unique_nonempty(attempted)),
                )

    return AttributeLookupResult(
        "unresolved",
        normalize_attribute_surface(surface),
        (),
        None,
        "unresolved_no_oewn_attribute_mwe_synset",
        "",
        "",
        "chosen",
        "no_oewn_attribute_synset",
        lookup_forms=tuple(_unique_nonempty(attempted)),
    )


def _matching_attribute_mwe_synsets(oewn: Any, query: str) -> tuple[Any, ...]:
    query_key = separator_equivalent_key(query)
    return tuple(
        synset
        for synset in oewn.synsets(query)
        if any(
            separator_equivalent_key(str(lemma)) == query_key
            for lemma in synset.lemmas()
        )
    )


def _attribute_surface_query_conflict_result(
    exact_result: AttributeLookupResult,
    fallback_hits: Sequence[AttributeLookupResult],
) -> AttributeLookupResult | None:
    exact_id = _attribute_selected_synset_id(exact_result)
    if not exact_id:
        return None
    conflicting = [
        hit
        for hit in fallback_hits
        if _attribute_selected_synset_id(hit)
        and _attribute_selected_synset_id(hit) != exact_id
    ]
    if not conflicting:
        return None
    synsets: list[Any] = []
    seen_synset_ids: set[str] = set()
    for hit in (exact_result, *conflicting):
        for synset in hit.synsets:
            synset_id = str(synset.id)
            if synset_id in seen_synset_ids:
                continue
            synsets.append(synset)
            seen_synset_ids.add(synset_id)
    queries = _unique_nonempty(hit.query for hit in (exact_result, *conflicting))
    details = _unique_nonempty(
        f"{hit.query}:{hit.lookup_case}:{_attribute_selected_synset_id(hit)}"
        for hit in (exact_result, *conflicting)
    )
    return AttributeLookupResult(
        "surface_query_conflict",
        "|".join(queries),
        tuple(synsets),
        None,
        "ambiguous_observed_vs_surface_changed_query",
        "||".join(details),
        "",
        "needs_manual",
        "manual_surface_query_conflict_required",
    )


def _attribute_selected_synset_id(lookup: AttributeLookupResult) -> str:
    return str(lookup.selected_synset.id) if lookup.selected_synset is not None else ""


def _morphy_attribute_queries(query: str, morphy: Any) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()
    if not query:
        return queries
    for pos in ATTRIBUTE_MORPHY_POS:
        result = morphy(query, pos)
        lemmas = result.get(pos, set()) if result else set()
        for lemma in sorted(lemmas):
            normalized = _normalize_query(str(lemma))
            if normalized and normalized not in seen:
                queries.append((f"morphy_{pos}", normalized))
                seen.add(normalized)
    return queries


def _with_selected_attribute_synset(
    lookup_case: str,
    query: str,
    synsets: tuple[Any, ...],
) -> AttributeLookupResult:
    selected, tag, counts = _select_attribute_synset(list(synsets), query)
    attribute_gate = (
        _attribute_gate_for_lexfile(selected.lexfile()) if selected is not None else ""
    )
    return AttributeLookupResult(
        lookup_case,
        query,
        synsets,
        selected,
        tag,
        counts,
        attribute_gate,
        _attribute_decision_status(
            selected_synset=selected,
            synsets=synsets,
            attribute_gate=attribute_gate,
        ),
        _attribute_decision_reason(
            selected_synset=selected,
            synsets=synsets,
            attribute_gate=attribute_gate,
        ),
    )


def _select_attribute_synset(synsets: list[Any], query: str) -> tuple[Any | None, str, str]:
    if not synsets:
        return None, "unresolved_no_oewn_attribute_synset", ""
    if len(synsets) == 1:
        return synsets[0], "single_oewn_attribute_synset", ""

    compatible = [s for s in synsets if s.lexfile() in ATTRIBUTE_COMPATIBLE_LEXFILES]
    conditional = [s for s in synsets if s.lexfile() in CONDITIONAL_ATTRIBUTE_LEXFILES]
    other = [s for s in synsets if s not in compatible and s not in conditional]

    evidence: list[str] = []
    for group_name, group_synsets, stop_on_tie in (
        ("attribute_compatible", compatible, True),
        ("conditional", conditional, True),
        ("other", other, True),
    ):
        if not group_synsets:
            continue
        selected, tag, counts = _select_by_wn30_lemma_count(group_synsets, query)
        if counts:
            evidence.append(f"{group_name}:{counts}")
        if selected is not None:
            return selected, f"selected_by_wn30_{group_name}_lemma_count", "|".join(evidence)
        if stop_on_tie and tag == "wn30_tie":
            return None, f"ambiguous_{group_name}_wn30_tie", "|".join(evidence)

    return None, "ambiguous_wn30_all_zero_or_mapping_missing", "|".join(evidence)


def _attribute_gate_for_lexfile(lexfile: str) -> str:
    if lexfile in ATTRIBUTE_COMPATIBLE_LEXFILES:
        return "attribute_compatible"
    if lexfile in CONDITIONAL_ATTRIBUTE_LEXFILES:
        return "conditional"
    if lexfile in HARD_CONFLICT_ATTRIBUTE_LEXFILES or lexfile:
        return "hard_conflict"
    return ""


def _attribute_decision_status(
    *,
    selected_synset: Any | None,
    synsets: Sequence[Any],
    attribute_gate: str,
) -> str:
    if selected_synset is None:
        return "needs_manual" if synsets else "chosen"
    if attribute_gate == "attribute_compatible":
        return "chosen"
    return "needs_manual"


def _attribute_decision_reason(
    *,
    selected_synset: Any | None,
    synsets: Sequence[Any],
    attribute_gate: str,
) -> str:
    if selected_synset is None:
        return "manual_synset_required" if synsets else "no_oewn_attribute_synset"
    if attribute_gate == "attribute_compatible":
        return "selected_attribute_compatible"
    return "manual_attribute_gate_required"


def _inventory_synsets(row: Mapping[str, str]) -> tuple[_InventorySynset, ...]:
    synset_ids = _split_pipe(row.get("all_oewn_synsets", ""))
    lexfiles = _split_pipe(row.get("all_oewn_lexfiles", ""))
    if not synset_ids and row.get("selected_oewn_synset"):
        synset_ids = [row["selected_oewn_synset"]]
        lexfiles = [row.get("selected_oewn_lexfile", "")]
    selected_lemmas = tuple(_split_pipe(row.get("synset_lemmas", "")))
    synsets: list[_InventorySynset] = []
    for index, synset_id in enumerate(synset_ids):
        lexfile = lexfiles[index] if index < len(lexfiles) else ""
        lemmas = selected_lemmas if synset_id == row.get("selected_oewn_synset") else ()
        synsets.append(_InventorySynset(synset_id, lexfile, lemmas))
    return tuple(synsets)


def _inventory_selected_synset(
    row: Mapping[str, str],
    synsets: Sequence[_InventorySynset],
) -> _InventorySynset | None:
    selected_id = row.get("selected_oewn_synset", "")
    if not selected_id:
        return None
    for synset in synsets:
        if synset.id == selected_id:
            return synset
    return _InventorySynset(
        selected_id,
        row.get("selected_oewn_lexfile", ""),
        tuple(_split_pipe(row.get("synset_lemmas", ""))),
    )


def _inventory_row(acc: AttributeAccumulator) -> dict[str, str]:
    lookup = acc.lookup
    selected = lookup.selected_synset if lookup is not None else None
    return {
        "span_key": acc.span_key,
        "attribute_unit_type": acc.attribute_unit_type,
        "span_token_count": str(acc.span_token_count),
        "anchor_token_offset": str(acc.anchor_token_offset),
        "lookup_forms": "|".join(acc.lookup_forms),
        "attribute_mwe_rule_version": ATTRIBUTE_MWE_RULE_VERSION,
        "observed_surface": acc.surfaces.most_common(1)[0][0] if acc.surfaces else acc.span_key,
        "decision_status": lookup.decision_status if lookup is not None else "chosen",
        "decision_reason": lookup.decision_reason if lookup is not None else "no_oewn_attribute_synset",
        "count": str(acc.count),
        "caption_count": str(len(acc.caption_ids)),
        "example_caption_ids": "|".join(sorted(acc.caption_ids)[:5]),
        "example_surfaces": "|".join(surface for surface, _ in acc.surfaces.most_common(5)),
        "selected_lookup_case": lookup.lookup_case if lookup is not None else "unresolved",
        "selected_query": lookup.query if lookup is not None else "",
        "has_oewn_attribute_synset": "true" if lookup is not None and lookup.synsets else "false",
        "oewn_synset_count": str(len(lookup.synsets) if lookup is not None else 0),
        "selected_oewn_synset": selected.id if selected is not None else "",
        "selected_oewn_lexfile": selected.lexfile() if selected is not None else "",
        "attribute_gate": lookup.attribute_gate if lookup is not None else "",
        "synset_lemmas": "|".join(selected.lemmas()) if selected is not None else "",
        "canonical_surface": _preserved_prior_value(lookup, "canonical_surface"),
        "canonical_label_key": _preserved_prior_value(lookup, "canonical_label_key"),
        "canonical_selection_tag": _preserved_prior_value(lookup, "canonical_selection_tag"),
        "canonical_candidate_lemmas": _preserved_prior_value(lookup, "canonical_candidate_lemmas"),
        "canonical_candidate_lemma_counts": _preserved_prior_value(
            lookup,
            "canonical_candidate_lemma_counts",
        ),
        "google_ngram_candidate_surfaces": _preserved_prior_value(
            lookup,
            "google_ngram_candidate_surfaces",
        ),
        "google_ngram_candidate_mean_frequencies": _preserved_prior_value(
            lookup,
            "google_ngram_candidate_mean_frequencies",
        ),
        "attribute_parent": _preserved_prior_value(lookup, "attribute_parent"),
        "attribute_parent_selection_tag": _preserved_prior_value(
            lookup,
            "attribute_parent_selection_tag",
        ),
        "all_oewn_synsets": "|".join(s.id for s in lookup.synsets) if lookup is not None else "",
        "all_oewn_lexfiles": "|".join(s.lexfile() for s in lookup.synsets) if lookup is not None else "",
        "synset_selection_tag": lookup.synset_selection_tag
        if lookup is not None
        else "unresolved_no_oewn_attribute_synset",
        "wn30_lemma_counts": lookup.wn30_lemma_counts if lookup is not None else "",
        "decision_basis": "gpic_observed_attribute_inventory",
    }


def _preserved_prior_value(lookup: AttributeLookupResult | None, field: str) -> str:
    if lookup is None or lookup.source_row is None:
        return ""
    canonical_tag = lookup.source_row.get("canonical_selection_tag", "")
    if canonical_tag.startswith("manual_"):
        return ""
    return lookup.source_row.get(field, "")


def _lookup_rank(lookup: AttributeLookupResult | None) -> int:
    if lookup is None:
        return 0
    if lookup.decision_status == "needs_manual":
        return 4
    if lookup.selected_synset is not None:
        return 3
    if lookup.synsets:
        return 2
    return 1


def _limited_records(records: Iterable[Mapping[str, Any]], limit: int | None) -> Iterable[Mapping[str, Any]]:
    for index, record in enumerate(records):
        if limit is not None and index >= limit:
            break
        yield record


def _resume_records(
    records: Iterable[Mapping[str, Any]],
    *,
    resume_caption_total: int,
    limit: int | None,
) -> Iterable[Mapping[str, Any]]:
    for index, record in enumerate(records):
        if index < resume_caption_total:
            continue
        if limit is not None and index >= limit:
            break
        yield record


def _split_pipe(value: str) -> list[str]:
    return [part for part in value.split("|") if part]


def _unique_nonempty(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def _is_automatic_surface_changed_prior_row(row: Mapping[str, str]) -> bool:
    span_key = _normalize_query(row.get("span_key", "") or row.get("observed_surface", ""))
    selected_query = _normalize_query(row.get("selected_query", ""))
    if not span_key or not selected_query or span_key == selected_query:
        return False
    return not _has_manual_decision_evidence(row)


def _has_manual_decision_evidence(row: Mapping[str, str]) -> bool:
    evidence_fields = (
        "decision_basis",
        "synset_selection_tag",
        "decision_reason",
        "manual_resolution_type",
        "source_detail",
    )
    return any("manual" in row.get(field, "").lower() for field in evidence_fields)


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(handle, FIELDNAMES, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
