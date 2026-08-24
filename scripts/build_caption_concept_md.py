from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from gpic_concepts_v1.stage1 import caption_id_from_gpic_row


JsonObject = dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a caption-by-caption Markdown report from Stage 3/5/6 outputs.",
    )
    parser.add_argument("--sentence-rows", required=True, help="Stage 1 sentence rows JSONL")
    parser.add_argument("--stage3-records", help="Optional Stage 3 annotation records JSONL")
    parser.add_argument("--canonical-mentions", required=True, help="Stage 5 canonical mentions JSONL")
    parser.add_argument("--canonical-edges", required=True, help="Stage 5 canonical edges JSONL")
    parser.add_argument("--facts", required=True, help="Stage 6 facts JSONL")
    parser.add_argument("--output", required=True, help="Output Markdown file")
    parser.add_argument("--limit", type=int, default=100, help="Number of captions to include")
    parser.add_argument("--start", type=int, default=0, help="Start offset in sentence rows")
    parser.add_argument(
        "--max-object-pairs-per-caption",
        type=int,
        default=40,
        help="Maximum object co-occurrence pair rows shown per caption.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    captions = list(iter_jsonl(args.sentence_rows))[args.start : args.start + args.limit]
    caption_ids = [caption_id(row) for row in captions]
    caption_id_set = set(caption_ids)

    stage3_records = (
        group_by_caption(
            row for row in iter_jsonl(args.stage3_records) if row["caption_id"] in caption_id_set
        )
        if args.stage3_records
        else {}
    )
    mentions = group_by_caption(
        row for row in iter_jsonl(args.canonical_mentions) if row["caption_id"] in caption_id_set
    )
    edges = group_by_caption(
        row for row in iter_jsonl(args.canonical_edges) if row["caption_id"] in caption_id_set
    )
    facts = group_by_caption(
        row for row in iter_jsonl(args.facts) if row["caption_id"] in caption_id_set
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_report(
            captions,
            stage3_records,
            mentions,
            edges,
            facts,
            start=args.start,
            max_object_pairs_per_caption=args.max_object_pairs_per_caption,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "caption_count": len(captions),
                "start": args.start,
                "limit": args.limit,
                "max_object_pairs_per_caption": args.max_object_pairs_per_caption,
                "stage3_records_included": bool(args.stage3_records),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def build_report(
    captions: list[JsonObject],
    stage3_records: dict[str, list[JsonObject]],
    mentions: dict[str, list[JsonObject]],
    edges: dict[str, list[JsonObject]],
    facts: dict[str, list[JsonObject]],
    *,
    start: int,
    max_object_pairs_per_caption: int,
) -> str:
    first_case = start + 1
    last_case = start + len(captions)
    lines: list[str] = [
        f"# Caption To Concept Cases {first_case:04d}-{last_case:04d}",
        "",
        "현재 v1 explainable baseline 결과를 caption별로 펼친 보고서다.",
        "",
        "- Stage 3: token, POS, TAG, MORPH, lemma, dependency, noun chunk inspection",
        "- Stage 4: raw mention and edge extraction",
        "- Stage 5: canonical label and parent concept assignment",
        "- Stage 6: countable fact export",
        "- v1 제외: coreference, undocumented relation MWE repair, phrasal action collapse",
        "",
        "> 주의: 이 파일은 이미 생성된 Stage 3/5/6 output을 읽어서 보여주는 rendering 결과다. 새 extraction rule은 적용하지 않는다.",
        "",
        "## Summary",
        "",
        f"- captions: {len(captions)}",
        f"- stage3 records: {sum(len(stage3_records.get(caption_id(row), [])) for row in captions)}",
        f"- mentions: {sum(len(mentions.get(caption_id(row), [])) for row in captions)}",
        f"- edges: {sum(len(edges.get(caption_id(row), [])) for row in captions)}",
        f"- facts: {sum(len(facts.get(caption_id(row), [])) for row in captions)}",
        "",
    ]

    for index, row in enumerate(captions, start=1):
        cid = caption_id(row)
        caption_mentions = mentions.get(cid, [])
        caption_edges = edges.get(cid, [])
        caption_facts = facts.get(cid, [])
        caption_stage3 = first_or_none(stage3_records.get(cid, []))
        mention_by_id = {mention["mention_id"]: mention for mention in caption_mentions}
        lines.extend(
            render_case(
                index,
                row,
                caption_stage3,
                caption_mentions,
                caption_edges,
                caption_facts,
                mention_by_id,
                max_object_pairs_per_caption=max_object_pairs_per_caption,
            ),
        )
    return "\n".join(lines) + "\n"


def render_case(
    index: int,
    row: JsonObject,
    stage3_record: JsonObject | None,
    mentions: list[JsonObject],
    edges: list[JsonObject],
    facts: list[JsonObject],
    mention_by_id: dict[str, JsonObject],
    *,
    max_object_pairs_per_caption: int,
) -> list[str]:
    cid = caption_id(row)
    lines: list[str] = [
        f"## {index:04d}",
        "",
        f"- caption_id: `{cid}`",
        f"- caption_type: `{row.get('caption_type', '')}`",
        "",
        "> " + str(row.get("caption", "")).replace("\n", " "),
        "",
    ]

    lines.extend(render_stage3_section(stage3_record))
    lines.extend(["### Mentions", ""])

    for mention_type, title in (
        ("object", "Objects"),
        ("attribute", "Attributes"),
        ("quantity", "Quantities"),
        ("action", "Actions"),
    ):
        filtered = [mention for mention in mentions if mention["mention_type"] == mention_type]
        lines.extend(render_mentions_table(title, filtered))

    lines.extend(["### Edges", ""])
    for edge_type, title in (
        ("has_attribute", "Object-Attribute"),
        ("has_quantity", "Object-Quantity"),
        ("event_role", "Agent / Patient"),
        ("relation", "Relations"),
        ("ambiguous_relation_candidate", "Ambiguous Relation Candidates"),
    ):
        filtered_edges = [edge for edge in edges if edge["edge_type"] == edge_type]
        lines.extend(render_edges_table(title, filtered_edges, mention_by_id))

    pair_facts = [fact for fact in facts if fact["fact_type"] == "object_pair_in_caption"]
    lines.extend(render_object_pairs(pair_facts, max_object_pairs_per_caption))
    return lines


def render_stage3_section(stage3_record: JsonObject | None) -> list[str]:
    lines = ["### Stage 3 Linguistic Annotation", ""]
    if stage3_record is None:
        return lines + ["_stage3 record missing_", ""]
    lines.extend(render_protected_spans(stage3_record.get("protected_spans") or []))
    lines.extend(render_noun_chunks(stage3_record.get("noun_chunks") or []))
    lines.extend(render_tokens(stage3_record.get("tokens") or []))
    return lines


def render_protected_spans(spans: list[JsonObject]) -> list[str]:
    lines = ["#### Protected Spans", ""]
    if not spans:
        return lines + ["_none_", ""]
    lines.extend(
        [
            "| kind | text | token_span | char_span | rule |",
            "|---|---|---|---|---|",
        ],
    )
    for span in spans:
        token_span = f"{span.get('token_start', '')}:{span.get('token_end', '')}"
        char_span = f"{span.get('char_start', '')}:{span.get('char_end', '')}"
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    span.get("kind", ""),
                    span.get("text", ""),
                    token_span,
                    char_span,
                    span.get("rule_id", ""),
                )
            )
            + " |",
        )
    return lines + [""]


def render_noun_chunks(chunks: list[JsonObject]) -> list[str]:
    lines = ["#### Noun Chunks", ""]
    if not chunks:
        return lines + ["_none_", ""]
    lines.extend(
        [
            "| chunk | token_span | root | root_lemma | root_pos | root_tag | root_dep | root_head |",
            "|---|---|---|---|---|---|---|---|",
        ],
    )
    for chunk in chunks:
        token_span = f"{chunk.get('token_start', '')}:{chunk.get('token_end', '')}"
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    chunk.get("text", ""),
                    token_span,
                    chunk.get("root_text", ""),
                    chunk.get("root_lemma", ""),
                    chunk.get("root_pos", ""),
                    chunk.get("root_tag", ""),
                    chunk.get("root_dep", ""),
                    chunk.get("root_head_text", ""),
                )
            )
            + " |",
        )
    return lines + [""]


def render_tokens(tokens: list[JsonObject]) -> list[str]:
    lines = ["#### Tokens / POS / Lemma / Dependency", ""]
    if not tokens:
        return lines + ["_none_", ""]
    lines.extend(
        [
            "| i | text | lemma | pos | tag | morph | dep | head_i | head |",
            "|---:|---|---|---|---|---|---|---:|---|",
        ],
    )
    for token in tokens:
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    token.get("i", ""),
                    token.get("text", ""),
                    token.get("lemma", ""),
                    token.get("pos", ""),
                    token.get("tag", ""),
                    token.get("morph", ""),
                    token.get("dep", ""),
                    token.get("head_i", ""),
                    token.get("head_text", ""),
                )
            )
            + " |",
        )
    return lines + [""]


def render_mentions_table(title: str, mentions: list[JsonObject]) -> list[str]:
    lines = [f"#### {title}", ""]
    if not mentions:
        return lines + ["_none_", ""]
    if title == "Attributes":
        lines.extend(
            [
                "| id | raw | lemma | canonical | source | confidence |",
                "|---|---|---|---|---|---|",
            ],
        )
        for mention in mentions:
            lines.append(
                "| "
                + " | ".join(
                    md_cell(value)
                    for value in (
                        mention["mention_id"],
                        mention.get("raw_text", ""),
                        mention.get("raw_lemma", ""),
                        mention.get("canonical", ""),
                        mention.get("canonical_source", ""),
                        mention.get("confidence", ""),
                    )
                )
                + " |",
            )
        return lines + [""]
    lines.extend(
        [
            "| id | raw | lemma | canonical | parent | parent_synset_ids | source | confidence |",
            "|---|---|---|---|---|---|---|---|",
        ],
    )
    for mention in mentions:
        parent = ", ".join(mention.get("parent_concepts") or [])
        parent_synset_ids = ", ".join(parent_oewn_synsets(mention))
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    mention["mention_id"],
                    mention.get("raw_text", ""),
                    mention.get("raw_lemma", ""),
                    mention.get("canonical", ""),
                    parent,
                    parent_synset_ids,
                    mention.get("canonical_source", ""),
                    mention.get("confidence", ""),
                )
            )
            + " |",
        )
    return lines + [""]


def render_edges_table(
    title: str,
    edges: list[JsonObject],
    mention_by_id: dict[str, JsonObject],
) -> list[str]:
    lines = [f"#### {title}", ""]
    if not edges:
        return lines + ["_none_", ""]
    lines.extend(
        [
            "| edge | source | label | target | rule | confidence |",
            "|---|---|---|---|---|---|",
        ],
    )
    for edge in edges:
        source = mention_label(mention_by_id.get(edge["source_mention_id"]))
        target = mention_label(mention_by_id.get(edge["target_mention_id"]))
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    edge["edge_id"],
                    source,
                    edge.get("canonical_label") or edge.get("label", ""),
                    target,
                    edge.get("rule_id", ""),
                    edge.get("confidence", ""),
                )
            )
            + " |",
        )
    return lines + [""]


def render_object_pairs(pair_facts: list[JsonObject], max_rows: int) -> list[str]:
    lines = [
        "### Object Co-occurrence Pairs",
        "",
        f"- ordered pair facts: {len(pair_facts)}",
    ]
    if not pair_facts:
        return lines + ["", "_none_", ""]
    shown = pair_facts[:max_rows]
    if len(pair_facts) > max_rows:
        lines.append(f"- shown: first {max_rows}")
    lines.extend(
        [
            "",
            "| source_object | target_object | rule_ids |",
            "|---|---|---|",
        ],
    )
    for fact in shown:
        values = fact.get("values", {})
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    values.get("source_object", ""),
                    values.get("target_object", ""),
                    ", ".join(fact.get("rule_ids") or []),
                )
            )
            + " |",
        )
    return lines + [""]


def mention_label(mention: JsonObject | None) -> str:
    if mention is None:
        return "missing"
    raw = mention.get("raw_text", "")
    canonical = mention.get("canonical", "")
    return f"{canonical} ({raw})" if raw and raw != canonical else str(canonical)


def parent_oewn_synsets(mention: JsonObject) -> list[str]:
    detail = mention.get("canonical_detail")
    if not isinstance(detail, dict):
        return []
    value = detail.get("parent_oewn_synsets")
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [item for item in value.split("|") if item]
    return []


def group_by_caption(rows: Iterable[JsonObject]) -> dict[str, list[JsonObject]]:
    grouped: dict[str, list[JsonObject]] = defaultdict(list)
    for row in rows:
        grouped[row["caption_id"]].append(row)
    return grouped


def first_or_none(rows: list[JsonObject]) -> JsonObject | None:
    return rows[0] if rows else None


def caption_id(row: JsonObject) -> str:
    return caption_id_from_gpic_row(row)


def iter_jsonl(path: str | Path) -> Iterable[JsonObject]:
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def md_cell(value: Any) -> str:
    text = str(value).replace("\n", " ")
    return text.replace("|", "\\|")


if __name__ == "__main__":
    main()
