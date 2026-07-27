from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
import csv
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


ATTRIBUTE_UNIT_SINGLE_TOKEN = "single_token"
ATTRIBUTE_UNIT_MWE = "mwe"
ATTRIBUTE_MWE_RULE_VERSION = "r11.1-attribute-mwe-v2"
ATTRIBUTE_MODIFIER_DEPS = frozenset(("amod", "compound", "nmod"))
ATTRIBUTE_MWE_BOUNDARY_DEPS = frozenset(("cc", "punct"))
ATTRIBUTE_MWE_BOUNDARY_POS = frozenset(("CCONJ", "PUNCT", "SPACE"))
ATTRIBUTE_MWE_SCHEMA_FIELDS = frozenset(
    (
        "attribute_unit_type",
        "span_token_count",
        "anchor_token_offset",
        "lookup_forms",
        "attribute_mwe_rule_version",
    )
)

_SEPARATOR_RE = re.compile(r"[\s_-]+")


@dataclass(frozen=True, slots=True)
class AttributeTokenView:
    i: int
    text: str
    lemma: str
    dep: str
    pos: str
    tag: str
    char_start: int | None = None
    char_end: int | None = None
    is_quantity: bool = False


@dataclass(frozen=True, slots=True)
class AttributeAnchor:
    token_i: int
    conj_head_i: int | None = None


@dataclass(frozen=True, slots=True)
class AttributeMweCandidate:
    tokens: tuple[AttributeTokenView, ...]
    anchor: AttributeAnchor
    surface: str

    @property
    def token_indices(self) -> tuple[int, ...]:
        return tuple(token.i for token in self.tokens)

    @property
    def token_start(self) -> int:
        return self.tokens[0].i

    @property
    def token_end(self) -> int:
        return self.tokens[-1].i + 1

    @property
    def anchor_token_offset(self) -> int:
        return len(self.tokens) - 1


@dataclass(frozen=True, slots=True)
class SelectedAttributeMwe:
    candidate: AttributeMweCandidate
    lookup: Any


class ResolvedAttributeMweIndex:
    def __init__(self, rows_by_form: Mapping[str, Mapping[str, str]]) -> None:
        self._rows_by_form = dict(rows_by_form)

    @classmethod
    def from_tsv(cls, path: str | Path) -> "ResolvedAttributeMweIndex":
        chosen_rows: list[Mapping[str, str]] = []
        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            missing_fields = ATTRIBUTE_MWE_SCHEMA_FIELDS - set(reader.fieldnames or ())
            if missing_fields:
                raise ValueError(
                    "attribute inventory predates the Attribute MWE schema; missing columns: "
                    + ", ".join(sorted(missing_fields))
                )
            for raw_row in reader:
                row = dict(raw_row)
                _validate_inventory_rule_version(row)
                unit_type = inventory_attribute_unit_type(row)
                if unit_type == ATTRIBUTE_UNIT_SINGLE_TOKEN:
                    _validate_single_token_row(row)
                    continue
                if unit_type != ATTRIBUTE_UNIT_MWE:
                    raise ValueError(f"unsupported attribute_unit_type: {unit_type!r}")
                if row.get("decision_status", "").strip() != "chosen":
                    continue
                _validate_mwe_row(row)
                chosen_rows.append(row)

        exact_rows_by_form: dict[str, Mapping[str, str]] = {}
        for row in chosen_rows:
            for form in _inventory_mwe_exact_forms(row):
                _register_mwe_form(
                    exact_rows_by_form,
                    form=form,
                    row=row,
                    form_kind="exact",
                )

        rows_by_form = dict(exact_rows_by_form)
        alias_rows_by_form: dict[str, Mapping[str, str]] = {}
        for row in chosen_rows:
            for form in _inventory_mwe_alias_forms(row):
                key = separator_equivalent_key(form)
                if not key or key in exact_rows_by_form:
                    continue
                _register_mwe_form(
                    alias_rows_by_form,
                    form=form,
                    row=row,
                    form_kind="alias",
                )
                rows_by_form[key] = row
        return cls(rows_by_form)

    def __call__(self, candidate: AttributeMweCandidate) -> Mapping[str, str] | None:
        return self.lookup_surface(candidate.surface)

    def lookup_surface(self, surface: str) -> Mapping[str, str] | None:
        return self._rows_by_form.get(separator_equivalent_key(surface))

    def __len__(self) -> int:
        return len(self._rows_by_form)


def normalize_attribute_surface(value: str) -> str:
    return " ".join(value.strip().lower().split())


def separator_equivalent_key(value: str) -> str:
    return _SEPARATOR_RE.sub(" ", value.strip().lower()).strip()


def separator_variants(words: Sequence[str]) -> tuple[str, ...]:
    normalized_words = [
        normalize_attribute_surface(word)
        for word in words
        if normalize_attribute_surface(word)
    ]
    if not normalized_words:
        return ()
    variants: list[str] = []
    for separator in (" ", "-", "_"):
        value = separator.join(normalized_words)
        if value not in variants:
            variants.append(value)
    return tuple(variants)


def collect_attribute_anchors(
    tokens: Sequence[AttributeTokenView],
    *,
    children_by_head: Mapping[int, Sequence[AttributeTokenView]],
    excluded_token_indices: set[int],
) -> tuple[AttributeAnchor, ...]:
    token_indices = {token.i for token in tokens}
    anchors: list[AttributeAnchor] = []
    emitted: set[int] = set()

    def append(token_i: int, conj_head_i: int | None = None) -> None:
        if token_i in emitted:
            return
        emitted.add(token_i)
        anchors.append(AttributeAnchor(token_i=token_i, conj_head_i=conj_head_i))

    for token in tokens:
        if token.i in excluded_token_indices or token.is_quantity:
            continue
        if token.dep not in ATTRIBUTE_MODIFIER_DEPS:
            continue
        append(token.i)
        seen = {token.i}
        pending: deque[AttributeTokenView] = deque((token,))
        while pending:
            head = pending.popleft()
            for child in children_by_head.get(head.i, ()):
                if child.i in seen:
                    continue
                if child.i not in token_indices or child.i in excluded_token_indices:
                    continue
                if child.dep != "conj" or child.is_quantity:
                    continue
                seen.add(child.i)
                append(child.i, head.i)
                pending.append(child)
    return tuple(anchors)


def select_attribute_mwes(
    tokens: Sequence[AttributeTokenView],
    *,
    anchors: Sequence[AttributeAnchor],
    excluded_token_indices: set[int],
    lookup: Callable[[AttributeMweCandidate], Any | None],
) -> tuple[SelectedAttributeMwe, ...]:
    ordered = tuple(sorted(tokens, key=lambda token: token.i))
    position_by_i = {token.i: index for index, token in enumerate(ordered)}
    valid: list[SelectedAttributeMwe] = []
    seen_candidates: set[tuple[int, ...]] = set()

    for anchor in anchors:
        anchor_position = position_by_i.get(anchor.token_i)
        if anchor_position is None:
            continue
        anchor_token = ordered[anchor_position]
        if _is_mwe_boundary(anchor_token, excluded_token_indices):
            continue
        for start_position in range(anchor_position - 1, -1, -1):
            start_token = ordered[start_position]
            if _is_mwe_boundary(start_token, excluded_token_indices):
                break
            span_tokens = ordered[start_position : anchor_position + 1]
            if not _has_contiguous_indices(span_tokens):
                break
            token_indices = tuple(token.i for token in span_tokens)
            if token_indices in seen_candidates:
                continue
            seen_candidates.add(token_indices)
            candidate = AttributeMweCandidate(
                tokens=span_tokens,
                anchor=anchor,
                surface=" ".join(token.text for token in span_tokens),
            )
            match = lookup(candidate)
            if match is not None:
                valid.append(SelectedAttributeMwe(candidate=candidate, lookup=match))

    selected: list[SelectedAttributeMwe] = []
    occupied: set[int] = set()
    for match in sorted(
        valid,
        key=lambda item: (
            -len(item.candidate.tokens),
            -item.candidate.anchor.token_i,
            item.candidate.token_start,
        ),
    ):
        token_indices = set(match.candidate.token_indices)
        if token_indices & occupied:
            continue
        selected.append(match)
        occupied.update(token_indices)
    selected.sort(key=lambda item: item.candidate.token_start)
    return tuple(selected)


def candidate_lookup_forms(
    candidate: AttributeMweCandidate,
    *,
    anchor_lemmas: Sequence[str] = (),
) -> tuple[tuple[str, str], ...]:
    forms: list[tuple[str, str]] = []
    seen: set[str] = set()
    raw_words = [token.text for token in candidate.tokens]

    def append(case: str, query: str) -> None:
        normalized = normalize_attribute_surface(query)
        if normalized and normalized not in seen:
            seen.add(normalized)
            forms.append((case, normalized))

    for index, query in enumerate(separator_variants(raw_words)):
        append(
            ("mwe_exact" if index == 0 else ("mwe_hyphen_variant" if index == 1 else "mwe_underscore_variant")),
            query,
        )
    prefix_words = raw_words[:-1]
    for anchor_lemma in anchor_lemmas:
        words = [*prefix_words, anchor_lemma]
        for index, query in enumerate(separator_variants(words)):
            suffix = ("space", "hyphen", "underscore")[index]
            append(f"mwe_anchor_morphy_{suffix}", query)
    return tuple(forms)


def inventory_attribute_unit_type(row: Mapping[str, str]) -> str:
    value = row.get("attribute_unit_type", "").strip()
    return value or ATTRIBUTE_UNIT_SINGLE_TOKEN


def inventory_attribute_key(row: Mapping[str, str]) -> tuple[str, str]:
    span_key = row.get("span_key", "") or row.get("observed_surface", "")
    return inventory_attribute_unit_type(row), normalize_attribute_surface(span_key)


def _inventory_mwe_exact_forms(row: Mapping[str, str]) -> tuple[str, ...]:
    forms: list[str] = []
    for field in ("span_key", "observed_surface", "example_surfaces"):
        for value in str(row.get(field, "")).split("|"):
            normalized = normalize_attribute_surface(value)
            if normalized and normalized not in forms:
                forms.append(normalized)
    return tuple(forms)


def _inventory_mwe_alias_forms(row: Mapping[str, str]) -> tuple[str, ...]:
    forms: list[str] = []
    for value in str(row.get("lookup_forms", "")).split("|"):
        normalized = normalize_attribute_surface(value)
        if normalized and normalized not in forms:
            forms.append(normalized)
    return tuple(forms)


def _register_mwe_form(
    rows_by_form: dict[str, Mapping[str, str]],
    *,
    form: str,
    row: Mapping[str, str],
    form_kind: str,
) -> None:
    key = separator_equivalent_key(form)
    if not key:
        return
    existing = rows_by_form.get(key)
    if existing is not None and _mwe_identity(existing) != _mwe_identity(row):
        raise ValueError(
            f"conflicting attribute MWE inventory {form_kind} forms: "
            f"{form!r} -> {_mwe_identity(existing)!r} / {_mwe_identity(row)!r}"
        )
    rows_by_form[key] = row


def _validate_mwe_row(row: Mapping[str, str]) -> None:
    span_key = normalize_attribute_surface(row.get("span_key", ""))
    if not span_key:
        raise ValueError("attribute MWE inventory row is missing span_key")
    try:
        span_token_count = int(row.get("span_token_count", "") or 0)
        anchor_token_offset = int(row.get("anchor_token_offset", "") or -1)
    except ValueError as exc:
        raise ValueError(f"invalid attribute MWE structure for {span_key}") from exc
    if span_token_count < 2:
        raise ValueError(f"attribute MWE must contain at least two Stage 3 tokens: {span_key}")
    if anchor_token_offset < 1 or anchor_token_offset >= span_token_count:
        raise ValueError(f"invalid attribute MWE anchor offset for {span_key}")
    _validate_inventory_rule_version(row)


def _mwe_identity(row: Mapping[str, str]) -> tuple[str, str]:
    return (
        normalize_attribute_surface(row.get("span_key", "")),
        normalize_attribute_surface(row.get("canonical_surface", "")),
    )


def _validate_single_token_row(row: Mapping[str, str]) -> None:
    span_key = normalize_attribute_surface(row.get("span_key", ""))
    try:
        span_token_count = int(row.get("span_token_count", "") or 0)
        anchor_token_offset = int(row.get("anchor_token_offset", "") or -1)
    except ValueError as exc:
        raise ValueError(f"invalid single-token attribute structure for {span_key}") from exc
    if span_token_count != 1 or anchor_token_offset != 0:
        raise ValueError(f"invalid single-token attribute structure for {span_key}")


def _validate_inventory_rule_version(row: Mapping[str, str]) -> None:
    version = row.get("attribute_mwe_rule_version", "")
    if version != ATTRIBUTE_MWE_RULE_VERSION:
        raise ValueError(
            "attribute inventory rule version mismatch: "
            f"expected={ATTRIBUTE_MWE_RULE_VERSION!r} actual={version!r} "
            f"span_key={row.get('span_key', '')!r}"
        )


def _is_mwe_boundary(token: AttributeTokenView, excluded_token_indices: set[int]) -> bool:
    return (
        token.i in excluded_token_indices
        or token.is_quantity
        or token.dep in ATTRIBUTE_MWE_BOUNDARY_DEPS
        or token.pos in ATTRIBUTE_MWE_BOUNDARY_POS
    )


def _has_contiguous_indices(tokens: Sequence[AttributeTokenView]) -> bool:
    return all(right.i == left.i + 1 for left, right in zip(tokens, tokens[1:]))
