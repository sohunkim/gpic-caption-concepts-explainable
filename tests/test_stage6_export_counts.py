from pathlib import Path
import csv
import os
import tempfile
import unittest
import uuid

from gpic_concepts_v1.io_jsonl import iter_jsonl, write_jsonl
from gpic_concepts_v1.schema import CanonicalEdge, CanonicalMention
from gpic_concepts_v1.schema import MISSING_SOURCE_MENTION_ID
from gpic_concepts_v1.stage6_export_counts import (
    COUNT_TABLE_SPECS,
    _count_integrity_report,
    export_count_facts,
    run_stage6_export_counts,
)


def mention(
    caption_id: str,
    mention_id: str,
    mention_type: str,
    canonical: str,
    canonical_rule_id: str,
    *,
    raw_text: str | None = None,
    raw_lemma: str | None = None,
    parent_concepts: list[str] | None = None,
    parent_rule_id: str | None = None,
    canonical_detail: dict[str, object] | None = None,
) -> CanonicalMention:
    return CanonicalMention(
        caption_id=caption_id,
        mention_id=mention_id,
        mention_type=mention_type,  # type: ignore[arg-type]
        raw_text=raw_text or canonical,
        raw_lemma=raw_lemma or canonical,
        canonical=canonical,
        parent_concepts=parent_concepts or [],
        canonical_rule_id=canonical_rule_id,
        parent_rule_id=parent_rule_id,
        canonical_source="raw_fallback",
        parent_source="lexicon" if parent_rule_id else None,
        canonical_detail=canonical_detail or {},
    )


def edge(
    caption_id: str,
    edge_id: str,
    edge_type: str,
    source: str,
    target: str,
    label: str,
    rule_id: str,
    *,
    canonical_rule_id: str | None = None,
    canonical_detail: dict[str, object] | None = None,
) -> CanonicalEdge:
    return CanonicalEdge(
        caption_id=caption_id,
        edge_id=edge_id,
        edge_type=edge_type,  # type: ignore[arg-type]
        source_mention_id=source,
        target_mention_id=target,
        label=label,
        canonical_label=label,
        source_canonical="unused",
        target_canonical="unused",
        rule_id=rule_id,
        canonical_rule_id=canonical_rule_id,
        canonical_detail=canonical_detail or {},
    )


class Stage6ExportCountsTest(unittest.TestCase):
    def test_exports_facts_and_count_tables(self) -> None:
        mentions = [
            mention(
                "c1",
                "m0",
                "object",
                "dog",
                "R19",
                raw_text="dogs",
                raw_lemma="dog",
                parent_concepts=["animal"],
                parent_rule_id="R23",
                canonical_detail={"parent_oewn_synsets": ["oewn-animal-n"]},
            ),
            mention(
                "c1",
                "m1",
                "attribute",
                "red",
                "R20",
                canonical_detail={"attribute_type": "color_attribute"},
            ),
            mention("c1", "m2", "quantity", "two", "R21"),
            mention(
                "c1",
                "m3",
                "action",
                "sit",
                "R22",
                raw_text="sits",
                raw_lemma="sit",
                canonical_detail={"action_type": "pose"},
            ),
            mention("c1", "m4", "object", "bench", "R19"),
            mention("c2", "m0", "object", "cat", "R19"),
            mention("c2", "m1", "object", "mat", "R19"),
        ]
        edges = [
            edge("c1", "e0", "has_attribute", "m0", "m1", "has_attribute", "R13"),
            edge("c1", "e1", "has_quantity", "m0", "m2", "has_quantity", "R14"),
            edge("c1", "e2", "event_role", "m3", "m0", "agent", "R16"),
            edge(
                "c1",
                "e3",
                "relation",
                "m0",
                "m4",
                "in front of",
                "R18.1",
                canonical_rule_id="R24",
                canonical_detail={
                    "relation_source": "preposition_mwe",
                    "raw_span_surface": "in front of",
                    "relation_components": ["in", "front", "of"],
                },
            ),
            edge(
                "c1",
                "e4",
                "ambiguous_relation_candidate",
                "m0",
                "m4",
                "in front of",
                "R18.1",
                canonical_rule_id="R24",
                canonical_detail={
                    "relation_source": "preposition_mwe",
                    "raw_span_surface": "in front of",
                    "candidate_source_count": 2,
                    "relation_components": ["in", "front", "of"],
                },
            ),
            edge("c2", "e0", "relation", "m0", "m1", "on", "R18", canonical_rule_id="R24"),
        ]

        result = export_count_facts(mentions, edges)
        fact_types = [fact.fact_type for fact in result.facts]
        self.assertEqual(fact_types.count("entity_exists"), 4)
        self.assertEqual(fact_types.count("attribute_exists"), 2)
        self.assertEqual(fact_types.count("quantity_exists"), 0)
        self.assertEqual(fact_types.count("object_parent"), 1)
        self.assertEqual(fact_types.count("action_event"), 1)
        self.assertEqual(fact_types.count("has_attribute"), 2)
        self.assertEqual(fact_types.count("has_quantity"), 0)
        self.assertEqual(fact_types.count("event_role"), 1)
        self.assertEqual(fact_types.count("relation"), 2)
        self.assertEqual(fact_types.count("ambiguous_relation_candidate"), 1)
        self.assertEqual(fact_types.count("relation_component"), 4)
        self.assertEqual(fact_types.count("object_pair_in_caption"), 4)

        relation_rows = result.count_tables["relation_triple_counts.tsv"]
        relation_keys = {row.count_key for row in relation_rows}
        self.assertIn("relation:dog:in front of:bench", relation_keys)
        self.assertIn("relation:cat:on:mat", relation_keys)

        attribute_rows = result.count_tables["attribute_counts.tsv"]
        attribute_keys = {row.count_key for row in attribute_rows}
        self.assertEqual(
            attribute_keys,
            {"attribute:red:attribute", "attribute:two:quantity"},
        )
        self.assertEqual(
            next(
                row
                for row in attribute_rows
                if row.count_key == "attribute:red:attribute"
            ).values["attribute_kind"],
            "attribute",
        )
        self.assertEqual(
            next(
                row
                for row in attribute_rows
                if row.count_key == "attribute:two:quantity"
            ).values["attribute_kind"],
            "quantity",
        )
        object_rows = result.count_tables["object_counts.tsv"]
        dog_row = next(row for row in object_rows if row.count_key == "object:dog")
        self.assertEqual(dog_row.raw_variants, ["dogs"])
        self.assertEqual(dog_row.values["parent_concepts"], "animal")
        self.assertEqual(dog_row.values["parent_synset_ids"], "oewn-animal-n")
        dog_relation_row = next(
            row for row in relation_rows if row.count_key == "relation:dog:in front of:bench"
        )
        self.assertEqual(dog_relation_row.values["source_parent_concepts"], "animal")
        self.assertEqual(dog_relation_row.values["source_parent_synset_ids"], "oewn-animal-n")
        action_fact = next(fact for fact in result.facts if fact.fact_type == "action_event")
        self.assertNotIn("action_type", action_fact.values)
        pair_rows = result.count_tables["object_cooccurrence_pair_counts.tsv"]
        pair_keys = {row.count_key for row in pair_rows}
        self.assertIn("object_pair_in_caption:dog:bench", pair_keys)
        self.assertIn("object_pair_in_caption:bench:dog", pair_keys)
        dog_bench_pair = next(
            row for row in pair_rows if row.count_key == "object_pair_in_caption:dog:bench"
        )
        self.assertEqual(dog_bench_pair.values["source_parent_concepts"], "animal")
        self.assertEqual(dog_bench_pair.values["source_parent_synset_ids"], "oewn-animal-n")
        attribute_fact = next(fact for fact in result.facts if fact.fact_type == "has_attribute")
        self.assertNotIn("attribute_type", attribute_fact.values)
        relation_component_rows = result.count_tables["relation_component_counts.tsv"]
        relation_component_keys = {row.count_key for row in relation_component_rows}
        self.assertIn("relation_component:in front of:0:in", relation_component_keys)
        self.assertIn("relation_component:in front of:1:front", relation_component_keys)
        self.assertIn("relation_component:in front of:2:of", relation_component_keys)
        self.assertIn("relation_component:on:0:on", relation_component_keys)
        candidate_rows = result.count_tables["ambiguous_relation_candidate_counts.tsv"]
        candidate_keys = {row.count_key for row in candidate_rows}
        self.assertIn(
            "ambiguous_relation_candidate:source_resolved:in front of:target_resolved",
            candidate_keys,
        )
        parent_rows = result.count_tables["object_parent_counts.tsv"]
        self.assertEqual(parent_rows[0].values["parent_synset_id"], "oewn-animal-n")
        object_attribute_rows = result.count_tables["object_attribute_pair_counts.tsv"]
        object_attribute_keys = {row.count_key for row in object_attribute_rows}
        self.assertEqual(
            object_attribute_keys,
            {
                "object_attribute_pair:dog:red:attribute",
                "object_attribute_pair:dog:two:quantity",
            },
        )
        self.assertNotIn("attribute_type", object_attribute_rows[0].values)
        self.assertEqual(
            next(
                row
                for row in object_attribute_rows
                if row.count_key == "object_attribute_pair:dog:two:quantity"
            ).values["attribute_kind"],
            "quantity",
        )

    def test_attribute_kind_is_part_of_attribute_and_pair_keys(self) -> None:
        mentions = [
            mention("c1", "m0", "object", "vehicles", "R19"),
            mention("c1", "m1", "attribute", "few", "R20"),
            mention("c1", "m2", "quantity", "few", "R21"),
        ]
        edges = [
            edge("c1", "e0", "has_attribute", "m0", "m1", "has_attribute", "R13"),
            edge("c1", "e1", "has_quantity", "m0", "m2", "has_quantity", "R14"),
        ]

        result = export_count_facts(mentions, edges)

        attribute_rows = result.count_tables["attribute_counts.tsv"]
        self.assertEqual(
            {row.count_key for row in attribute_rows},
            {"attribute:few:attribute", "attribute:few:quantity"},
        )
        pair_rows = result.count_tables["object_attribute_pair_counts.tsv"]
        self.assertEqual(
            {row.count_key for row in pair_rows},
            {
                "object_attribute_pair:vehicles:few:attribute",
                "object_attribute_pair:vehicles:few:quantity",
            },
        )

    def test_ambiguous_relation_candidate_count_is_per_mwe_occurrence(self) -> None:
        mentions = [
            mention("c1", "m0", "object", "text", "R19"),
            mention("c1", "m1", "object", "cup title", "R19"),
            mention("c1", "m2", "object", "date text", "R19"),
            mention("c1", "m3", "object", "hashtag", "R19"),
        ]
        detail = {
            "relation_source": "preposition_mwe",
            "raw_span_surface": "along with",
            "matched_token_indices": [75, 76],
            "candidate_source_count": 2,
            "candidate_target_count": 2,
            "relation_components": ["along", "with"],
        }
        edges = [
            edge(
                "c1",
                "e0",
                "ambiguous_relation_candidate",
                "m0",
                "m2",
                "along with",
                "R18.1",
                canonical_rule_id="R24",
                canonical_detail=detail,
            ),
            edge(
                "c1",
                "e1",
                "ambiguous_relation_candidate",
                "m0",
                "m3",
                "along with",
                "R18.1",
                canonical_rule_id="R24",
                canonical_detail=detail,
            ),
            edge(
                "c1",
                "e2",
                "ambiguous_relation_candidate",
                "m1",
                "m2",
                "along with",
                "R18.1",
                canonical_rule_id="R24",
                canonical_detail=detail,
            ),
            edge(
                "c1",
                "e3",
                "ambiguous_relation_candidate",
                "m1",
                "m3",
                "along with",
                "R18.1",
                canonical_rule_id="R24",
                canonical_detail=detail,
            ),
        ]

        result = export_count_facts(mentions, edges)
        facts = [
            fact
            for fact in result.facts
            if fact.fact_type == "ambiguous_relation_candidate"
        ]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].values["source_status"], "source_ambiguous")
        self.assertEqual(facts[0].values["target_status"], "target_ambiguous")
        self.assertEqual(facts[0].values["candidate_pair_count"], "4")
        self.assertEqual(facts[0].source_edge_ids, ["e0", "e1", "e2", "e3"])

        rows = result.count_tables["ambiguous_relation_candidate_counts.tsv"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0].count_key,
            "ambiguous_relation_candidate:source_ambiguous:along with:target_ambiguous",
        )
        self.assertEqual(rows[0].count, 1)
        self.assertEqual(rows[0].values["candidate_pair_count"], "4")
        self.assertEqual(rows[0].values["candidate_targets"], "date text|hashtag")

    def test_missing_source_ambiguous_relation_candidate_count(self) -> None:
        mentions = [
            mention("c1", "m0", "object", "wall", "R19"),
        ]
        edges = [
            edge(
                "c1",
                "e0",
                "ambiguous_relation_candidate",
                MISSING_SOURCE_MENTION_ID,
                "m0",
                "in front of",
                "R18.1",
                canonical_rule_id="R24",
                canonical_detail={
                    "relation_source": "preposition_mwe",
                    "raw_span_surface": "in front of",
                    "matched_token_indices": [4, 5, 6],
                    "candidate_source_count": 0,
                    "candidate_target_count": 1,
                    "source_endpoint_status": "source_missing",
                    "target_endpoint_status": "target_resolved",
                    "relation_components": ["in", "front", "of"],
                },
            ),
        ]

        result = export_count_facts(mentions, edges)
        facts = [
            fact
            for fact in result.facts
            if fact.fact_type == "ambiguous_relation_candidate"
        ]

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].values["source_status"], "source_missing")
        self.assertEqual(facts[0].values["target_status"], "target_resolved")
        self.assertEqual(facts[0].values["candidate_sources"], ["source_missing"])
        self.assertEqual(facts[0].values["candidate_targets"], ["wall"])
        self.assertEqual(facts[0].source_mention_ids, ["m0"])
        self.assertEqual(
            facts[0].count_key,
            "ambiguous_relation_candidate:source_missing:in front of:target_resolved",
        )

    def test_event_role_fact_preserves_passive_voice_metadata(self) -> None:
        mentions = [
            mention("c1", "m0", "object", "ball", "R19"),
            mention("c1", "m1", "action", "hold", "R22"),
        ]
        edges = [
            edge(
                "c1",
                "e0",
                "event_role",
                "m1",
                "m0",
                "patient",
                "R17.1",
                canonical_detail={
                    "raw_role": "theme",
                    "voice_normalization": "passive_to_active",
                },
            )
        ]

        result = export_count_facts(mentions, edges)
        fact = next(fact for fact in result.facts if fact.fact_type == "event_role")
        row = result.count_tables["agent_patient_pair_counts.tsv"][0]

        self.assertEqual(fact.values["raw_role"], "theme")
        self.assertEqual(fact.values["voice_normalization"], "passive_to_active")
        self.assertEqual(row.values["raw_role"], "theme")
        self.assertEqual(row.values["voice_normalization"], "passive_to_active")
        self.assertEqual(row.count_key, "event_role:hold:patient:ball")

    def test_run_stage6_writes_facts_and_tsv_tables(self) -> None:
        mentions = [
            mention("c1", "m0", "object", "dog", "R19"),
            mention("c1", "m1", "object", "bench", "R19"),
            mention("c1", "m2", "action", "sit", "R22"),
        ]
        edges = [
            edge("c1", "e0", "event_role", "m2", "m0", "agent", "R16"),
            edge("c1", "e1", "relation", "m0", "m1", "on", "R18", canonical_rule_id="R24"),
        ]
        tmp_path = _stage6_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            canonical_mentions_path = tmp_path / "canonical_mentions.jsonl"
            canonical_edges_path = tmp_path / "canonical_edges.jsonl"
            output_dir = tmp_path / "stage6"
            summary_path = tmp_path / "summary.jsonl"
            write_jsonl(canonical_mentions_path, mentions)
            write_jsonl(canonical_edges_path, edges)

            summary = run_stage6_export_counts(
                canonical_mentions_path,
                canonical_edges_path,
                output_dir=output_dir,
                summary_path=summary_path,
                max_rss_gib=1000,
            )

            self.assertTrue((output_dir / "facts.jsonl").exists())
            self.assertTrue((output_dir / "stage6_count_accumulators.sqlite3").exists())
            self.assertTrue((output_dir / "object_counts.tsv").exists())
            self.assertTrue((output_dir / "relation_triple_counts.tsv").exists())
            self.assertTrue((output_dir / "ambiguous_relation_candidate_counts.tsv").exists())
            self.assertEqual(summary["count_backend"], "sqlite")
            self.assertEqual(summary["sqlite_cache_rows"], None)
            self.assertIn("adaptive", summary["sqlite_cache_policy"])
            self.assertEqual(summary["count_integrity"]["status"], "ok")
            self.assertEqual(
                summary["count_integrity"]["total_counted_facts"],
                summary["fact_total"],
            )
            self.assertEqual(summary["fact_type_counts"]["relation"], 1)
            self.assertEqual(
                summary["count_integrity"]["actual_fact_type_count_sums"]["relation"],
                1,
            )
            self.assertEqual(len(list(iter_jsonl(output_dir / "facts.jsonl"))), summary["fact_total"])
            self.assertEqual(list(iter_jsonl(summary_path))[0]["fact_total"], summary["fact_total"])

            with (output_dir / "object_counts.tsv").open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[0]["count"], "1")
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

    def test_memory_backend_writes_full_header_for_empty_count_tables(self) -> None:
        mentions = [
            mention("c1", "m0", "object", "dog", "R19"),
        ]
        tmp_path = _stage6_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            canonical_mentions_path = tmp_path / "canonical_mentions.jsonl"
            canonical_edges_path = tmp_path / "canonical_edges.jsonl"
            output_dir = tmp_path / "stage6"
            write_jsonl(canonical_mentions_path, mentions)
            write_jsonl(canonical_edges_path, [])

            run_stage6_export_counts(
                canonical_mentions_path,
                canonical_edges_path,
                output_dir=output_dir,
                count_backend="memory",
            )

            spec = next(
                item
                for item in COUNT_TABLE_SPECS
                if item.file_name == "relation_component_counts.tsv"
            )
            expected_header = "\t".join(
                [
                    "count_key",
                    *spec.value_fields,
                    *spec.extra_value_fields,
                    "count",
                    "caption_count",
                    "example_caption_ids",
                    "raw_variants",
                    "rule_ids",
                ]
            )
            actual_header = (
                output_dir / "relation_component_counts.tsv"
            ).read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(actual_header, expected_header)
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

    def test_sqlite_and_memory_count_backends_match(self) -> None:
        mentions = [
            mention("c1", "m0", "object", "dog", "R19"),
            mention("c1", "m1", "object", "bench", "R19"),
            mention("c1", "m2", "action", "sit", "R22"),
            mention("c2", "m0", "object", "dog", "R19"),
            mention("c2", "m1", "object", "bench", "R19"),
        ]
        edges = [
            edge("c1", "e0", "event_role", "m2", "m0", "agent", "R16"),
            edge("c1", "e1", "relation", "m0", "m1", "on", "R18", canonical_rule_id="R24"),
            edge("c2", "e0", "relation", "m0", "m1", "on", "R18", canonical_rule_id="R24"),
        ]
        tmp_path = _stage6_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            canonical_mentions_path = tmp_path / "canonical_mentions.jsonl"
            canonical_edges_path = tmp_path / "canonical_edges.jsonl"
            write_jsonl(canonical_mentions_path, mentions)
            write_jsonl(canonical_edges_path, edges)

            sqlite_dir = tmp_path / "sqlite"
            memory_dir = tmp_path / "memory"
            run_stage6_export_counts(
                canonical_mentions_path,
                canonical_edges_path,
                output_dir=sqlite_dir,
                count_backend="sqlite",
                sqlite_cache_rows=1,
            )
            run_stage6_export_counts(
                canonical_mentions_path,
                canonical_edges_path,
                output_dir=memory_dir,
                count_backend="memory",
            )

            for file_name in (
                "object_counts.tsv",
                "relation_triple_counts.tsv",
                "object_cooccurrence_pair_counts.tsv",
                "agent_patient_pair_counts.tsv",
            ):
                with self.subTest(file_name=file_name):
                    self.assertEqual(
                        (sqlite_dir / file_name).read_text(encoding="utf-8"),
                        (memory_dir / file_name).read_text(encoding="utf-8"),
                    )
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

    def test_discard_facts_output_mode_keeps_counts_without_facts_jsonl(self) -> None:
        mentions = [
            mention("c1", "m0", "object", "dog", "R19"),
            mention("c1", "m1", "object", "bench", "R19"),
            mention("c1", "m2", "action", "sit", "R22"),
            mention("c2", "m0", "object", "dog", "R19"),
            mention("c2", "m1", "object", "bench", "R19"),
        ]
        edges = [
            edge("c1", "e0", "event_role", "m2", "m0", "agent", "R16"),
            edge("c1", "e1", "relation", "m0", "m1", "on", "R18", canonical_rule_id="R24"),
            edge("c2", "e0", "relation", "m0", "m1", "on", "R18", canonical_rule_id="R24"),
        ]
        tmp_path = _stage6_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            canonical_mentions_path = tmp_path / "canonical_mentions.jsonl"
            canonical_edges_path = tmp_path / "canonical_edges.jsonl"
            write_jsonl(canonical_mentions_path, mentions)
            write_jsonl(canonical_edges_path, edges)

            write_dir = tmp_path / "write"
            discard_dir = tmp_path / "discard"
            write_summary = run_stage6_export_counts(
                canonical_mentions_path,
                canonical_edges_path,
                output_dir=write_dir,
                count_backend="memory",
            )
            discard_summary = run_stage6_export_counts(
                canonical_mentions_path,
                canonical_edges_path,
                output_dir=discard_dir,
                count_backend="memory",
                facts_output_mode="discard",
            )

            self.assertTrue((write_dir / "facts.jsonl").exists())
            self.assertFalse((discard_dir / "facts.jsonl").exists())
            self.assertEqual(write_summary["facts_output_mode"], "write")
            self.assertEqual(discard_summary["facts_output_mode"], "discard")
            self.assertEqual(discard_summary["count_integrity"]["status"], "ok")
            self.assertEqual(write_summary["fact_total"], discard_summary["fact_total"])
            self.assertEqual(
                write_summary["fact_type_counts"],
                discard_summary["fact_type_counts"],
            )
            for file_name in (
                "object_counts.tsv",
                "relation_triple_counts.tsv",
                "object_cooccurrence_pair_counts.tsv",
                "agent_patient_pair_counts.tsv",
            ):
                with self.subTest(file_name=file_name):
                    self.assertEqual(
                        (write_dir / file_name).read_text(encoding="utf-8"),
                        (discard_dir / file_name).read_text(encoding="utf-8"),
                    )
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

    def test_count_integrity_report_rejects_dropped_or_duplicated_counts(self) -> None:
        with self.assertRaisesRegex(ValueError, "count integrity check failed"):
            _count_integrity_report(
                expected_fact_type_counts={"entity_exists": 2},
                fact_total=2,
                table_count_sums={"object_counts.tsv": 1},
                table_row_counts={"object_counts.tsv": 1},
            )


def _stage6_temp_base() -> Path:
    roots = [
        os.environ.get("GPIC_TEST_TEMP_ROOT"),
        str(Path.cwd() / ".tmp_tests"),
        r"C:\Users\Public\Documents\ESTsoft\CreatorTemp",
        tempfile.gettempdir(),
    ]
    for root in roots:
        if not root:
            continue
        base = Path(root) / "stage6_export_counts"
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / f"{uuid.uuid4().hex}.tmp"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return base
        except PermissionError:
            continue
    raise PermissionError("no writable temp directory for stage6 tests")


if __name__ == "__main__":
    unittest.main()
