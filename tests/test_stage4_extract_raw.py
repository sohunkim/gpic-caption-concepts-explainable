from pathlib import Path
import importlib.util
import os
import tempfile
import unittest
import uuid

from gpic_concepts_v1.io_jsonl import iter_jsonl, write_jsonl
from gpic_concepts_v1.attribute_units import (
    ATTRIBUTE_MWE_RULE_VERSION,
    ResolvedAttributeMweIndex,
)
from gpic_concepts_v1.schema import MISSING_SOURCE_MENTION_ID
from gpic_concepts_v1.stage4_extract_raw import (
    _ActionLookupResult,
    _ObjectLookupResult,
    _PrepositionMweEntry,
    _build_preposition_mwe_index,
    _lookup_oewn_verb_synsets,
    _with_selected_synset,
    Stage4SynsetAmbiguityError,
    extract_raw_concepts_from_doc,
    extract_raw_concepts_from_stage3_record,
    load_gpic_action_inventory,
    load_gpic_object_inventory,
    run_stage4_extract_raw,
)
from gpic_concepts_v1.stage3_annotate import (
    DEFAULT_STAGE3_MODEL,
    iter_annotated_docs_from_rows,
    iter_stage3_records_from_rows,
    make_stage3_nlp,
    spacy,
)


def token(
    i: int,
    text: str,
    lemma: str,
    pos: str,
    dep: str,
    head_i: int,
    *,
    tag: str = "NN",
) -> dict[str, object]:
    return {
        "i": i,
        "text": text,
        "lemma": lemma,
        "pos": pos,
        "tag": tag,
        "morph": "",
        "dep": dep,
        "head_i": head_i,
        "head_text": "",
        "char_start": i * 2,
        "char_end": i * 2 + len(text),
        "whitespace": " ",
    }


def chunk(
    text: str,
    root_i: int,
    start: int,
    end: int,
    root_text: str,
) -> dict[str, object]:
    return {
        "text": text,
        "root_i": root_i,
        "root_text": root_text,
        "root_lemma": root_text.lower(),
        "root_pos": "NOUN",
        "root_tag": "NN",
        "root_dep": "dep",
        "root_head_i": 0,
        "root_head_text": "",
        "token_start": start,
        "token_end": end,
        "char_start": start * 2,
        "char_end": end * 2,
    }


class Stage4ExtractRawTest(unittest.TestCase):
    def test_attribute_mwe_is_one_mention_and_one_edge(self) -> None:
        record = {
            "caption_id": "c-attribute-mwe",
            "caption": "A light brown dog.",
            "tokens": [
                token(0, "light", "light", "ADJ", "compound", 1, tag="JJ"),
                token(1, "brown", "brown", "ADJ", "amod", 2, tag="JJ"),
                token(2, "dog", "dog", "NOUN", "ROOT", 2, tag="NN"),
            ],
            "noun_chunks": [chunk("light brown dog", 2, 0, 3, "dog")],
        }
        matcher = _attribute_mwe_index("light brown")

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            attribute_mwe_lookup=matcher,
        )

        attributes = [
            mention.to_dict()
            for mention in result.raw_mentions
            if mention.mention_type == "attribute"
        ]
        attribute_edges = [
            edge.to_dict()
            for edge in result.raw_edges
            if edge.edge_type == "has_attribute"
        ]
        self.assertEqual([row["text"] for row in attributes], ["light brown"])
        self.assertEqual(len(attribute_edges), 1)
        self.assertEqual(
            attributes[0]["source_detail"]["selected_token_indices"],
            [0, 1],
        )

    def test_attribute_mwe_does_not_cross_quantity_boundary(self) -> None:
        record = {
            "caption_id": "c-attribute-mwe-quantity",
            "caption": "Two light brown dogs.",
            "tokens": [
                token(0, "Two", "two", "NUM", "nummod", 3, tag="CD"),
                token(1, "light", "light", "ADJ", "compound", 2, tag="JJ"),
                token(2, "brown", "brown", "ADJ", "amod", 3, tag="JJ"),
                token(3, "dogs", "dog", "NOUN", "ROOT", 3, tag="NNS"),
            ],
            "noun_chunks": [chunk("Two light brown dogs", 3, 0, 4, "dogs")],
        }
        matcher = ResolvedAttributeMweIndex(
            {
                "two light brown": _attribute_mwe_row("two light brown", token_count=3),
                "light brown": _attribute_mwe_row("light brown"),
            }
        )

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            attribute_mwe_lookup=matcher,
        )

        self.assertEqual(
            [
                mention.text
                for mention in result.raw_mentions
                if mention.mention_type == "attribute"
            ],
            ["light brown"],
        )
        self.assertEqual(
            [
                mention.text
                for mention in result.raw_mentions
                if mention.mention_type == "quantity"
            ],
            ["Two"],
        )

    def test_conjunct_single_is_suppressed_when_it_is_inside_attribute_mwe(self) -> None:
        record = {
            "caption_id": "c-conj-attribute-mwe",
            "caption": "White and light blue jerseys.",
            "tokens": [
                token(0, "white", "white", "ADJ", "amod", 4, tag="JJ"),
                token(1, "and", "and", "CCONJ", "cc", 0, tag="CC"),
                token(2, "light", "light", "ADJ", "amod", 3, tag="JJ"),
                token(3, "blue", "blue", "ADJ", "conj", 0, tag="JJ"),
                token(4, "jerseys", "jersey", "NOUN", "ROOT", 4, tag="NNS"),
            ],
            "noun_chunks": [
                chunk("white and light blue jerseys", 4, 0, 5, "jerseys")
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            attribute_mwe_lookup=_attribute_mwe_index("light blue"),
        )

        attributes = [
            mention.to_dict()
            for mention in result.raw_mentions
            if mention.mention_type == "attribute"
        ]
        attribute_edges = [
            edge.to_dict()
            for edge in result.raw_edges
            if edge.edge_type == "has_attribute"
        ]
        self.assertEqual([row["text"] for row in attributes], ["white", "light blue"])
        self.assertEqual(len(attribute_edges), 2)
        self.assertNotIn("blue", [row["text"] for row in attributes])

    def test_extracts_objects_attributes_quantity_action_and_roles(self) -> None:
        record = {
            "caption_id": "c1",
            "caption": "Two brown dogs chase a ball.",
            "tokens": [
                token(0, "Two", "two", "NUM", "nummod", 2, tag="CD"),
                token(1, "brown", "brown", "ADJ", "amod", 2, tag="JJ"),
                token(2, "dogs", "dog", "NOUN", "nsubj", 3, tag="NNS"),
                token(3, "chase", "chase", "VERB", "ROOT", 3, tag="VBP"),
                token(4, "a", "a", "DET", "det", 5, tag="DT"),
                token(5, "ball", "ball", "NOUN", "dobj", 3),
            ],
            "noun_chunks": [
                chunk("Two brown dogs", 2, 0, 3, "dogs"),
                chunk("a ball", 5, 4, 6, "ball"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        mentions = [mention.to_dict() for mention in result.raw_mentions]
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertEqual(
            [(m["mention_type"], m["lemma"], m["rule_id"]) for m in mentions],
            [
                ("object", "dog", "R12"),
                ("quantity", "two", "R14"),
                ("attribute", "brown", "R13"),
                ("object", "ball", "R12"),
                ("action", "chase", "R15"),
            ],
        )
        self.assertIn(("has_quantity", "has_quantity", "R14"), _edge_sig(edges))
        self.assertIn(("has_attribute", "has_attribute", "R13"), _edge_sig(edges))
        self.assertIn(("event_role", "agent", "R16"), _edge_sig(edges))
        self.assertIn(("event_role", "patient", "R17"), _edge_sig(edges))

    def test_tag_list_extracts_segment_objects_modifiers_only(self) -> None:
        brown = token(0, "brown", "brown", "ADJ", "amod", 1, tag="JJ")
        dog = token(1, "dog", "dog", "NOUN", "ROOT", 1)
        two = token(2, "two", "two", "NUM", "nummod", 3, tag="CD")
        bench = token(3, "bench", "bench", "NOUN", "ROOT", 3)
        standing = token(4, "standing", "stand", "VERB", "ROOT", 4, tag="VBG")
        dog_chunk = chunk("brown dog", 1, 0, 2, "dog")
        bench_chunk = chunk("two bench", 3, 2, 4, "bench")
        record = {
            "caption_id": "tag1",
            "caption": "brown dog, two bench, standing",
            "tokens": [brown, dog, two, bench, standing],
            "noun_chunks": [dog_chunk, bench_chunk],
            "tag_segments": [
                {
                    "segment_id": "t0",
                    "text": "brown dog",
                    "tokens": [brown, dog],
                    "noun_chunks": [dog_chunk],
                },
                {
                    "segment_id": "t1",
                    "text": "two bench",
                    "tokens": [two, bench],
                    "noun_chunks": [bench_chunk],
                },
                {
                    "segment_id": "t2",
                    "text": "standing",
                    "tokens": [standing],
                    "noun_chunks": [],
                },
            ],
            "meta": {"caption_shape": "tag_list"},
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            action_lookup=fake_action_lookup,
            preposition_mwe_lookup=(),
        )
        mentions = [mention.to_dict() for mention in result.raw_mentions]
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertEqual(
            [(m["mention_type"], m["text"], m["rule_id"]) for m in mentions],
            [
                ("object", "dog", "R12"),
                ("attribute", "brown", "R13"),
                ("object", "bench", "R12"),
                ("quantity", "two", "R14"),
                ("attribute", "standing", "R13"),
            ],
        )
        self.assertEqual(_edge_sig(edges), {("has_attribute", "has_attribute", "R13"), ("has_quantity", "has_quantity", "R14")})
        self.assertTrue(all(m["source_detail"].get("caption_shape") == "tag_list" for m in mentions))
        self.assertEqual(mentions[-1]["source_detail"]["modifier_source"], "tag_list_unattached_attribute")

    def test_tag_list_known_attribute_mwe_is_preserved_without_edge(self) -> None:
        dark = token(0, "dark", "dark", "ADJ", "amod", 1, tag="JJ")
        brown = token(1, "brown", "brown", "ADJ", "ROOT", 1, tag="JJ")
        record = {
            "caption_id": "tag-mwe",
            "caption": "dark brown",
            "tokens": [dark, brown],
            "noun_chunks": [],
            "tag_segments": [
                {
                    "segment_id": "t0",
                    "text": "dark brown",
                    "tokens": [dark, brown],
                    "noun_chunks": [],
                }
            ],
            "meta": {"caption_shape": "tag_list"},
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            attribute_mwe_lookup=_attribute_mwe_index("dark brown"),
            preposition_mwe_lookup=(),
        )

        self.assertEqual(
            [(mention.mention_type, mention.text) for mention in result.raw_mentions],
            [("attribute", "dark brown")],
        )
        self.assertEqual(result.raw_edges, [])
        self.assertEqual(
            result.raw_mentions[0].source_detail["modifier_source"],
            "tag_list_unattached_attribute_mwe",
        )

    def test_ambiguous_object_synset_stops_raw_extraction(self) -> None:
        record = {
            "caption_id": "c-ambiguous",
            "caption": "A bat.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "bat", "bat", "NOUN", "ROOT", 1),
            ],
            "noun_chunks": [chunk("A bat", 1, 0, 2, "bat")],
        }

        with self.assertRaises(Stage4SynsetAmbiguityError):
            extract_raw_concepts_from_stage3_record(
                record,
                object_lookup=fake_ambiguous_object_lookup,
            )

    def test_gpic_object_inventory_lookup_drives_object_selection(self) -> None:
        record = {
            "caption_id": "c-inventory",
            "caption": "A brown dog.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 2, tag="DT"),
                token(1, "brown", "brown", "ADJ", "amod", 2, tag="JJ"),
                token(2, "dog", "dog", "NOUN", "ROOT", 2),
            ],
            "noun_chunks": [chunk("A brown dog", 2, 0, 3, "dog")],
        }
        tmp_path = _stage4_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory_path = tmp_path / "observed_object_span_inventory.tsv"
            inventory_path.write_text(
                "\t".join(
                    [
                        "span_key",
                        "observed_surface",
                        "decision_status",
                        "decision_reason",
                        "selected_lookup_case",
                        "selected_query",
                        "all_oewn_synsets",
                        "all_oewn_lexfiles",
                        "selected_oewn_synset",
                        "selected_oewn_lexfile",
                        "objectness_gate",
                        "synset_lemmas",
                        "parent_oewn_synsets",
                        "parent_oewn_lexfiles",
                        "parent_lemmas",
                        "parent_selection_tag",
                        "canonical_surface",
                        "canonical_label_key",
                        "canonical_selection_tag",
                        "canonical_candidate_lemmas",
                        "canonical_candidate_lemma_counts",
                        "google_ngram_candidate_surfaces",
                        "google_ngram_candidate_mean_frequencies",
                        "synset_selection_tag",
                        "wn30_lemma_counts",
                    ]
                )
                + "\n"
                + "\t".join(
                    [
                        "brown dog",
                        "brown dog",
                        "chosen",
                        "selected_object_compatible",
                        "test_inventory",
                        "brown dog",
                        "fake-brown-dog-n",
                        "noun.animal",
                        "fake-brown-dog-n",
                        "noun.animal",
                        "object_compatible",
                        "brown dog|dog",
                        "fake-parent-n",
                        "fake-parent-n:noun.animal",
                        "fake-parent-n:canine",
                        "selected_all_immediate_oewn_hypernyms",
                        "dog",
                        "dog",
                        "selected_by_wn30_lemma_count_unique_positive_max",
                        "dog",
                        "dog:42",
                        "",
                        "",
                        "manual_select",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = extract_raw_concepts_from_stage3_record(
                record,
                object_lookup=load_gpic_object_inventory(inventory_path),
            )
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

        object_mentions = [
            mention.to_dict()
            for mention in result.raw_mentions
            if mention.mention_type == "object"
        ]
        self.assertEqual(len(object_mentions), 1)
        self.assertEqual(object_mentions[0]["text"], "dog")
        self.assertEqual(object_mentions[0]["source_detail"]["lookup_span_surface"], "brown dog")
        self.assertEqual(object_mentions[0]["source_detail"]["lookup_token_indices"], [1, 2])
        self.assertEqual(object_mentions[0]["source_detail"]["selected_token_indices"], [2])
        self.assertEqual(
            object_mentions[0]["source_detail"]["selected_oewn_synset"],
            "fake-brown-dog-n",
        )
        self.assertEqual(
            object_mentions[0]["source_detail"]["parent_oewn_synsets"],
            ["fake-parent-n"],
        )
        self.assertEqual(
            object_mentions[0]["source_detail"]["parent_selection_tag"],
            "selected_all_immediate_oewn_hypernyms",
        )
        self.assertEqual(object_mentions[0]["source_detail"]["canonical_surface"], "dog")
        self.assertEqual(
            object_mentions[0]["source_detail"]["canonical_selection_tag"],
            "selected_by_wn30_lemma_count_unique_positive_max",
        )

    def test_lookup_span_core_suffix_leaves_modifier_as_attribute(self) -> None:
        record = {
            "caption_id": "c-core-suffix",
            "caption": "A black top.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 2, tag="DT"),
                token(1, "black", "black", "ADJ", "amod", 2, tag="JJ"),
                token(2, "top", "top", "NOUN", "ROOT", 2),
            ],
            "noun_chunks": [chunk("A black top", 2, 0, 3, "top")],
        }
        synset = FakeSynset("fake-top-n", "noun.artifact", ["top"])

        def object_lookup(surface: str) -> _ObjectLookupResult | None:
            if surface != "black top":
                return None
            return _ObjectLookupResult(
                lookup_case="test",
                query=surface,
                synsets=(synset,),
                selected_synset=synset,
                synset_selection_tag="manual_select",
                wn30_lemma_counts="",
                objectness_gate="object_compatible",
                decision_status="chosen",
                canonical_surface="top",
                canonical_label_key="top",
                canonical_selection_tag="selected_single_observed_variant_matched_synset_lemma",
            )

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=object_lookup,
        )

        mentions = [mention.to_dict() for mention in result.raw_mentions]
        object_mentions = [m for m in mentions if m["mention_type"] == "object"]
        attribute_mentions = [m for m in mentions if m["mention_type"] == "attribute"]
        self.assertEqual(object_mentions[0]["text"], "top")
        self.assertEqual(object_mentions[0]["source_detail"]["lookup_span_surface"], "black top")
        self.assertEqual(object_mentions[0]["source_detail"]["selected_token_indices"], [2])
        self.assertEqual([m["text"] for m in attribute_mentions], ["black"])
        self.assertIn(("has_attribute", "has_attribute", "R13"), _edge_sig([e.to_dict() for e in result.raw_edges]))

    def test_inventory_canonical_ambiguity_requires_manual_resolution(self) -> None:
        record = {
            "caption_id": "c-canonical-ambiguous",
            "caption": "The sun shines.",
            "tokens": [
                token(0, "The", "the", "DET", "det", 1, tag="DT"),
                token(1, "sun", "sun", "NOUN", "nsubj", 2),
                token(2, "shines", "shine", "VERB", "ROOT", 2),
            ],
            "noun_chunks": [chunk("The sun", 1, 0, 2, "sun")],
        }
        tmp_path = _stage4_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory_path = tmp_path / "observed_object_span_inventory.tsv"
            inventory_path.write_text(
                "\t".join(
                    [
                        "span_key",
                        "observed_surface",
                        "decision_status",
                        "decision_reason",
                        "selected_lookup_case",
                        "selected_query",
                        "all_oewn_synsets",
                        "all_oewn_lexfiles",
                        "selected_oewn_synset",
                        "selected_oewn_lexfile",
                        "objectness_gate",
                        "synset_lemmas",
                        "canonical_surface",
                        "canonical_label_key",
                        "canonical_selection_tag",
                        "canonical_candidate_lemmas",
                        "canonical_candidate_lemma_counts",
                        "synset_selection_tag",
                        "wn30_lemma_counts",
                    ]
                )
                + "\n"
                + "\t".join(
                    [
                        "sun",
                        "sun",
                        "chosen",
                        "selected_object_compatible",
                        "exact",
                        "sun",
                        "fake-sun-n",
                        "noun.object",
                        "fake-sun-n",
                        "noun.object",
                        "object_compatible",
                        "sun|Sun",
                        "",
                        "",
                        "ambiguous_wn30_tie_google_ngram_evidence_missing",
                        "sun|Sun",
                        "sun:42|Sun:42",
                        "selected_by_wn30_lemma_count",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(Stage4SynsetAmbiguityError) as caught:
                extract_raw_concepts_from_stage3_record(
                    record,
                    object_lookup=load_gpic_object_inventory(inventory_path),
                )
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

        self.assertIn("canonical_surface=''", str(caught.exception))
        self.assertIn("ambiguous_wn30_tie_google_ngram_evidence_missing", str(caught.exception))

    def test_conditional_inventory_synset_requires_manual_resolution(self) -> None:
        record = {
            "caption_id": "c-conditional",
            "caption": "A scene.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "scene", "scene", "NOUN", "ROOT", 1),
            ],
            "noun_chunks": [chunk("A scene", 1, 0, 2, "scene")],
        }
        tmp_path = _stage4_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory_path = tmp_path / "observed_object_span_inventory.tsv"
            inventory_path.write_text(
                "\t".join(
                    [
                        "span_key",
                        "observed_surface",
                        "decision_status",
                        "decision_reason",
                        "selected_lookup_case",
                        "selected_query",
                        "all_oewn_synsets",
                        "all_oewn_lexfiles",
                        "selected_oewn_synset",
                        "selected_oewn_lexfile",
                        "objectness_gate",
                        "synset_lemmas",
                        "synset_selection_tag",
                        "wn30_lemma_counts",
                    ]
                )
                + "\n"
                + "\t".join(
                    [
                        "scene",
                        "scene",
                        "needs_manual",
                        "manual_objectness_required",
                        "test_inventory",
                        "scene",
                        "fake-scene-n",
                        "noun.location",
                        "fake-scene-n",
                        "noun.location",
                        "conditional",
                        "scene",
                        "selected_by_wn30_lemma_count",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(Stage4SynsetAmbiguityError):
                extract_raw_concepts_from_stage3_record(
                    record,
                    object_lookup=load_gpic_object_inventory(inventory_path),
                )
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

    def test_excluded_inventory_row_is_counted_with_status_metadata(self) -> None:
        record = {
            "caption_id": "c-excluded-counted",
            "caption": "Muted colors.",
            "tokens": [
                token(0, "Muted", "muted", "ADJ", "amod", 1, tag="JJ"),
                token(1, "colors", "color", "NOUN", "ROOT", 1, tag="NNS"),
            ],
            "noun_chunks": [chunk("Muted colors", 1, 0, 2, "colors")],
        }
        tmp_path = _stage4_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory_path = tmp_path / "observed_object_span_inventory.tsv"
            inventory_path.write_text(
                "\t".join(
                    [
                        "span_key",
                        "observed_surface",
                        "decision_status",
                        "decision_reason",
                        "selected_lookup_case",
                        "selected_query",
                        "all_oewn_synsets",
                        "all_oewn_lexfiles",
                        "selected_oewn_synset",
                        "selected_oewn_lexfile",
                        "objectness_gate",
                        "synset_lemmas",
                        "synset_selection_tag",
                        "wn30_lemma_counts",
                    ]
                )
                + "\n"
                + "\t".join(
                    [
                        "colors",
                        "colors",
                        "excluded",
                        "resolved_excluded_visual_attribute_not_object_inventory_unit",
                        "test_inventory",
                        "color",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "unresolved_no_oewn_noun_synset",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = extract_raw_concepts_from_stage3_record(
                record,
                object_lookup=load_gpic_object_inventory(inventory_path),
            )
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

        object_mentions = [
            mention.to_dict()
            for mention in result.raw_mentions
            if mention.mention_type == "object"
        ]
        self.assertEqual(len(object_mentions), 1)
        self.assertEqual(object_mentions[0]["text"], "colors")
        self.assertEqual(object_mentions[0]["source_detail"]["decision_status"], "excluded")
        self.assertEqual(
            object_mentions[0]["source_detail"]["decision_reason"],
            "resolved_excluded_visual_attribute_not_object_inventory_unit",
        )
        self.assertFalse(object_mentions[0]["source_detail"]["has_oewn_noun_synset"])

    def test_runtime_surface_query_conflict_blocks_extraction_without_inventory(self) -> None:
        record = {
            "caption_id": "c-plural",
            "caption": "Two men stand.",
            "tokens": [
                token(0, "Two", "two", "NUM", "nummod", 1, tag="CD"),
                token(1, "men", "man", "NOUN", "nsubj", 2, tag="NNS"),
                token(2, "stand", "stand", "VERB", "ROOT", 2, tag="VBP"),
            ],
            "noun_chunks": [chunk("Two men", 1, 0, 2, "men")],
        }

        with self.assertRaisesRegex(
            Stage4SynsetAmbiguityError,
            "manual_surface_query_conflict_required",
        ):
            extract_raw_concepts_from_stage3_record(
                record,
                object_lookup=fake_plural_exact_and_lemma_hit_lookup,
            )

    def test_inventory_exact_row_wins_over_surface_changed_query_conflict(self) -> None:
        record = {
            "caption_id": "c-plural-inventory",
            "caption": "Two men stand.",
            "tokens": [
                token(0, "Two", "two", "NUM", "nummod", 1, tag="CD"),
                token(1, "men", "man", "NOUN", "nsubj", 2, tag="NNS"),
                token(2, "stand", "stand", "VERB", "ROOT", 2, tag="VBP"),
            ],
            "noun_chunks": [chunk("Two men", 1, 0, 2, "men")],
        }
        tmp_path = _stage4_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory_path = tmp_path / "object_inventory.tsv"
            inventory_path.write_text(
                "\t".join(
                    [
                        "span_key",
                        "observed_surface",
                        "decision_status",
                        "decision_reason",
                        "selected_lookup_case",
                        "selected_query",
                        "selected_oewn_synset",
                        "selected_oewn_lexfile",
                        "objectness_gate",
                        "synset_lemmas",
                        "canonical_surface",
                        "canonical_label_key",
                        "canonical_selection_tag",
                    ]
                )
                + "\n"
                + "\t".join(
                    [
                        "men",
                        "men",
                        "chosen",
                        "manual_object_synset_selected",
                        "manual_object_inventory_resolution",
                        "men",
                        "fake-men-n",
                        "noun.person",
                        "object_compatible",
                        "men",
                        "men",
                        "men",
                        "manual_canonical",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = extract_raw_concepts_from_stage3_record(
                record,
                object_lookup=load_gpic_object_inventory(inventory_path),
            )
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

        object_mentions = [
            mention.to_dict()
            for mention in result.raw_mentions
            if mention.mention_type == "object"
        ]
        self.assertEqual(len(object_mentions), 1)
        self.assertEqual(object_mentions[0]["text"], "men")
        self.assertEqual(object_mentions[0]["source_detail"]["lookup_query"], "men")
        self.assertEqual(
            object_mentions[0]["source_detail"]["selected_oewn_synset"],
            "fake-men-n",
        )

    def test_joined_variant_lookup_requires_manual_even_when_object_compatible(self) -> None:
        synset = FakeSynset("fake-blackshirt-n", "noun.person", ["Blackshirt"])

        lookup = _with_selected_synset("joined_variant", "blackshirt", (synset,))

        self.assertEqual(lookup.decision_status, "needs_manual")
        self.assertEqual(lookup.decision_reason, "manual_joined_variant_required")

    def test_exact_lookup_can_still_be_chosen_for_object_compatible_synset(self) -> None:
        synset = FakeSynset("fake-trash-can-n", "noun.artifact", ["trash_can"])

        lookup = _with_selected_synset("exact", "trash can", (synset,))

        self.assertEqual(lookup.decision_status, "chosen")
        self.assertEqual(lookup.decision_reason, "selected_object_compatible")

    def test_left_expanding_span_skips_determiner_start(self) -> None:
        record = {
            "caption_id": "c-det-start",
            "caption": "A man stands.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "man", "man", "NOUN", "nsubj", 2),
                token(2, "stands", "stand", "VERB", "ROOT", 2, tag="VBZ"),
            ],
            "noun_chunks": [chunk("A man", 1, 0, 2, "man")],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_determiner_start_polluted_lookup,
        )

        object_mentions = [
            mention.to_dict()
            for mention in result.raw_mentions
            if mention.mention_type == "object"
        ]
        self.assertEqual(len(object_mentions), 1)
        self.assertEqual(object_mentions[0]["text"], "man")
        self.assertEqual(object_mentions[0]["source_detail"]["lookup_query"], "man")

    def test_prepositional_object_is_not_action_patient(self) -> None:
        record = {
            "caption_id": "c2",
            "caption": "A dog sits on a bench.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "dog", "dog", "NOUN", "nsubj", 2),
                token(2, "sits", "sit", "VERB", "ROOT", 2, tag="VBZ"),
                token(3, "on", "on", "ADP", "prep", 2, tag="IN"),
                token(4, "a", "a", "DET", "det", 5, tag="DT"),
                token(5, "bench", "bench", "NOUN", "pobj", 3),
            ],
            "noun_chunks": [
                chunk("A dog", 1, 0, 2, "dog"),
                chunk("a bench", 5, 4, 6, "bench"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertIn(("event_role", "agent", "R16"), _edge_sig(edges))
        self.assertNotIn(("event_role", "patient", "R17"), _edge_sig(edges))
        self.assertNotIn(("relation", "on", "R18"), _edge_sig(edges))

    def test_conjunct_action_inherits_agent_from_source_action(self) -> None:
        record = {
            "caption_id": "c-action-agent-conj",
            "caption": "Dogs stand and move.",
            "tokens": [
                token(0, "Dogs", "dog", "NOUN", "nsubj", 1, tag="NNS"),
                token(1, "stand", "stand", "VERB", "ROOT", 1, tag="VBP"),
                token(2, "and", "and", "CCONJ", "cc", 3, tag="CC"),
                token(3, "move", "move", "VERB", "conj", 1, tag="VBP"),
            ],
            "noun_chunks": [chunk("Dogs", 0, 0, 1, "Dogs")],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        agent_edges = [
            edge
            for edge in edges
            if edge["edge_type"] == "event_role" and edge["label"] == "agent"
        ]

        self.assertEqual({edge["rule_id"] for edge in agent_edges}, {"R16", "R16.1"})
        inherited = next(edge for edge in agent_edges if edge["rule_id"] == "R16.1")
        self.assertEqual(inherited["source_detail"]["role_source"], "conj_agent_inheritance")
        self.assertEqual(inherited["source_detail"]["source_action_i"], 1)
        self.assertEqual(inherited["source_detail"]["target_action_i"], 3)
        self.assertEqual(inherited["source_detail"]["conj_head_i"], 1)
        self.assertEqual(inherited["source_detail"]["target_i"], 0)

    def test_chained_conjunct_action_inherits_agent_by_fixed_point(self) -> None:
        record = {
            "caption_id": "c-action-agent-conj-chain",
            "caption": "Dogs stand, move, and play.",
            "tokens": [
                token(0, "Dogs", "dog", "NOUN", "nsubj", 1, tag="NNS"),
                token(1, "stand", "stand", "VERB", "ROOT", 1, tag="VBP"),
                token(2, ",", ",", "PUNCT", "punct", 1, tag=","),
                token(3, "move", "move", "VERB", "conj", 1, tag="VBP"),
                token(4, ",", ",", "PUNCT", "punct", 3, tag=","),
                token(5, "and", "and", "CCONJ", "cc", 6, tag="CC"),
                token(6, "play", "play", "VERB", "conj", 3, tag="VBP"),
            ],
            "noun_chunks": [chunk("Dogs", 0, 0, 1, "Dogs")],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        inherited_edges = [
            edge
            for edge in edges
            if edge["edge_type"] == "event_role"
            and edge["label"] == "agent"
            and edge["rule_id"] == "R16.1"
        ]

        self.assertEqual(len(inherited_edges), 2)
        self.assertEqual(
            {edge["source_detail"]["target_action_i"] for edge in inherited_edges},
            {3, 6},
        )
        play_agent = next(
            edge for edge in inherited_edges if edge["source_detail"]["target_action_i"] == 6
        )
        self.assertEqual(play_agent["source_detail"]["source_action_i"], 3)
        self.assertEqual(play_agent["source_detail"]["conj_head_i"], 3)

    def test_conjunct_action_does_not_inherit_patient(self) -> None:
        record = {
            "caption_id": "c-action-no-patient-conj",
            "caption": "A man stands and holds a ball.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "man", "man", "NOUN", "nsubj", 2),
                token(2, "stands", "stand", "VERB", "ROOT", 2, tag="VBZ"),
                token(3, "and", "and", "CCONJ", "cc", 4, tag="CC"),
                token(4, "holds", "hold", "VERB", "conj", 2, tag="VBZ"),
                token(5, "a", "a", "DET", "det", 6, tag="DT"),
                token(6, "ball", "ball", "NOUN", "dobj", 4),
            ],
            "noun_chunks": [
                chunk("A man", 1, 0, 2, "man"),
                chunk("a ball", 6, 5, 7, "ball"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        patient_edges = [
            edge
            for edge in edges
            if edge["edge_type"] == "event_role" and edge["label"] == "patient"
        ]

        self.assertIn(("event_role", "agent", "R16.1"), _edge_sig(edges))
        self.assertEqual(len(patient_edges), 1)
        self.assertEqual(patient_edges[0]["rule_id"], "R17")
        self.assertEqual(patient_edges[0]["source_detail"]["action_i"], 4)
        self.assertEqual(patient_edges[0]["source_detail"]["target_i"], 6)

    def test_conjunct_action_does_not_inherit_agent_into_passive_target(self) -> None:
        record = {
            "caption_id": "c-action-agent-conj-passive-target",
            "caption": "People stand and a truck is parked.",
            "tokens": [
                token(0, "People", "people", "NOUN", "nsubj", 1, tag="NNS"),
                token(1, "stand", "stand", "VERB", "ROOT", 1, tag="VBP"),
                token(2, "and", "and", "CCONJ", "cc", 5, tag="CC"),
                token(3, "a", "a", "DET", "det", 4, tag="DT"),
                token(4, "truck", "truck", "NOUN", "nsubjpass", 5),
                token(5, "parked", "park", "VERB", "conj", 1, tag="VBN"),
                token(6, "is", "be", "AUX", "auxpass", 5, tag="VBZ"),
            ],
            "noun_chunks": [
                chunk("People", 0, 0, 1, "People"),
                chunk("a truck", 4, 3, 5, "truck"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertIn(("event_role", "agent", "R16"), _edge_sig(edges))
        self.assertNotIn(("event_role", "agent", "R16.1"), _edge_sig(edges))

    def test_selected_phrasal_action_prep_creates_patient_and_suppresses_relation(self) -> None:
        record = {
            "caption_id": "c-look-at",
            "caption": "A man look at a dog.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "man", "man", "NOUN", "nsubj", 2),
                token(2, "look", "look", "VERB", "ROOT", 2, tag="VB"),
                token(3, "at", "at", "ADP", "prep", 2, tag="IN"),
                token(4, "a", "a", "DET", "det", 5, tag="DT"),
                token(5, "dog", "dog", "NOUN", "pobj", 3),
            ],
            "noun_chunks": [
                chunk("A man", 1, 0, 2, "man"),
                chunk("a dog", 5, 4, 6, "dog"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            action_lookup=fake_action_lookup,
        )
        mentions = [mention.to_dict() for mention in result.raw_mentions]
        edges = [edge.to_dict() for edge in result.raw_edges]

        action_mentions = [
            mention for mention in mentions if mention["mention_type"] == "action"
        ]
        self.assertEqual(action_mentions[0]["text"], "look at")
        self.assertEqual(
            action_mentions[0]["source_detail"]["selected_token_indices"],
            [2, 3],
        )
        self.assertIn(("event_role", "agent", "R16"), _edge_sig(edges))
        self.assertIn(("event_role", "patient", "R17"), _edge_sig(edges))
        self.assertNotIn(("relation", "at", "R18"), _edge_sig(edges))

    def test_action_prep_before_verb_is_not_phrasal_action(self) -> None:
        record = {
            "caption_id": "c-frame-fronted-pp",
            "caption": "In the road, a man frame a dog.",
            "tokens": [
                token(0, "In", "in", "ADP", "prep", 5, tag="IN"),
                token(1, "the", "the", "DET", "det", 2, tag="DT"),
                token(2, "road", "road", "NOUN", "pobj", 0),
                token(3, ",", ",", "PUNCT", "punct", 5, tag=","),
                token(4, "man", "man", "NOUN", "nsubj", 5),
                token(5, "frame", "frame", "VERB", "ROOT", 5, tag="VBP"),
                token(6, "a", "a", "DET", "det", 7, tag="DT"),
                token(7, "dog", "dog", "NOUN", "dobj", 5),
            ],
            "noun_chunks": [
                chunk("the road", 2, 1, 3, "road"),
                chunk("a man", 4, 4, 5, "man"),
                chunk("a dog", 7, 6, 8, "dog"),
            ],
        }

        def frame_in_lookup(surface: str) -> _ActionLookupResult | None:
            key = " ".join(surface.strip().lower().split())
            if key != "frame in":
                return None
            synset = FakeSynset("fake-frame-in-v", "verb.contact", ["frame_in"])
            return _ActionLookupResult(
                lookup_case="test",
                query=key,
                synsets=(synset,),
                selected_synset=synset,
                synset_selection_tag="test_single_verb_synset",
                wn30_lemma_counts="",
                decision_status="chosen",
                decision_reason="selected_verb_synset",
            )

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            action_lookup=frame_in_lookup,
        )
        action_mentions = [
            mention.to_dict()
            for mention in result.raw_mentions
            if mention.mention_type == "action"
        ]

        self.assertEqual(action_mentions[0]["text"], "frame")
        self.assertEqual(action_mentions[0]["source_detail"]["selected_token_indices"], [5])
        self.assertEqual(action_mentions[0]["source_detail"]["prep_token_indices"], [])

    def test_ambiguous_action_synset_blocks_raw_extraction(self) -> None:
        record = {
            "caption_id": "c-ambiguous-action",
            "caption": "A sign marked a road.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "sign", "sign", "NOUN", "nsubj", 2),
                token(2, "marked", "mark", "VERB", "ROOT", 2, tag="VBD"),
                token(3, "a", "a", "DET", "det", 4, tag="DT"),
                token(4, "road", "road", "NOUN", "dobj", 2),
            ],
            "noun_chunks": [
                chunk("A sign", 1, 0, 2, "sign"),
                chunk("a road", 4, 3, 5, "road"),
            ],
        }

        with self.assertRaises(Stage4SynsetAmbiguityError):
            extract_raw_concepts_from_stage3_record(
                record,
                object_lookup=fake_object_lookup,
                action_lookup=fake_ambiguous_action_lookup,
            )

    def test_action_lookup_requires_exact_surface_lemma_before_morphy(self) -> None:
        sit_synset = FakeSynset("fake-sit-v", "verb.contact", ["sit"])
        oewn = FakeOewn(
            {
                ("sitting", "v"): (sit_synset,),
                ("sit", "v"): (sit_synset,),
            }
        )
        morphy = FakeMorphy({"sitting": {"v": {"sit"}}})

        lookup = _lookup_oewn_verb_synsets("sitting", oewn, morphy)

        self.assertEqual(lookup.lookup_case, "verb_head_morphy")
        self.assertEqual(lookup.query, "sit")
        self.assertEqual(lookup.selected_synset, sit_synset)
        self.assertEqual(lookup.decision_status, "chosen")

    def test_action_lookup_keeps_multiple_morphy_hits_manual(self) -> None:
        shin_synset = FakeSynset("fake-shin-v", "verb.motion", ["shin"])
        shine_synset = FakeSynset("fake-shine-v", "verb.weather", ["shine"])
        oewn = FakeOewn(
            {
                ("shining", "v"): (shin_synset, shine_synset),
                ("shin", "v"): (shin_synset,),
                ("shine", "v"): (shine_synset,),
            }
        )
        morphy = FakeMorphy({"shining": {"v": {"shin", "shine"}}})

        lookup = _lookup_oewn_verb_synsets("shining", oewn, morphy)

        self.assertEqual(lookup.lookup_case, "verb_head_morphy_ambiguous")
        self.assertEqual(lookup.query, "shin|shine")
        self.assertEqual(lookup.selected_synset, None)
        self.assertEqual(lookup.decision_status, "needs_manual")
        self.assertEqual(lookup.decision_reason, "manual_action_morphy_required")

    def test_gpic_action_inventory_lookup_drives_action_selection(self) -> None:
        record = {
            "caption_id": "c-action-inventory",
            "caption": "A light shines.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "light", "light", "NOUN", "nsubj", 2),
                token(2, "shines", "shine", "VERB", "ROOT", 2, tag="VBZ"),
            ],
            "noun_chunks": [chunk("A light", 1, 0, 2, "light")],
        }
        tmp_path = _stage4_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory_path = tmp_path / "observed_action_inventory.tsv"
            inventory_path.write_text(
                "\t".join(
                    [
                        "span_key",
                        "observed_surface",
                        "decision_status",
                        "decision_reason",
                        "selected_lookup_case",
                        "selected_query",
                        "all_oewn_synsets",
                        "all_oewn_lexfiles",
                        "selected_oewn_synset",
                        "selected_oewn_lexfile",
                        "synset_lemmas",
                        "synset_selection_tag",
                        "wn30_lemma_counts",
                    ]
                )
                + "\n"
                + "\t".join(
                    [
                        "shines",
                        "shines",
                        "chosen",
                        "manual_action_synset_selected",
                        "manual_action_inventory_resolution",
                        "shine",
                        "fake-shine-v",
                        "verb.weather",
                        "fake-shine-v",
                        "verb.weather",
                        "shine",
                        "manual_select",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = extract_raw_concepts_from_stage3_record(
                record,
                object_lookup=fake_object_lookup,
                action_lookup=load_gpic_action_inventory(inventory_path),
            )
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

        actions = [
            mention.to_dict()
            for mention in result.raw_mentions
            if mention.mention_type == "action"
        ]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["lemma"], "shine")
        self.assertEqual(
            actions[0]["source_detail"]["selected_oewn_synset"],
            "fake-shine-v",
        )

    def test_relation_requires_adp_head_and_pobj_to_be_existing_objects(self) -> None:
        record = {
            "caption_id": "c3",
            "caption": "A dog with a collar.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "dog", "dog", "NOUN", "ROOT", 1),
                token(2, "with", "with", "ADP", "prep", 1, tag="IN"),
                token(3, "a", "a", "DET", "det", 4, tag="DT"),
                token(4, "collar", "collar", "NOUN", "pobj", 2),
            ],
            "noun_chunks": [
                chunk("A dog", 1, 0, 2, "dog"),
                chunk("a collar", 4, 3, 5, "collar"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertIn(("relation", "with", "R18"), _edge_sig(edges))

    def test_single_adp_relation_expands_target_conj_chain(self) -> None:
        record = {
            "caption_id": "c-on-target-conj",
            "caption": "A dog on a bench and sign.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "dog", "dog", "NOUN", "ROOT", 1),
                token(2, "on", "on", "ADP", "prep", 1, tag="IN"),
                token(3, "a", "a", "DET", "det", 4, tag="DT"),
                token(4, "bench", "bench", "NOUN", "pobj", 2),
                token(5, "and", "and", "CCONJ", "cc", 4, tag="CC"),
                token(6, "sign", "sign", "NOUN", "conj", 4),
            ],
            "noun_chunks": [
                chunk("A dog", 1, 0, 2, "dog"),
                chunk("a bench", 4, 3, 5, "bench"),
                chunk("sign", 6, 6, 7, "sign"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        relations = [
            edge
            for edge in edges
            if edge["edge_type"] == "relation" and edge["rule_id"] == "R18"
        ]

        self.assertEqual(len(relations), 2)
        self.assertEqual({edge["source_detail"]["target_i"] for edge in relations}, {4, 6})
        self.assertEqual(
            {edge["source_detail"]["target_resolution"] for edge in relations},
            {"direct_pobj", "conj_of_pobj"},
        )
        conj_relation = next(
            edge for edge in relations if edge["source_detail"]["target_i"] == 6
        )
        self.assertEqual(conj_relation["source_detail"]["target_base_i"], 4)
        self.assertEqual(conj_relation["source_detail"]["conj_head_i"], 4)

    def test_preposition_mwe_relation_uses_canonical_span_and_suppresses_single_adp(self) -> None:
        record = {
            "caption_id": "c-front-of",
            "caption": "A dog in front of a house.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "dog", "dog", "NOUN", "ROOT", 1),
                token(2, "in", "in", "ADP", "prep", 1, tag="IN"),
                token(3, "front", "front", "NOUN", "pobj", 2),
                token(4, "of", "of", "ADP", "prep", 3, tag="IN"),
                token(5, "a", "a", "DET", "det", 6, tag="DT"),
                token(6, "house", "house", "NOUN", "pobj", 4),
            ],
            "noun_chunks": [
                chunk("A dog", 1, 0, 2, "dog"),
                chunk("front", 3, 3, 4, "front"),
                chunk("a house", 6, 5, 7, "house"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            preposition_mwe_lookup=(in_front_of_entry(),),
        )
        mentions = [mention.to_dict() for mention in result.raw_mentions]
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertNotIn(
            ("object", "front", "R12"),
            {(m["mention_type"], m["lemma"], m["rule_id"]) for m in mentions},
        )
        self.assertIn(("relation", "in front of", "R18.1"), _edge_sig(edges))
        self.assertNotIn(("relation", "of", "R18"), _edge_sig(edges))
        relation = next(edge for edge in edges if edge["rule_id"] == "R18.1")
        self.assertEqual(relation["source_detail"]["relation_components"], ["in", "front", "of"])
        self.assertEqual(relation["source_detail"]["matched_token_indices"], [2, 3, 4])

    def test_preposition_mwe_index_preserves_longest_overlap_policy(self) -> None:
        record = {
            "caption_id": "c-front-of-indexed",
            "caption": "A dog in front of a house.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "dog", "dog", "NOUN", "ROOT", 1),
                token(2, "in", "in", "ADP", "prep", 1, tag="IN"),
                token(3, "front", "front", "NOUN", "pobj", 2),
                token(4, "of", "of", "ADP", "prep", 3, tag="IN"),
                token(5, "a", "a", "DET", "det", 6, tag="DT"),
                token(6, "house", "house", "NOUN", "pobj", 4),
            ],
            "noun_chunks": [
                chunk("A dog", 1, 0, 2, "dog"),
                chunk("front", 3, 3, 4, "front"),
                chunk("a house", 6, 5, 7, "house"),
            ],
        }
        lookup = _build_preposition_mwe_index((front_of_entry(), in_front_of_entry()))

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            preposition_mwe_lookup=lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertIn(("relation", "in front of", "R18.1"), _edge_sig(edges))
        relation = next(edge for edge in edges if edge["rule_id"] == "R18.1")
        self.assertEqual(relation["source_detail"]["matched_token_indices"], [2, 3, 4])

    def test_action_attached_preposition_mwe_single_source_creates_relation(self) -> None:
        record = {
            "caption_id": "c-stand-front-of",
            "caption": "Dogs stand in front of a house.",
            "tokens": [
                token(0, "Dogs", "dog", "NOUN", "nsubj", 1, tag="NNS"),
                token(1, "stand", "stand", "VERB", "ROOT", 1, tag="VBP"),
                token(2, "in", "in", "ADP", "prep", 1, tag="IN"),
                token(3, "front", "front", "NOUN", "pobj", 2),
                token(4, "of", "of", "ADP", "prep", 3, tag="IN"),
                token(5, "a", "a", "DET", "det", 6, tag="DT"),
                token(6, "house", "house", "NOUN", "pobj", 4),
            ],
            "noun_chunks": [
                chunk("Dogs", 0, 0, 1, "Dogs"),
                chunk("front", 3, 3, 4, "front"),
                chunk("a house", 6, 5, 7, "house"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            preposition_mwe_lookup=(in_front_of_entry(),),
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertIn(("relation", "in front of", "R18.1"), _edge_sig(edges))
        relation = next(edge for edge in edges if edge["edge_type"] == "relation")
        self.assertEqual(relation["source_detail"]["source_resolution"], "head_direct_object_child")
        self.assertEqual(relation["source_detail"]["source_dep"], "nsubj")
        self.assertEqual(relation["source_detail"]["candidate_source_count"], 1)

    def test_preposition_mwe_relation_expands_target_conj_chain(self) -> None:
        record = {
            "caption_id": "c-front-of-target-conj",
            "caption": "Dogs stand in front of a bench and sign.",
            "tokens": [
                token(0, "Dogs", "dog", "NOUN", "nsubj", 1, tag="NNS"),
                token(1, "stand", "stand", "VERB", "ROOT", 1, tag="VBP"),
                token(2, "in", "in", "ADP", "prep", 1, tag="IN"),
                token(3, "front", "front", "NOUN", "pobj", 2),
                token(4, "of", "of", "ADP", "prep", 3, tag="IN"),
                token(5, "a", "a", "DET", "det", 6, tag="DT"),
                token(6, "bench", "bench", "NOUN", "pobj", 4),
                token(7, "and", "and", "CCONJ", "cc", 6, tag="CC"),
                token(8, "sign", "sign", "NOUN", "conj", 6),
            ],
            "noun_chunks": [
                chunk("Dogs", 0, 0, 1, "Dogs"),
                chunk("front", 3, 3, 4, "front"),
                chunk("a bench", 6, 5, 7, "bench"),
                chunk("sign", 8, 8, 9, "sign"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            preposition_mwe_lookup=(in_front_of_entry(),),
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        relations = [
            edge
            for edge in edges
            if edge["edge_type"] == "relation" and edge["rule_id"] == "R18.1"
        ]

        self.assertNotIn(
            ("ambiguous_relation_candidate", "in front of", "R18.1"),
            _edge_sig(edges),
        )
        self.assertEqual(len(relations), 2)
        self.assertEqual({edge["source_detail"]["target_i"] for edge in relations}, {6, 8})
        self.assertEqual(
            {edge["source_detail"]["target_resolution"] for edge in relations},
            {"direct_final_pobj", "conj_of_final_pobj"},
        )
        self.assertTrue(
            all(edge["source_detail"]["candidate_source_count"] == 1 for edge in relations)
        )
        self.assertTrue(
            all(edge["source_detail"]["candidate_target_count"] == 2 for edge in relations)
        )
        self.assertTrue(
            all(edge["source_detail"]["candidate_target_base_count"] == 1 for edge in relations)
        )
        conj_relation = next(
            edge for edge in relations if edge["source_detail"]["target_i"] == 8
        )
        self.assertEqual(conj_relation["source_detail"]["target_base_i"], 6)
        self.assertEqual(conj_relation["source_detail"]["conj_head_i"], 6)

    def test_action_attached_preposition_mwe_nsubjpass_source_creates_relation(self) -> None:
        record = {
            "caption_id": "c-parked-front-of",
            "caption": "A van is parked in front of a house.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "van", "van", "NOUN", "nsubjpass", 3),
                token(2, "is", "be", "AUX", "auxpass", 3, tag="VBZ"),
                token(3, "parked", "park", "VERB", "ROOT", 3, tag="VBN"),
                token(4, "in", "in", "ADP", "prep", 3, tag="IN"),
                token(5, "front", "front", "NOUN", "pobj", 4),
                token(6, "of", "of", "ADP", "prep", 5, tag="IN"),
                token(7, "a", "a", "DET", "det", 8, tag="DT"),
                token(8, "house", "house", "NOUN", "pobj", 6),
            ],
            "noun_chunks": [
                chunk("A van", 1, 0, 2, "van"),
                chunk("front", 5, 5, 6, "front"),
                chunk("a house", 8, 7, 9, "house"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            preposition_mwe_lookup=(in_front_of_entry(),),
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertIn(("relation", "in front of", "R18.1"), _edge_sig(edges))
        relation = next(edge for edge in edges if edge["edge_type"] == "relation")
        self.assertEqual(relation["source_detail"]["source_resolution"], "head_direct_object_child")
        self.assertEqual(relation["source_detail"]["source_dep"], "nsubjpass")
        self.assertEqual(relation["source_detail"]["candidate_source_count"], 1)

    def test_action_attached_preposition_mwe_attr_source_creates_relation(self) -> None:
        record = {
            "caption_id": "c-screen-front-of",
            "caption": "In front of a house, there is a screen.",
            "tokens": [
                token(0, "In", "in", "ADP", "prep", 7, tag="IN"),
                token(1, "front", "front", "NOUN", "pobj", 0),
                token(2, "of", "of", "ADP", "prep", 1, tag="IN"),
                token(3, "a", "a", "DET", "det", 4, tag="DT"),
                token(4, "house", "house", "NOUN", "pobj", 2),
                token(5, ",", ",", "PUNCT", "punct", 7, tag=","),
                token(6, "there", "there", "PRON", "expl", 7, tag="EX"),
                token(7, "is", "be", "AUX", "ROOT", 7, tag="VBZ"),
                token(8, "a", "a", "DET", "det", 9, tag="DT"),
                token(9, "screen", "screen", "NOUN", "attr", 7),
            ],
            "noun_chunks": [
                chunk("front", 1, 1, 2, "front"),
                chunk("a house", 4, 3, 5, "house"),
                chunk("a screen", 9, 8, 10, "screen"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            preposition_mwe_lookup=(in_front_of_entry(),),
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertIn(("relation", "in front of", "R18.1"), _edge_sig(edges))
        relation = next(edge for edge in edges if edge["edge_type"] == "relation")
        self.assertEqual(relation["source_detail"]["source_resolution"], "head_direct_object_child")
        self.assertEqual(relation["source_detail"]["source_dep"], "attr")
        self.assertEqual(relation["source_detail"]["candidate_source_count"], 1)

    def test_aux_attached_preposition_mwe_nsubj_source_creates_relation(self) -> None:
        record = {
            "caption_id": "c-legs-out-of-focus",
            "caption": "The swimmer's legs are out of focus.",
            "tokens": [
                token(0, "The", "the", "DET", "det", 2, tag="DT"),
                token(1, "swimmer", "swimmer", "NOUN", "poss", 2),
                token(2, "legs", "leg", "NOUN", "nsubj", 3, tag="NNS"),
                token(3, "are", "be", "AUX", "ROOT", 3, tag="VBP"),
                token(4, "out", "out", "ADP", "prep", 3, tag="IN"),
                token(5, "of", "of", "ADP", "prep", 4, tag="IN"),
                token(6, "focus", "focus", "NOUN", "pobj", 5),
            ],
            "noun_chunks": [
                chunk("The swimmer's legs", 2, 0, 3, "legs"),
                chunk("focus", 6, 6, 7, "focus"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            preposition_mwe_lookup=(out_of_entry(),),
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertIn(("relation", "out of", "R18.1"), _edge_sig(edges))
        relation = next(edge for edge in edges if edge["rule_id"] == "R18.1")
        self.assertEqual(relation["source_detail"]["source_resolution"], "head_direct_object_child")
        self.assertEqual(relation["source_detail"]["source_dep"], "nsubj")
        self.assertEqual(relation["source_detail"]["candidate_source_count"], 1)

    def test_preposition_mwe_missing_source_creates_ambiguous_candidate(self) -> None:
        record = {
            "caption_id": "c-standing-front-of",
            "caption": "A man speaks standing in front of a wall.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "man", "man", "NOUN", "nsubj", 2),
                token(2, "speaks", "speak", "VERB", "ROOT", 2, tag="VBZ"),
                token(3, "standing", "stand", "VERB", "advcl", 2, tag="VBG"),
                token(4, "in", "in", "ADP", "prep", 3, tag="IN"),
                token(5, "front", "front", "NOUN", "pobj", 4),
                token(6, "of", "of", "ADP", "prep", 5, tag="IN"),
                token(7, "a", "a", "DET", "det", 8, tag="DT"),
                token(8, "wall", "wall", "NOUN", "pobj", 6),
            ],
            "noun_chunks": [
                chunk("A man", 1, 0, 2, "man"),
                chunk("front", 5, 5, 6, "front"),
                chunk("a wall", 8, 7, 9, "wall"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            preposition_mwe_lookup=(in_front_of_entry(),),
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        candidate = next(edge for edge in edges if edge["rule_id"] == "R18.1")

        self.assertEqual(candidate["edge_type"], "ambiguous_relation_candidate")
        self.assertEqual(candidate["source_mention_id"], MISSING_SOURCE_MENTION_ID)
        self.assertEqual(candidate["label"], "in front of")
        self.assertEqual(candidate["source_detail"]["candidate_source_count"], 0)
        self.assertEqual(candidate["source_detail"]["candidate_target_count"], 1)
        self.assertEqual(candidate["source_detail"]["source_endpoint_status"], "source_missing")
        self.assertEqual(candidate["source_detail"]["target_endpoint_status"], "target_resolved")
        self.assertEqual(candidate["source_detail"]["ambiguity_scope"], "source_missing")

    def test_action_attached_preposition_mwe_multiple_sources_creates_candidates(self) -> None:
        record = {
            "caption_id": "c-show-front-of",
            "caption": "A dog shows a ball in front of a house.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "dog", "dog", "NOUN", "nsubj", 2),
                token(2, "shows", "show", "VERB", "ROOT", 2, tag="VBZ"),
                token(3, "a", "a", "DET", "det", 4, tag="DT"),
                token(4, "ball", "ball", "NOUN", "dobj", 2),
                token(5, "in", "in", "ADP", "prep", 2, tag="IN"),
                token(6, "front", "front", "NOUN", "pobj", 5),
                token(7, "of", "of", "ADP", "prep", 6, tag="IN"),
                token(8, "a", "a", "DET", "det", 9, tag="DT"),
                token(9, "house", "house", "NOUN", "pobj", 7),
            ],
            "noun_chunks": [
                chunk("A dog", 1, 0, 2, "dog"),
                chunk("a ball", 4, 3, 5, "ball"),
                chunk("front", 6, 6, 7, "front"),
                chunk("a house", 9, 8, 10, "house"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            preposition_mwe_lookup=(in_front_of_entry(),),
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        candidate_edges = [
            edge for edge in edges if edge["edge_type"] == "ambiguous_relation_candidate"
        ]

        self.assertNotIn(("relation", "in front of", "R18.1"), _edge_sig(edges))
        self.assertEqual(len(candidate_edges), 2)
        self.assertEqual(
            {edge["source_detail"]["source_dep"] for edge in candidate_edges},
            {"nsubj", "dobj"},
        )
        self.assertTrue(
            all(edge["source_detail"]["candidate_source_count"] == 2 for edge in candidate_edges)
        )
        self.assertTrue(
            all(edge["source_detail"]["candidate_target_count"] == 1 for edge in candidate_edges)
        )

    def test_preposition_mwe_multiple_targets_creates_ambiguous_candidate_edges(self) -> None:
        record = {
            "caption_id": "c-stand-front-of-multiple-targets",
            "caption": "Dogs stand in front of a bench and sign.",
            "tokens": [
                token(0, "Dogs", "dog", "NOUN", "nsubj", 1, tag="NNS"),
                token(1, "stand", "stand", "VERB", "ROOT", 1, tag="VBP"),
                token(2, "in", "in", "ADP", "prep", 1, tag="IN"),
                token(3, "front", "front", "NOUN", "pobj", 2),
                token(4, "of", "of", "ADP", "prep", 3, tag="IN"),
                token(5, "a", "a", "DET", "det", 6, tag="DT"),
                token(6, "bench", "bench", "NOUN", "pobj", 4),
                token(7, "and", "and", "CCONJ", "cc", 8),
                token(8, "sign", "sign", "NOUN", "pobj", 4),
            ],
            "noun_chunks": [
                chunk("Dogs", 0, 0, 1, "Dogs"),
                chunk("front", 3, 3, 4, "front"),
                chunk("a bench", 6, 5, 7, "bench"),
                chunk("sign", 8, 8, 9, "sign"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            preposition_mwe_lookup=(in_front_of_entry(),),
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        candidate_edges = [
            edge for edge in edges if edge["edge_type"] == "ambiguous_relation_candidate"
        ]

        self.assertNotIn(("relation", "in front of", "R18.1"), _edge_sig(edges))
        self.assertEqual(len(candidate_edges), 2)
        self.assertEqual(
            {edge["target_mention_id"] for edge in candidate_edges},
            {"m1", "m2"},
        )
        self.assertTrue(
            all(edge["source_detail"]["candidate_source_count"] == 1 for edge in candidate_edges)
        )
        self.assertTrue(
            all(edge["source_detail"]["candidate_target_count"] == 2 for edge in candidate_edges)
        )
        self.assertTrue(
            all(edge["source_detail"]["ambiguity_scope"] == "target" for edge in candidate_edges)
        )

    def test_preposition_mwe_tokens_are_excluded_from_action_candidates(self) -> None:
        record = {
            "caption_id": "c-action-mwe-exclusion",
            "caption": "A frame in front of a house.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "frame", "frame", "VERB", "ROOT", 1, tag="VB"),
                token(2, "in", "in", "ADP", "prep", 1, tag="IN"),
                token(3, "front", "front", "NOUN", "pobj", 2),
                token(4, "of", "of", "ADP", "prep", 3, tag="IN"),
                token(5, "a", "a", "DET", "det", 6, tag="DT"),
                token(6, "house", "house", "NOUN", "pobj", 4),
            ],
            "noun_chunks": [chunk("a house", 6, 5, 7, "house")],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
            action_lookup=fake_frame_in_action_lookup,
            preposition_mwe_lookup=(in_front_of_entry(),),
        )
        actions = [
            mention.to_dict()
            for mention in result.raw_mentions
            if mention.mention_type == "action"
        ]

        self.assertEqual(actions[0]["text"], "frame")
        self.assertEqual(actions[0]["source_detail"]["selected_token_indices"], [1])

    def test_acl_action_inherits_agent_from_head_object(self) -> None:
        record = {
            "caption_id": "c-acl-agent",
            "caption": "A man holding a ball.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "man", "man", "NOUN", "ROOT", 1),
                token(2, "holding", "hold", "VERB", "acl", 1, tag="VBG"),
                token(3, "a", "a", "DET", "det", 4, tag="DT"),
                token(4, "ball", "ball", "NOUN", "dobj", 2),
            ],
            "noun_chunks": [
                chunk("A man", 1, 0, 2, "man"),
                chunk("a ball", 4, 3, 5, "ball"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        acl_agent = [
            edge
            for edge in edges
            if edge["edge_type"] == "event_role"
            and edge["label"] == "agent"
            and edge["rule_id"] == "R16.3"
        ]

        self.assertNotIn(("event_role", "agent", "R16"), _edge_sig(edges))
        self.assertIn(("event_role", "patient", "R17"), _edge_sig(edges))
        self.assertEqual(len(acl_agent), 1)
        self.assertEqual(acl_agent[0]["source_detail"]["dep"], "acl")
        self.assertEqual(acl_agent[0]["source_detail"]["target_i"], 1)
        self.assertEqual(acl_agent[0]["source_detail"]["role_source"], "acl_head_object_agent")

    def test_acl_action_does_not_add_agent_when_direct_agent_exists(self) -> None:
        record = {
            "caption_id": "c-acl-agent-existing",
            "caption": "A man dogs holding a ball.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "man", "man", "NOUN", "ROOT", 1),
                token(2, "dogs", "dog", "NOUN", "nsubj", 3, tag="NNS"),
                token(3, "holding", "hold", "VERB", "acl", 1, tag="VBG"),
                token(4, "a", "a", "DET", "det", 5, tag="DT"),
                token(5, "ball", "ball", "NOUN", "dobj", 3),
            ],
            "noun_chunks": [
                chunk("A man", 1, 0, 2, "man"),
                chunk("dogs", 2, 2, 3, "dogs"),
                chunk("a ball", 5, 4, 6, "ball"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        agent_edges = [
            edge
            for edge in edges
            if edge["edge_type"] == "event_role" and edge["label"] == "agent"
        ]

        self.assertEqual({edge["rule_id"] for edge in agent_edges}, {"R16"})
        self.assertNotIn(("event_role", "agent", "R16.3"), _edge_sig(edges))
        self.assertEqual(agent_edges[0]["source_detail"]["target_i"], 2)

    def test_acl_action_does_not_inherit_agent_into_passive_like_action(self) -> None:
        record = {
            "caption_id": "c-acl-agent-passive-like",
            "caption": "A ball held by a man.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "ball", "ball", "NOUN", "ROOT", 1),
                token(2, "held", "hold", "VERB", "acl", 1, tag="VBN"),
                token(3, "by", "by", "ADP", "agent", 2, tag="IN"),
                token(4, "a", "a", "DET", "det", 5, tag="DT"),
                token(5, "man", "man", "NOUN", "pobj", 3),
            ],
            "noun_chunks": [
                chunk("A ball", 1, 0, 2, "ball"),
                chunk("a man", 5, 4, 6, "man"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertNotIn(("event_role", "agent", "R16.3"), _edge_sig(edges))

    def test_acl_action_does_not_inherit_agent_from_vbn_modifier(self) -> None:
        record = {
            "caption_id": "c-acl-agent-vbn",
            "caption": "A ball placed near a wall.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "ball", "ball", "NOUN", "ROOT", 1),
                token(2, "placed", "place", "VERB", "acl", 1, tag="VBN"),
                token(3, "near", "near", "ADP", "prep", 2, tag="IN"),
                token(4, "a", "a", "DET", "det", 5, tag="DT"),
                token(5, "wall", "wall", "NOUN", "pobj", 3),
            ],
            "noun_chunks": [
                chunk("A ball", 1, 0, 2, "ball"),
                chunk("a wall", 5, 4, 6, "wall"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertNotIn(("event_role", "agent", "R16.3"), _edge_sig(edges))

    def test_nsubjpass_is_normalized_to_passive_patient(self) -> None:
        record = {
            "caption_id": "c4",
            "caption": "A ball is held.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "ball", "ball", "NOUN", "nsubjpass", 3),
                token(2, "is", "be", "AUX", "auxpass", 3, tag="VBZ"),
                token(3, "held", "hold", "VERB", "ROOT", 3, tag="VBN"),
            ],
            "noun_chunks": [chunk("A ball", 1, 0, 2, "ball")],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        passive_patient = [
            edge
            for edge in edges
            if edge["edge_type"] == "event_role"
            and edge["label"] == "patient"
            and edge["rule_id"] == "R17.1"
        ]

        self.assertNotIn(("event_role", "agent", "R16"), _edge_sig(edges))
        self.assertEqual(len(passive_patient), 1)
        self.assertEqual(passive_patient[0]["source_detail"]["dep"], "nsubjpass")
        self.assertEqual(passive_patient[0]["source_detail"]["raw_role"], "theme")
        self.assertEqual(
            passive_patient[0]["source_detail"]["voice_normalization"],
            "passive_to_active",
        )

    def test_passive_by_phrase_creates_agent_only_with_passive_subject(self) -> None:
        record = {
            "caption_id": "c-passive-by",
            "caption": "A ball is held by a man.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "ball", "ball", "NOUN", "nsubjpass", 3),
                token(2, "is", "be", "AUX", "auxpass", 3, tag="VBZ"),
                token(3, "held", "hold", "VERB", "ROOT", 3, tag="VBN"),
                token(4, "by", "by", "ADP", "agent", 3, tag="IN"),
                token(5, "a", "a", "DET", "det", 6, tag="DT"),
                token(6, "man", "man", "NOUN", "pobj", 4),
            ],
            "noun_chunks": [
                chunk("A ball", 1, 0, 2, "ball"),
                chunk("a man", 6, 5, 7, "man"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]
        passive_agent = [
            edge
            for edge in edges
            if edge["edge_type"] == "event_role"
            and edge["label"] == "agent"
            and edge["rule_id"] == "R16.2"
        ]

        self.assertIn(("event_role", "patient", "R17.1"), _edge_sig(edges))
        self.assertNotIn(("event_role", "agent", "R16"), _edge_sig(edges))
        self.assertEqual(len(passive_agent), 1)
        self.assertEqual(passive_agent[0]["source_detail"]["dep"], "agent")
        self.assertEqual(passive_agent[0]["source_detail"]["by_i"], 4)
        self.assertEqual(passive_agent[0]["source_detail"]["target_i"], 6)
        self.assertEqual(passive_agent[0]["source_detail"]["raw_role"], "by_agent_or_causer")
        self.assertEqual(
            passive_agent[0]["source_detail"]["voice_normalization"],
            "passive_to_active",
        )

    def test_active_by_phrase_does_not_create_passive_agent(self) -> None:
        record = {
            "caption_id": "c-active-by",
            "caption": "A man walks by a river.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 1, tag="DT"),
                token(1, "man", "man", "NOUN", "nsubj", 2),
                token(2, "walks", "walk", "VERB", "ROOT", 2, tag="VBZ"),
                token(3, "by", "by", "ADP", "prep", 2, tag="IN"),
                token(4, "a", "a", "DET", "det", 5, tag="DT"),
                token(5, "river", "river", "NOUN", "pobj", 3),
            ],
            "noun_chunks": [
                chunk("A man", 1, 0, 2, "man"),
                chunk("a river", 5, 4, 6, "river"),
            ],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertIn(("event_role", "agent", "R16"), _edge_sig(edges))
        self.assertNotIn(("event_role", "agent", "R16.2"), _edge_sig(edges))

    def test_run_stage4_extract_raw_writes_outputs(self) -> None:
        record = {
            "caption_id": "c5",
            "caption": "A brown dog.",
            "tokens": [
                token(0, "A", "a", "DET", "det", 2, tag="DT"),
                token(1, "brown", "brown", "ADJ", "amod", 2, tag="JJ"),
                token(2, "dog", "dog", "NOUN", "ROOT", 2),
            ],
            "noun_chunks": [chunk("A brown dog", 2, 0, 3, "dog")],
        }
        tmp_path = _stage4_temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            input_path = tmp_path / "stage3_records.jsonl"
            raw_mentions_path = tmp_path / "raw_mentions.jsonl"
            raw_edges_path = tmp_path / "raw_edges.jsonl"
            summary_path = tmp_path / "summary.jsonl"
            write_jsonl(input_path, [record])

            summary = run_stage4_extract_raw(
                input_path,
                raw_mentions_path=raw_mentions_path,
                raw_edges_path=raw_edges_path,
                summary_path=summary_path,
                object_lookup=fake_object_lookup,
            )

            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["raw_mention_total"], 2)
            self.assertEqual(summary["raw_edge_total"], 1)
            self.assertEqual(len(list(iter_jsonl(raw_mentions_path))), 2)
            self.assertEqual(len(list(iter_jsonl(raw_edges_path))), 1)
            self.assertNotIn('": ', raw_mentions_path.read_text(encoding="utf-8"))
            self.assertNotIn('": ', raw_edges_path.read_text(encoding="utf-8"))
            self.assertEqual(list(iter_jsonl(summary_path))[0]["raw_mention_total"], 2)
        finally:
            for path in sorted(tmp_path.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_path.rmdir()

    def test_nmod_inside_noun_chunk_is_attribute_modifier(self) -> None:
        record = {
            "caption_id": "c6",
            "caption": "Players wear maroon jerseys.",
            "tokens": [
                token(0, "maroon", "maroon", "NOUN", "nmod", 1, tag="NN"),
                token(1, "jerseys", "jersey", "NOUN", "ROOT", 1, tag="NNS"),
            ],
            "noun_chunks": [chunk("maroon jerseys", 1, 0, 2, "jerseys")],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        mentions = [mention.to_dict() for mention in result.raw_mentions]
        edges = [edge.to_dict() for edge in result.raw_edges]

        self.assertTrue(
            any(
                mention["mention_type"] == "attribute"
                and mention["text"] == "maroon"
                and mention["rule_id"] == "R13"
                for mention in mentions
            )
        )
        self.assertIn(("has_attribute", "has_attribute", "R13"), _edge_sig(edges))

    def test_conjunct_attribute_modifier_inherits_from_base_modifier(self) -> None:
        record = {
            "caption_id": "c-conj-attr",
            "caption": "Players wear maroon and yellow jerseys.",
            "tokens": [
                token(0, "maroon", "maroon", "NOUN", "nmod", 3, tag="NN"),
                token(1, "and", "and", "CCONJ", "cc", 0, tag="CC"),
                token(2, "yellow", "yellow", "ADJ", "conj", 0, tag="JJ"),
                token(3, "jerseys", "jersey", "NOUN", "ROOT", 3, tag="NNS"),
            ],
            "noun_chunks": [chunk("maroon and yellow jerseys", 3, 0, 4, "jerseys")],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        mentions = [mention.to_dict() for mention in result.raw_mentions]
        edges = [edge.to_dict() for edge in result.raw_edges]
        attribute_mentions = [mention for mention in mentions if mention["mention_type"] == "attribute"]
        attribute_edges = [edge for edge in edges if edge["edge_type"] == "has_attribute"]

        self.assertEqual([mention["text"] for mention in attribute_mentions], ["maroon", "yellow"])
        self.assertEqual(len(attribute_edges), 2)
        yellow = next(mention for mention in attribute_mentions if mention["text"] == "yellow")
        self.assertEqual(yellow["source_detail"].get("modifier_source"), "conj_of_attribute_modifier")
        self.assertEqual(yellow["source_detail"].get("conj_head_i"), 0)

    def test_chained_conjunct_attribute_modifier_inherits_from_base_modifier(self) -> None:
        record = {
            "caption_id": "c-conj-attr-chain",
            "caption": "Players wear blue, white, and yellow jerseys.",
            "tokens": [
                token(0, "blue", "blue", "ADJ", "amod", 5, tag="JJ"),
                token(1, ",", ",", "PUNCT", "punct", 0, tag=","),
                token(2, "white", "white", "ADJ", "conj", 0, tag="JJ"),
                token(3, "and", "and", "CCONJ", "cc", 2, tag="CC"),
                token(4, "yellow", "yellow", "ADJ", "conj", 2, tag="JJ"),
                token(5, "jerseys", "jersey", "NOUN", "ROOT", 5, tag="NNS"),
            ],
            "noun_chunks": [chunk("blue, white, and yellow jerseys", 5, 0, 6, "jerseys")],
        }

        result = extract_raw_concepts_from_stage3_record(
            record,
            object_lookup=fake_object_lookup,
        )
        mentions = [mention.to_dict() for mention in result.raw_mentions]
        attribute_mentions = [mention for mention in mentions if mention["mention_type"] == "attribute"]

        self.assertEqual([mention["text"] for mention in attribute_mentions], ["blue", "white", "yellow"])
        yellow = next(mention for mention in attribute_mentions if mention["text"] == "yellow")
        self.assertEqual(yellow["source_detail"].get("modifier_source"), "conj_of_attribute_modifier")
        self.assertEqual(yellow["source_detail"].get("conj_head_i"), 2)


def can_load_trf_model() -> bool:
    if spacy is None:
        return False
    return importlib.util.find_spec(DEFAULT_STAGE3_MODEL) is not None


@unittest.skipUnless(can_load_trf_model(), "en_core_web_trf is not installed")
class Stage4DocDirectExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.nlp = make_stage3_nlp()

    def test_doc_direct_path_matches_stage3_record_path(self) -> None:
        rows = [
            {
                "key": "k-doc-direct",
                "caption": "A light brown dog sits on a wooden bench.",
                "caption_type": "short",
            },
            {
                "key": "k-doc-direct-conj-mwe",
                "caption": "White and light blue jerseys are displayed.",
                "caption_type": "short",
            },
        ]
        attribute_mwe_lookup = _attribute_mwe_index("light brown", "light blue")

        stage3_records = list(
            iter_stage3_records_from_rows(
                rows,
                nlp=self.nlp,
                batch_size=1,
            )
        )
        annotated_docs = list(
            iter_annotated_docs_from_rows(
                rows,
                nlp=self.nlp,
                batch_size=1,
            )
        )

        for stage3_record, annotated in zip(
            stage3_records,
            annotated_docs,
            strict=True,
        ):
            record_result = extract_raw_concepts_from_stage3_record(
                stage3_record.to_dict(),
                object_lookup=fake_object_lookup,
                attribute_mwe_lookup=attribute_mwe_lookup,
            )
            doc_result = extract_raw_concepts_from_doc(
                annotated.caption_id,
                annotated.doc,
                object_lookup=fake_object_lookup,
                attribute_mwe_lookup=attribute_mwe_lookup,
            )

            self.assertEqual(
                [mention.to_dict() for mention in doc_result.raw_mentions],
                [mention.to_dict() for mention in record_result.raw_mentions],
            )
            self.assertEqual(
                [edge.to_dict() for edge in doc_result.raw_edges],
                [edge.to_dict() for edge in record_result.raw_edges],
            )

        conj_attributes = [
            mention.text
            for mention in record_result.raw_mentions
            if mention.mention_type == "attribute"
        ]
        self.assertIn("light blue", conj_attributes)
        self.assertNotIn("blue", conj_attributes)


def _edge_sig(edges: list[dict[str, object]]) -> set[tuple[object, object, object]]:
    return {(edge["edge_type"], edge["label"], edge["rule_id"]) for edge in edges}


def _attribute_mwe_row(surface: str, *, token_count: int = 2) -> dict[str, str]:
    return {
        "span_key": surface,
        "attribute_unit_type": "mwe",
        "span_token_count": str(token_count),
        "anchor_token_offset": str(token_count - 1),
        "lookup_forms": surface,
        "attribute_mwe_rule_version": ATTRIBUTE_MWE_RULE_VERSION,
        "decision_status": "chosen",
        "canonical_surface": surface.replace(" ", "_"),
    }


def _attribute_mwe_index(*surfaces: str) -> ResolvedAttributeMweIndex:
    return ResolvedAttributeMweIndex(
        {surface: _attribute_mwe_row(surface) for surface in surfaces}
    )


class FakeSynset:
    def __init__(self, synset_id: str, lexfile: str, lemmas: list[str]) -> None:
        self.id = synset_id
        self._lexfile = lexfile
        self._lemmas = lemmas

    def lexfile(self) -> str:
        return self._lexfile

    def lemmas(self) -> list[str]:
        return self._lemmas


class FakeOewn:
    def __init__(self, synsets_by_query: dict[tuple[str, str], tuple[FakeSynset, ...]]) -> None:
        self._synsets_by_query = synsets_by_query

    def synsets(self, query: str, *, pos: str) -> tuple[FakeSynset, ...]:
        return self._synsets_by_query.get((query, pos), ())


class FakeMorphy:
    def __init__(self, results_by_query: dict[str, dict[str, set[str]]]) -> None:
        self._results_by_query = results_by_query

    def __call__(self, query: str, pos: str) -> dict[str, set[str]]:
        return self._results_by_query.get(query, {})


def fake_object_lookup(surface: str) -> _ObjectLookupResult | None:
    key = " ".join(surface.strip().lower().split())
    synsets = {
        "man": FakeSynset("fake-man-n", "noun.person", ["man"]),
        "dog": FakeSynset("fake-dog-n", "noun.animal", ["dog"]),
        "dogs": FakeSynset("fake-dog-n", "noun.animal", ["dog"]),
        "people": FakeSynset("fake-people-n", "noun.person", ["people"]),
        "ball": FakeSynset("fake-ball-n", "noun.artifact", ["ball"]),
        "bench": FakeSynset("fake-bench-n", "noun.artifact", ["bench"]),
        "collar": FakeSynset("fake-collar-n", "noun.artifact", ["collar"]),
        "house": FakeSynset("fake-house-n", "noun.artifact", ["house"]),
        "jerseys": FakeSynset("fake-jersey-n", "noun.artifact", ["jersey"]),
        "legs": FakeSynset("fake-leg-n", "noun.body", ["leg"]),
        "focus": FakeSynset("fake-focus-n", "noun.attribute", ["focus"]),
        "river": FakeSynset("fake-river-n", "noun.object", ["river"]),
        "road": FakeSynset("fake-road-n", "noun.artifact", ["road"]),
        "screen": FakeSynset("fake-screen-n", "noun.artifact", ["screen"]),
        "sign": FakeSynset("fake-sign-n", "noun.artifact", ["sign"]),
        "truck": FakeSynset("fake-truck-n", "noun.artifact", ["truck"]),
        "van": FakeSynset("fake-van-n", "noun.artifact", ["van"]),
        "wall": FakeSynset("fake-wall-n", "noun.artifact", ["wall"]),
    }
    synset = synsets.get(key)
    if synset is None:
        return None
    return _ObjectLookupResult(
        lookup_case="test",
        query=key,
        synsets=(synset,),
        selected_synset=synset,
        synset_selection_tag="test_single_noun_synset",
        wn30_lemma_counts="",
        objectness_gate="object_compatible",
        decision_status="chosen",
        canonical_surface=synset.lemmas()[0],
        canonical_label_key=synset.lemmas()[0],
        canonical_selection_tag="selected_single_observed_variant_matched_synset_lemma",
    )


def fake_action_lookup(surface: str) -> _ActionLookupResult | None:
    key = " ".join(surface.strip().lower().split())
    if key != "look at":
        return None
    synset = FakeSynset("fake-look-at-v", "verb.perception", ["look_at"])
    return _ActionLookupResult(
        lookup_case="test",
        query=key,
        synsets=(synset,),
        selected_synset=synset,
        synset_selection_tag="test_single_verb_synset",
        wn30_lemma_counts="",
        decision_status="chosen",
        decision_reason="selected_verb_synset",
    )


def fake_frame_in_action_lookup(surface: str) -> _ActionLookupResult | None:
    key = " ".join(surface.strip().lower().split())
    if key != "frame in":
        return None
    synset = FakeSynset("fake-frame-in-v", "verb.contact", ["frame_in"])
    return _ActionLookupResult(
        lookup_case="test",
        query=key,
        synsets=(synset,),
        selected_synset=synset,
        synset_selection_tag="test_single_verb_synset",
        wn30_lemma_counts="",
        decision_status="chosen",
        decision_reason="selected_verb_synset",
    )


def in_front_of_entry() -> _PrepositionMweEntry:
    return _PrepositionMweEntry(
        surface="in front of",
        token_keys=("in", "front", "of"),
        canonical_relation="in front of",
        relation_components=("in", "front", "of"),
        initial_relation_token_offset=0,
        final_adp_token_offset=2,
        source="test",
    )


def front_of_entry() -> _PrepositionMweEntry:
    return _PrepositionMweEntry(
        surface="front of",
        token_keys=("front", "of"),
        canonical_relation="front of",
        relation_components=("front", "of"),
        initial_relation_token_offset=0,
        final_adp_token_offset=1,
        source="test",
    )


def out_of_entry() -> _PrepositionMweEntry:
    return _PrepositionMweEntry(
        surface="out of",
        token_keys=("out", "of"),
        canonical_relation="out of",
        relation_components=("out", "of"),
        initial_relation_token_offset=0,
        final_adp_token_offset=1,
        source="test",
    )


def fake_ambiguous_action_lookup(surface: str) -> _ActionLookupResult | None:
    key = " ".join(surface.strip().lower().split())
    if key != "marked":
        return None
    synsets = (
        FakeSynset("fake-mark-contact-v", "verb.contact", ["mark"]),
        FakeSynset("fake-mark-communication-v", "verb.communication", ["mark"]),
    )
    return _ActionLookupResult(
        lookup_case="test",
        query="mark",
        synsets=synsets,
        selected_synset=None,
        synset_selection_tag="ambiguous_wn30_tie",
        wn30_lemma_counts="fake-mark-contact-v:13|fake-mark-communication-v:13",
        decision_status="needs_manual",
        decision_reason="manual_action_synset_required",
    )


def fake_ambiguous_object_lookup(surface: str) -> _ObjectLookupResult | None:
    key = " ".join(surface.strip().lower().split())
    if key != "bat":
        return None
    synsets = (
        FakeSynset("fake-bat-animal-n", "noun.animal", ["bat"]),
        FakeSynset("fake-bat-artifact-n", "noun.artifact", ["bat"]),
    )
    return _ObjectLookupResult(
        lookup_case="test",
        query=key,
        synsets=synsets,
        selected_synset=None,
        synset_selection_tag="ambiguous_wn30_tie",
        wn30_lemma_counts="fake-bat-animal-n:2|fake-bat-artifact-n:2",
        objectness_gate="",
        decision_status="needs_manual",
    )


def fake_plural_exact_and_lemma_hit_lookup(surface: str) -> _ObjectLookupResult | None:
    key = " ".join(surface.strip().lower().split())
    if key == "man":
        synset = FakeSynset("fake-man-n", "noun.person", ["man"])
        return _ObjectLookupResult(
            lookup_case="test_lemma_first",
            query=key,
            synsets=(synset,),
            selected_synset=synset,
            synset_selection_tag="test_single_noun_synset",
            wn30_lemma_counts="",
            objectness_gate="object_compatible",
            decision_status="chosen",
            canonical_surface="man",
            canonical_label_key="man",
            canonical_selection_tag="selected_single_observed_variant_matched_synset_lemma",
        )
    if key == "men":
        synset = FakeSynset("fake-men-n", "noun.person", ["men"])
        return _ObjectLookupResult(
            lookup_case="test_exact",
            query=key,
            synsets=(synset,),
            selected_synset=synset,
            synset_selection_tag="test_single_noun_synset",
            wn30_lemma_counts="",
            objectness_gate="object_compatible",
            decision_status="chosen",
            canonical_surface="men",
            canonical_label_key="men",
            canonical_selection_tag="selected_single_observed_variant_matched_synset_lemma",
        )
    return None


def fake_determiner_start_polluted_lookup(surface: str) -> _ObjectLookupResult | None:
    key = " ".join(surface.strip().lower().split())
    if key == "a man":
        raise AssertionError("determiner-start object span should not be probed")
    if key == "man":
        synset = FakeSynset("fake-man-n", "noun.person", ["man"])
        return _ObjectLookupResult(
            lookup_case="test_root_after_det_skip",
            query=key,
            synsets=(synset,),
            selected_synset=synset,
            synset_selection_tag="test_single_noun_synset",
            wn30_lemma_counts="",
            objectness_gate="object_compatible",
            decision_status="chosen",
            canonical_surface="man",
            canonical_label_key="man",
            canonical_selection_tag="selected_single_observed_variant_matched_synset_lemma",
        )
    return None


def _stage4_temp_base() -> Path:
    roots = [
        os.environ.get("GPIC_TEST_TEMP_ROOT"),
        str(Path.cwd() / ".tmp_tests"),
        r"C:\Users\Public\Documents\ESTsoft\CreatorTemp",
        tempfile.gettempdir(),
    ]
    for root in roots:
        if not root:
            continue
        base = Path(root) / "stage4_extract_raw"
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / f"{uuid.uuid4().hex}.tmp"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return base
        except PermissionError:
            continue
    raise PermissionError("no writable temp directory for stage4 tests")


if __name__ == "__main__":
    unittest.main()
