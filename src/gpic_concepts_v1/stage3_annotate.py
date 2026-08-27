"""Stage 3 spaCy linguistic annotation.

Stage 3 runs the documented spaCy model over Stage 2 protected sentence Docs
and records linguistic evidence. It does not create concepts.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.io_jsonl import iter_jsonl, write_jsonl
from gpic_concepts_v1.runtime_memory import ProgressWriter, memory_config_from_kwargs
from gpic_concepts_v1.schema import JsonObject, JsonRecord, PIPELINE_VERSION
from gpic_concepts_v1.stage1 import make_caption_record_from_gpic_row
from gpic_concepts_v1.stage2_preprocess import (
    ProtectedSpanRecord,
    Stage2DependencyError,
    Stage2InputError,
    protect_doc,
    spacy,
)

try:  # pragma: no cover - exercised when spaCy is installed.
    from spacy.language import Language
    from spacy.tokens import Doc, Token
except ModuleNotFoundError:  # pragma: no cover - keeps non-spaCy tests importable.
    Language = Any  # type: ignore[misc,assignment]
    Doc = Any  # type: ignore[misc,assignment]
    Token = Any  # type: ignore[misc,assignment]


DEFAULT_STAGE3_MODEL = "en_core_web_trf"
DEFAULT_STAGE3_BATCH_SIZE = 128
DEFAULT_STAGE3_DISABLED_COMPONENTS = ("ner",)

TAG_RULE_ID = "R6"
PARSER_RULE_ID = "R8"
POS_MORPH_RULE_ID = "R9"
LEMMA_RULE_ID = "R10"
NOUN_CHUNK_RULE_ID = "R11"


class Stage3DependencyError(RuntimeError):
    """Raised when the configured spaCy model cannot be loaded."""


@dataclass(slots=True)
class Stage3Record(JsonRecord):
    caption_id: str
    caption: str
    model: str
    tokens: list[JsonObject]
    sentences: list[JsonObject]
    noun_chunks: list[JsonObject]
    protected_spans: list[JsonObject]
    tag_segments: list[JsonObject] = field(default_factory=list)
    stage: int = 3
    pipeline_version: str = PIPELINE_VERSION
    rule_ids: list[str] = field(default_factory=list)
    meta: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class _PreparedStage3Doc:
    caption_id: str
    caption: str
    doc: Doc
    protected_spans: list[ProtectedSpanRecord]
    stage2_rule_ids: list[str]
    meta: JsonObject


@dataclass(slots=True)
class _TagSegment:
    segment_id: str
    raw_text: str
    text: str
    char_start: int
    char_end: int


@dataclass(slots=True)
class _PreparedTagSegmentDoc:
    segment: _TagSegment
    doc: Doc
    protected_spans: list[ProtectedSpanRecord]
    stage2_rule_ids: list[str]


@dataclass(slots=True)
class AnnotatedStage3Doc:
    """Annotated spaCy Doc plus the Stage 1/2 metadata needed by fast runners."""

    caption_id: str
    caption: str
    doc: Doc
    protected_spans: list[ProtectedSpanRecord]
    rule_ids: list[str]
    meta: JsonObject


@dataclass(slots=True)
class Stage3Timing:
    """Benchmark-only timing accumulator for Stage 2 preparation and Stage 3."""

    stage2_seconds: float = 0.0
    stage3_seconds: float = 0.0
    stage2_doc_count: int = 0
    stage3_doc_count: int = 0
    stage3_batch_count: int = 0


def make_stage3_nlp(
    model: str = DEFAULT_STAGE3_MODEL,
    *,
    gpu_mode: str = "none",
    disabled_components: Sequence[str] | None = None,
) -> Language:
    """Load the Stage 3 spaCy model."""
    if spacy is None:
        raise Stage2DependencyError(
            "Stage 3 requires spaCy. Install/use the project environment."
        )
    gpu_info = _configure_spacy_gpu(gpu_mode)
    disabled = normalize_stage3_disabled_components(disabled_components)
    try:
        nlp = spacy.load(model, disable=list(disabled))
    except OSError as exc:
        raise Stage3DependencyError(
            f"Could not load spaCy model {model!r}. Run scripts/setup_env.ps1."
        ) from exc

    nlp.meta["gpic_model_id"] = model
    nlp.meta["gpic_gpu_mode"] = gpu_info["gpu_mode"]
    nlp.meta["gpic_gpu_enabled"] = gpu_info["gpu_enabled"]
    nlp.meta["gpic_disabled_components"] = list(disabled)
    nlp.meta["gpic_enabled_components"] = list(nlp.pipe_names)
    return nlp


def normalize_stage3_disabled_components(
    disabled_components: Sequence[str] | str | None = None,
) -> tuple[str, ...]:
    """Return the normalized spaCy components disabled for Stage 3.

    The default intentionally keeps the historical behavior: NER is disabled
    because Stage 3 records POS, morphology, dependency, lemma, and noun chunks,
    but it does not consume entity spans.
    """
    if disabled_components is None:
        values: Sequence[str] = DEFAULT_STAGE3_DISABLED_COMPONENTS
    elif isinstance(disabled_components, str):
        values = disabled_components.split(",")
    else:
        values = disabled_components
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return tuple(normalized)


def annotate_gpic_sentence_row(
    row: Mapping[str, Any],
    *,
    nlp: Language,
) -> Stage3Record:
    """Annotate one confirmed sentence GPIC row."""
    caption_record = make_caption_record_from_gpic_row(row)
    if caption_record.skipped or caption_record.caption_shape != "sentence":
        raise Stage2InputError("Stage 3 only accepts sentence captions")
    return annotate_text(
        caption_id=caption_record.caption_id,
        caption=caption_record.caption,
        nlp=nlp,
        meta=caption_record.meta,
    )


def annotate_gpic_tag_list_row(
    row: Mapping[str, Any],
    *,
    nlp: Language,
) -> Stage3Record:
    """Annotate one confirmed tag-list GPIC row by comma segment."""
    caption_record = make_caption_record_from_gpic_row(row)
    if caption_record.skipped or caption_record.caption_shape != "tag_list":
        raise Stage2InputError("Stage 3 tag-list annotation only accepts tag-list captions")
    return annotate_tag_list_text(
        caption_id=caption_record.caption_id,
        caption=caption_record.caption,
        nlp=nlp,
        meta=caption_record.meta,
    )


def annotate_text(
    *,
    caption_id: str,
    caption: str,
    nlp: Language,
    meta: Mapping[str, Any] | None = None,
) -> Stage3Record:
    """Apply Stage 2 protection and Stage 3 annotation to one sentence caption."""
    prepared = _prepare_stage3_doc(
        caption_id=caption_id,
        caption=caption,
        nlp=nlp,
        meta=meta,
    )
    doc = _run_pipeline_components(nlp, prepared.doc)
    return _stage3_record_from_doc(prepared, doc, nlp=nlp)


def annotate_tag_list_text(
    *,
    caption_id: str,
    caption: str,
    nlp: Language,
    meta: Mapping[str, Any] | None = None,
) -> Stage3Record:
    """Annotate a tag-list caption segment by segment."""
    prepared_segments = _prepare_tag_segment_docs(caption=caption, nlp=nlp)
    docs = [prepared.doc for prepared in prepared_segments]
    annotated_docs = list(nlp.pipe(docs, batch_size=max(1, min(len(docs), 32)))) if docs else []
    return _stage3_record_from_tag_segments(
        caption_id=caption_id,
        caption=caption,
        prepared_segments=prepared_segments,
        docs=annotated_docs,
        nlp=nlp,
        meta=meta,
    )


def run_stage3_annotate(
    input_path: str | Path,
    *,
    output_path: str | Path,
    summary_path: str | Path | None = None,
    model: str = DEFAULT_STAGE3_MODEL,
    limit: int | None = None,
    batch_size: int = DEFAULT_STAGE3_BATCH_SIZE,
    gpu_mode: str = "none",
    disabled_components: Sequence[str] | str | None = None,
    caption_shape: str = "sentence",
    progress_output: str | Path | None = None,
    progress_interval_records: int = 5000,
    memory_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run Stage 3 over Stage 1 sentence rows or tag-list rows."""
    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")
    if caption_shape not in {"sentence", "tag_list"}:
        raise ValueError("caption_shape must be one of: sentence, tag_list")
    if progress_interval_records < 1:
        raise ValueError("progress_interval_records must be greater than zero")
    started = perf_counter()
    memory_guard = ProgressWriter(None, stage_name="stage3",
                                  memory_config=memory_config_from_kwargs(memory_kwargs))
    memory_guard.check_memory(phase="model_load", force=True)
    progress_path = Path(progress_output) if progress_output is not None else None
    _write_stage3_progress(
        progress_path,
        status="running",
        phase="model_load",
        input_path=str(input_path),
        output_path=str(output_path),
        caption_shape=caption_shape,
        total=0,
        elapsed_seconds=round(perf_counter() - started, 3),
    )
    model_load_start = perf_counter()
    nlp = make_stage3_nlp(
        model,
        gpu_mode=gpu_mode,
        disabled_components=disabled_components,
    )
    model_load_seconds = perf_counter() - model_load_start
    memory_guard.check_memory(phase="model_loaded", force=True)

    span_counts: Counter[str] = Counter()
    token_total = 0
    noun_chunk_total = 0
    tag_segment_total = 0
    total = 0
    timing = Stage3Timing()
    annotation_start = perf_counter()
    _write_stage3_progress(
        progress_path,
        status="running",
        phase="annotation",
        input_path=str(input_path),
        output_path=str(output_path),
        caption_shape=caption_shape,
        model=model,
        batch_size=batch_size,
        gpu_mode=nlp.meta.get("gpic_gpu_mode", gpu_mode),
        gpu_enabled=bool(nlp.meta.get("gpic_gpu_enabled", False)),
        disabled_components=nlp.meta.get("gpic_disabled_components", []),
        enabled_components=nlp.meta.get("gpic_enabled_components", []),
        total=0,
        elapsed_seconds=round(perf_counter() - started, 3),
    )

    def iter_records() -> Any:
        nonlocal noun_chunk_total, tag_segment_total, token_total, total
        if caption_shape == "tag_list":
            records = iter_stage3_tag_list_records_from_rows(
                iter_jsonl(input_path),
                nlp=nlp,
                limit=limit,
            )
        else:
            records = iter_stage3_records_from_rows(
                iter_jsonl(input_path),
                nlp=nlp,
                batch_size=batch_size,
                limit=limit,
                timing=timing,
            )
        for record in records:
            memory_guard.check_memory(phase="annotation")
            total += 1
            token_total += len(record.tokens)
            noun_chunk_total += len(record.noun_chunks)
            tag_segment_total += len(record.tag_segments)
            for span in record.protected_spans:
                kind = span.get("kind")
                if isinstance(kind, str):
                    span_counts[kind] += 1
            if total == 1 or total % progress_interval_records == 0:
                _write_stage3_progress(
                    progress_path,
                    status="running",
                    phase="annotation",
                    input_path=str(input_path),
                    output_path=str(output_path),
                    caption_shape=caption_shape,
                    model=model,
                    batch_size=batch_size,
                    gpu_mode=nlp.meta.get("gpic_gpu_mode", gpu_mode),
                    gpu_enabled=bool(nlp.meta.get("gpic_gpu_enabled", False)),
                    disabled_components=nlp.meta.get("gpic_disabled_components", []),
                    enabled_components=nlp.meta.get("gpic_enabled_components", []),
                    total=total,
                    token_total=token_total,
                    noun_chunk_total=noun_chunk_total,
                    tag_segment_total=tag_segment_total,
                    elapsed_seconds=round(perf_counter() - started, 3),
                )
            yield record

    written = write_jsonl(output_path, iter_records(), sort_keys=False, compact=True)
    annotation_and_write_seconds = perf_counter() - annotation_start
    total_seconds = perf_counter() - started
    record_build_json_write_seconds = max(
        0.0,
        annotation_and_write_seconds - timing.stage2_seconds - timing.stage3_seconds,
    )
    summary = {
        "total": total,
        "written": written,
        "max_rss_gib": memory_guard.memory_config.effective_max_rss_gib,
        "model": model,
        "batch_size": batch_size,
        "caption_shape": caption_shape,
        "gpu_mode": nlp.meta.get("gpic_gpu_mode", gpu_mode),
        "gpu_enabled": bool(nlp.meta.get("gpic_gpu_enabled", False)),
        "disabled_components": list(nlp.meta.get("gpic_disabled_components", [])),
        "enabled_components": list(nlp.meta.get("gpic_enabled_components", [])),
        "output_path": str(output_path),
        "token_total": token_total,
        "noun_chunk_total": noun_chunk_total,
        "tag_segment_total": tag_segment_total,
        "protected_span_counts": dict(sorted(span_counts.items())),
        "timing_seconds": {
            "total": round(total_seconds, 6),
            "model_load": round(model_load_seconds, 6),
            "annotation_and_write": round(annotation_and_write_seconds, 6),
            "stage2_prepare": round(timing.stage2_seconds, 6),
            "spacy_pipe": round(timing.stage3_seconds, 6),
            "record_build_json_write_overhead": round(record_build_json_write_seconds, 6),
        },
        "timing_counts": {
            "stage2_doc_count": timing.stage2_doc_count,
            "stage3_doc_count": timing.stage3_doc_count,
            "stage3_batch_count": timing.stage3_batch_count,
            "profile_scope": (
                "sentence_stage2_spacy_record_write"
                if caption_shape == "sentence"
                else "tag_list_total_only"
            ),
        },
    }
    if summary_path is not None:
        write_jsonl(summary_path, [summary])
    _write_stage3_progress(
        progress_path,
        status="complete",
        phase="complete",
        input_path=str(input_path),
        output_path=str(output_path),
        caption_shape=caption_shape,
        summary=summary,
        total=total,
        elapsed_seconds=round(perf_counter() - started, 3),
    )
    return summary


def _write_stage3_progress(
    path: Path | None,
    *,
    status: str,
    phase: str,
    **payload: Any,
) -> None:
    if path is None:
        return
    progress = {
        "schema_version": 1,
        "artifact_type": "stage3_annotation_progress",
        "status": status,
        "phase": phase,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    with atomic_text_writer(path) as handle:
        handle.write(json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")


def iter_stage3_records_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    nlp: Language,
    batch_size: int = DEFAULT_STAGE3_BATCH_SIZE,
    limit: int | None = None,
    timing: Stage3Timing | None = None,
) -> Iterator[Stage3Record]:
    """Yield Stage 3 records from Stage 1 sentence rows using batched nlp.pipe."""
    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")
    pending: list[_PreparedStage3Doc] = []

    def flush_pending() -> Iterator[Stage3Record]:
        docs = [item.doc for item in pending]
        if timing is None:
            for prepared, doc in zip(
                pending,
                nlp.pipe(docs, batch_size=batch_size),
                strict=True,
            ):
                yield _stage3_record_from_doc(prepared, doc, nlp=nlp)
            return

        stage3_start = perf_counter()
        annotated_docs = list(nlp.pipe(docs, batch_size=batch_size))
        timing.stage3_seconds += perf_counter() - stage3_start
        timing.stage3_doc_count += len(annotated_docs)
        timing.stage3_batch_count += 1
        for prepared, doc in zip(pending, annotated_docs, strict=True):
            yield _stage3_record_from_doc(prepared, doc, nlp=nlp)

    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            break
        caption_record = make_caption_record_from_gpic_row(row)
        if caption_record.skipped or caption_record.caption_shape != "sentence":
            raise Stage2InputError("Stage 3 only accepts sentence captions")
        stage2_start = perf_counter()
        prepared = _prepare_stage3_doc(
            caption_id=caption_record.caption_id,
            caption=caption_record.caption,
            nlp=nlp,
            meta=caption_record.meta,
        )
        if timing is not None:
            timing.stage2_seconds += perf_counter() - stage2_start
            timing.stage2_doc_count += 1
        pending.append(prepared)
        if len(pending) >= batch_size:
            yield from flush_pending()
            pending.clear()

    if pending:
        yield from flush_pending()


def iter_stage3_tag_list_records_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    nlp: Language,
    limit: int | None = None,
) -> Iterator[Stage3Record]:
    """Yield Stage 3 records from Stage 1 tag-list rows."""
    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            break
        yield annotate_gpic_tag_list_row(row, nlp=nlp)


def iter_annotated_docs_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    nlp: Language,
    batch_size: int = DEFAULT_STAGE3_BATCH_SIZE,
    limit: int | None = None,
    timing: Stage3Timing | None = None,
) -> Iterator[AnnotatedStage3Doc]:
    """Yield annotated spaCy Docs without serializing the Stage 3 evidence table."""
    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")
    pending: list[_PreparedStage3Doc] = []

    def flush_pending() -> Iterator[AnnotatedStage3Doc]:
        docs = [item.doc for item in pending]
        if timing is None:
            for prepared, doc in zip(
                pending,
                nlp.pipe(docs, batch_size=batch_size),
                strict=True,
            ):
                yield AnnotatedStage3Doc(
                    caption_id=prepared.caption_id,
                    caption=prepared.caption,
                    doc=doc,
                    protected_spans=prepared.protected_spans,
                    rule_ids=prepared.stage2_rule_ids + _stage3_rule_ids(prepared.protected_spans),
                    meta=dict(prepared.meta),
                )
            return

        stage3_start = perf_counter()
        annotated_docs = list(nlp.pipe(docs, batch_size=batch_size))
        timing.stage3_seconds += perf_counter() - stage3_start
        timing.stage3_doc_count += len(annotated_docs)
        timing.stage3_batch_count += 1
        for prepared, doc in zip(pending, annotated_docs, strict=True):
            yield AnnotatedStage3Doc(
                caption_id=prepared.caption_id,
                caption=prepared.caption,
                doc=doc,
                protected_spans=prepared.protected_spans,
                rule_ids=prepared.stage2_rule_ids + _stage3_rule_ids(prepared.protected_spans),
                meta=dict(prepared.meta),
            )

    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            break
        caption_record = make_caption_record_from_gpic_row(row)
        if caption_record.skipped or caption_record.caption_shape != "sentence":
            raise Stage2InputError("Stage 3 only accepts sentence captions")
        stage2_start = perf_counter()
        prepared = _prepare_stage3_doc(
            caption_id=caption_record.caption_id,
            caption=caption_record.caption,
            nlp=nlp,
            meta=caption_record.meta,
        )
        if timing is not None:
            timing.stage2_seconds += perf_counter() - stage2_start
            timing.stage2_doc_count += 1
        pending.append(prepared)
        if len(pending) >= batch_size:
            yield from flush_pending()
            pending.clear()

    if pending:
        yield from flush_pending()


def _configure_spacy_gpu(gpu_mode: str) -> JsonObject:
    normalized = gpu_mode.lower()
    if normalized not in {"none", "prefer", "require"}:
        raise ValueError("gpu_mode must be one of: none, prefer, require")
    if normalized == "none":
        return {"gpu_mode": "none", "gpu_enabled": False}
    if spacy is None:
        raise Stage2DependencyError(
            "Stage 3 requires spaCy. Install/use the project environment."
        )
    if normalized == "prefer":
        return {"gpu_mode": "prefer", "gpu_enabled": bool(spacy.prefer_gpu())}
    try:
        spacy.require_gpu()
    except Exception as exc:  # pragma: no cover - depends on local CUDA/CuPy setup.
        raise Stage3DependencyError(
            "Could not activate spaCy GPU. Install a CuPy build matching CUDA, "
            "or rerun without --require-gpu."
        ) from exc
    return {"gpu_mode": "require", "gpu_enabled": True}


def _prepare_stage3_doc(
    *,
    caption_id: str,
    caption: str,
    nlp: Language,
    meta: Mapping[str, Any] | None = None,
) -> _PreparedStage3Doc:
    doc = nlp.make_doc(caption)
    doc, protected_spans, stage2_rule_ids = protect_doc(
        doc,
        nlp=nlp,
    )
    return _PreparedStage3Doc(
        caption_id=caption_id,
        caption=caption,
        doc=doc,
        protected_spans=protected_spans,
        stage2_rule_ids=stage2_rule_ids,
        meta=dict(meta or {}),
    )


def _prepare_tag_segment_docs(
    *,
    caption: str,
    nlp: Language,
) -> list[_PreparedTagSegmentDoc]:
    prepared: list[_PreparedTagSegmentDoc] = []
    for segment in _split_tag_segments(caption):
        doc = nlp.make_doc(segment.text)
        doc, protected_spans, stage2_rule_ids = protect_doc(
            doc,
            nlp=nlp,
        )
        prepared.append(
            _PreparedTagSegmentDoc(
                segment=segment,
                doc=doc,
                protected_spans=protected_spans,
                stage2_rule_ids=stage2_rule_ids,
            )
        )
    return prepared


def _split_tag_segments(caption: str) -> list[_TagSegment]:
    segments: list[_TagSegment] = []
    raw_start = 0
    for index, raw_segment in enumerate(caption.split(",")):
        raw_end = raw_start + len(raw_segment)
        left_trimmed = raw_segment.lstrip()
        right_trimmed = raw_segment.rstrip()
        text = raw_segment.strip()
        if text:
            char_start = raw_start + (len(raw_segment) - len(left_trimmed))
            char_end = raw_start + len(right_trimmed)
            segments.append(
                _TagSegment(
                    segment_id=f"t{index}",
                    raw_text=raw_segment,
                    text=text,
                    char_start=char_start,
                    char_end=char_end,
                )
            )
        raw_start = raw_end + 1
    return segments


def _stage3_record_from_doc(
    prepared: _PreparedStage3Doc,
    doc: Doc,
    *,
    nlp: Language,
) -> Stage3Record:
    stage3_rule_ids = _stage3_rule_ids(prepared.protected_spans)
    return Stage3Record(
        caption_id=prepared.caption_id,
        caption=prepared.caption,
        model=nlp.meta.get("gpic_model_id", DEFAULT_STAGE3_MODEL),
        tokens=[_token_to_dict(token) for token in doc],
        sentences=[_sentence_to_dict(sent) for sent in doc.sents],
        noun_chunks=[_noun_chunk_to_dict(chunk) for chunk in doc.noun_chunks],
        protected_spans=[record.to_dict() for record in prepared.protected_spans],
        rule_ids=prepared.stage2_rule_ids + stage3_rule_ids,
        meta=dict(prepared.meta),
    )


def _stage3_record_from_tag_segments(
    *,
    caption_id: str,
    caption: str,
    prepared_segments: Sequence[_PreparedTagSegmentDoc],
    docs: Sequence[Doc],
    nlp: Language,
    meta: Mapping[str, Any] | None = None,
) -> Stage3Record:
    tokens: list[JsonObject] = []
    sentences: list[JsonObject] = []
    noun_chunks: list[JsonObject] = []
    protected_spans: list[JsonObject] = []
    tag_segments: list[JsonObject] = []
    token_offset = 0
    rule_ids: list[str] = []
    seen_rule_ids: set[str] = set()

    def add_rule_ids(values: Sequence[str]) -> None:
        for value in values:
            if value not in seen_rule_ids:
                rule_ids.append(value)
                seen_rule_ids.add(value)

    for prepared, doc in zip(prepared_segments, docs, strict=True):
        segment = prepared.segment
        add_rule_ids(prepared.stage2_rule_ids + _stage3_rule_ids(prepared.protected_spans))
        segment_tokens = [
            _token_to_dict_with_offsets(
                token,
                token_offset=token_offset,
                char_offset=segment.char_start,
                segment_id=segment.segment_id,
            )
            for token in doc
        ]
        segment_chunks = [
            _noun_chunk_to_dict_with_offsets(
                chunk,
                token_offset=token_offset,
                char_offset=segment.char_start,
                segment_id=segment.segment_id,
            )
            for chunk in doc.noun_chunks
        ]
        segment_sentences = [
            _sentence_to_dict_with_offsets(
                sent,
                token_offset=token_offset,
                char_offset=segment.char_start,
            )
            for sent in doc.sents
        ]
        segment_protected_spans = [
            _protected_span_to_dict_with_offsets(
                span,
                token_offset=token_offset,
                char_offset=segment.char_start,
                segment_id=segment.segment_id,
            )
            for span in prepared.protected_spans
        ]
        tokens.extend(segment_tokens)
        noun_chunks.extend(segment_chunks)
        sentences.extend(segment_sentences)
        protected_spans.extend(segment_protected_spans)
        tag_segments.append(
            {
                "segment_id": segment.segment_id,
                "raw_text": segment.raw_text,
                "text": segment.text,
                "char_start": segment.char_start,
                "char_end": segment.char_end,
                "token_start": token_offset,
                "token_end": token_offset + len(doc),
                "tokens": segment_tokens,
                "noun_chunks": segment_chunks,
            }
        )
        token_offset += len(doc)

    record_meta = dict(meta or {})
    record_meta["caption_shape"] = "tag_list"
    return Stage3Record(
        caption_id=caption_id,
        caption=caption,
        model=nlp.meta.get("gpic_model_id", DEFAULT_STAGE3_MODEL),
        tokens=tokens,
        sentences=sentences,
        noun_chunks=noun_chunks,
        protected_spans=protected_spans,
        tag_segments=tag_segments,
        rule_ids=rule_ids,
        meta=record_meta,
    )


def _run_pipeline_components(nlp: Language, doc: Doc) -> Doc:
    for _name, component in nlp.pipeline:
        doc = component(doc)
    return doc


def _stage3_rule_ids(protected_spans: Sequence[ProtectedSpanRecord]) -> list[str]:
    _ = protected_spans
    return [TAG_RULE_ID, PARSER_RULE_ID, POS_MORPH_RULE_ID, LEMMA_RULE_ID, NOUN_CHUNK_RULE_ID]


def _token_to_dict(token: Token) -> JsonObject:
    return {
        "i": token.i,
        "text": token.text,
        "lemma": token.lemma_,
        "pos": token.pos_,
        "tag": token.tag_,
        "morph": str(token.morph),
        "dep": token.dep_,
        "head_i": token.head.i,
        "head_text": token.head.text,
        "char_start": token.idx,
        "char_end": token.idx + len(token.text),
        "whitespace": token.whitespace_,
    }


def _token_to_dict_with_offsets(
    token: Token,
    *,
    token_offset: int,
    char_offset: int,
    segment_id: str,
) -> JsonObject:
    record = _token_to_dict(token)
    record["i"] = token.i + token_offset
    record["head_i"] = token.head.i + token_offset
    record["char_start"] = token.idx + char_offset
    record["char_end"] = token.idx + char_offset + len(token.text)
    record["segment_id"] = segment_id
    return record


def _sentence_to_dict(sent: Any) -> JsonObject:
    return {
        "text": sent.text,
        "token_start": sent.start,
        "token_end": sent.end,
        "char_start": sent.start_char,
        "char_end": sent.end_char,
    }


def _sentence_to_dict_with_offsets(
    sent: Any,
    *,
    token_offset: int,
    char_offset: int,
) -> JsonObject:
    return {
        "text": sent.text,
        "token_start": sent.start + token_offset,
        "token_end": sent.end + token_offset,
        "char_start": sent.start_char + char_offset,
        "char_end": sent.end_char + char_offset,
    }


def _noun_chunk_to_dict(chunk: Any) -> JsonObject:
    return {
        "text": chunk.text,
        "root_i": chunk.root.i,
        "root_text": chunk.root.text,
        "root_lemma": chunk.root.lemma_,
        "root_pos": chunk.root.pos_,
        "root_tag": chunk.root.tag_,
        "root_dep": chunk.root.dep_,
        "root_head_i": chunk.root.head.i,
        "root_head_text": chunk.root.head.text,
        "token_start": chunk.start,
        "token_end": chunk.end,
        "char_start": chunk.start_char,
        "char_end": chunk.end_char,
    }


def _noun_chunk_to_dict_with_offsets(
    chunk: Any,
    *,
    token_offset: int,
    char_offset: int,
    segment_id: str,
) -> JsonObject:
    record = _noun_chunk_to_dict(chunk)
    record["root_i"] = chunk.root.i + token_offset
    record["root_head_i"] = chunk.root.head.i + token_offset
    record["token_start"] = chunk.start + token_offset
    record["token_end"] = chunk.end + token_offset
    record["char_start"] = chunk.start_char + char_offset
    record["char_end"] = chunk.end_char + char_offset
    record["segment_id"] = segment_id
    return record


def _protected_span_to_dict_with_offsets(
    span: ProtectedSpanRecord,
    *,
    token_offset: int,
    char_offset: int,
    segment_id: str,
) -> JsonObject:
    record = span.to_dict()
    record["char_start"] = span.char_start + char_offset
    record["char_end"] = span.char_end + char_offset
    if span.token_start is not None:
        record["token_start"] = span.token_start + token_offset
    if span.token_end is not None:
        record["token_end"] = span.token_end + token_offset
    record["segment_id"] = segment_id
    return record
