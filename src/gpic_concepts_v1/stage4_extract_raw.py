"""Stage 4 raw concept extraction.

Stage 4 consumes Stage 3 annotation records and creates raw mentions/edges
using only the documented v1 extraction rules R12-R18.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os
import re
from typing import Any

from gpic_concepts_v1.attribute_units import (
    ATTRIBUTE_UNIT_MWE,
    AttributeAnchor,
    AttributeMweCandidate,
    AttributeTokenView,
    ResolvedAttributeMweIndex,
    collect_attribute_anchors,
    select_attribute_mwes,
)
from gpic_concepts_v1.io_jsonl import iter_jsonl, write_jsonl
from gpic_concepts_v1.inventory_validation import (
    normalize_inventory_decision_status,
    normalize_legacy_decision_status,
)
from gpic_concepts_v1.schema import JsonObject, RawEdge, RawMention, make_local_id
from gpic_concepts_v1.schema import MISSING_SOURCE_MENTION_ID, MISSING_TARGET_MENTION_ID
from gpic_concepts_v1.runtime_memory import MemorySafetyConfig, ProgressWriter

try:  # pragma: no cover - exercised when runtime OEWN dependencies exist.
    import nltk
    import wn
    from nltk.corpus import wordnet as wn30
    from wn.morphy import Morphy
except ModuleNotFoundError:  # pragma: no cover - keeps lightweight tests importable.
    nltk = None  # type: ignore[assignment]
    wn = None  # type: ignore[assignment]
    wn30 = None  # type: ignore[assignment]
    Morphy = Any  # type: ignore[misc,assignment]

try:  # pragma: no cover - exercised when spaCy is installed.
    from spacy.tokens import Doc, Token
except ModuleNotFoundError:  # pragma: no cover - keeps non-spaCy tests importable.
    Doc = Any  # type: ignore[misc,assignment]
    Token = Any  # type: ignore[misc,assignment]


OBJECT_RULE_ID = "R12"
ATTRIBUTE_RULE_ID = "R13"
QUANTITY_RULE_ID = "R14"
ACTION_RULE_ID = "R15"
AGENT_RULE_ID = "R16"
AGENT_INHERIT_RULE_ID = "R16.1"
PASSIVE_AGENT_RULE_ID = "R16.2"
ACL_AGENT_RULE_ID = "R16.3"
PATIENT_RULE_ID = "R17"
PASSIVE_PATIENT_RULE_ID = "R17.1"
RELATION_RULE_ID = "R18"
RELATION_MWE_RULE_ID = "R18.1"

ATTRIBUTE_MODIFIER_DEPS = frozenset(("amod", "compound", "nmod"))
QUANTITY_MODIFIER_DEPS = frozenset(("nummod",))
PATIENT_DEPS = frozenset(("obj", "dobj"))
PASSIVE_SUBJECT_DEPS = frozenset(("nsubjpass", "csubjpass"))
PASSIVE_BY_DEPS = frozenset(("agent", "prep"))
PASSIVE_BY_OBJECT_DEPS = frozenset(("pobj",))
PASSIVE_LIKE_ACTION_CHILD_DEPS = frozenset(("nsubjpass", "auxpass", "agent"))
ACL_AGENT_ALLOWED_TAGS = frozenset(("VBG",))
RELATION_MWE_SOURCE_HEAD_POS = frozenset(("VERB", "AUX"))
OEWN_SPEC = "oewn:2025+"
ROOT = Path(os.environ.get("GPIC_RUNTIME_ROOT", Path.cwd()))
WN_DATA_DIR = ROOT / "resources" / "wn_data"
NLTK_DATA_DIR = ROOT / "resources" / "nltk_data"
SPAN_START_BLOCKED_DEPS = frozenset(("case", "cc", "det", "mark", "punct"))
SPAN_START_BLOCKED_POS = frozenset(
    ("ADP", "CCONJ", "DET", "PART", "PRON", "PUNCT", "SCONJ", "SPACE", "SYM")
)

OBJECT_COMPATIBLE_LEXFILES = frozenset(
    (
        "noun.animal",
        "noun.artifact",
        "noun.body",
        "noun.food",
        "noun.object",
        "noun.person",
        "noun.plant",
        "noun.substance",
    )
)
CONDITIONAL_OBJECT_LEXFILES = frozenset(
    (
        "noun.communication",
        "noun.group",
        "noun.location",
        "noun.phenomenon",
        "noun.possession",
        "noun.shape",
        "noun.Tops",
    )
)


@dataclass(slots=True)
class RawExtractionResult:
    raw_mentions: list[RawMention]
    raw_edges: list[RawEdge]


@dataclass(frozen=True, slots=True)
class _ObjectLookupResult:
    lookup_case: str
    query: str
    synsets: tuple[Any, ...]
    selected_synset: Any | None
    synset_selection_tag: str
    wn30_lemma_counts: str
    objectness_gate: str = ""
    decision_status: str = ""
    decision_reason: str = ""
    parent_oewn_synsets: tuple[str, ...] = ()
    parent_oewn_lexfiles: tuple[str, ...] = ()
    parent_lemmas: tuple[str, ...] = ()
    parent_selection_tag: str = ""
    canonical_surface: str = ""
    canonical_label_key: str = ""
    canonical_selection_tag: str = ""
    canonical_candidate_lemmas: tuple[str, ...] = ()
    canonical_candidate_lemma_counts: str = ""
    google_ngram_candidate_surfaces: tuple[str, ...] = ()
    google_ngram_candidate_mean_frequencies: str = ""


@dataclass(frozen=True, slots=True)
class _ActionLookupResult:
    lookup_case: str
    query: str
    synsets: tuple[Any, ...]
    selected_synset: Any | None
    synset_selection_tag: str
    wn30_lemma_counts: str
    decision_status: str = ""
    decision_reason: str = ""


class Stage4SynsetAmbiguityError(RuntimeError):
    """Raised when an object span is not ready for automatic extraction."""


@dataclass(frozen=True, slots=True)
class _InventorySynset:
    id: str
    _lexfile: str
    _lemmas: tuple[str, ...]

    def lexfile(self) -> str:
        return self._lexfile

    def lemmas(self) -> list[str]:
        return list(self._lemmas)


class GpicObjectInventoryLookup:
    """Lookup object span decisions from a GPIC-observed object inventory TSV."""

    def __init__(self, rows_by_key: Mapping[str, Mapping[str, str]]) -> None:
        self._rows_by_key = dict(rows_by_key)

    @classmethod
    def from_tsv(cls, path: str | Path) -> "GpicObjectInventoryLookup":
        rows_by_key: dict[str, Mapping[str, str]] = {}
        with Path(path).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                span_key = row.get("span_key", "")
                if not span_key:
                    span_key = _normalize_query(row.get("observed_surface", ""))
                if span_key and span_key not in rows_by_key:
                    rows_by_key[span_key] = dict(row)
        return cls(rows_by_key)

    def __call__(self, surface: str) -> _ObjectLookupResult | None:
        row = self._rows_by_key.get(_normalize_query(surface))
        if row is None:
            return None
        decision_status = _inventory_decision_status(row)
        synsets = _inventory_synsets(row)
        selected = _inventory_selected_synset(row, synsets)
        objectness_gate = row.get("objectness_gate", "")
        if selected is not None and not objectness_gate:
            objectness_gate = _objectness_gate_for_lexfile(selected.lexfile())
        if not row.get("decision_status"):
            decision_status = _decision_status_for_selection(
                selected_synset=selected,
                synsets=synsets,
                objectness_gate=objectness_gate,
            )
        decision_reason = row.get("decision_reason", "")
        if not decision_reason:
            decision_reason = _decision_reason_for_selection(
                selected_synset=selected,
                synsets=synsets,
                objectness_gate=objectness_gate,
            )
        return _ObjectLookupResult(
            lookup_case=row.get("selected_lookup_case", "gpic_inventory"),
            query=row.get("selected_query", "") or row.get("span_key", ""),
            synsets=synsets,
            selected_synset=selected,
            synset_selection_tag=row.get("synset_selection_tag", "gpic_inventory"),
            wn30_lemma_counts=row.get("wn30_lemma_counts", ""),
            objectness_gate=objectness_gate,
            decision_status=decision_status,
            decision_reason=decision_reason,
            parent_oewn_synsets=tuple(_split_pipe(row.get("parent_oewn_synsets", ""))),
            parent_oewn_lexfiles=tuple(_split_pipe(row.get("parent_oewn_lexfiles", ""))),
            parent_lemmas=tuple(_split_pipe(row.get("parent_lemmas", ""))),
            parent_selection_tag=row.get("parent_selection_tag", ""),
            canonical_surface=row.get("canonical_surface", ""),
            canonical_label_key=row.get("canonical_label_key", ""),
            canonical_selection_tag=row.get("canonical_selection_tag", ""),
            canonical_candidate_lemmas=tuple(
                _split_pipe(row.get("canonical_candidate_lemmas", ""))
            ),
            canonical_candidate_lemma_counts=row.get("canonical_candidate_lemma_counts", ""),
            google_ngram_candidate_surfaces=tuple(
                _split_pipe(row.get("google_ngram_candidate_surfaces", ""))
            ),
            google_ngram_candidate_mean_frequencies=row.get(
                "google_ngram_candidate_mean_frequencies",
                "",
            ),
        )


class GpicActionInventoryLookup:
    """Lookup resolved action span decisions from a GPIC-observed action inventory TSV."""

    def __init__(
        self,
        rows_by_key: Mapping[str, Mapping[str, str]],
        rows_by_selected_query: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._rows_by_key = dict(rows_by_key)
        self._rows_by_selected_query = dict(rows_by_selected_query or {})

    @classmethod
    def from_tsv(cls, path: str | Path) -> "GpicActionInventoryLookup":
        rows_by_key: dict[str, Mapping[str, str]] = {}
        rows: list[Mapping[str, str]] = []
        with Path(path).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                row_dict = dict(row)
                rows.append(row_dict)
                span_key = row.get("span_key", "")
                if not span_key:
                    span_key = _normalize_query(row.get("observed_surface", ""))
                if span_key and span_key not in rows_by_key:
                    rows_by_key[span_key] = row_dict
        return cls(rows_by_key, _unique_action_rows_by_selected_query(rows))

    def __call__(self, surface: str) -> _ActionLookupResult | None:
        row = self._rows_by_key.get(_normalize_query(surface))
        if row is None:
            return None
        return _action_lookup_result_from_inventory_row(row)

    def lookup_selected_query(self, query: str) -> _ActionLookupResult | None:
        row = self._rows_by_selected_query.get(_normalize_query(query))
        if row is None:
            return None
        return _action_lookup_result_from_inventory_row(
            row,
            lookup_case="gpic_action_inventory_selected_query",
        )


def _unique_action_rows_by_selected_query(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    grouped: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        decision_key = _action_reuse_decision_key(row)
        if decision_key is None:
            continue
        selected_query = _normalize_query(row.get("selected_query", ""))
        if not selected_query:
            continue
        grouped.setdefault(selected_query, []).append(row)

    unique_rows: dict[str, Mapping[str, str]] = {}
    for selected_query, group in grouped.items():
        decision_keys = {
            key
            for row in group
            if (key := _action_reuse_decision_key(row)) is not None
        }
        if len(decision_keys) == 1:
            unique_rows[selected_query] = group[0]
    return unique_rows


def _action_reuse_decision_key(row: Mapping[str, str]) -> tuple[str, str] | None:
    decision_status = _inventory_decision_status(row)
    selected_synset = row.get("selected_oewn_synset", "").strip()
    if decision_status == "chosen" and selected_synset:
        return ("chosen", selected_synset)
    if decision_status == "raw_fallback" and not selected_synset:
        return ("raw_fallback", "")
    return None


def _action_lookup_result_from_inventory_row(
    row: Mapping[str, str],
    *,
    lookup_case: str | None = None,
) -> _ActionLookupResult:
    synsets = _inventory_synsets(row)
    selected = _inventory_selected_synset(row, synsets)
    return _ActionLookupResult(
        lookup_case=lookup_case or row.get("selected_lookup_case", "gpic_action_inventory"),
        query=row.get("selected_query", "") or row.get("span_key", ""),
        synsets=synsets,
        selected_synset=selected,
        synset_selection_tag=row.get("synset_selection_tag", "gpic_action_inventory"),
        wn30_lemma_counts=row.get("wn30_lemma_counts", ""),
        decision_status=_inventory_decision_status(row),
        decision_reason=row.get("decision_reason", ""),
    )


@dataclass(frozen=True, slots=True)
class _ObjectSpanSelection:
    token_indices: tuple[int, ...]
    lookup_token_indices: tuple[int, ...]
    text: str
    lookup_text: str
    lemma: str
    char_start: int | None
    char_end: int | None
    token_start: int
    token_end: int
    source_detail: JsonObject


@dataclass(frozen=True, slots=True)
class _ActionSpanCandidate:
    tokens: tuple[Any, ...]
    token_indices: tuple[int, ...]
    prep_token_indices: tuple[int, ...]
    text: str
    candidate_type: str


@dataclass(frozen=True, slots=True)
class _ActionSpanSelection:
    head_i: int
    token_indices: tuple[int, ...]
    prep_token_indices: tuple[int, ...]
    text: str
    lemma: str
    char_start: int | None
    char_end: int | None
    token_start: int
    token_end: int
    source_detail: JsonObject


@dataclass(frozen=True, slots=True)
class _PrepositionMweEntry:
    surface: str
    token_keys: tuple[str, ...]
    canonical_relation: str
    relation_components: tuple[str, ...]
    initial_relation_token_offset: int
    final_adp_token_offset: int
    source: str = ""
    notes: str = ""


@dataclass(frozen=True, slots=True)
class _PrepositionMweIndex:
    entries_by_token_keys: Mapping[tuple[str, ...], tuple[_PrepositionMweEntry, ...]]
    widths: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _PrepositionMweMatch:
    entry: _PrepositionMweEntry
    token_indices: tuple[int, ...]
    raw_span_surface: str


@dataclass(frozen=True, slots=True)
class _RelationSourceCandidate:
    source_i: int
    source_mention_id: str
    source_dep: str
    source_resolution: str


@dataclass(frozen=True, slots=True)
class _RelationTargetCandidate:
    target_i: int
    target_mention_id: str
    target_text: str
    target_dep: str
    target_resolution: str
    target_base_i: int
    conj_head_i: int | None = None


class _RawBuilder:
    def __init__(self, caption_id: str) -> None:
        self.caption_id = caption_id
        self.raw_mentions: list[RawMention] = []
        self.raw_edges: list[RawEdge] = []
        self.object_by_token: dict[int, str] = {}
        self.action_by_token: dict[int, str] = {}
        self.action_head_tokens: set[int] = set()
        self.action_head_by_id: dict[str, int] = {}
        self.consumed_action_tokens: set[int] = set()
        self.consumed_action_adp_tokens: set[int] = set()
        self.relation_mwe_matches: list[_PrepositionMweMatch] = []
        self.relation_mwe_consumed_tokens: set[int] = set()

    def add_mention(
        self,
        *,
        mention_type: str,
        text: str,
        lemma: str,
        rule_id: str,
        char_start: int | None,
        char_end: int | None,
        token_start: int | None,
        token_end: int | None,
        source_text: str | None,
        source_detail: Mapping[str, Any] | None = None,
    ) -> str:
        mention_id = make_local_id("m", len(self.raw_mentions))
        mention = RawMention(
            caption_id=self.caption_id,
            mention_id=mention_id,
            mention_type=mention_type,  # type: ignore[arg-type]
            text=text,
            lemma=lemma,
            rule_id=rule_id,
            char_start=char_start,
            char_end=char_end,
            token_start=token_start,
            token_end=token_end,
            source_text=source_text,
            source_detail=dict(source_detail or {}),
        )
        self.raw_mentions.append(mention)
        return mention_id

    def add_edge(
        self,
        *,
        edge_type: str,
        source_mention_id: str,
        target_mention_id: str,
        label: str,
        rule_id: str,
        evidence_text: str | None,
        source_detail: Mapping[str, Any] | None = None,
    ) -> str:
        edge_id = make_local_id("e", len(self.raw_edges))
        edge = RawEdge(
            caption_id=self.caption_id,
            edge_id=edge_id,
            edge_type=edge_type,  # type: ignore[arg-type]
            source_mention_id=source_mention_id,
            target_mention_id=target_mention_id,
            label=label,
            rule_id=rule_id,
            evidence_text=evidence_text,
            source_detail=dict(source_detail or {}),
        )
        self.raw_edges.append(edge)
        return edge_id


def extract_raw_concepts_from_stage3_record(
    stage3_record: Mapping[str, Any],
    *,
    object_lookup: Any | None = None,
    attribute_mwe_lookup: ResolvedAttributeMweIndex | None = None,
    action_lookup: Any | None = None,
    preposition_mwe_lookup: _PrepositionMweIndex | Sequence[_PrepositionMweEntry] | None = None,
) -> RawExtractionResult:
    """Extract raw mentions and edges from one Stage 3 annotation record."""
    caption_id = _require_text(stage3_record, "caption_id")
    tokens = _require_list(stage3_record, "tokens")
    noun_chunks = _require_list(stage3_record, "noun_chunks")

    token_by_i = {_require_int(token, "i"): token for token in tokens}
    children_by_head = _build_children_by_head(tokens)
    builder = _RawBuilder(caption_id)
    if object_lookup is None:
        object_lookup = _load_object_lookup_runtime()
    if action_lookup is None and isinstance(object_lookup, dict):
        action_lookup = object_lookup
    if _is_tag_list_stage3_record(stage3_record):
        try:
            _extract_tag_list_objects_and_modifiers(
                builder,
                stage3_record=stage3_record,
                object_lookup=object_lookup,
                attribute_mwe_lookup=attribute_mwe_lookup,
                children_by_head=children_by_head,
            )
        except Stage4SynsetAmbiguityError as exc:
            raise Stage4SynsetAmbiguityError(f"caption_id={caption_id}: {exc}") from exc
        return RawExtractionResult(
            raw_mentions=builder.raw_mentions,
            raw_edges=builder.raw_edges,
        )
    if preposition_mwe_lookup is None:
        preposition_mwe_lookup = _load_preposition_mwe_lookup_runtime()
    builder.relation_mwe_matches = _find_preposition_mwe_matches_in_token_records(
        tokens,
        preposition_mwe_lookup,
    )
    builder.relation_mwe_consumed_tokens.update(
        token_i
        for match in builder.relation_mwe_matches
        for token_i in match.token_indices
    )

    try:
        _extract_objects_and_chunk_modifiers(
            builder,
            noun_chunks=noun_chunks,
            token_by_i=token_by_i,
            object_lookup=object_lookup,
            attribute_mwe_lookup=attribute_mwe_lookup,
            children_by_head=children_by_head,
        )
    except Stage4SynsetAmbiguityError as exc:
        raise Stage4SynsetAmbiguityError(f"caption_id={caption_id}: {exc}") from exc
    _extract_relation_mwe_edges(
        builder,
        tokens=tokens,
        children_by_head=children_by_head,
        token_by_i=token_by_i,
    )
    _extract_actions(
        builder,
        tokens=tokens,
        children_by_head=children_by_head,
        action_lookup=action_lookup,
    )
    _extract_event_roles(
        builder,
        tokens=tokens,
        children_by_head=children_by_head,
    )
    _inherit_acl_action_head_agents(
        builder,
        tokens=tokens,
        children_by_head=children_by_head,
    )
    _inherit_conjunct_action_agents(
        builder,
        tokens=tokens,
        children_by_head=children_by_head,
    )
    _extract_relations(
        builder,
        tokens=tokens,
        children_by_head=children_by_head,
    )

    return RawExtractionResult(
        raw_mentions=builder.raw_mentions,
        raw_edges=builder.raw_edges,
    )


def _is_tag_list_stage3_record(stage3_record: Mapping[str, Any]) -> bool:
    meta = stage3_record.get("meta")
    if isinstance(meta, Mapping) and meta.get("caption_shape") == "tag_list":
        return True
    if _optional_text(stage3_record, "caption_shape") == "tag_list":
        return True
    return bool(stage3_record.get("tag_segments"))


def extract_raw_concepts_from_doc(
    caption_id: str,
    doc: Doc,
    *,
    object_lookup: Any | None = None,
    attribute_mwe_lookup: ResolvedAttributeMweIndex | None = None,
    action_lookup: Any | None = None,
    preposition_mwe_lookup: _PrepositionMweIndex | Sequence[_PrepositionMweEntry] | None = None,
) -> RawExtractionResult:
    """Extract raw mentions and edges directly from an annotated spaCy Doc."""
    builder = _RawBuilder(caption_id)
    children_by_head = _build_doc_children_by_head(doc)
    if object_lookup is None:
        object_lookup = _load_object_lookup_runtime()
    if action_lookup is None and isinstance(object_lookup, dict):
        action_lookup = object_lookup
    if preposition_mwe_lookup is None:
        preposition_mwe_lookup = _load_preposition_mwe_lookup_runtime()
    builder.relation_mwe_matches = _find_preposition_mwe_matches_in_doc(
        doc,
        preposition_mwe_lookup,
    )
    builder.relation_mwe_consumed_tokens.update(
        token_i
        for match in builder.relation_mwe_matches
        for token_i in match.token_indices
    )

    try:
        _extract_doc_objects_and_chunk_modifiers(
            builder,
            doc=doc,
            object_lookup=object_lookup,
            attribute_mwe_lookup=attribute_mwe_lookup,
            children_by_head=children_by_head,
        )
    except Stage4SynsetAmbiguityError as exc:
        raise Stage4SynsetAmbiguityError(f"caption_id={caption_id}: {exc}") from exc
    _extract_doc_relation_mwe_edges(
        builder,
        doc=doc,
        children_by_head=children_by_head,
    )
    _extract_doc_actions(
        builder,
        doc=doc,
        children_by_head=children_by_head,
        action_lookup=action_lookup,
    )
    _extract_doc_event_roles(
        builder,
        doc=doc,
        children_by_head=children_by_head,
    )
    _inherit_doc_acl_action_head_agents(
        builder,
        doc=doc,
        children_by_head=children_by_head,
    )
    _inherit_doc_conjunct_action_agents(
        builder,
        doc=doc,
        children_by_head=children_by_head,
    )
    _extract_doc_relations(
        builder,
        doc=doc,
        children_by_head=children_by_head,
    )

    return RawExtractionResult(
        raw_mentions=builder.raw_mentions,
        raw_edges=builder.raw_edges,
    )


def run_stage4_extract_raw(
    input_path: str | Path,
    *,
    raw_mentions_path: str | Path,
    raw_edges_path: str | Path,
    summary_path: str | Path | None = None,
    limit: int | None = None,
    object_lookup: Any | None = None,
    attribute_mwe_lookup: ResolvedAttributeMweIndex | None = None,
    action_lookup: Any | None = None,
    preposition_mwe_lookup: _PrepositionMweIndex | Sequence[_PrepositionMweEntry] | None = None,
    max_rss_gib: float | None = None,
    memory_limit_gib: float | None = None,
    rss_limit_fraction: float = 0.75,
    rss_reserve_gib: float = 16.0,
    progress_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run Stage 4 over Stage 3 JSONL records."""
    if object_lookup is None:
        object_lookup = _load_object_lookup_runtime()
    if action_lookup is None and isinstance(object_lookup, dict):
        action_lookup = object_lookup
    if preposition_mwe_lookup is None:
        preposition_mwe_lookup = _load_preposition_mwe_lookup_runtime()
    else:
        preposition_mwe_lookup = _coerce_preposition_mwe_index(preposition_mwe_lookup)
    all_mentions: list[RawMention] = []
    all_edges: list[RawEdge] = []
    mention_counts: Counter[str] = Counter()
    edge_counts: Counter[str] = Counter()
    total = 0
    memory_config = MemorySafetyConfig(
        max_rss_gib=max_rss_gib,
        memory_limit_gib=memory_limit_gib,
        rss_limit_fraction=rss_limit_fraction,
        rss_reserve_gib=rss_reserve_gib,
    )
    progress = ProgressWriter(
        progress_path,
        stage_name="stage4",
        memory_config=memory_config,
    )
    progress.write(
        status="running",
        phase="stage4_extract_raw",
        note="started",
        metrics={
            "records_processed": total,
            "raw_mention_total": len(all_mentions),
            "raw_edge_total": len(all_edges),
            "mention_type_counts": dict(sorted(mention_counts.items())),
            "edge_type_counts": dict(sorted(edge_counts.items())),
        },
        outputs={
            "raw_mentions": raw_mentions_path,
            "raw_edges": raw_edges_path,
        },
    )

    try:
        for index, record in enumerate(iter_jsonl(input_path)):
            if limit is not None and index >= limit:
                break
            progress.check_memory(
                phase="stage4_extract_raw",
                metrics={"records_processed": total},
            )
            result = extract_raw_concepts_from_stage3_record(
                record,
                object_lookup=object_lookup,
                attribute_mwe_lookup=attribute_mwe_lookup,
                action_lookup=action_lookup,
                preposition_mwe_lookup=preposition_mwe_lookup,
            )
            total += 1
            all_mentions.extend(result.raw_mentions)
            all_edges.extend(result.raw_edges)
            mention_counts.update(mention.mention_type for mention in result.raw_mentions)
            edge_counts.update(edge.edge_type for edge in result.raw_edges)
            if total % 1000 == 0:
                progress.write(
                    status="running",
                    phase="stage4_extract_raw",
                    note="extracting_raw_graph",
                    metrics={
                        "records_processed": total,
                        "raw_mention_total": len(all_mentions),
                        "raw_edge_total": len(all_edges),
                        "mention_type_counts": dict(sorted(mention_counts.items())),
                        "edge_type_counts": dict(sorted(edge_counts.items())),
                    },
                    outputs={
                        "raw_mentions": raw_mentions_path,
                        "raw_edges": raw_edges_path,
                    },
                )
    except Exception as exc:
        progress.write(
            status="failed",
            phase="stage4_extract_raw",
            note=f"{type(exc).__name__}: {exc}",
            metrics={
                "records_processed": total,
                "raw_mention_total": len(all_mentions),
                "raw_edge_total": len(all_edges),
                "mention_type_counts": dict(sorted(mention_counts.items())),
                "edge_type_counts": dict(sorted(edge_counts.items())),
            },
            outputs={
                "raw_mentions": raw_mentions_path,
                "raw_edges": raw_edges_path,
            },
        )
        raise

    progress.write(
        status="running",
        phase="stage4_writing_outputs",
        note="extraction_complete",
        metrics={
            "records_processed": total,
            "raw_mention_total": len(all_mentions),
            "raw_edge_total": len(all_edges),
            "mention_type_counts": dict(sorted(mention_counts.items())),
            "edge_type_counts": dict(sorted(edge_counts.items())),
        },
        outputs={
            "raw_mentions": raw_mentions_path,
            "raw_edges": raw_edges_path,
        },
    )

    write_jsonl(raw_mentions_path, all_mentions, sort_keys=False, compact=True)
    write_jsonl(raw_edges_path, all_edges, sort_keys=False, compact=True)
    summary = {
        "total": total,
        "raw_mentions_path": str(raw_mentions_path),
        "raw_edges_path": str(raw_edges_path),
        "raw_mention_total": len(all_mentions),
        "raw_edge_total": len(all_edges),
        "mention_type_counts": dict(sorted(mention_counts.items())),
        "edge_type_counts": dict(sorted(edge_counts.items())),
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
        phase="stage4_complete",
        note="complete",
        metrics={
            "records_processed": total,
            "raw_mention_total": len(all_mentions),
            "raw_edge_total": len(all_edges),
            "mention_type_counts": dict(sorted(mention_counts.items())),
            "edge_type_counts": dict(sorted(edge_counts.items())),
        },
        outputs={
            "raw_mentions": raw_mentions_path,
            "raw_edges": raw_edges_path,
        },
        summary=summary,
    )
    return summary


def _extract_tag_list_objects_and_modifiers(
    builder: _RawBuilder,
    *,
    stage3_record: Mapping[str, Any],
    object_lookup: Any | None,
    attribute_mwe_lookup: ResolvedAttributeMweIndex | None,
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> None:
    for segment in _require_list(stage3_record, "tag_segments"):
        segment_id = _optional_text(segment, "segment_id") or ""
        segment_text = _optional_text(segment, "text") or ""
        segment_tokens = _require_list(segment, "tokens")
        segment_chunks = _require_list(segment, "noun_chunks")
        token_by_i = {_require_int(token, "i"): token for token in segment_tokens}
        mention_start = len(builder.raw_mentions)
        edge_start = len(builder.raw_edges)
        _extract_objects_and_chunk_modifiers(
            builder,
            noun_chunks=segment_chunks,
            token_by_i=token_by_i,
            object_lookup=object_lookup,
            attribute_mwe_lookup=attribute_mwe_lookup,
            children_by_head=children_by_head,
        )
        _mark_tag_list_outputs(
            builder,
            mention_start=mention_start,
            edge_start=edge_start,
            segment_id=segment_id,
            segment_text=segment_text,
        )
        object_added = any(
            mention.mention_type == "object"
            for mention in builder.raw_mentions[mention_start:]
        )
        if not object_added:
            _extract_tag_list_floating_attribute(
                builder,
                segment_tokens=segment_tokens,
                segment_id=segment_id,
                segment_text=segment_text,
                attribute_mwe_lookup=attribute_mwe_lookup,
            )


def _mark_tag_list_outputs(
    builder: _RawBuilder,
    *,
    mention_start: int,
    edge_start: int,
    segment_id: str,
    segment_text: str,
) -> None:
    detail = {
        "caption_shape": "tag_list",
        "tag_segment_id": segment_id,
        "tag_segment_text": segment_text,
    }
    for mention in builder.raw_mentions[mention_start:]:
        mention.source_detail.update(detail)
    for edge in builder.raw_edges[edge_start:]:
        edge.source_detail.update(detail)


def _extract_tag_list_floating_attribute(
    builder: _RawBuilder,
    *,
    segment_tokens: Sequence[Mapping[str, Any]],
    segment_id: str,
    segment_text: str,
    attribute_mwe_lookup: ResolvedAttributeMweIndex | None,
) -> None:
    content_tokens = [
        token
        for token in segment_tokens
        if _optional_text(token, "pos") not in {"PUNCT", "SPACE"}
    ]
    if len(content_tokens) >= 2 and attribute_mwe_lookup is not None:
        token_views = _record_attribute_token_views(content_tokens)
        candidate = AttributeMweCandidate(
            tokens=token_views,
            anchor=AttributeAnchor(token_i=token_views[-1].i),
            surface=" ".join(token.text for token in token_views),
        )
        inventory_row = attribute_mwe_lookup(candidate)
        if inventory_row is not None:
            _add_unattached_record_attribute_mwe(
                builder,
                candidate=candidate,
                inventory_row=inventory_row,
                source_text=segment_text,
                source_detail={
                    "caption_shape": "tag_list",
                    "tag_segment_id": segment_id,
                    "tag_segment_text": segment_text,
                    "modifier_source": "tag_list_unattached_attribute_mwe",
                },
            )
            return
    if len(content_tokens) != 1:
        return
    token = content_tokens[0]
    if not _is_tag_list_floating_attribute_token(token):
        return
    token_i = _require_int(token, "i")
    builder.add_mention(
        mention_type="attribute",
        text=_token_text(token),
        lemma=_token_lemma(token),
        rule_id=ATTRIBUTE_RULE_ID,
        char_start=_optional_int(token, "char_start"),
        char_end=_optional_int(token, "char_end"),
        token_start=token_i,
        token_end=token_i + 1,
        source_text=segment_text,
        source_detail={
            "caption_shape": "tag_list",
            "tag_segment_id": segment_id,
            "tag_segment_text": segment_text,
            "modifier_source": "tag_list_unattached_attribute",
        },
    )


def _is_tag_list_floating_attribute_token(token: Mapping[str, Any]) -> bool:
    pos = _optional_text(token, "pos")
    tag = _optional_text(token, "tag")
    if pos in {"ADJ", "ADV"}:
        return True
    return tag in {"JJ", "JJR", "JJS", "VBG", "VBN"}


def _extract_doc_objects_and_chunk_modifiers(
    builder: _RawBuilder,
    *,
    doc: Doc,
    object_lookup: Any | None,
    attribute_mwe_lookup: ResolvedAttributeMweIndex | None,
    children_by_head: Mapping[int, Sequence[Token]],
) -> None:
    for chunk in doc.noun_chunks:
        selection = _select_doc_chunk_object_span(chunk, object_lookup=object_lookup)
        if selection is None:
            continue
        if any(token_i in builder.relation_mwe_consumed_tokens for token_i in selection.token_indices):
            continue
        if any(token_i in builder.object_by_token for token_i in selection.token_indices):
            continue

        object_id = builder.add_mention(
            mention_type="object",
            text=selection.text,
            lemma=selection.lemma,
            rule_id=OBJECT_RULE_ID,
            char_start=selection.char_start,
            char_end=selection.char_end,
            token_start=selection.token_start,
            token_end=selection.token_end,
            source_text=chunk.text,
            source_detail=selection.source_detail,
        )
        for token_i in selection.token_indices:
            builder.object_by_token[token_i] = object_id

        chunk_token_indices = {token.i for token in chunk}
        excluded_modifier_tokens = set(selection.token_indices) | builder.relation_mwe_consumed_tokens
        selected_attribute_mwes = _select_doc_attribute_mwes(
            chunk,
            children_by_head=children_by_head,
            excluded_token_indices=excluded_modifier_tokens,
            attribute_mwe_lookup=attribute_mwe_lookup,
        )
        attribute_mwe_by_start = {
            selected.candidate.token_start: selected
            for selected in selected_attribute_mwes
        }
        attribute_mwe_consumed_tokens = {
            token_i
            for selected in selected_attribute_mwes
            for token_i in selected.candidate.token_indices
        }
        emitted_attribute_tokens: set[int] = set()

        def add_attribute_modifier(token: Token, *, conj_head: Token | None = None) -> None:
            token_i = token.i
            if token_i in attribute_mwe_consumed_tokens:
                return
            if token_i in emitted_attribute_tokens:
                return
            emitted_attribute_tokens.add(token_i)
            source_detail = _doc_modifier_detail(token, chunk.root.i)
            edge_detail: JsonObject = {"root_i": chunk.root.i, "modifier_i": token_i}
            if conj_head is not None:
                source_detail.update(
                    {
                        "modifier_source": "conj_of_attribute_modifier",
                        "conj_head_i": conj_head.i,
                        "conj_head_text": conj_head.text,
                    }
                )
                edge_detail.update(
                    {
                        "modifier_source": "conj_of_attribute_modifier",
                        "conj_head_i": conj_head.i,
                    }
                )
            attribute_id = builder.add_mention(
                mention_type="attribute",
                text=token.text,
                lemma=_doc_token_lemma(token),
                rule_id=ATTRIBUTE_RULE_ID,
                char_start=token.idx,
                char_end=token.idx + len(token.text),
                token_start=token_i,
                token_end=token_i + 1,
                source_text=chunk.text,
                source_detail=source_detail,
            )
            builder.add_edge(
                edge_type="has_attribute",
                source_mention_id=object_id,
                target_mention_id=attribute_id,
                label="has_attribute",
                rule_id=ATTRIBUTE_RULE_ID,
                evidence_text=chunk.text,
                source_detail=edge_detail,
            )

        def add_attribute_mwe(selected: Any) -> None:
            candidate = selected.candidate
            inventory_row = selected.lookup
            attribute_id = builder.add_mention(
                mention_type="attribute",
                text=_doc_attribute_mwe_text(doc, candidate),
                lemma=" ".join(token.lemma for token in candidate.tokens),
                rule_id=ATTRIBUTE_RULE_ID,
                char_start=candidate.tokens[0].char_start,
                char_end=candidate.tokens[-1].char_end,
                token_start=candidate.token_start,
                token_end=candidate.token_end,
                source_text=chunk.text,
                source_detail=_attribute_mwe_detail(candidate, inventory_row, chunk.root.i),
            )
            builder.add_edge(
                edge_type="has_attribute",
                source_mention_id=object_id,
                target_mention_id=attribute_id,
                label="has_attribute",
                rule_id=ATTRIBUTE_RULE_ID,
                evidence_text=chunk.text,
                source_detail={
                    "root_i": chunk.root.i,
                    "modifier_i": candidate.anchor.token_i,
                    "modifier_source": "attribute_mwe",
                    "selected_token_indices": list(candidate.token_indices),
                },
            )

        for token in chunk:
            token_i = token.i
            if token_i in selection.token_indices:
                continue
            if token_i in builder.relation_mwe_consumed_tokens:
                continue
            selected_mwe = attribute_mwe_by_start.get(token_i)
            if selected_mwe is not None:
                add_attribute_mwe(selected_mwe)
                continue
            if token_i in attribute_mwe_consumed_tokens:
                continue
            if _is_doc_quantity_modifier(token):
                quantity_id = builder.add_mention(
                    mention_type="quantity",
                    text=token.text,
                    lemma=_doc_token_lemma(token),
                    rule_id=QUANTITY_RULE_ID,
                    char_start=token.idx,
                    char_end=token.idx + len(token.text),
                    token_start=token_i,
                    token_end=token_i + 1,
                    source_text=chunk.text,
                    source_detail=_doc_modifier_detail(token, chunk.root.i),
                )
                builder.add_edge(
                    edge_type="has_quantity",
                    source_mention_id=object_id,
                    target_mention_id=quantity_id,
                    label="has_quantity",
                    rule_id=QUANTITY_RULE_ID,
                    evidence_text=chunk.text,
                    source_detail={"root_i": chunk.root.i, "modifier_i": token_i},
                )
            elif _is_doc_attribute_modifier(token):
                add_attribute_modifier(token)
                for conj_token, conj_head in _doc_conjunct_attribute_modifiers(
                    token,
                    children_by_head=children_by_head,
                    chunk_token_indices=chunk_token_indices,
                    excluded_token_indices=excluded_modifier_tokens,
                ):
                    add_attribute_modifier(conj_token, conj_head=conj_head)


def _extract_doc_actions(
    builder: _RawBuilder,
    *,
    doc: Doc,
    children_by_head: Mapping[int, Sequence[Token]],
    action_lookup: Any | None,
) -> None:
    for token in doc:
        if token.pos_ != "VERB":
            continue
        if token.i in builder.relation_mwe_consumed_tokens:
            continue
        selection = _select_doc_action_span(
            token,
            children_by_head=children_by_head,
            excluded_token_indices=builder.relation_mwe_consumed_tokens,
            action_lookup=action_lookup,
        )
        action_id = builder.add_mention(
            mention_type="action",
            text=selection.text,
            lemma=selection.lemma,
            rule_id=ACTION_RULE_ID,
            char_start=selection.char_start,
            char_end=selection.char_end,
            token_start=selection.token_start,
            token_end=selection.token_end,
            source_text=selection.text,
            source_detail=selection.source_detail,
        )
        builder.action_head_tokens.add(selection.head_i)
        builder.action_head_by_id[action_id] = selection.head_i
        for token_i in selection.token_indices:
            builder.action_by_token[token_i] = action_id
            builder.consumed_action_tokens.add(token_i)
        for prep_i in selection.prep_token_indices:
            builder.consumed_action_adp_tokens.add(prep_i)


def _extract_doc_event_roles(
    builder: _RawBuilder,
    *,
    doc: Doc,
    children_by_head: Mapping[int, Sequence[Token]],
) -> None:
    for token in doc:
        action_id = builder.action_by_token.get(token.i)
        if action_id is None or token.i not in builder.action_head_tokens:
            continue
        passive_subject_added = False
        for child in children_by_head.get(token.i, ()):
            target_id = builder.object_by_token.get(child.i)
            if target_id is None:
                continue
            if child.dep_ == "nsubj":
                builder.add_edge(
                    edge_type="event_role",
                    source_mention_id=action_id,
                    target_mention_id=target_id,
                    label="agent",
                    rule_id=AGENT_RULE_ID,
                    evidence_text=f"{child.text} -> {token.text}",
                    source_detail={"dep": child.dep_, "action_i": token.i, "target_i": child.i},
                )
            elif child.dep_ in PASSIVE_SUBJECT_DEPS:
                builder.add_edge(
                    edge_type="event_role",
                    source_mention_id=action_id,
                    target_mention_id=target_id,
                    label="patient",
                    rule_id=PASSIVE_PATIENT_RULE_ID,
                    evidence_text=f"{child.text} -> {token.text}",
                    source_detail={
                        "dep": child.dep_,
                        "action_i": token.i,
                        "target_i": child.i,
                        "raw_role": "theme",
                        "voice_normalization": "passive_to_active",
                        "role_source": "passive_subject",
                    },
                )
                passive_subject_added = True
            elif child.dep_ in PATIENT_DEPS:
                builder.add_edge(
                    edge_type="event_role",
                    source_mention_id=action_id,
                    target_mention_id=target_id,
                    label="patient",
                    rule_id=PATIENT_RULE_ID,
                    evidence_text=f"{token.text} -> {child.text}",
                    source_detail={"dep": child.dep_, "action_i": token.i, "target_i": child.i},
                )
        if passive_subject_added:
            _extract_doc_passive_by_agent_edges(
                builder,
                action_id=action_id,
                action_i=token.i,
                children_by_head=children_by_head,
            )
    for prep_i in sorted(builder.consumed_action_adp_tokens):
        action_id = builder.action_by_token.get(prep_i)
        if action_id is None:
            continue
        action_head_i = builder.action_head_by_id.get(action_id, prep_i)
        prep = doc[prep_i]
        for child in children_by_head.get(prep_i, ()):
            if child.dep_ != "pobj":
                continue
            target_id = builder.object_by_token.get(child.i)
            if target_id is None:
                continue
            builder.add_edge(
                edge_type="event_role",
                source_mention_id=action_id,
                target_mention_id=target_id,
                label="patient",
                rule_id=PATIENT_RULE_ID,
                evidence_text=f"{prep.text} -> {child.text}",
                source_detail={
                    "dep": child.dep_,
                    "action_i": action_head_i,
                    "prep_i": prep_i,
                    "target_i": child.i,
                    "role_source": "selected_phrasal_action_prep_pobj",
                },
            )


def _extract_doc_relations(
    builder: _RawBuilder,
    *,
    doc: Doc,
    children_by_head: Mapping[int, Sequence[Token]],
) -> None:
    for token in doc:
        if token.pos_ != "ADP":
            continue
        if token.i in builder.consumed_action_adp_tokens:
            continue
        if token.i in builder.relation_mwe_consumed_tokens:
            continue
        source_id = builder.object_by_token.get(token.head.i)
        if source_id is None:
            continue
        for child in children_by_head.get(token.i, ()):
            if child.dep_ != "pobj":
                continue
            for target_candidate in _doc_relation_target_candidates_from_base(
                builder,
                base_target=child,
                children_by_head=children_by_head,
                direct_resolution="direct_pobj",
                conj_resolution="conj_of_pobj",
            ):
                source_detail: JsonObject = {
                    "prep_i": token.i,
                    "source_i": token.head.i,
                    "target_i": target_candidate.target_i,
                    "target_dep": target_candidate.target_dep,
                    "target_resolution": target_candidate.target_resolution,
                    "target_base_i": target_candidate.target_base_i,
                }
                if target_candidate.conj_head_i is not None:
                    source_detail["conj_head_i"] = target_candidate.conj_head_i
                builder.add_edge(
                    edge_type="relation",
                    source_mention_id=source_id,
                    target_mention_id=target_candidate.target_mention_id,
                    label=_doc_token_lemma(token),
                    rule_id=RELATION_RULE_ID,
                    evidence_text=f"{token.text} -> {target_candidate.target_text}",
                    source_detail=source_detail,
                )


def _extract_doc_relation_mwe_edges(
    builder: _RawBuilder,
    *,
    doc: Doc,
    children_by_head: Mapping[int, Sequence[Token]],
) -> None:
    for match in builder.relation_mwe_matches:
        if not match.token_indices:
            continue
        initial_i = _match_offset_token_i(
            match,
            match.entry.initial_relation_token_offset,
        )
        final_adp_i = _match_offset_token_i(match, match.entry.final_adp_token_offset)
        if initial_i is None or final_adp_i is None:
            continue
        initial = doc[initial_i]
        final_adp = doc[final_adp_i]
        if final_adp.pos_ != "ADP":
            continue
        source_candidates = _relation_mwe_doc_source_candidates(
            builder,
            initial=initial,
            children_by_head=children_by_head,
        )
        if not source_candidates:
            continue
        target_candidates: list[_RelationTargetCandidate] = []
        for child in children_by_head.get(final_adp_i, ()):
            if child.dep_ != "pobj":
                continue
            target_candidates.extend(
                _doc_relation_target_candidates_from_base(
                    builder,
                    base_target=child,
                    children_by_head=children_by_head,
                    direct_resolution="direct_final_pobj",
                    conj_resolution="conj_of_final_pobj",
                ),
            )
        if not target_candidates:
            continue
        _add_relation_mwe_candidate_edges(
            builder,
            match=match,
            source_candidates=source_candidates,
            target_candidates=target_candidates,
            initial_i=initial_i,
            final_adp_i=final_adp_i,
        )


def _extract_objects_and_chunk_modifiers(
    builder: _RawBuilder,
    *,
    noun_chunks: Sequence[Mapping[str, Any]],
    token_by_i: Mapping[int, Mapping[str, Any]],
    object_lookup: Any | None,
    attribute_mwe_lookup: ResolvedAttributeMweIndex | None,
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> None:
    for chunk in noun_chunks:
        selection = _select_chunk_object_span(
            chunk,
            token_by_i=token_by_i,
            object_lookup=object_lookup,
        )
        if selection is None:
            continue
        if any(token_i in builder.relation_mwe_consumed_tokens for token_i in selection.token_indices):
            continue
        if any(token_i in builder.object_by_token for token_i in selection.token_indices):
            continue

        object_id = builder.add_mention(
            mention_type="object",
            text=selection.text,
            lemma=selection.lemma,
            rule_id=OBJECT_RULE_ID,
            char_start=selection.char_start,
            char_end=selection.char_end,
            token_start=selection.token_start,
            token_end=selection.token_end,
            source_text=_optional_text(chunk, "text"),
            source_detail=selection.source_detail,
        )
        for token_i in selection.token_indices:
            builder.object_by_token[token_i] = object_id

        chunk_tokens = list(_chunk_tokens(chunk, token_by_i))
        chunk_token_indices = {_require_int(token, "i") for token in chunk_tokens}
        excluded_modifier_tokens = set(selection.token_indices) | builder.relation_mwe_consumed_tokens
        selected_attribute_mwes = _select_record_attribute_mwes(
            chunk_tokens,
            children_by_head=children_by_head,
            excluded_token_indices=excluded_modifier_tokens,
            attribute_mwe_lookup=attribute_mwe_lookup,
        )
        attribute_mwe_by_start = {
            selected.candidate.token_start: selected
            for selected in selected_attribute_mwes
        }
        attribute_mwe_consumed_tokens = {
            token_i
            for selected in selected_attribute_mwes
            for token_i in selected.candidate.token_indices
        }
        emitted_attribute_tokens: set[int] = set()
        root_i = _require_int(chunk, "root_i")
        chunk_text = _optional_text(chunk, "text")

        def add_attribute_modifier(
            token: Mapping[str, Any],
            *,
            conj_head: Mapping[str, Any] | None = None,
        ) -> None:
            token_i = _require_int(token, "i")
            if token_i in attribute_mwe_consumed_tokens:
                return
            if token_i in emitted_attribute_tokens:
                return
            emitted_attribute_tokens.add(token_i)
            source_detail = _modifier_detail(token, root_i)
            edge_detail: JsonObject = {"root_i": root_i, "modifier_i": token_i}
            if conj_head is not None:
                source_detail.update(
                    {
                        "modifier_source": "conj_of_attribute_modifier",
                        "conj_head_i": _require_int(conj_head, "i"),
                        "conj_head_text": _token_text(conj_head),
                    }
                )
                edge_detail.update(
                    {
                        "modifier_source": "conj_of_attribute_modifier",
                        "conj_head_i": _require_int(conj_head, "i"),
                    }
                )
            attribute_id = builder.add_mention(
                mention_type="attribute",
                text=_token_text(token),
                lemma=_token_lemma(token),
                rule_id=ATTRIBUTE_RULE_ID,
                char_start=_optional_int(token, "char_start"),
                char_end=_optional_int(token, "char_end"),
                token_start=token_i,
                token_end=token_i + 1,
                source_text=chunk_text,
                source_detail=source_detail,
            )
            builder.add_edge(
                edge_type="has_attribute",
                source_mention_id=object_id,
                target_mention_id=attribute_id,
                label="has_attribute",
                rule_id=ATTRIBUTE_RULE_ID,
                evidence_text=chunk_text,
                source_detail=edge_detail,
            )

        def add_attribute_mwe(selected: Any) -> None:
            candidate = selected.candidate
            inventory_row = selected.lookup
            attribute_id = builder.add_mention(
                mention_type="attribute",
                text=candidate.surface,
                lemma=" ".join(token.lemma for token in candidate.tokens),
                rule_id=ATTRIBUTE_RULE_ID,
                char_start=candidate.tokens[0].char_start,
                char_end=candidate.tokens[-1].char_end,
                token_start=candidate.token_start,
                token_end=candidate.token_end,
                source_text=chunk_text,
                source_detail=_attribute_mwe_detail(candidate, inventory_row, root_i),
            )
            builder.add_edge(
                edge_type="has_attribute",
                source_mention_id=object_id,
                target_mention_id=attribute_id,
                label="has_attribute",
                rule_id=ATTRIBUTE_RULE_ID,
                evidence_text=chunk_text,
                source_detail={
                    "root_i": root_i,
                    "modifier_i": candidate.anchor.token_i,
                    "modifier_source": "attribute_mwe",
                    "selected_token_indices": list(candidate.token_indices),
                },
            )

        for token in chunk_tokens:
            token_i = _require_int(token, "i")
            if token_i in selection.token_indices:
                continue
            if token_i in builder.relation_mwe_consumed_tokens:
                continue
            selected_mwe = attribute_mwe_by_start.get(token_i)
            if selected_mwe is not None:
                add_attribute_mwe(selected_mwe)
                continue
            if token_i in attribute_mwe_consumed_tokens:
                continue
            if _is_quantity_modifier(token):
                quantity_id = builder.add_mention(
                    mention_type="quantity",
                    text=_token_text(token),
                    lemma=_token_lemma(token),
                    rule_id=QUANTITY_RULE_ID,
                    char_start=_optional_int(token, "char_start"),
                    char_end=_optional_int(token, "char_end"),
                    token_start=token_i,
                    token_end=token_i + 1,
                    source_text=_optional_text(chunk, "text"),
                    source_detail=_modifier_detail(token, _require_int(chunk, "root_i")),
                )
                builder.add_edge(
                    edge_type="has_quantity",
                    source_mention_id=object_id,
                    target_mention_id=quantity_id,
                    label="has_quantity",
                    rule_id=QUANTITY_RULE_ID,
                    evidence_text=_optional_text(chunk, "text"),
                    source_detail={"root_i": root_i, "modifier_i": token_i},
                )
            elif _is_attribute_modifier(token):
                add_attribute_modifier(token)
                for conj_token, conj_head in _conjunct_attribute_modifiers(
                    token,
                    children_by_head=children_by_head,
                    chunk_token_indices=chunk_token_indices,
                    excluded_token_indices=excluded_modifier_tokens,
                ):
                    add_attribute_modifier(conj_token, conj_head=conj_head)


def _extract_actions(
    builder: _RawBuilder,
    *,
    tokens: Sequence[Mapping[str, Any]],
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
    action_lookup: Any | None,
) -> None:
    for token in tokens:
        if _optional_text(token, "pos") != "VERB":
            continue
        if _require_int(token, "i") in builder.relation_mwe_consumed_tokens:
            continue
        selection = _select_action_span_from_token_record(
            token,
            children_by_head=children_by_head,
            excluded_token_indices=builder.relation_mwe_consumed_tokens,
            action_lookup=action_lookup,
        )
        action_id = builder.add_mention(
            mention_type="action",
            text=selection.text,
            lemma=selection.lemma,
            rule_id=ACTION_RULE_ID,
            char_start=selection.char_start,
            char_end=selection.char_end,
            token_start=selection.token_start,
            token_end=selection.token_end,
            source_text=selection.text,
            source_detail=selection.source_detail,
        )
        builder.action_head_tokens.add(selection.head_i)
        builder.action_head_by_id[action_id] = selection.head_i
        for token_i in selection.token_indices:
            builder.action_by_token[token_i] = action_id
            builder.consumed_action_tokens.add(token_i)
        for prep_i in selection.prep_token_indices:
            builder.consumed_action_adp_tokens.add(prep_i)


def _extract_event_roles(
    builder: _RawBuilder,
    *,
    tokens: Sequence[Mapping[str, Any]],
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> None:
    for token in tokens:
        token_i = _require_int(token, "i")
        action_id = builder.action_by_token.get(token_i)
        if action_id is None or token_i not in builder.action_head_tokens:
            continue
        passive_subject_added = False
        for child in children_by_head.get(token_i, ()):
            child_i = _require_int(child, "i")
            target_id = builder.object_by_token.get(child_i)
            if target_id is None:
                continue
            dep = _optional_text(child, "dep")
            if dep == "nsubj":
                builder.add_edge(
                    edge_type="event_role",
                    source_mention_id=action_id,
                    target_mention_id=target_id,
                    label="agent",
                    rule_id=AGENT_RULE_ID,
                    evidence_text=f"{_token_text(child)} -> {_token_text(token)}",
                    source_detail={"dep": dep, "action_i": token_i, "target_i": child_i},
                )
            elif dep in PASSIVE_SUBJECT_DEPS:
                builder.add_edge(
                    edge_type="event_role",
                    source_mention_id=action_id,
                    target_mention_id=target_id,
                    label="patient",
                    rule_id=PASSIVE_PATIENT_RULE_ID,
                    evidence_text=f"{_token_text(child)} -> {_token_text(token)}",
                    source_detail={
                        "dep": dep,
                        "action_i": token_i,
                        "target_i": child_i,
                        "raw_role": "theme",
                        "voice_normalization": "passive_to_active",
                        "role_source": "passive_subject",
                    },
                )
                passive_subject_added = True
            elif dep in PATIENT_DEPS:
                builder.add_edge(
                    edge_type="event_role",
                    source_mention_id=action_id,
                    target_mention_id=target_id,
                    label="patient",
                    rule_id=PATIENT_RULE_ID,
                    evidence_text=f"{_token_text(token)} -> {_token_text(child)}",
                    source_detail={"dep": dep, "action_i": token_i, "target_i": child_i},
                )
        if passive_subject_added:
            _extract_passive_by_agent_edges(
                builder,
                action_id=action_id,
                action_i=token_i,
                children_by_head=children_by_head,
            )
    for prep_i in sorted(builder.consumed_action_adp_tokens):
        action_id = builder.action_by_token.get(prep_i)
        if action_id is None:
            continue
        action_head_i = builder.action_head_by_id.get(action_id, prep_i)
        prep = next((token for token in tokens if _require_int(token, "i") == prep_i), None)
        if prep is None:
            continue
        for child in children_by_head.get(prep_i, ()):
            if _optional_text(child, "dep") != "pobj":
                continue
            child_i = _require_int(child, "i")
            target_id = builder.object_by_token.get(child_i)
            if target_id is None:
                continue
            builder.add_edge(
                edge_type="event_role",
                source_mention_id=action_id,
                target_mention_id=target_id,
                label="patient",
                rule_id=PATIENT_RULE_ID,
                evidence_text=f"{_token_text(prep)} -> {_token_text(child)}",
                source_detail={
                    "dep": "pobj",
                    "action_i": action_head_i,
                    "prep_i": prep_i,
                    "target_i": child_i,
                    "role_source": "selected_phrasal_action_prep_pobj",
                },
            )


def _extract_passive_by_agent_edges(
    builder: _RawBuilder,
    *,
    action_id: str,
    action_i: int,
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> None:
    for child in children_by_head.get(action_i, ()):
        dep = _optional_text(child, "dep")
        if dep not in PASSIVE_BY_DEPS:
            continue
        if _token_lemma(child).lower() != "by" and _token_text(child).lower() != "by":
            continue
        by_i = _require_int(child, "i")
        for by_child in children_by_head.get(by_i, ()):
            target_dep = _optional_text(by_child, "dep")
            if target_dep not in PASSIVE_BY_OBJECT_DEPS:
                continue
            target_i = _require_int(by_child, "i")
            target_id = builder.object_by_token.get(target_i)
            if target_id is None:
                continue
            builder.add_edge(
                edge_type="event_role",
                source_mention_id=action_id,
                target_mention_id=target_id,
                label="agent",
                rule_id=PASSIVE_AGENT_RULE_ID,
                evidence_text=f"{_token_text(child)} -> {_token_text(by_child)}",
                source_detail={
                    "dep": dep,
                    "action_i": action_i,
                    "by_i": by_i,
                    "target_i": target_i,
                    "target_dep": target_dep,
                    "raw_role": "by_agent_or_causer",
                    "voice_normalization": "passive_to_active",
                    "role_source": "passive_by_phrase",
                },
            )


def _extract_doc_passive_by_agent_edges(
    builder: _RawBuilder,
    *,
    action_id: str,
    action_i: int,
    children_by_head: Mapping[int, Sequence[Token]],
) -> None:
    for child in children_by_head.get(action_i, ()):
        if child.dep_ not in PASSIVE_BY_DEPS:
            continue
        if child.lemma_.lower() != "by" and child.text.lower() != "by":
            continue
        for by_child in children_by_head.get(child.i, ()):
            if by_child.dep_ not in PASSIVE_BY_OBJECT_DEPS:
                continue
            target_id = builder.object_by_token.get(by_child.i)
            if target_id is None:
                continue
            builder.add_edge(
                edge_type="event_role",
                source_mention_id=action_id,
                target_mention_id=target_id,
                label="agent",
                rule_id=PASSIVE_AGENT_RULE_ID,
                evidence_text=f"{child.text} -> {by_child.text}",
                source_detail={
                    "dep": child.dep_,
                    "action_i": action_i,
                    "by_i": child.i,
                    "target_i": by_child.i,
                    "target_dep": by_child.dep_,
                    "raw_role": "by_agent_or_causer",
                    "voice_normalization": "passive_to_active",
                    "role_source": "passive_by_phrase",
                },
            )


def _inherit_acl_action_head_agents(
    builder: _RawBuilder,
    *,
    tokens: Sequence[Mapping[str, Any]],
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> None:
    agent_edges_by_action = _agent_edges_by_action(builder)
    token_by_i = {_require_int(token, "i"): token for token in tokens}
    for token in tokens:
        action_i = _require_int(token, "i")
        if action_i not in builder.action_head_tokens:
            continue
        if _optional_text(token, "dep") != "acl":
            continue
        if _optional_text(token, "tag") not in ACL_AGENT_ALLOWED_TAGS:
            continue
        if _is_passive_like_action_token(token, children_by_head=children_by_head):
            continue
        action_id = builder.action_by_token.get(action_i)
        if action_id is None:
            continue
        if agent_edges_by_action.get(action_id):
            continue
        head_i = _require_int(token, "head_i")
        target_id = builder.object_by_token.get(head_i)
        if target_id is None:
            continue
        head_token = token_by_i.get(head_i)
        head_text = _token_text(head_token) if head_token is not None else str(head_i)
        builder.add_edge(
            edge_type="event_role",
            source_mention_id=action_id,
            target_mention_id=target_id,
            label="agent",
            rule_id=ACL_AGENT_RULE_ID,
            evidence_text=f"{head_text} -> {_token_text(token)}",
            source_detail={
                "dep": "acl",
                "action_i": action_i,
                "target_i": head_i,
                "role_source": "acl_head_object_agent",
                "acl_head_i": head_i,
            },
        )


def _inherit_doc_acl_action_head_agents(
    builder: _RawBuilder,
    *,
    doc: Doc,
    children_by_head: Mapping[int, Sequence[Token]],
) -> None:
    agent_edges_by_action = _agent_edges_by_action(builder)
    for token in doc:
        if token.i not in builder.action_head_tokens:
            continue
        if token.dep_ != "acl":
            continue
        if token.tag_ not in ACL_AGENT_ALLOWED_TAGS:
            continue
        if _is_passive_like_doc_action_token(token, children_by_head=children_by_head):
            continue
        action_id = builder.action_by_token.get(token.i)
        if action_id is None:
            continue
        if agent_edges_by_action.get(action_id):
            continue
        target_id = builder.object_by_token.get(token.head.i)
        if target_id is None:
            continue
        builder.add_edge(
            edge_type="event_role",
            source_mention_id=action_id,
            target_mention_id=target_id,
            label="agent",
            rule_id=ACL_AGENT_RULE_ID,
            evidence_text=f"{token.head.text} -> {token.text}",
            source_detail={
                "dep": "acl",
                "action_i": token.i,
                "target_i": token.head.i,
                "role_source": "acl_head_object_agent",
                "acl_head_i": token.head.i,
            },
        )


def _inherit_conjunct_action_agents(
    builder: _RawBuilder,
    *,
    tokens: Sequence[Mapping[str, Any]],
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> None:
    token_by_i = {_require_int(token, "i"): token for token in tokens}
    while True:
        agent_edges_by_action = _agent_edges_by_action(builder)
        changed = False
        for token in tokens:
            target_action_i = _require_int(token, "i")
            if target_action_i not in builder.action_head_tokens:
                continue
            if _optional_text(token, "dep") != "conj":
                continue
            if _is_passive_like_action_token(token, children_by_head=children_by_head):
                continue
            target_action_id = builder.action_by_token.get(target_action_i)
            if target_action_id is None:
                continue
            if agent_edges_by_action.get(target_action_id):
                continue
            source_action_i = _require_int(token, "head_i")
            if source_action_i not in builder.action_head_tokens:
                continue
            source_action_id = builder.action_by_token.get(source_action_i)
            if source_action_id is None or source_action_id == target_action_id:
                continue
            source_agent_edge = _single_agent_edge(
                agent_edges_by_action.get(source_action_id, ()),
            )
            if source_agent_edge is None:
                continue
            source_token = token_by_i.get(source_action_i)
            target_i = source_agent_edge.source_detail.get("target_i")
            source_text = _token_text(source_token) if source_token is not None else str(source_action_i)
            builder.add_edge(
                edge_type="event_role",
                source_mention_id=target_action_id,
                target_mention_id=source_agent_edge.target_mention_id,
                label="agent",
                rule_id=AGENT_INHERIT_RULE_ID,
                evidence_text=f"{source_text} -> {_token_text(token)}",
                source_detail={
                    "dep": "conj",
                    "action_i": target_action_i,
                    "target_i": target_i if isinstance(target_i, int) else None,
                    "role_source": "conj_agent_inheritance",
                    "source_action_i": source_action_i,
                    "target_action_i": target_action_i,
                    "conj_head_i": source_action_i,
                    "source_action_mention_id": source_action_id,
                    "source_agent_edge_id": source_agent_edge.edge_id,
                    "source_agent_rule_id": source_agent_edge.rule_id,
                },
            )
            changed = True
        if not changed:
            return


def _inherit_doc_conjunct_action_agents(
    builder: _RawBuilder,
    *,
    doc: Doc,
    children_by_head: Mapping[int, Sequence[Token]],
) -> None:
    while True:
        agent_edges_by_action = _agent_edges_by_action(builder)
        changed = False
        for token in doc:
            if token.i not in builder.action_head_tokens:
                continue
            if token.dep_ != "conj":
                continue
            if _is_passive_like_doc_action_token(token, children_by_head=children_by_head):
                continue
            target_action_id = builder.action_by_token.get(token.i)
            if target_action_id is None:
                continue
            if agent_edges_by_action.get(target_action_id):
                continue
            source_action_i = token.head.i
            if source_action_i not in builder.action_head_tokens:
                continue
            source_action_id = builder.action_by_token.get(source_action_i)
            if source_action_id is None or source_action_id == target_action_id:
                continue
            source_agent_edge = _single_agent_edge(
                agent_edges_by_action.get(source_action_id, ()),
            )
            if source_agent_edge is None:
                continue
            target_i = source_agent_edge.source_detail.get("target_i")
            builder.add_edge(
                edge_type="event_role",
                source_mention_id=target_action_id,
                target_mention_id=source_agent_edge.target_mention_id,
                label="agent",
                rule_id=AGENT_INHERIT_RULE_ID,
                evidence_text=f"{token.head.text} -> {token.text}",
                source_detail={
                    "dep": "conj",
                    "action_i": token.i,
                    "target_i": target_i if isinstance(target_i, int) else None,
                    "role_source": "conj_agent_inheritance",
                    "source_action_i": source_action_i,
                    "target_action_i": token.i,
                    "conj_head_i": source_action_i,
                    "source_action_mention_id": source_action_id,
                    "source_agent_edge_id": source_agent_edge.edge_id,
                    "source_agent_rule_id": source_agent_edge.rule_id,
                },
            )
            changed = True
        if not changed:
            return


def _agent_edges_by_action(builder: _RawBuilder) -> dict[str, list[RawEdge]]:
    edges_by_action: dict[str, list[RawEdge]] = defaultdict(list)
    for edge in builder.raw_edges:
        if edge.edge_type != "event_role":
            continue
        if edge.label != "agent":
            continue
        edges_by_action[edge.source_mention_id].append(edge)
    return edges_by_action


def _is_passive_like_action_token(
    token: Mapping[str, Any],
    *,
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> bool:
    token_i = _require_int(token, "i")
    return any(
        _optional_text(child, "dep") in PASSIVE_LIKE_ACTION_CHILD_DEPS
        for child in children_by_head.get(token_i, ())
    )


def _is_passive_like_doc_action_token(
    token: Token,
    *,
    children_by_head: Mapping[int, Sequence[Token]],
) -> bool:
    return any(
        child.dep_ in PASSIVE_LIKE_ACTION_CHILD_DEPS
        for child in children_by_head.get(token.i, ())
    )


def _single_agent_edge(edges: Sequence[RawEdge]) -> RawEdge | None:
    edge_by_target: dict[str, RawEdge] = {}
    for edge in edges:
        edge_by_target.setdefault(edge.target_mention_id, edge)
    if len(edge_by_target) != 1:
        return None
    return next(iter(edge_by_target.values()))


def _extract_relations(
    builder: _RawBuilder,
    *,
    tokens: Sequence[Mapping[str, Any]],
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> None:
    for token in tokens:
        if _optional_text(token, "pos") != "ADP":
            continue
        prep_i = _require_int(token, "i")
        if prep_i in builder.consumed_action_adp_tokens:
            continue
        if prep_i in builder.relation_mwe_consumed_tokens:
            continue
        source_id = builder.object_by_token.get(_require_int(token, "head_i"))
        if source_id is None:
            continue
        for child in children_by_head.get(prep_i, ()):
            if _optional_text(child, "dep") != "pobj":
                continue
            for target_candidate in _relation_target_candidates_from_base(
                builder,
                base_target=child,
                children_by_head=children_by_head,
                direct_resolution="direct_pobj",
                conj_resolution="conj_of_pobj",
            ):
                source_detail: JsonObject = {
                    "prep_i": prep_i,
                    "source_i": _require_int(token, "head_i"),
                    "target_i": target_candidate.target_i,
                    "target_dep": target_candidate.target_dep,
                    "target_resolution": target_candidate.target_resolution,
                    "target_base_i": target_candidate.target_base_i,
                }
                if target_candidate.conj_head_i is not None:
                    source_detail["conj_head_i"] = target_candidate.conj_head_i
                builder.add_edge(
                    edge_type="relation",
                    source_mention_id=source_id,
                    target_mention_id=target_candidate.target_mention_id,
                    label=_token_lemma(token),
                    rule_id=RELATION_RULE_ID,
                    evidence_text=f"{_token_text(token)} -> {target_candidate.target_text}",
                    source_detail=source_detail,
                )


def _extract_relation_mwe_edges(
    builder: _RawBuilder,
    *,
    tokens: Sequence[Mapping[str, Any]],
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
    token_by_i: Mapping[int, Mapping[str, Any]],
) -> None:
    for match in builder.relation_mwe_matches:
        if not match.token_indices:
            continue
        initial_i = _match_offset_token_i(
            match,
            match.entry.initial_relation_token_offset,
        )
        final_adp_i = _match_offset_token_i(match, match.entry.final_adp_token_offset)
        if initial_i is None or final_adp_i is None:
            continue
        initial = token_by_i.get(initial_i)
        final_adp = token_by_i.get(final_adp_i)
        if initial is None or final_adp is None:
            continue
        if _optional_text(final_adp, "pos") != "ADP":
            continue
        source_candidates = _relation_mwe_token_source_candidates(
            builder,
            initial=initial,
            children_by_head=children_by_head,
            token_by_i=token_by_i,
        )
        target_candidates: list[_RelationTargetCandidate] = []
        for child in children_by_head.get(final_adp_i, ()):
            if _optional_text(child, "dep") != "pobj":
                continue
            target_candidates.extend(
                _relation_target_candidates_from_base(
                    builder,
                    base_target=child,
                    children_by_head=children_by_head,
                    direct_resolution="direct_final_pobj",
                    conj_resolution="conj_of_final_pobj",
                ),
            )
        _add_relation_mwe_candidate_edges(
            builder,
            match=match,
            source_candidates=source_candidates,
            target_candidates=target_candidates,
            initial_i=initial_i,
            final_adp_i=final_adp_i,
        )


def _relation_target_candidates_from_base(
    builder: _RawBuilder,
    *,
    base_target: Mapping[str, Any],
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
    direct_resolution: str,
    conj_resolution: str,
) -> tuple[_RelationTargetCandidate, ...]:
    base_i = _require_int(base_target, "i")
    base_id = builder.object_by_token.get(base_i)
    if base_id is None:
        return ()
    candidates = [
        _RelationTargetCandidate(
            target_i=base_i,
            target_mention_id=base_id,
            target_text=_token_text(base_target),
            target_dep=_optional_text(base_target, "dep") or "",
            target_resolution=direct_resolution,
            target_base_i=base_i,
        )
    ]
    seen_token_indices = {base_i}
    seen_mentions = {base_id}
    pending = [base_target]
    while pending:
        head = pending.pop(0)
        head_i = _require_int(head, "i")
        for child in children_by_head.get(head_i, ()):
            if _optional_text(child, "dep") != "conj":
                continue
            child_i = _require_int(child, "i")
            if child_i in seen_token_indices:
                continue
            target_id = builder.object_by_token.get(child_i)
            if target_id is None:
                continue
            seen_token_indices.add(child_i)
            if target_id in seen_mentions:
                continue
            seen_mentions.add(target_id)
            candidates.append(
                _RelationTargetCandidate(
                    target_i=child_i,
                    target_mention_id=target_id,
                    target_text=_token_text(child),
                    target_dep=_optional_text(child, "dep") or "",
                    target_resolution=conj_resolution,
                    target_base_i=base_i,
                    conj_head_i=head_i,
                )
            )
            pending.append(child)
    return tuple(candidates)


def _doc_relation_target_candidates_from_base(
    builder: _RawBuilder,
    *,
    base_target: Token,
    children_by_head: Mapping[int, Sequence[Token]],
    direct_resolution: str,
    conj_resolution: str,
) -> tuple[_RelationTargetCandidate, ...]:
    base_i = base_target.i
    base_id = builder.object_by_token.get(base_i)
    if base_id is None:
        return ()
    candidates = [
        _RelationTargetCandidate(
            target_i=base_i,
            target_mention_id=base_id,
            target_text=base_target.text,
            target_dep=base_target.dep_,
            target_resolution=direct_resolution,
            target_base_i=base_i,
        )
    ]
    seen_token_indices = {base_i}
    seen_mentions = {base_id}
    pending = [base_target]
    while pending:
        head = pending.pop(0)
        for child in children_by_head.get(head.i, ()):
            if child.dep_ != "conj":
                continue
            if child.i in seen_token_indices:
                continue
            target_id = builder.object_by_token.get(child.i)
            if target_id is None:
                continue
            seen_token_indices.add(child.i)
            if target_id in seen_mentions:
                continue
            seen_mentions.add(target_id)
            candidates.append(
                _RelationTargetCandidate(
                    target_i=child.i,
                    target_mention_id=target_id,
                    target_text=child.text,
                    target_dep=child.dep_,
                    target_resolution=conj_resolution,
                    target_base_i=base_i,
                    conj_head_i=head.i,
                )
            )
            pending.append(child)
    return tuple(candidates)


def _relation_target_base_count(
    target_candidates: Sequence[_RelationTargetCandidate],
) -> int:
    return len({candidate.target_base_i for candidate in target_candidates if candidate.target_base_i >= 0})


def _relation_mwe_edge_detail(
    match: _PrepositionMweMatch,
    *,
    initial_i: int,
    final_adp_i: int,
    source_i: int,
    source_resolution: str,
    source_dep: str,
    target_i: int,
    target_dep: str,
    target_resolution: str,
    target_base_i: int,
    conj_head_i: int | None,
    candidate_source_count: int,
    candidate_sources: Sequence[Mapping[str, Any]],
    candidate_target_count: int,
    candidate_target_base_count: int,
    candidate_targets: Sequence[Mapping[str, Any]],
    ambiguity_scope: str,
) -> JsonObject:
    return {
        "relation_source": "preposition_mwe",
        "raw_span_surface": match.raw_span_surface,
        "canonical_relation": match.entry.canonical_relation,
        "matched_token_indices": list(match.token_indices),
        "relation_components": list(match.entry.relation_components),
        "initial_relation_token_i": initial_i,
        "final_adp_i": final_adp_i,
        "source_i": source_i,
        "source_resolution": source_resolution,
        "source_dep": source_dep,
        "candidate_source_count": candidate_source_count,
        "candidate_sources": [dict(candidate) for candidate in candidate_sources],
        "source_endpoint_status": _relation_endpoint_status(
            candidate_source_count,
            endpoint_name="source",
        ),
        "target_i": target_i,
        "target_dep": target_dep,
        "target_resolution": target_resolution,
        "target_base_i": target_base_i,
        "conj_head_i": conj_head_i,
        "candidate_target_count": candidate_target_count,
        "candidate_target_base_count": candidate_target_base_count,
        "candidate_targets": [dict(candidate) for candidate in candidate_targets],
        "target_endpoint_status": _relation_endpoint_status(
            candidate_target_base_count,
            endpoint_name="target",
        ),
        "ambiguity_scope": ambiguity_scope,
        "lexicon_surface": match.entry.surface,
        "lexicon_source": match.entry.source,
    }


def _add_relation_mwe_candidate_edges(
    builder: _RawBuilder,
    *,
    match: _PrepositionMweMatch,
    source_candidates: Sequence[_RelationSourceCandidate],
    target_candidates: Sequence[_RelationTargetCandidate],
    initial_i: int,
    final_adp_i: int,
) -> None:
    candidate_sources = [
        {
            "source_i": candidate.source_i,
            "source_mention_id": candidate.source_mention_id,
            "source_dep": candidate.source_dep,
            "source_resolution": candidate.source_resolution,
        }
        for candidate in source_candidates
    ]
    candidate_targets = [
        {
            "target_i": candidate.target_i,
            "target_mention_id": candidate.target_mention_id,
            "target_dep": candidate.target_dep,
            "target_resolution": candidate.target_resolution,
            "target_base_i": candidate.target_base_i,
            "conj_head_i": candidate.conj_head_i,
        }
        for candidate in target_candidates
    ]
    candidate_target_base_count = _relation_target_base_count(target_candidates)
    source_edge_candidates = (
        tuple(source_candidates)
        if source_candidates
        else (
            _RelationSourceCandidate(
                source_i=-1,
                source_mention_id=MISSING_SOURCE_MENTION_ID,
                source_dep="",
                source_resolution="missing_source",
            ),
        )
    )
    target_edge_candidates = (
        tuple(target_candidates)
        if target_candidates
        else (
            _RelationTargetCandidate(
                target_i=-1,
                target_mention_id=MISSING_TARGET_MENTION_ID,
                target_text="missing_target",
                target_dep="",
                target_resolution="missing_target",
                target_base_i=-1,
            ),
        )
    )
    edge_type = (
        "relation"
        if len(source_candidates) == 1 and candidate_target_base_count == 1
        else "ambiguous_relation_candidate"
    )
    ambiguity_scope = _relation_mwe_ambiguity_scope(
        source_count=len(source_candidates),
        target_count=candidate_target_base_count,
    )
    for source_candidate in source_edge_candidates:
        for target_candidate in target_edge_candidates:
            builder.add_edge(
                edge_type=edge_type,
                source_mention_id=source_candidate.source_mention_id,
                target_mention_id=target_candidate.target_mention_id,
                label=match.entry.canonical_relation,
                rule_id=RELATION_MWE_RULE_ID,
                evidence_text=f"{match.raw_span_surface} -> {target_candidate.target_text}",
                source_detail=_relation_mwe_edge_detail(
                    match,
                    initial_i=initial_i,
                    final_adp_i=final_adp_i,
                    source_i=source_candidate.source_i,
                    source_resolution=source_candidate.source_resolution,
                    source_dep=source_candidate.source_dep,
                    target_i=target_candidate.target_i,
                    target_dep=target_candidate.target_dep,
                    target_resolution=target_candidate.target_resolution,
                    target_base_i=target_candidate.target_base_i,
                    conj_head_i=target_candidate.conj_head_i,
                    candidate_source_count=len(source_candidates),
                    candidate_sources=candidate_sources,
                    candidate_target_count=len(target_candidates),
                    candidate_target_base_count=candidate_target_base_count,
                    candidate_targets=candidate_targets,
                    ambiguity_scope=ambiguity_scope,
                ),
            )


def _relation_mwe_ambiguity_scope(*, source_count: int, target_count: int) -> str:
    if source_count == 0 and target_count == 0:
        return "source_and_target_missing"
    if source_count == 0:
        return "source_missing"
    if target_count == 0:
        return "target_missing"
    source_ambiguous = source_count > 1
    target_ambiguous = target_count > 1
    if source_ambiguous and target_ambiguous:
        return "source_and_target"
    if source_ambiguous:
        return "source"
    if target_ambiguous:
        return "target"
    return "none"


def _relation_endpoint_status(candidate_count: int, *, endpoint_name: str) -> str:
    if candidate_count == 0:
        return f"{endpoint_name}_missing"
    if candidate_count == 1:
        return f"{endpoint_name}_resolved"
    return f"{endpoint_name}_ambiguous"


def _relation_mwe_token_source_candidates(
    builder: _RawBuilder,
    *,
    initial: Mapping[str, Any],
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
    token_by_i: Mapping[int, Mapping[str, Any]],
) -> tuple[_RelationSourceCandidate, ...]:
    head_i = _require_int(initial, "head_i")
    direct_source_id = builder.object_by_token.get(head_i)
    if direct_source_id is not None:
        return (
            _RelationSourceCandidate(
                source_i=head_i,
                source_mention_id=direct_source_id,
                source_dep="initial_head",
                source_resolution="direct_initial_head_object",
            ),
        )
    head = token_by_i.get(head_i)
    if head is None or _optional_text(head, "pos") not in RELATION_MWE_SOURCE_HEAD_POS:
        return ()
    return _dedupe_relation_source_candidates(
        _RelationSourceCandidate(
            source_i=_require_int(child, "i"),
            source_mention_id=source_id,
            source_dep=_optional_text(child, "dep") or "",
            source_resolution="head_direct_object_child",
        )
        for child in children_by_head.get(head_i, ())
        for source_id in (builder.object_by_token.get(_require_int(child, "i")),)
        if source_id is not None
    )


def _relation_mwe_doc_source_candidates(
    builder: _RawBuilder,
    *,
    initial: Token,
    children_by_head: Mapping[int, Sequence[Token]],
) -> tuple[_RelationSourceCandidate, ...]:
    direct_source_id = builder.object_by_token.get(initial.head.i)
    if direct_source_id is not None:
        return (
            _RelationSourceCandidate(
                source_i=initial.head.i,
                source_mention_id=direct_source_id,
                source_dep="initial_head",
                source_resolution="direct_initial_head_object",
            ),
        )
    if initial.head.pos_ not in RELATION_MWE_SOURCE_HEAD_POS:
        return ()
    return _dedupe_relation_source_candidates(
        _RelationSourceCandidate(
            source_i=child.i,
            source_mention_id=source_id,
            source_dep=child.dep_,
            source_resolution="head_direct_object_child",
        )
        for child in children_by_head.get(initial.head.i, ())
        for source_id in (builder.object_by_token.get(child.i),)
        if source_id is not None
    )


def _dedupe_relation_source_candidates(
    candidates: Iterable[_RelationSourceCandidate],
) -> tuple[_RelationSourceCandidate, ...]:
    selected: list[_RelationSourceCandidate] = []
    seen_mentions: set[str] = set()
    for candidate in candidates:
        if candidate.source_mention_id in seen_mentions:
            continue
        seen_mentions.add(candidate.source_mention_id)
        selected.append(candidate)
    return tuple(selected)


def _match_offset_token_i(match: _PrepositionMweMatch, offset: int) -> int | None:
    if offset < 0 or offset >= len(match.token_indices):
        return None
    return match.token_indices[offset]


def _select_action_span_from_token_record(
    token: Mapping[str, Any],
    *,
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
    excluded_token_indices: set[int],
    action_lookup: Any | None,
) -> _ActionSpanSelection:
    candidates = _action_candidates_from_token_record(
        token,
        children_by_head=children_by_head,
        excluded_token_indices=excluded_token_indices,
    )
    candidate, lookup = _select_action_candidate(candidates, action_lookup)
    return _action_selection_from_candidate(
        candidate,
        lookup=lookup,
        head_i=_require_int(token, "i"),
        token_detail_fn=_token_detail,
        token_i_fn=lambda item: _require_int(item, "i"),
        token_char_start_fn=lambda item: _optional_int(item, "char_start"),
        token_char_end_fn=lambda item: _optional_int(item, "char_end"),
        fallback_lemma=_token_lemma(token),
    )


def _select_doc_action_span(
    token: Token,
    *,
    children_by_head: Mapping[int, Sequence[Token]],
    excluded_token_indices: set[int],
    action_lookup: Any | None,
) -> _ActionSpanSelection:
    candidates = _action_candidates_from_doc_token(
        token,
        children_by_head=children_by_head,
        excluded_token_indices=excluded_token_indices,
    )
    candidate, lookup = _select_action_candidate(candidates, action_lookup)
    return _action_selection_from_candidate(
        candidate,
        lookup=lookup,
        head_i=token.i,
        token_detail_fn=_doc_token_detail,
        token_i_fn=lambda item: item.i,
        token_char_start_fn=lambda item: item.idx,
        token_char_end_fn=lambda item: item.idx + len(item.text),
        fallback_lemma=_doc_token_lemma(token),
    )


def _action_candidates_from_token_record(
    token: Mapping[str, Any],
    *,
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
    excluded_token_indices: set[int],
) -> list[_ActionSpanCandidate]:
    head_i = _require_int(token, "i")
    children = tuple(
        child
        for child in children_by_head.get(head_i, ())
        if _require_int(child, "i") not in excluded_token_indices
    )
    particles = tuple(child for child in children if _is_action_particle_token_record(child))
    direct_preps = tuple(
        child
        for child in children
        if _is_action_prep_token_record(child) and _require_int(child, "i") > head_i
    )
    particle_child_preps = tuple(
        (particle, prep)
        for particle in particles
        for prep in children_by_head.get(_require_int(particle, "i"), ())
        if _require_int(prep, "i") not in excluded_token_indices
        if _is_action_prep_token_record(prep) and _require_int(prep, "i") > head_i
    )
    return _build_action_candidates(
        head=token,
        particles=particles,
        direct_preps=direct_preps,
        particle_child_preps=particle_child_preps,
        token_i_fn=lambda item: _require_int(item, "i"),
        token_text_fn=_token_text,
        is_prep_fn=_is_action_prep_token_record,
    )


def _action_candidates_from_doc_token(
    token: Token,
    *,
    children_by_head: Mapping[int, Sequence[Token]],
    excluded_token_indices: set[int],
) -> list[_ActionSpanCandidate]:
    children = tuple(
        child for child in children_by_head.get(token.i, ()) if child.i not in excluded_token_indices
    )
    particles = tuple(child for child in children if _is_action_particle_doc_token(child))
    direct_preps = tuple(
        child for child in children if _is_action_prep_doc_token(child) and child.i > token.i
    )
    particle_child_preps = tuple(
        (particle, prep)
        for particle in particles
        for prep in children_by_head.get(particle.i, ())
        if prep.i not in excluded_token_indices
        if _is_action_prep_doc_token(prep) and prep.i > token.i
    )
    return _build_action_candidates(
        head=token,
        particles=particles,
        direct_preps=direct_preps,
        particle_child_preps=particle_child_preps,
        token_i_fn=lambda item: item.i,
        token_text_fn=lambda item: item.text,
        is_prep_fn=_is_action_prep_doc_token,
    )


def _build_action_candidates(
    *,
    head: Any,
    particles: Sequence[Any],
    direct_preps: Sequence[Any],
    particle_child_preps: Sequence[tuple[Any, Any]],
    token_i_fn: Any,
    token_text_fn: Any,
    is_prep_fn: Any,
) -> list[_ActionSpanCandidate]:
    candidates: list[_ActionSpanCandidate] = []
    seen: set[tuple[str, tuple[int, ...]]] = set()

    def add(candidate_type: str, items: Sequence[Any]) -> None:
        token_indices = tuple(token_i_fn(item) for item in items)
        key = (candidate_type, token_indices)
        if key in seen:
            return
        seen.add(key)
        prep_token_indices = tuple(
            token_i_fn(item) for item in items if item is not head and is_prep_fn(item)
        )
        text = _normalize_display_surface(" ".join(token_text_fn(item) for item in items))
        candidates.append(
            _ActionSpanCandidate(
                tokens=tuple(items),
                token_indices=token_indices,
                prep_token_indices=prep_token_indices,
                text=text,
                candidate_type=candidate_type,
            )
        )

    add("verb", (head,))
    for particle in particles:
        add("verb_prt", (head, particle))
    for prep in direct_preps:
        add("verb_prep", (head, prep))
    for particle in particles:
        for prep in direct_preps:
            add("verb_prt_prep", (head, particle, prep))
    for particle, prep in particle_child_preps:
        add("verb_prt_prep", (head, particle, prep))
    return candidates


def _select_action_candidate(
    candidates: Sequence[_ActionSpanCandidate],
    action_lookup: Any | None,
) -> tuple[_ActionSpanCandidate, _ActionLookupResult | None]:
    if not candidates:
        raise ValueError("action candidate list must not be empty")
    valid: list[tuple[_ActionSpanCandidate, _ActionLookupResult]] = []
    for candidate in candidates:
        lookup = _lookup_action_candidate(candidate, action_lookup)
        if lookup is not None and lookup.synsets:
            valid.append((candidate, lookup))
    if not valid:
        return candidates[0], None
    candidate, lookup = min(
        valid,
        key=lambda item: (
            -len(item[0].token_indices),
            _action_candidate_priority(item[0].candidate_type),
            item[0].token_indices,
        ),
    )
    _raise_if_ambiguous_action_lookup(candidate.text, lookup)
    return candidate, lookup


def _action_candidate_priority(candidate_type: str) -> int:
    return {
        "verb_prt_prep": 0,
        "verb_prt": 1,
        "verb_prep": 2,
        "verb": 3,
    }.get(candidate_type, 99)


def _lookup_action_candidate(
    candidate: _ActionSpanCandidate,
    action_lookup: Any | None,
) -> _ActionLookupResult | None:
    if action_lookup is None:
        return None
    surfaces = (_normalize_query(candidate.text),)
    if callable(action_lookup):
        for surface in surfaces:
            lookup = action_lookup(surface)
            if lookup is not None:
                return lookup
        return None
    for surface in surfaces:
        lookup = _lookup_oewn_verb_synsets(surface, action_lookup["oewn"], action_lookup["morphy"])
        if lookup.synsets:
            return lookup
    return None


def _action_selection_from_candidate(
    candidate: _ActionSpanCandidate,
    *,
    lookup: _ActionLookupResult | None,
    head_i: int,
    token_detail_fn: Any,
    token_i_fn: Any,
    token_char_start_fn: Any,
    token_char_end_fn: Any,
    fallback_lemma: str,
) -> _ActionSpanSelection:
    sorted_tokens = sorted(candidate.tokens, key=token_i_fn)
    token_indices = tuple(token_i_fn(token) for token in candidate.tokens)
    token_start = min(token_indices)
    token_end = max(token_indices) + 1
    char_start = token_char_start_fn(sorted_tokens[0])
    char_end = token_char_end_fn(sorted_tokens[-1])
    lemma = lookup.query if lookup is not None and lookup.query else fallback_lemma
    detail = _action_span_detail(
        candidate=candidate,
        lookup=lookup,
        head_i=head_i,
        token_detail=[token_detail_fn(token) for token in candidate.tokens],
    )
    return _ActionSpanSelection(
        head_i=head_i,
        token_indices=token_indices,
        prep_token_indices=candidate.prep_token_indices,
        text=candidate.text,
        lemma=lemma,
        char_start=char_start,
        char_end=char_end,
        token_start=token_start,
        token_end=token_end,
        source_detail=detail,
    )


def _action_span_detail(
    *,
    candidate: _ActionSpanCandidate,
    lookup: _ActionLookupResult | None,
    head_i: int,
    token_detail: Sequence[JsonObject],
) -> JsonObject:
    detail: JsonObject = {
        "raw_surface": candidate.text,
        "head_i": head_i,
        "selected_token_indices": list(candidate.token_indices),
        "prep_token_indices": list(candidate.prep_token_indices),
        "candidate_type": candidate.candidate_type,
        "token_detail": list(token_detail),
    }
    if lookup is None:
        detail.update(
            {
                "lookup_case": "raw_fallback",
                "lookup_query": _normalize_query(candidate.text),
                "has_oewn_verb_synset": False,
                "oewn_synset_count": 0,
                "synset_selection_tag": "not_applicable_no_oewn_verb_candidate",
                "decision_status": "excluded",
                "decision_reason": "no_oewn_verb_synset",
            }
        )
        return detail

    selected = lookup.selected_synset
    detail.update(
        {
            "lookup_case": lookup.lookup_case,
            "lookup_query": lookup.query,
            "has_oewn_verb_synset": bool(lookup.synsets),
            "oewn_synset_count": len(lookup.synsets),
            "all_oewn_synsets": [synset.id for synset in lookup.synsets],
            "all_oewn_lexfiles": [synset.lexfile() for synset in lookup.synsets],
            "synset_selection_tag": lookup.synset_selection_tag,
            "decision_status": lookup.decision_status,
            "decision_reason": lookup.decision_reason,
        }
    )
    if lookup.wn30_lemma_counts:
        detail["wn30_lemma_counts"] = lookup.wn30_lemma_counts
    if selected is not None:
        detail["selected_oewn_synset"] = selected.id
        detail["selected_oewn_lexfile"] = selected.lexfile()
        detail["synset_lemmas"] = [lemma for lemma in selected.lemmas()]
    return detail


def _is_action_particle_token_record(token: Mapping[str, Any]) -> bool:
    return _optional_text(token, "dep") == "prt" or _optional_text(token, "tag") == "RP"


def _is_action_prep_token_record(token: Mapping[str, Any]) -> bool:
    return _optional_text(token, "dep") == "prep" or _optional_text(token, "pos") == "ADP"


def _is_action_particle_doc_token(token: Token) -> bool:
    return token.dep_ == "prt" or token.tag_ == "RP"


def _is_action_prep_doc_token(token: Token) -> bool:
    return token.dep_ == "prep" or token.pos_ == "ADP"


def _build_children_by_head(
    tokens: Sequence[Mapping[str, Any]],
) -> dict[int, list[Mapping[str, Any]]]:
    children_by_head: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for token in tokens:
        token_i = _require_int(token, "i")
        head_i = _require_int(token, "head_i")
        if token_i != head_i:
            children_by_head[head_i].append(token)
    return children_by_head


def _build_doc_children_by_head(doc: Doc) -> dict[int, list[Token]]:
    children_by_head: dict[int, list[Token]] = defaultdict(list)
    for token in doc:
        if token.i != token.head.i:
            children_by_head[token.head.i].append(token)
    return children_by_head


def _chunk_tokens(
    chunk: Mapping[str, Any],
    token_by_i: Mapping[int, Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    start = _require_int(chunk, "token_start")
    end = _require_int(chunk, "token_end")
    return [token_by_i[i] for i in range(start, end) if i in token_by_i]


def _select_chunk_object_span(
    chunk: Mapping[str, Any],
    *,
    token_by_i: Mapping[int, Mapping[str, Any]],
    object_lookup: Any | None,
) -> _ObjectSpanSelection | None:
    tokens = _chunk_tokens(chunk, token_by_i)
    root_i = _require_int(chunk, "root_i")
    root_pos = next((index for index, token in enumerate(tokens) if _require_int(token, "i") == root_i), None)
    if root_pos is None:
        return None
    for start_pos in range(0, root_pos + 1):
        span_tokens = tokens[start_pos : root_pos + 1]
        if len(span_tokens) > 1 and not _is_allowed_token_record_span_start(span_tokens[0]):
            continue
        selection = _selection_from_token_records(
            span_tokens,
            chunk_text=_optional_text(chunk, "text"),
            root_i=root_i,
            object_lookup=object_lookup,
        )
        if selection is not None:
            return selection
    return None


def _select_doc_chunk_object_span(
    chunk: Any,
    *,
    object_lookup: Any | None,
) -> _ObjectSpanSelection | None:
    tokens = list(chunk)
    root_pos = next((index for index, token in enumerate(tokens) if token.i == chunk.root.i), None)
    if root_pos is None:
        return None
    for start_pos in range(0, root_pos + 1):
        span_tokens = tokens[start_pos : root_pos + 1]
        if len(span_tokens) > 1 and not _is_allowed_doc_token_span_start(span_tokens[0]):
            continue
        selection = _selection_from_doc_tokens(
            span_tokens,
            chunk_text=chunk.text,
            root_i=chunk.root.i,
            object_lookup=object_lookup,
        )
        if selection is not None:
            return selection
    return None


def _selection_from_token_records(
    tokens: Sequence[Mapping[str, Any]],
    *,
    chunk_text: str | None,
    root_i: int,
    object_lookup: Any | None,
) -> _ObjectSpanSelection | None:
    if not tokens:
        return None
    text = _token_record_span_text(tokens)
    surfaces = _token_record_span_lookup_surfaces(tokens)
    lookup = _lookup_object_surface(
        surfaces,
        object_lookup,
        raw_surface=text,
        require_manual_on_any_surface_changed_hit=_is_plural_common_noun_token(tokens[-1]),
    )
    if lookup is None:
        return None
    lookup_token_indices = tuple(_require_int(token, "i") for token in tokens)
    core_tokens = _object_core_token_records(tokens, lookup)
    token_indices = tuple(_require_int(token, "i") for token in core_tokens)
    core_text = _token_record_span_text(core_tokens)
    first = core_tokens[0]
    last = core_tokens[-1]
    lemma = _token_lemma(last) if len(core_tokens) == 1 else _normalize_query(core_text)
    return _ObjectSpanSelection(
        token_indices=token_indices,
        lookup_token_indices=lookup_token_indices,
        text=core_text,
        lookup_text=text,
        lemma=lemma,
        char_start=_optional_int(first, "char_start"),
        char_end=_optional_int(last, "char_end"),
        token_start=token_indices[0],
        token_end=token_indices[-1] + 1,
        source_detail=_object_span_detail(
            lookup=lookup,
            text=core_text,
            lookup_text=text,
            chunk_text=chunk_text,
            root_i=root_i,
            token_indices=token_indices,
            lookup_token_indices=lookup_token_indices,
        ),
    )


def _selection_from_doc_tokens(
    tokens: Sequence[Token],
    *,
    chunk_text: str | None,
    root_i: int,
    object_lookup: Any | None,
) -> _ObjectSpanSelection | None:
    if not tokens:
        return None
    text = _doc_token_span_text(tokens)
    surfaces = _doc_token_span_lookup_surfaces(tokens)
    lookup = _lookup_object_surface(
        surfaces,
        object_lookup,
        raw_surface=text,
        require_manual_on_any_surface_changed_hit=_is_plural_common_noun_doc_token(tokens[-1]),
    )
    if lookup is None:
        return None
    lookup_token_indices = tuple(token.i for token in tokens)
    core_tokens = _object_core_doc_tokens(tokens, lookup)
    token_indices = tuple(token.i for token in core_tokens)
    core_text = _doc_token_span_text(core_tokens)
    first = core_tokens[0]
    last = core_tokens[-1]
    lemma = _doc_token_lemma(last) if len(core_tokens) == 1 else _normalize_query(core_text)
    return _ObjectSpanSelection(
        token_indices=token_indices,
        lookup_token_indices=lookup_token_indices,
        text=core_text,
        lookup_text=text,
        lemma=lemma,
        char_start=first.idx,
        char_end=last.idx + len(last.text),
        token_start=token_indices[0],
        token_end=token_indices[-1] + 1,
        source_detail=_object_span_detail(
            lookup=lookup,
            text=core_text,
            lookup_text=text,
            chunk_text=chunk_text,
            root_i=root_i,
            token_indices=token_indices,
            lookup_token_indices=lookup_token_indices,
        ),
    )


def _object_core_token_indices_from_token_records(
    tokens: Sequence[Mapping[str, Any]],
    lookup: Any,
) -> tuple[int, ...]:
    return tuple(_require_int(token, "i") for token in _object_core_token_records(tokens, lookup))


def _object_core_token_records(
    tokens: Sequence[Mapping[str, Any]],
    lookup: Any,
) -> tuple[Mapping[str, Any], ...]:
    if not tokens:
        return ()
    for candidates in _object_core_candidate_groups(lookup):
        match = _best_token_record_suffix_match(tokens, candidates)
        if match:
            return match
    return tuple(tokens)


def _object_core_doc_tokens(tokens: Sequence[Token], lookup: Any) -> tuple[Token, ...]:
    if not tokens:
        return ()
    for candidates in _object_core_candidate_groups(lookup):
        match = _best_doc_token_suffix_match(tokens, candidates)
        if match:
            return match
    return tuple(tokens)


def _object_core_candidate_groups(lookup: Any) -> list[list[str]]:
    groups: list[list[str]] = []
    canonical_candidates = _unique_nonempty(
        (
            getattr(lookup, "canonical_surface", ""),
            getattr(lookup, "canonical_label_key", ""),
        )
    )
    if canonical_candidates:
        groups.append(canonical_candidates)

    selected_synset = getattr(lookup, "selected_synset", None)
    synset_lemmas: list[str] = []
    if selected_synset is not None:
        try:
            synset_lemmas = _unique_nonempty(str(lemma) for lemma in selected_synset.lemmas())
        except AttributeError:
            synset_lemmas = []
    if synset_lemmas:
        groups.append(synset_lemmas)

    query = getattr(lookup, "query", "")
    query_candidates = _unique_nonempty((str(query),))
    if query_candidates:
        groups.append(query_candidates)
    return groups


def _best_token_record_suffix_match(
    tokens: Sequence[Mapping[str, Any]],
    candidates: Sequence[str],
) -> tuple[Mapping[str, Any], ...]:
    best: tuple[Mapping[str, Any], ...] = ()
    candidate_keys = _candidate_surface_keys(candidates)
    if not candidate_keys:
        return best
    for start in range(len(tokens)):
        suffix = tuple(tokens[start:])
        suffix_keys = _surface_variant_keys(
            " ".join(_token_text(token) for token in suffix)
        )
        suffix_keys.update(
            _surface_variant_keys(" ".join(_token_lemma(token) for token in suffix))
        )
        if suffix_keys & candidate_keys and len(suffix) > len(best):
            best = suffix
    return best


def _best_doc_token_suffix_match(
    tokens: Sequence[Token],
    candidates: Sequence[str],
) -> tuple[Token, ...]:
    best: tuple[Token, ...] = ()
    candidate_keys = _candidate_surface_keys(candidates)
    if not candidate_keys:
        return best
    for start in range(len(tokens)):
        suffix = tuple(tokens[start:])
        suffix_keys = _surface_variant_keys(" ".join(token.text for token in suffix))
        suffix_keys.update(_surface_variant_keys(" ".join(_doc_token_lemma(token) for token in suffix)))
        if suffix_keys & candidate_keys and len(suffix) > len(best):
            best = suffix
    return best


def _candidate_surface_keys(candidates: Sequence[str]) -> set[str]:
    keys: set[str] = set()
    for candidate in candidates:
        keys.update(_surface_variant_keys(candidate))
    return keys


def _surface_variant_keys(value: str) -> set[str]:
    normalized = _normalize_query(value)
    if not normalized:
        return set()
    separator_normalized = _normalize_query(re.sub(r"[-_]+", " ", normalized))
    joined = re.sub(r"[\s_-]+", "", normalized)
    keys = {normalized, separator_normalized, joined}
    if separator_normalized:
        keys.add(separator_normalized.replace(" ", "_"))
    return {key for key in keys if key}


def _unique_nonempty(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_display_surface(str(value).replace("_", " "))
        if normalized and normalized not in seen:
            output.append(normalized)
            seen.add(normalized)
    return output


def _token_record_span_lookup_surfaces(tokens: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    text = _token_record_span_text(tokens)
    last = tokens[-1]
    return _span_lookup_surfaces(
        text,
        last_lemma=_token_lemma(last),
    )


def _is_allowed_token_record_span_start(token: Mapping[str, Any]) -> bool:
    return (
        _optional_text(token, "dep") not in SPAN_START_BLOCKED_DEPS
        and _optional_text(token, "pos") not in SPAN_START_BLOCKED_POS
    )


def _is_allowed_doc_token_span_start(token: Token) -> bool:
    return token.dep_ not in SPAN_START_BLOCKED_DEPS and token.pos_ not in SPAN_START_BLOCKED_POS


def _token_record_span_text(tokens: Sequence[Mapping[str, Any]]) -> str:
    if not tokens:
        return ""
    text = _normalize_display_surface(
        "".join(
            _token_text(token) + (_optional_text(token, "whitespace") or " ")
            for token in tokens[:-1]
        )
        + _token_text(tokens[-1])
    )
    return text


def _doc_token_span_lookup_surfaces(tokens: Sequence[Token]) -> tuple[str, ...]:
    text = _doc_token_span_text(tokens)
    return _span_lookup_surfaces(
        text,
        last_lemma=_doc_token_lemma(tokens[-1]),
    )


def _doc_token_span_text(tokens: Sequence[Token]) -> str:
    if not tokens:
        return ""
    text = _normalize_display_surface(
        "".join(token.text_with_ws for token in tokens[:-1]) + tokens[-1].text
    )
    return text


def _span_lookup_surfaces(
    text: str,
    *,
    last_lemma: str,
) -> tuple[str, ...]:
    surfaces: list[str] = []
    words = text.split()
    lemma_surface = ""
    if words and last_lemma and _normalize_query(words[-1]) != _normalize_query(last_lemma):
        lemma_surface = " ".join([*words[:-1], last_lemma])
    _append_unique(surfaces, text)
    if lemma_surface:
        _append_unique(surfaces, lemma_surface)
    return tuple(surfaces)


def _is_plural_common_noun_token(token: Mapping[str, Any]) -> bool:
    if _optional_text(token, "pos") != "NOUN":
        return False
    tag = _optional_text(token, "tag") or ""
    morph = _optional_text(token, "morph") or ""
    return tag == "NNS" or "Number=Plur" in morph


def _is_plural_common_noun_doc_token(token: Token) -> bool:
    if token.pos_ != "NOUN":
        return False
    return token.tag_ == "NNS" or "Number=Plur" in str(token.morph)


def _lookup_object_surface(
    surfaces: Sequence[str],
    object_lookup: Any | None,
    *,
    raw_surface: str,
    require_manual_on_any_surface_changed_hit: bool = False,
) -> _ObjectLookupResult | None:
    lookup = _probe_object_surface(
        surfaces,
        object_lookup,
        require_manual_on_any_surface_changed_hit=require_manual_on_any_surface_changed_hit,
    )
    if lookup is not None:
        _raise_if_ambiguous_lookup(raw_surface, lookup)
    return lookup


def _probe_object_surface(
    surfaces: Sequence[str],
    object_lookup: Any | None,
    *,
    require_manual_on_any_surface_changed_hit: bool = False,
) -> _ObjectLookupResult | None:
    if object_lookup is None:
        return None
    observed_query = _normalize_query(surfaces[0]) if surfaces else ""
    hits: list[_ObjectLookupResult] = []
    enable_surface_conflict_guard = not isinstance(object_lookup, GpicObjectInventoryLookup)
    if callable(object_lookup):
        for surface in surfaces:
            lookup = object_lookup(surface)
            if lookup is not None:
                hits.append(lookup)
    else:
        for surface in surfaces:
            lookup = _lookup_oewn_synsets(surface, object_lookup["oewn"], object_lookup["morphy"])
            if lookup.synsets:
                hits.append(lookup)
    if not hits:
        return None
    return _resolve_object_surface_hits(
        observed_query,
        hits,
        enable_surface_conflict_guard=enable_surface_conflict_guard,
        require_manual_on_any_surface_changed_hit=require_manual_on_any_surface_changed_hit,
    )


def _resolve_object_surface_hits(
    observed_query: str,
    hits: Sequence[_ObjectLookupResult],
    *,
    enable_surface_conflict_guard: bool = True,
    require_manual_on_any_surface_changed_hit: bool = False,
) -> _ObjectLookupResult:
    observed_hit = next(
        (hit for hit in hits if _normalize_query(hit.query) == observed_query),
        None,
    )
    if observed_hit is not None and enable_surface_conflict_guard:
        conflict = _object_surface_query_conflict_result(
            observed_hit,
            hits,
            require_manual_on_any_surface_changed_hit=require_manual_on_any_surface_changed_hit,
        )
        if conflict is not None:
            return conflict
    return observed_hit if observed_hit is not None else hits[0]


def _object_surface_query_conflict_result(
    observed_hit: _ObjectLookupResult,
    hits: Sequence[_ObjectLookupResult],
    *,
    require_manual_on_any_surface_changed_hit: bool = False,
) -> _ObjectLookupResult | None:
    observed_id = _selected_synset_id(observed_hit)
    if not observed_id and not require_manual_on_any_surface_changed_hit:
        return None
    conflicting = [
        hit
        for hit in hits
        if _normalize_query(hit.query) != _normalize_query(observed_hit.query)
        and hit.synsets
        and (
            (
                require_manual_on_any_surface_changed_hit
                and _selected_synset_id(hit) != observed_id
            )
            or (
                not require_manual_on_any_surface_changed_hit
                and _selected_synset_id(hit)
                and _selected_synset_id(hit) != observed_id
            )
        )
    ]
    if not conflicting:
        return None
    synsets: list[Any] = []
    seen_synset_ids: set[str] = set()
    for hit in (observed_hit, *conflicting):
        for synset in hit.synsets:
            synset_id = str(synset.id)
            if synset_id in seen_synset_ids:
                continue
            synsets.append(synset)
            seen_synset_ids.add(synset_id)
    queries = _unique_nonempty(hit.query for hit in (observed_hit, *conflicting))
    details = _unique_nonempty(
        f"{hit.query}:{hit.lookup_case}:{_selected_synset_id(hit)}"
        for hit in (observed_hit, *conflicting)
    )
    return _ObjectLookupResult(
        lookup_case="plural_surface_query_conflict"
        if require_manual_on_any_surface_changed_hit
        else "surface_query_conflict",
        query="|".join(queries),
        synsets=tuple(synsets),
        selected_synset=None,
        synset_selection_tag="ambiguous_plural_observed_vs_base_query"
        if require_manual_on_any_surface_changed_hit
        else "ambiguous_observed_vs_surface_changed_query",
        wn30_lemma_counts="||".join(details),
        objectness_gate="",
        decision_status="needs_manual",
        decision_reason="manual_surface_query_conflict_required",
    )


def _selected_synset_id(lookup: _ObjectLookupResult) -> str:
    return str(lookup.selected_synset.id) if lookup.selected_synset is not None else ""


def load_gpic_object_inventory(path: str | Path) -> GpicObjectInventoryLookup:
    return GpicObjectInventoryLookup.from_tsv(path)


def load_gpic_action_inventory(path: str | Path) -> GpicActionInventoryLookup:
    return GpicActionInventoryLookup.from_tsv(path)


def load_gpic_attribute_mwe_inventory(path: str | Path) -> ResolvedAttributeMweIndex:
    return ResolvedAttributeMweIndex.from_tsv(path)


def load_preposition_mwe_lexicon(path: str | Path) -> tuple[_PrepositionMweEntry, ...]:
    entries: list[_PrepositionMweEntry] = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            entry = _preposition_mwe_entry_from_row(row)
            if entry is not None:
                entries.append(entry)
    return tuple(entries)


def _build_preposition_mwe_index(
    entries: Sequence[_PrepositionMweEntry],
) -> _PrepositionMweIndex:
    entries_by_token_keys: dict[tuple[str, ...], list[_PrepositionMweEntry]] = {}
    for entry in entries:
        if len(entry.token_keys) < 2:
            continue
        entries_by_token_keys.setdefault(entry.token_keys, []).append(entry)
    frozen_entries = {
        token_keys: tuple(grouped_entries)
        for token_keys, grouped_entries in entries_by_token_keys.items()
    }
    widths = tuple(sorted({len(token_keys) for token_keys in frozen_entries}, reverse=True))
    return _PrepositionMweIndex(
        entries_by_token_keys=frozen_entries,
        widths=widths,
    )


def _coerce_preposition_mwe_index(
    lookup: _PrepositionMweIndex | Sequence[_PrepositionMweEntry] | None,
) -> _PrepositionMweIndex:
    if lookup is None:
        return _PrepositionMweIndex(entries_by_token_keys={}, widths=())
    if isinstance(lookup, _PrepositionMweIndex):
        return lookup
    return _build_preposition_mwe_index(lookup)


def _preposition_mwe_entry_from_row(
    row: Mapping[str, str],
) -> _PrepositionMweEntry | None:
    surface = (
        row.get("surface")
        or row.get("lookup_form")
        or row.get("entry")
        or row.get("entry_key")
        or ""
    ).strip()
    canonical = (row.get("canonical_relation") or row.get("entry") or surface).strip().lower()
    token_sequence = (row.get("token_sequence") or surface).strip()
    token_keys = tuple(_normalize_query(part) for part in token_sequence.split())
    token_keys = tuple(part for part in token_keys if part)
    if len(token_keys) < 2 or not canonical:
        return None
    components = tuple(
        part
        for part in (
            _split_pipe(row.get("relation_components", ""))
            or canonical.split()
        )
        if part
    )
    initial_offset = _parse_offset(
        row.get("initial_relation_token_offset", ""),
        default=0,
    )
    final_offset = _parse_offset(
        row.get("final_adp_token_offset", ""),
        default=len(token_keys) - 1,
    )
    if initial_offset < 0 or initial_offset >= len(token_keys):
        return None
    if final_offset < 0 or final_offset >= len(token_keys):
        return None
    return _PrepositionMweEntry(
        surface=surface,
        token_keys=token_keys,
        canonical_relation=canonical,
        relation_components=components,
        initial_relation_token_offset=initial_offset,
        final_adp_token_offset=final_offset,
        source=(row.get("source") or row.get("sources") or "").strip(),
        notes=(row.get("notes") or "").strip(),
    )


def _parse_offset(value: str, *, default: int) -> int:
    text = value.strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _find_preposition_mwe_matches_in_token_records(
    tokens: Sequence[Mapping[str, Any]],
    entries: _PrepositionMweIndex | Sequence[_PrepositionMweEntry] | None,
) -> list[_PrepositionMweMatch]:
    token_keys = tuple(_normalize_query(_token_text(token)) for token in tokens)
    token_indices = tuple(_require_int(token, "i") for token in tokens)
    return _find_preposition_mwe_matches(
        entries,
        token_keys=token_keys,
        token_indices=token_indices,
        span_text_fn=lambda start, end: _token_record_span_text(tokens[start:end]),
    )


def _find_preposition_mwe_matches_in_doc(
    doc: Doc,
    entries: _PrepositionMweIndex | Sequence[_PrepositionMweEntry] | None,
) -> list[_PrepositionMweMatch]:
    token_keys = tuple(_normalize_query(token.text) for token in doc)
    token_indices = tuple(token.i for token in doc)
    return _find_preposition_mwe_matches(
        entries,
        token_keys=token_keys,
        token_indices=token_indices,
        span_text_fn=lambda start, end: _doc_token_span_text(tuple(doc[start:end])),
    )


def _find_preposition_mwe_matches(
    entries: _PrepositionMweIndex | Sequence[_PrepositionMweEntry] | None,
    *,
    token_keys: Sequence[str],
    token_indices: Sequence[int],
    span_text_fn: Any,
) -> list[_PrepositionMweMatch]:
    index = _coerce_preposition_mwe_index(entries)
    if not index.widths:
        return []
    candidates: list[tuple[int, int, _PrepositionMweEntry, str]] = []
    token_count = len(token_keys)
    for width in index.widths:
        if width > token_count:
            continue
        for start in range(0, token_count - width + 1):
            end = start + width
            token_key = tuple(token_keys[start:end])
            for entry in index.entries_by_token_keys.get(token_key, ()):
                candidates.append((start, end, entry, span_text_fn(start, end)))
    candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
    occupied: set[int] = set()
    matches: list[_PrepositionMweMatch] = []
    for start, end, entry, raw_span_surface in candidates:
        positions = set(range(start, end))
        if positions & occupied:
            continue
        occupied.update(positions)
        matches.append(
            _PrepositionMweMatch(
                entry=entry,
                token_indices=tuple(token_indices[start:end]),
                raw_span_surface=raw_span_surface,
            )
        )
    matches.sort(key=lambda match: match.token_indices)
    return matches


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
    synset_status = row.get("synset_selection_status", "")
    if synset_status and synset_status != "selected":
        return None
    if not synset_status and _inventory_decision_status(row) not in (
        "chosen",
        "needs_manual",
        "excluded",
    ):
        return None
    for synset in synsets:
        if synset.id == selected_id:
            return synset
    return _InventorySynset(
        selected_id,
        row.get("selected_oewn_lexfile", ""),
        tuple(_split_pipe(row.get("synset_lemmas", ""))),
    )


def _inventory_decision_status(row: Mapping[str, str]) -> str:
    return normalize_inventory_decision_status(row)


def _legacy_extraction_status_to_decision_status(value: str) -> str:
    return normalize_legacy_decision_status(value)


def _split_pipe(value: str) -> list[str]:
    return [part for part in value.split("|") if part]


def _raise_if_ambiguous_lookup(surface: str, lookup: _ObjectLookupResult) -> None:
    if (
        lookup.decision_status != "needs_manual"
        and not _has_unresolved_canonical_lookup(lookup)
    ):
        return
    synsets = ",".join(synset.id for synset in lookup.synsets)
    lexfiles = ",".join(synset.lexfile() for synset in lookup.synsets)
    detail = (
        f"surface={surface!r}; query={lookup.query!r}; "
        f"decision_status={lookup.decision_status}; "
        f"decision_reason={lookup.decision_reason}; "
        f"objectness_gate={lookup.objectness_gate}; "
        f"tag={lookup.synset_selection_tag}; synsets={synsets}; "
        f"lexfiles={lexfiles}; wn30_lemma_counts={lookup.wn30_lemma_counts}; "
        f"canonical_surface={lookup.canonical_surface!r}; "
        f"canonical_selection_tag={lookup.canonical_selection_tag!r}"
    )
    raise Stage4SynsetAmbiguityError(
        "object span must be resolved before raw extraction: "
        + detail
    )


def _has_unresolved_canonical_lookup(lookup: _ObjectLookupResult) -> bool:
    if lookup.selected_synset is None:
        return False
    if lookup.decision_status == "excluded":
        return False
    if lookup.canonical_surface:
        return False
    if lookup.canonical_selection_tag == "not_applicable_no_selected_synset":
        return False
    return True


@lru_cache(maxsize=1)
def _load_object_lookup_runtime() -> Any | None:
    if wn is None:
        return None
    wn.config.data_directory = str(WN_DATA_DIR)
    if nltk is not None:
        nltk.data.path.insert(0, str(NLTK_DATA_DIR))
    try:
        oewn = wn.Wordnet(OEWN_SPEC, expand="")
        morphy = Morphy(oewn)
    except Exception:
        return None
    return {"oewn": oewn, "morphy": morphy, "wn30_available": _check_wn30_available()}


@lru_cache(maxsize=1)
def _load_preposition_mwe_lookup_runtime() -> _PrepositionMweIndex:
    path = ROOT / "resources" / "lexicons" / "preposition_mwes.tsv"
    if not path.exists():
        return _PrepositionMweIndex(entries_by_token_keys={}, widths=())
    return _build_preposition_mwe_index(load_preposition_mwe_lexicon(path))


def _lookup_oewn_synsets(label: str, oewn: Any, morphy: Any) -> _ObjectLookupResult:
    for case, query in _lookup_queries(label):
        synsets = tuple(_noun_synsets(oewn, query))
        if synsets:
            return _with_selected_synset(case, query, synsets)
    for case, query in _last_word_morphy_queries(label, morphy):
        synsets = tuple(_noun_synsets(oewn, query))
        if synsets:
            return _with_selected_synset(case, query, synsets)
    return _ObjectLookupResult(
        "unresolved",
        "",
        (),
        None,
        "unresolved_no_oewn_noun_synset",
        "",
        "",
        "excluded",
        "no_oewn_noun_synset",
    )


def _lookup_oewn_verb_synsets(label: str, oewn: Any, morphy: Any) -> _ActionLookupResult:
    for case, query in _action_lookup_queries(label):
        synsets = _verb_synsets_matching_query(oewn, query)
        if synsets:
            return _with_selected_action_synset(case, query, synsets)
    morphy_hits: list[tuple[str, str, tuple[Any, ...]]] = []
    for case, query in _verb_head_morphy_queries(label, morphy):
        synsets = _verb_synsets_matching_query(oewn, query)
        if synsets:
            morphy_hits.append((case, query, synsets))
    if len(morphy_hits) == 1:
        case, query, synsets = morphy_hits[0]
        return _with_selected_action_synset(case, query, synsets)
    if len(morphy_hits) > 1:
        return _with_ambiguous_action_morphy_hits(morphy_hits)
    return _ActionLookupResult(
        "unresolved",
        "",
        (),
        None,
        "unresolved_no_oewn_verb_synset",
        "",
        "excluded",
        "no_oewn_verb_synset",
    )


def _lookup_queries(label: str) -> list[tuple[str, str]]:
    exact = _normalize_query(label)
    separator_variant = _normalize_query(re.sub(r"[-_]+", " ", label))
    joined_variant = re.sub(r"[\s_-]+", "", exact)
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for case, query in (
        ("exact", exact),
        ("separator_variant", separator_variant),
        ("joined_variant", joined_variant),
    ):
        if query and query not in seen:
            queries.append((case, query))
            seen.add(query)
    return queries


def _action_lookup_queries(label: str) -> list[tuple[str, str]]:
    exact = _normalize_query(label)
    return [("exact", exact)] if exact else []


def _verb_head_morphy_queries(label: str, morphy: Any) -> list[tuple[str, str]]:
    exact = _normalize_query(label)
    words = exact.split()
    if not words:
        return []
    result = morphy(words[0], "v")
    verb_lemmas = result.get("v", set()) if result else set()
    queries: list[tuple[str, str]] = []
    seen: set[str] = {exact}
    for lemma in sorted(verb_lemmas):
        query = _normalize_query(" ".join([lemma, *words[1:]]))
        if query and query not in seen:
            queries.append(("verb_head_morphy", query))
            seen.add(query)
    return queries


def _last_word_morphy_queries(label: str, morphy: Any) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for base_case, base_query in _lookup_queries(label):
        words = base_query.split()
        if not words:
            continue
        result = morphy(words[-1], "n")
        noun_lemmas = result.get("n", set()) if result else set()
        for lemma in sorted(noun_lemmas):
            query = _normalize_query(" ".join([*words[:-1], lemma]))
            if query and query not in seen:
                queries.append((f"last_word_morphy_after_{base_case}", query))
                seen.add(query)
    return queries


def _with_selected_action_synset(
    lookup_case: str,
    query: str,
    synsets: tuple[Any, ...],
) -> _ActionLookupResult:
    selected, tag, counts = _select_action_runtime_synset(list(synsets), query)
    decision_status = "chosen" if selected is not None else "needs_manual"
    decision_reason = (
        "selected_verb_synset"
        if selected is not None
        else "manual_action_synset_required"
    )
    return _ActionLookupResult(
        lookup_case,
        query,
        synsets,
        selected,
        tag,
        counts,
        decision_status,
        decision_reason,
    )


def _with_ambiguous_action_morphy_hits(
    hits: Sequence[tuple[str, str, tuple[Any, ...]]],
) -> _ActionLookupResult:
    synsets: list[Any] = []
    seen_synset_ids: set[str] = set()
    count_notes: list[str] = []
    queries: list[str] = []
    for _, query, query_synsets in hits:
        queries.append(query)
        _, wn30_tag, counts = _select_action_runtime_synset(list(query_synsets), query)
        count_notes.append(f"{query}:{wn30_tag}:{counts}")
        for synset in query_synsets:
            synset_id = str(synset.id)
            if synset_id in seen_synset_ids:
                continue
            synsets.append(synset)
            seen_synset_ids.add(synset_id)
    return _ActionLookupResult(
        "verb_head_morphy_ambiguous",
        "|".join(queries),
        tuple(synsets),
        None,
        "ambiguous_morphy_multiple_oewn_hit_queries",
        "||".join(count_notes),
        "needs_manual",
        "manual_action_morphy_required",
    )


def _with_selected_synset(
    lookup_case: str,
    query: str,
    synsets: tuple[Any, ...],
) -> _ObjectLookupResult:
    selected, tag, counts = _select_runtime_synset(list(synsets), query)
    objectness_gate = (
        _objectness_gate_for_lexfile(selected.lexfile()) if selected is not None else ""
    )
    parent_synsets, parent_selection_tag = _immediate_hypernym_parents(selected)
    decision_status = _decision_status_for_selection(
        selected_synset=selected,
        synsets=synsets,
        objectness_gate=objectness_gate,
    )
    decision_reason = _decision_reason_for_selection(
        selected_synset=selected,
        synsets=synsets,
        objectness_gate=objectness_gate,
    )
    if _is_joined_lookup_case(lookup_case) and decision_status == "chosen":
        decision_status = "needs_manual"
        decision_reason = "manual_joined_variant_required"
    return _ObjectLookupResult(
        lookup_case,
        query,
        synsets,
        selected,
        tag,
        counts,
        objectness_gate,
        decision_status,
        decision_reason,
        tuple(synset.id for synset in parent_synsets),
        tuple(f"{synset.id}:{synset.lexfile()}" for synset in parent_synsets),
        tuple(f"{synset.id}:{';'.join(synset.lemmas())}" for synset in parent_synsets),
        parent_selection_tag,
    )


def _select_action_runtime_synset(
    synsets: list[Any],
    query: str,
) -> tuple[Any | None, str, str]:
    if not synsets:
        return None, "unresolved_no_oewn_verb_synset", ""
    if len(synsets) == 1:
        return synsets[0], "single_oewn_verb_synset", ""
    selected, wn30_tag, counts = _select_by_wn30_lemma_count(synsets, query)
    if selected is not None:
        return selected, "selected_by_wn30_lemma_count", counts
    return None, f"ambiguous_{wn30_tag}", counts


def _is_joined_lookup_case(lookup_case: str) -> bool:
    return lookup_case == "joined_variant" or lookup_case.endswith("_after_joined_variant")


def _select_runtime_synset(synsets: list[Any], query: str) -> tuple[Any | None, str, str]:
    if not synsets:
        return None, "unresolved_no_oewn_noun_synset", ""
    if len(synsets) == 1:
        return synsets[0], "single_oewn_noun_synset", ""
    preferred = [
        synset
        for synset in synsets
        if synset.lexfile() in OBJECT_COMPATIBLE_LEXFILES
        or synset.lexfile() in CONDITIONAL_OBJECT_LEXFILES
    ]
    selected, wn30_tag, counts = _select_by_wn30_lemma_count(preferred or synsets, query)
    if selected is not None:
        return selected, "selected_by_wn30_lemma_count", counts
    return None, f"ambiguous_{wn30_tag}", counts


def _objectness_gate_for_lexfile(lexfile: str) -> str:
    if lexfile in OBJECT_COMPATIBLE_LEXFILES:
        return "object_compatible"
    if lexfile in CONDITIONAL_OBJECT_LEXFILES:
        return "conditional"
    if lexfile:
        return "hard_conflict"
    return ""


def _decision_status_for_selection(
    *,
    selected_synset: Any | None,
    synsets: Sequence[Any],
    objectness_gate: str,
) -> str:
    if selected_synset is None:
        return "needs_manual" if synsets else "excluded"
    if objectness_gate == "object_compatible":
        return "chosen"
    return "needs_manual"


def _decision_reason_for_selection(
    *,
    selected_synset: Any | None,
    synsets: Sequence[Any],
    objectness_gate: str,
) -> str:
    if selected_synset is None:
        return "manual_synset_required" if synsets else "no_oewn_noun_synset"
    if objectness_gate == "object_compatible":
        return "selected_object_compatible"
    return "manual_objectness_required"


def _immediate_hypernym_parents(selected_synset: Any | None) -> tuple[list[Any], str]:
    if selected_synset is None:
        return [], "not_applicable_no_selected_synset"
    try:
        parents = list(selected_synset.hypernyms())
    except AttributeError:
        return [], "not_available_selected_synset_has_no_hypernyms_api"
    if not parents:
        return [], "no_immediate_oewn_hypernym"
    return parents, "selected_all_immediate_oewn_hypernyms"


def _select_by_wn30_lemma_count(synsets: list[Any], query: str) -> tuple[Any | None, str, str]:
    if not _check_wn30_available():
        return None, "wn30_unavailable", ""
    rows: list[tuple[Any, int, str]] = []
    query_key = _lemma_key(query)
    for synset in synsets:
        count, note = _sense_key_lemma_count(synset=synset, query_key=query_key)
        rows.append((synset, count, f"{synset.id}:{note}"))
    counts = "|".join(row[2] for row in rows)
    valid_rows = [(synset, count) for synset, count, _ in rows if count >= 0]
    if not valid_rows:
        return None, "wn30_mapping_missing", counts
    max_count = max(count for _, count in valid_rows)
    if max_count <= 0:
        return None, "wn30_all_zero", counts
    winners = [synset for synset, count in valid_rows if count == max_count]
    if len(winners) == 1:
        return winners[0], "wn30_unique_max", counts
    return None, "wn30_tie", counts


def _sense_key_lemma_count(synset: Any, query_key: str) -> tuple[int, str]:
    if wn30 is None:
        return -1, "wn30_unavailable"
    count = 0
    mapped = 0
    notes: list[str] = []
    for sense in synset.senses():
        sense_key = _sense_key_from_oewn_sense_id(sense.id)
        if not sense_key:
            notes.append(f"{sense.id}=sense_key_missing")
            continue
        try:
            lemma = wn30.lemma_from_key(sense_key)
        except Exception:
            notes.append(f"{sense_key}=wn30_missing")
            continue
        mapped += 1
        if _lemma_key(lemma.name()) == query_key:
            count += lemma.count()
            notes.append(f"{sense_key}:{lemma.synset().name()}:{lemma.count()}")
    if mapped == 0:
        return -1, "wn30_missing"
    return count, ";".join(notes)


def _sense_key_from_oewn_sense_id(sense_id: str) -> str:
    match = re.match(r"^oewn-(.+)__(\d)\.(\d\d)\.(\d\d)\.\.$", sense_id)
    if match is None:
        return ""
    lemma, ss_type, lex_filenum, lex_id = match.groups()
    return f"{lemma}%{ss_type}:{lex_filenum}:{lex_id}::"


@lru_cache(maxsize=1)
def _check_wn30_available() -> bool:
    if wn30 is None:
        return False
    try:
        wn30.synsets("dog")
    except LookupError:
        if nltk is None:
            return False
        nltk.data.path.insert(0, str(NLTK_DATA_DIR))
    try:
        return bool(wn30.synsets("dog"))
    except LookupError:
        return False


def _noun_synsets(oewn: Any, query: str) -> list[Any]:
    return list(oewn.synsets(query, pos="n"))


def _verb_synsets(oewn: Any, query: str) -> list[Any]:
    return list(oewn.synsets(query, pos="v"))


def _verb_synsets_matching_query(oewn: Any, query: str) -> tuple[Any, ...]:
    return tuple(
        synset
        for synset in _verb_synsets(oewn, query)
        if _synset_has_surface_lemma(synset, query)
    )


def _synset_has_surface_lemma(synset: Any, query: str) -> bool:
    query_key = _lemma_key(query)
    return any(_lemma_key(str(lemma)) == query_key for lemma in synset.lemmas())


def _raise_if_ambiguous_action_lookup(surface: str, lookup: _ActionLookupResult) -> None:
    if lookup.decision_status != "needs_manual":
        return
    synsets = ",".join(synset.id for synset in lookup.synsets)
    lexfiles = ",".join(synset.lexfile() for synset in lookup.synsets)
    detail = (
        f"surface={surface!r}; query={lookup.query!r}; "
        f"decision_status={lookup.decision_status}; "
        f"tag={lookup.synset_selection_tag}; synsets={synsets}; "
        f"lexfiles={lexfiles}; wn30_lemma_counts={lookup.wn30_lemma_counts}"
    )
    raise Stage4SynsetAmbiguityError(
        "action span must be resolved before raw extraction: " + detail
    )


def _object_span_detail(
    *,
    lookup: _ObjectLookupResult,
    text: str,
    lookup_text: str,
    chunk_text: str | None,
    root_i: int,
    token_indices: tuple[int, ...],
    lookup_token_indices: tuple[int, ...],
) -> JsonObject:
    selected = lookup.selected_synset
    detail: JsonObject = {
        "raw_surface": text,
        "lookup_span_surface": lookup_text,
        "chunk_text": chunk_text,
        "root_i": root_i,
        "selected_token_indices": list(token_indices),
        "lookup_token_indices": list(lookup_token_indices),
        "lookup_case": lookup.lookup_case,
        "lookup_query": lookup.query,
        "has_oewn_noun_synset": bool(lookup.synsets),
        "oewn_synset_count": len(lookup.synsets),
        "all_oewn_synsets": [synset.id for synset in lookup.synsets],
        "all_oewn_lexfiles": [synset.lexfile() for synset in lookup.synsets],
        "synset_selection_tag": lookup.synset_selection_tag,
        "objectness_gate": lookup.objectness_gate,
        "decision_status": lookup.decision_status,
        "decision_reason": lookup.decision_reason,
    }
    if lookup.wn30_lemma_counts:
        detail["wn30_lemma_counts"] = lookup.wn30_lemma_counts
    if selected is not None:
        detail["selected_oewn_synset"] = selected.id
        detail["selected_oewn_lexfile"] = selected.lexfile()
        detail["synset_lemmas"] = [lemma for lemma in selected.lemmas()]
    if lookup.parent_oewn_synsets:
        detail["parent_oewn_synsets"] = list(lookup.parent_oewn_synsets)
        detail["parent_oewn_lexfiles"] = list(lookup.parent_oewn_lexfiles)
        detail["parent_lemmas"] = list(lookup.parent_lemmas)
        detail["parent_selection_tag"] = lookup.parent_selection_tag
    if lookup.canonical_surface:
        detail["canonical_surface"] = lookup.canonical_surface
        detail["canonical_label_key"] = lookup.canonical_label_key
        detail["canonical_selection_tag"] = lookup.canonical_selection_tag
    if lookup.canonical_candidate_lemmas:
        detail["canonical_candidate_lemmas"] = list(lookup.canonical_candidate_lemmas)
    if lookup.canonical_candidate_lemma_counts:
        detail["canonical_candidate_lemma_counts"] = lookup.canonical_candidate_lemma_counts
    if lookup.google_ngram_candidate_surfaces:
        detail["google_ngram_candidate_surfaces"] = list(
            lookup.google_ngram_candidate_surfaces
        )
    if lookup.google_ngram_candidate_mean_frequencies:
        detail["google_ngram_candidate_mean_frequencies"] = (
            lookup.google_ngram_candidate_mean_frequencies
        )
    return detail


def _normalize_display_surface(value: str) -> str:
    return " ".join(value.strip().split())


def _normalize_query(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _lemma_key(value: str) -> str:
    return _normalize_query(value).replace(" ", "_").replace("-", "_")


def _append_unique(values: list[str], value: str) -> None:
    normalized = _normalize_display_surface(value)
    if normalized and normalized not in values:
        values.append(normalized)


def _record_attribute_token_views(
    tokens: Sequence[Mapping[str, Any]],
) -> tuple[AttributeTokenView, ...]:
    return tuple(
        AttributeTokenView(
            i=_require_int(token, "i"),
            text=_token_text(token),
            lemma=_token_lemma(token),
            dep=_optional_text(token, "dep"),
            pos=_optional_text(token, "pos"),
            tag=_optional_text(token, "tag"),
            char_start=_optional_int(token, "char_start"),
            char_end=_optional_int(token, "char_end"),
            is_quantity=_is_quantity_modifier(token),
        )
        for token in tokens
    )


def _doc_attribute_token_views(tokens: Sequence[Token]) -> tuple[AttributeTokenView, ...]:
    return tuple(
        AttributeTokenView(
            i=token.i,
            text=token.text,
            lemma=_doc_token_lemma(token),
            dep=token.dep_,
            pos=token.pos_,
            tag=token.tag_,
            char_start=token.idx,
            char_end=token.idx + len(token.text),
            is_quantity=_is_doc_quantity_modifier(token),
        )
        for token in tokens
    )


def _record_attribute_children_views(
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
) -> dict[int, tuple[AttributeTokenView, ...]]:
    return {
        head_i: _record_attribute_token_views(children)
        for head_i, children in children_by_head.items()
    }


def _doc_attribute_children_views(
    children_by_head: Mapping[int, Sequence[Token]],
) -> dict[int, tuple[AttributeTokenView, ...]]:
    return {
        head_i: _doc_attribute_token_views(children)
        for head_i, children in children_by_head.items()
    }


def _select_record_attribute_mwes(
    chunk_tokens: Sequence[Mapping[str, Any]],
    *,
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
    excluded_token_indices: set[int],
    attribute_mwe_lookup: ResolvedAttributeMweIndex | None,
) -> tuple[Any, ...]:
    if attribute_mwe_lookup is None:
        return ()
    token_views = _record_attribute_token_views(chunk_tokens)
    anchors = collect_attribute_anchors(
        token_views,
        children_by_head=_record_attribute_children_views(children_by_head),
        excluded_token_indices=excluded_token_indices,
    )
    return select_attribute_mwes(
        token_views,
        anchors=anchors,
        excluded_token_indices=excluded_token_indices,
        lookup=attribute_mwe_lookup,
    )


def _select_doc_attribute_mwes(
    chunk: Any,
    *,
    children_by_head: Mapping[int, Sequence[Token]],
    excluded_token_indices: set[int],
    attribute_mwe_lookup: ResolvedAttributeMweIndex | None,
) -> tuple[Any, ...]:
    if attribute_mwe_lookup is None:
        return ()
    token_views = _doc_attribute_token_views(tuple(chunk))
    anchors = collect_attribute_anchors(
        token_views,
        children_by_head=_doc_attribute_children_views(children_by_head),
        excluded_token_indices=excluded_token_indices,
    )
    return select_attribute_mwes(
        token_views,
        anchors=anchors,
        excluded_token_indices=excluded_token_indices,
        lookup=attribute_mwe_lookup,
    )


def _attribute_mwe_detail(
    candidate: AttributeMweCandidate,
    inventory_row: Mapping[str, str],
    root_i: int | None,
) -> JsonObject:
    detail: JsonObject = {
        "root_i": root_i,
        "modifier_i": candidate.anchor.token_i,
        "modifier_source": "attribute_mwe",
        "attribute_unit_type": ATTRIBUTE_UNIT_MWE,
        "span_token_count": len(candidate.tokens),
        "anchor_token_offset": candidate.anchor_token_offset,
        "selected_token_indices": list(candidate.token_indices),
        "inventory_span_key": inventory_row.get("span_key", ""),
        "inventory_rule_version": inventory_row.get("attribute_mwe_rule_version", ""),
    }
    if candidate.anchor.conj_head_i is not None:
        detail["conj_head_i"] = candidate.anchor.conj_head_i
    return detail


def _doc_attribute_mwe_text(doc: Doc, candidate: AttributeMweCandidate) -> str:
    del doc
    return candidate.surface


def _add_unattached_record_attribute_mwe(
    builder: _RawBuilder,
    *,
    candidate: AttributeMweCandidate,
    inventory_row: Mapping[str, str],
    source_text: str,
    source_detail: JsonObject,
) -> None:
    detail = _attribute_mwe_detail(candidate, inventory_row, None)
    detail.update(source_detail)
    builder.add_mention(
        mention_type="attribute",
        text=candidate.surface,
        lemma=" ".join(token.lemma for token in candidate.tokens),
        rule_id=ATTRIBUTE_RULE_ID,
        char_start=candidate.tokens[0].char_start,
        char_end=candidate.tokens[-1].char_end,
        token_start=candidate.token_start,
        token_end=candidate.token_end,
        source_text=source_text,
        source_detail=detail,
    )


def _is_attribute_modifier(token: Mapping[str, Any]) -> bool:
    return _optional_text(token, "dep") in ATTRIBUTE_MODIFIER_DEPS


def _conjunct_attribute_modifiers(
    token: Mapping[str, Any],
    *,
    children_by_head: Mapping[int, Sequence[Mapping[str, Any]]],
    chunk_token_indices: set[int],
    excluded_token_indices: set[int],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    modifiers: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    seen = {_require_int(token, "i")}
    pending: list[Mapping[str, Any]] = [token]
    while pending:
        head = pending.pop(0)
        for child in children_by_head.get(_require_int(head, "i"), ()):
            child_i = _require_int(child, "i")
            if child_i in seen:
                continue
            if child_i not in chunk_token_indices:
                continue
            if child_i in excluded_token_indices:
                continue
            if _optional_text(child, "dep") != "conj":
                continue
            if _is_quantity_modifier(child):
                continue
            seen.add(child_i)
            modifiers.append((child, head))
            pending.append(child)
    return modifiers


def _is_quantity_modifier(token: Mapping[str, Any]) -> bool:
    return (
        _optional_text(token, "dep") in QUANTITY_MODIFIER_DEPS
        or _optional_text(token, "pos") == "NUM"
    )


def _is_doc_attribute_modifier(token: Token) -> bool:
    return token.dep_ in ATTRIBUTE_MODIFIER_DEPS


def _doc_conjunct_attribute_modifiers(
    token: Token,
    *,
    children_by_head: Mapping[int, Sequence[Token]],
    chunk_token_indices: set[int],
    excluded_token_indices: set[int],
) -> list[tuple[Token, Token]]:
    modifiers: list[tuple[Token, Token]] = []
    seen = {token.i}
    pending: list[Token] = [token]
    while pending:
        head = pending.pop(0)
        for child in children_by_head.get(head.i, ()):
            if child.i in seen:
                continue
            if child.i not in chunk_token_indices:
                continue
            if child.i in excluded_token_indices:
                continue
            if child.dep_ != "conj":
                continue
            if _is_doc_quantity_modifier(child):
                continue
            seen.add(child.i)
            modifiers.append((child, head))
            pending.append(child)
    return modifiers


def _is_doc_quantity_modifier(token: Token) -> bool:
    return token.dep_ in QUANTITY_MODIFIER_DEPS or token.pos_ == "NUM"


def _chunk_root_detail(chunk: Mapping[str, Any]) -> JsonObject:
    return {
        "root_i": _optional_int(chunk, "root_i"),
        "root_pos": _optional_text(chunk, "root_pos"),
        "root_tag": _optional_text(chunk, "root_tag"),
        "root_dep": _optional_text(chunk, "root_dep"),
        "root_head_i": _optional_int(chunk, "root_head_i"),
        "root_head_text": _optional_text(chunk, "root_head_text"),
    }


def _modifier_detail(token: Mapping[str, Any], root_i: int) -> JsonObject:
    detail = _token_detail(token)
    detail["root_i"] = root_i
    return detail


def _token_detail(token: Mapping[str, Any]) -> JsonObject:
    return {
        "i": _optional_int(token, "i"),
        "pos": _optional_text(token, "pos"),
        "tag": _optional_text(token, "tag"),
        "dep": _optional_text(token, "dep"),
        "head_i": _optional_int(token, "head_i"),
        "head_text": _optional_text(token, "head_text"),
    }


def _doc_chunk_root_detail(chunk: Any) -> JsonObject:
    return {
        "root_i": chunk.root.i,
        "root_pos": chunk.root.pos_,
        "root_tag": chunk.root.tag_,
        "root_dep": chunk.root.dep_,
        "root_head_i": chunk.root.head.i,
        "root_head_text": chunk.root.head.text,
    }


def _doc_modifier_detail(token: Token, root_i: int) -> JsonObject:
    detail = _doc_token_detail(token)
    detail["root_i"] = root_i
    return detail


def _doc_token_detail(token: Token) -> JsonObject:
    return {
        "i": token.i,
        "pos": token.pos_,
        "tag": token.tag_,
        "dep": token.dep_,
        "head_i": token.head.i,
        "head_text": token.head.text,
    }


def _token_text(token: Mapping[str, Any]) -> str:
    value = _optional_text(token, "text")
    if value is None or value == "":
        raise ValueError("token text must be a non-empty string")
    return value


def _token_lemma(token: Mapping[str, Any]) -> str:
    lemma = _optional_text(token, "lemma")
    if lemma:
        return lemma
    lower = _optional_text(token, "lower")
    if lower:
        return lower
    return _token_text(token).lower()


def _doc_token_lemma(token: Token) -> str:
    if token.lemma_:
        return token.lemma_
    return token.text.lower()


def _require_text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _require_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _require_list(record: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = record.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be a list of objects")
    return value


def _optional_text(record: Mapping[str, Any], key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) else None


def _optional_int(record: Mapping[str, Any], key: str) -> int | None:
    value = record.get(key)
    return value if isinstance(value, int) else None
