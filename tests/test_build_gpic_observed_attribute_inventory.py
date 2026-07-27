import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


def _load_attribute_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_gpic_observed_attribute_inventory.py"
    spec = importlib.util.spec_from_file_location("build_gpic_observed_attribute_inventory", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


attribute_script = _load_attribute_script()


class FakeSynset:
    def __init__(self, synset_id: str, lexfile: str, lemmas: tuple[str, ...]) -> None:
        self.id = synset_id
        self._lexfile = lexfile
        self._lemmas = lemmas

    def lexfile(self) -> str:
        return self._lexfile

    def lemmas(self) -> list[str]:
        return list(self._lemmas)


class FakeObjectLookupResult:
    def __init__(
        self,
        query: str,
        *,
        canonical_surface: str = "",
        canonical_label_key: str = "",
    ) -> None:
        self.query = query
        self.synsets = (object(),)
        self.selected_synset = None
        self.canonical_surface = canonical_surface
        self.canonical_label_key = canonical_label_key


class FakeMweOewn:
    def __init__(self, synsets_by_query):
        self.synsets_by_query = synsets_by_query

    def synsets(self, query):
        return self.synsets_by_query.get(query, ())


class FakeMorphy:
    def __init__(self, results_by_surface):
        self.results_by_surface = results_by_surface

    def __call__(self, surface, pos):
        return self.results_by_surface.get(surface, {})


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


def chunk(text: str, root_i: int, start: int, end: int) -> dict[str, object]:
    return {
        "text": text,
        "root_i": root_i,
        "root_text": text.split()[-1],
        "root_lemma": text.split()[-1].lower(),
        "root_pos": "NOUN",
        "root_tag": "NN",
        "root_dep": "ROOT",
        "root_head_i": root_i,
        "root_head_text": text.split()[-1],
        "token_start": start,
        "token_end": end,
        "char_start": start * 2,
        "char_end": end * 2,
    }


class BuildGpicObservedAttributeInventoryTest(unittest.TestCase):
    def test_attribute_mwe_lookup_uses_separator_variant_and_lemma_evidence(self) -> None:
        synset = FakeSynset("fake-dark-brown-a", "adj.all", ("dark-brown",))
        oewn = FakeMweOewn({"dark-brown": (synset,)})

        result = attribute_script._lookup_attribute_mwe_surface(
            "dark brown",
            oewn=oewn,
            morphy=FakeMorphy({}),
        )

        self.assertEqual(result.lookup_case, "mwe_hyphen_variant")
        self.assertEqual(result.query, "dark-brown")
        self.assertEqual(result.synsets, (synset,))
        self.assertNotIn("darkbrown", result.lookup_forms)

    def test_attribute_mwe_lookup_morphs_only_anchor(self) -> None:
        synset = FakeSynset("fake-cube-shape-a", "adj.all", ("cube-shape",))
        oewn = FakeMweOewn({"cube-shape": (synset,)})

        result = attribute_script._lookup_attribute_mwe_surface(
            "cube shaped",
            oewn=oewn,
            morphy=FakeMorphy({"shaped": {"a": {"shape"}}}),
        )

        self.assertEqual(result.lookup_case, "mwe_anchor_morphy_hyphen")
        self.assertEqual(result.query, "cube-shape")
        self.assertNotIn("cubeshape", result.lookup_forms)

    def test_attribute_mwe_lookup_rejects_synset_without_matching_lemma(self) -> None:
        unrelated = FakeSynset("fake-unrelated-a", "adj.all", ("brown",))
        result = attribute_script._lookup_attribute_mwe_surface(
            "dark brown",
            oewn=FakeMweOewn({"dark brown": (unrelated,)}),
            morphy=FakeMorphy({}),
        )

        self.assertEqual(result.synsets, ())
        self.assertEqual(result.decision_reason, "no_oewn_attribute_synset")

    def test_oewn_valid_longest_attribute_mwe_suppresses_internal_singles(self) -> None:
        record = {
            "caption_id": "c-light-brown",
            "tokens": [
                token(0, "light", "light", "ADJ", "compound", 1, tag="JJ"),
                token(1, "brown", "brown", "ADJ", "amod", 2, tag="JJ"),
                token(2, "moth", "moth", "NOUN", "ROOT", 2, tag="NN"),
            ],
            "noun_chunks": [chunk("light brown moth", 2, 0, 3)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "moth" else None

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
            attribute_unit_type: str = "single_token",
        ):
            if attribute_unit_type != "mwe" or surface != "light brown":
                return None
            synset = FakeSynset("fake-light-brown-a", "adj.all", ("light_brown",))
            return attribute_script.AttributeLookupResult(
                "mwe_exact",
                "light brown",
                (synset,),
                synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
                lookup_forms=("light brown",),
            )

        rows, summary = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual([row["span_key"] for row in rows], ["light brown"])
        self.assertEqual(rows[0]["attribute_unit_type"], "mwe")
        self.assertEqual(rows[0]["span_token_count"], "2")
        self.assertEqual(rows[0]["anchor_token_offset"], "1")
        self.assertEqual(summary["attribute_unit_type_counts"], {"mwe": 1})

    def test_attribute_mwe_conjunction_is_split_by_branch(self) -> None:
        record = {
            "caption_id": "c-color-branches",
            "tokens": [
                token(0, "dark", "dark", "ADJ", "compound", 1, tag="JJ"),
                token(1, "brown", "brown", "ADJ", "amod", 5, tag="JJ"),
                token(2, "and", "and", "CCONJ", "cc", 1, tag="CC"),
                token(3, "bright", "bright", "ADJ", "compound", 4, tag="JJ"),
                token(4, "blue", "blue", "ADJ", "conj", 1, tag="JJ"),
                token(5, "jersey", "jersey", "NOUN", "ROOT", 5, tag="NN"),
            ],
            "noun_chunks": [chunk("dark brown and bright blue jersey", 5, 0, 6)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "jersey" else None

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
            attribute_unit_type: str = "single_token",
        ):
            if attribute_unit_type != "mwe" or surface not in {"dark brown", "bright blue"}:
                return None
            synset = FakeSynset(f"fake-{surface}-a", "adj.all", (surface.replace(" ", "_"),))
            return attribute_script.AttributeLookupResult(
                "mwe_exact",
                surface,
                (synset,),
                synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
                lookup_forms=(surface,),
            )

        rows, _ = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(
            {(row["attribute_unit_type"], row["span_key"]) for row in rows},
            {("mwe", "dark brown"), ("mwe", "bright blue")},
        )

    def test_conjunct_branch_mwe_is_counted_and_replaces_its_single_anchor(self) -> None:
        record = {
            "caption_id": "c-conj-light-blue",
            "tokens": [
                token(0, "white", "white", "ADJ", "amod", 4, tag="JJ"),
                token(1, "and", "and", "CCONJ", "cc", 0, tag="CC"),
                token(2, "light", "light", "ADJ", "amod", 3, tag="JJ"),
                token(3, "blue", "blue", "ADJ", "conj", 0, tag="JJ"),
                token(4, "jerseys", "jersey", "NOUN", "ROOT", 4, tag="NNS"),
            ],
            "noun_chunks": [chunk("white and light blue jerseys", 4, 0, 5)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "jerseys" else None

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
            attribute_unit_type: str = "single_token",
        ):
            if attribute_unit_type == "mwe" and surface == "light blue":
                synset = FakeSynset(
                    "fake-light-blue-a",
                    "adj.all",
                    ("light-blue",),
                )
                return attribute_script.AttributeLookupResult(
                    "mwe_hyphen_variant",
                    "light-blue",
                    (synset,),
                    synset,
                    "single_oewn_attribute_synset",
                    "",
                    "attribute_compatible",
                    "chosen",
                    "selected_attribute_compatible",
                    lookup_forms=("light blue", "light-blue"),
                )
            if attribute_unit_type == "single_token" and surface == "white":
                synset = FakeSynset("fake-white-a", "adj.all", ("white",))
                return attribute_script.AttributeLookupResult(
                    "exact",
                    "white",
                    (synset,),
                    synset,
                    "single_oewn_attribute_synset",
                    "",
                    "attribute_compatible",
                    "chosen",
                    "selected_attribute_compatible",
                )
            return None

        rows, _ = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(
            {
                (row["attribute_unit_type"], row["span_key"], row["count"])
                for row in rows
            },
            {
                ("mwe", "light blue", "1"),
                ("single_token", "white", "1"),
            },
        )

    def test_excluded_longer_candidate_is_retained_but_does_not_hide_shorter_mwe(
        self,
    ) -> None:
        record = {
            "caption_id": "c-light-blue-collared",
            "tokens": [
                token(0, "light", "light", "ADJ", "amod", 1, tag="JJ"),
                token(1, "blue", "blue", "ADJ", "amod", 3, tag="JJ"),
                token(2, "collared", "collared", "ADJ", "amod", 3, tag="JJ"),
                token(3, "shirt", "shirt", "NOUN", "ROOT", 3, tag="NN"),
            ],
            "noun_chunks": [chunk("light blue collared shirt", 3, 0, 4)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "shirt" else None

        def mwe_result(
            surface: str,
            *,
            status: str,
        ) -> attribute_script.AttributeLookupResult:
            synset = FakeSynset(
                f"fake-{surface}-a",
                "adj.all",
                (surface.replace(" ", "-"),),
            )
            return attribute_script.AttributeLookupResult(
                "mwe_hyphen_variant",
                surface.replace(" ", "-"),
                (synset,),
                synset if status == "chosen" else None,
                "manual_rejected_all_candidate_senses"
                if status == "excluded"
                else "single_oewn_attribute_synset",
                "",
                "attribute_compatible" if status == "chosen" else "",
                status,
                "selected_attribute_compatible"
                if status == "chosen"
                else "manual_reject_synset_meaning_not_attribute_mwe",
                lookup_forms=(surface, surface.replace(" ", "-")),
            )

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
            attribute_unit_type: str = "single_token",
        ):
            if attribute_unit_type != "mwe":
                return None
            if surface == "blue collared":
                return mwe_result(surface, status="excluded")
            if surface == "light blue":
                return mwe_result(surface, status="chosen")
            return None

        rows, _ = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
            attribute_unit_mode="mwe_only",
        )

        self.assertEqual(
            {
                (row["span_key"], row["decision_status"], row["count"])
                for row in rows
            },
            {
                ("blue collared", "excluded", "1"),
                ("light blue", "chosen", "1"),
            },
        )

    def test_object_core_wins_over_overlapping_attribute_mwe(self) -> None:
        record = {
            "caption_id": "c-bright-blue-sky",
            "tokens": [
                token(0, "bright", "bright", "ADJ", "amod", 1, tag="JJ"),
                token(1, "blue", "blue", "ADJ", "compound", 2, tag="JJ"),
                token(2, "sky", "sky", "NOUN", "ROOT", 2, tag="NN"),
            ],
            "noun_chunks": [chunk("bright blue sky", 2, 0, 3)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "blue sky" else None

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
            attribute_unit_type: str = "single_token",
        ):
            if attribute_unit_type == "mwe":
                raise AssertionError("MWE lookup must not cross the selected object core")
            synset = FakeSynset("fake-bright-a", "adj.all", ("bright",))
            return attribute_script.AttributeLookupResult(
                "exact",
                surface,
                (synset,),
                synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
            )

        rows, _ = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(
            [(row["attribute_unit_type"], row["span_key"]) for row in rows],
            [("single_token", "bright")],
        )

    def test_objectless_tag_list_segment_discovers_full_attribute_mwe(self) -> None:
        segment_tokens = [
            token(0, "bright", "bright", "ADJ", "compound", 1, tag="JJ"),
            token(1, "blue", "blue", "ADJ", "ROOT", 1, tag="JJ"),
        ]
        record = {
            "caption_id": "tag-bright-blue",
            "tokens": segment_tokens,
            "noun_chunks": [],
            "tag_segments": [
                {
                    "segment_id": "segment-0",
                    "text": "bright blue",
                    "tokens": segment_tokens,
                    "noun_chunks": [],
                }
            ],
            "meta": {"caption_shape": "tag_list"},
        }

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
            attribute_unit_type: str = "single_token",
        ):
            if attribute_unit_type != "mwe" or surface != "bright blue":
                return None
            synset = FakeSynset("fake-bright-blue-a", "adj.all", ("bright_blue",))
            return attribute_script.AttributeLookupResult(
                "mwe_exact",
                surface,
                (synset,),
                synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
                lookup_forms=(surface,),
            )

        rows, summary = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=lambda _surface: None,
            attribute_lookup=attribute_lookup,
            attribute_unit_mode="mwe_only",
        )

        self.assertEqual(
            [(row["attribute_unit_type"], row["span_key"]) for row in rows],
            [("mwe", "bright blue")],
        )
        self.assertEqual(summary["attribute_candidate_total"], 1)

    def test_consumed_object_span_tokens_are_not_attribute_candidates(self) -> None:
        record = {
            "caption_id": "c1",
            "tokens": [
                token(0, "black", "black", "ADJ", "amod", 2, tag="JJ"),
                token(1, "trash", "trash", "NOUN", "compound", 2),
                token(2, "can", "can", "NOUN", "ROOT", 2),
            ],
            "noun_chunks": [chunk("black trash can", 2, 0, 3)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "trash can" else None

        black_synset = FakeSynset("fake-black-a", "adj.all", ("black",))

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
        ):
            if surface != "black":
                return None
            return attribute_script.AttributeLookupResult(
                "exact",
                "black",
                (black_synset,),
                black_synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
            )

        rows, summary = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(summary["attribute_candidate_total"], 1)
        self.assertEqual([row["span_key"] for row in rows], ["black"])
        self.assertEqual(rows[0]["decision_status"], "chosen")
        self.assertEqual(rows[0]["selected_oewn_synset"], "fake-black-a")

    def test_object_lookup_span_modifier_remains_attribute_when_core_is_suffix(self) -> None:
        record = {
            "caption_id": "c1",
            "tokens": [
                token(0, "black", "black", "ADJ", "amod", 1, tag="JJ"),
                token(1, "top", "top", "NOUN", "ROOT", 1),
            ],
            "noun_chunks": [chunk("black top", 1, 0, 2)],
        }

        def object_lookup(surface: str):
            return (
                FakeObjectLookupResult(
                    surface,
                    canonical_surface="top",
                    canonical_label_key="top",
                )
                if surface == "black top"
                else None
            )

        black_synset = FakeSynset("fake-black-a", "adj.all", ("black",))

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
        ):
            if surface != "black":
                return None
            return attribute_script.AttributeLookupResult(
                "exact",
                "black",
                (black_synset,),
                black_synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
            )

        rows, summary = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(summary["attribute_candidate_total"], 1)
        self.assertEqual([row["span_key"] for row in rows], ["black"])

    def test_nmod_modifier_is_attribute_inventory_candidate(self) -> None:
        record = {
            "caption_id": "c1",
            "tokens": [
                token(0, "maroon", "maroon", "NOUN", "nmod", 1, tag="NN"),
                token(1, "jerseys", "jersey", "NOUN", "ROOT", 1, tag="NNS"),
            ],
            "noun_chunks": [chunk("maroon jerseys", 1, 0, 2)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "jerseys" else None

        maroon_synset = FakeSynset("fake-maroon-a", "adj.all", ("maroon",))

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
        ):
            if surface != "maroon":
                return None
            return attribute_script.AttributeLookupResult(
                "exact",
                "maroon",
                (maroon_synset,),
                maroon_synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
            )

        rows, summary = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(summary["attribute_candidate_total"], 1)
        self.assertEqual([row["span_key"] for row in rows], ["maroon"])

    def test_conjunct_modifier_is_attribute_inventory_candidate(self) -> None:
        record = {
            "caption_id": "c-conj",
            "tokens": [
                token(0, "maroon", "maroon", "NOUN", "nmod", 3, tag="NN"),
                token(1, "and", "and", "CCONJ", "cc", 0, tag="CC"),
                token(2, "yellow", "yellow", "ADJ", "conj", 0, tag="JJ"),
                token(3, "jerseys", "jersey", "NOUN", "ROOT", 3, tag="NNS"),
            ],
            "noun_chunks": [chunk("maroon and yellow jerseys", 3, 0, 4)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "jerseys" else None

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
        ):
            synset = FakeSynset(f"fake-{surface}-a", "adj.all", (surface,))
            return attribute_script.AttributeLookupResult(
                "exact",
                surface,
                (synset,),
                synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
            )

        rows, summary = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(summary["attribute_candidate_total"], 2)
        self.assertEqual({row["span_key"] for row in rows}, {"maroon", "yellow"})

    def test_chained_conjunct_modifiers_are_attribute_inventory_candidates(self) -> None:
        record = {
            "caption_id": "c-conj-chain",
            "tokens": [
                token(0, "blue", "blue", "ADJ", "amod", 5, tag="JJ"),
                token(1, ",", ",", "PUNCT", "punct", 0, tag=","),
                token(2, "white", "white", "ADJ", "conj", 0, tag="JJ"),
                token(3, "and", "and", "CCONJ", "cc", 2, tag="CC"),
                token(4, "yellow", "yellow", "ADJ", "conj", 2, tag="JJ"),
                token(5, "jerseys", "jersey", "NOUN", "ROOT", 5, tag="NNS"),
            ],
            "noun_chunks": [chunk("blue, white, and yellow jerseys", 5, 0, 6)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "jerseys" else None

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
        ):
            synset = FakeSynset(f"fake-{surface}-a", "adj.all", (surface,))
            return attribute_script.AttributeLookupResult(
                "exact",
                surface,
                (synset,),
                synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
            )

        rows, summary = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(summary["attribute_candidate_total"], 3)
        self.assertEqual(
            {row["span_key"] for row in rows},
            {"blue", "white", "yellow"},
        )

    def test_quantity_conjunct_is_not_attribute_inventory_candidate(self) -> None:
        record = {
            "caption_id": "c-conj-quantity",
            "tokens": [
                token(0, "several", "several", "ADJ", "amod", 3, tag="JJ"),
                token(1, "and", "and", "CCONJ", "cc", 0, tag="CC"),
                token(2, "three", "three", "NUM", "conj", 0, tag="CD"),
                token(3, "cars", "car", "NOUN", "ROOT", 3, tag="NNS"),
            ],
            "noun_chunks": [chunk("several and three cars", 3, 0, 4)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "cars" else None

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
        ):
            synset = FakeSynset(f"fake-{surface}-a", "adj.all", (surface,))
            return attribute_script.AttributeLookupResult(
                "exact",
                surface,
                (synset,),
                synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
            )

        rows, summary = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(summary["attribute_candidate_total"], 1)
        self.assertEqual([row["span_key"] for row in rows], ["several"])

    def test_progress_writer_updates_during_record_scan(self) -> None:
        record = {
            "caption_id": "c1",
            "tokens": [
                token(0, "black", "black", "ADJ", "amod", 1, tag="JJ"),
                token(1, "dog", "dog", "NOUN", "ROOT", 1),
            ],
            "noun_chunks": [chunk("black dog", 1, 0, 2)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "dog" else None

        black_synset = FakeSynset("fake-black-a", "adj.all", ("black",))

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
        ):
            return attribute_script.AttributeLookupResult(
                "exact",
                surface,
                (black_synset,),
                black_synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
            )

        with tempfile.TemporaryDirectory() as root:
            progress_path = Path(root) / "attribute_progress.json"
            rows, summary = attribute_script.build_attribute_inventory_rows(
                [record],
                object_lookup=object_lookup,
                attribute_lookup=attribute_lookup,
                progress_output=progress_path,
                progress_interval_records=1,
            )
            progress = json.loads(progress_path.read_text(encoding="utf-8"))

        self.assertEqual([row["span_key"] for row in rows], ["black"])
        self.assertEqual(summary["attribute_candidate_total"], 1)
        self.assertEqual(progress["artifact_type"], "gpic_observed_attribute_inventory_progress")
        self.assertEqual(progress["status"], "complete")
        self.assertEqual(progress["caption_total"], 1)
        self.assertEqual(progress["attribute_candidate_total"], 1)
        self.assertEqual(progress["inventory_rows"], 1)

    def test_no_synset_attribute_remains_countable_inventory_row(self) -> None:
        record = {
            "caption_id": "c2",
            "tokens": [
                token(0, "shiny", "shiny", "ADJ", "amod", 1, tag="JJ"),
                token(1, "car", "car", "NOUN", "ROOT", 1),
            ],
            "noun_chunks": [chunk("shiny car", 1, 0, 2)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "car" else None

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
        ):
            return attribute_script.AttributeLookupResult(
                "unresolved",
                surface,
                (),
                None,
                "unresolved_no_oewn_attribute_synset",
                "",
                "",
                "chosen",
                "no_oewn_attribute_synset",
            )

        rows, _ = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["span_key"], "shiny")
        self.assertEqual(rows[0]["decision_status"], "chosen")
        self.assertEqual(rows[0]["has_oewn_attribute_synset"], "false")

    def test_prior_inventory_canonical_fields_are_preserved(self) -> None:
        record = {
            "caption_id": "c3",
            "tokens": [
                token(0, "green", "green", "ADJ", "amod", 1, tag="JJ"),
                token(1, "shirt", "shirt", "NOUN", "ROOT", 1),
            ],
            "noun_chunks": [chunk("green shirt", 1, 0, 2)],
        }

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface == "shirt" else None

        green_synset = FakeSynset("fake-green-a", "noun.attribute", ("green",))

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
        ):
            if surface != "green":
                return None
            return attribute_script.AttributeLookupResult(
                "exact",
                "green",
                (green_synset,),
                green_synset,
                "selected_by_wn30_attribute_compatible_lemma_count",
                "green:5",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
                {
                    "canonical_surface": "green",
                    "canonical_label_key": "green",
                    "canonical_selection_tag": "selected_single_observed_variant_matched_synset_lemma",
                    "canonical_candidate_lemmas": "green",
                    "canonical_candidate_lemma_counts": "green:5",
                },
            )

        rows, summary = attribute_script.build_attribute_inventory_rows(
            [record],
            object_lookup=object_lookup,
            attribute_lookup=attribute_lookup,
        )

        self.assertEqual(summary["prior_reused_rows"], 1)
        self.assertEqual(summary["prior_selected_synset_reused_rows"], 1)
        self.assertEqual(summary["prior_canonical_reused_rows"], 1)
        self.assertEqual(rows[0]["canonical_surface"], "green")
        self.assertEqual(
            rows[0]["canonical_selection_tag"],
            "selected_single_observed_variant_matched_synset_lemma",
        )

    def test_prior_no_synset_final_row_is_reusable(self) -> None:
        lookup = attribute_script.GpicAttributeInventoryLookup(
            {
                "tyr": {
                    "span_key": "tyr",
                    "observed_surface": "TYR",
                    "decision_status": "chosen",
                    "decision_reason": "no_oewn_attribute_synset",
                    "selected_query": "tyr",
                    "selected_oewn_synset": "",
                    "canonical_surface": "",
                    "canonical_selection_tag": "not_applicable_no_selected_synset",
                }
            }
        )

        result = lookup("TYR")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.decision_status, "chosen")
        self.assertEqual(result.decision_reason, "no_oewn_attribute_synset")
        self.assertIsNone(result.selected_synset)
        self.assertIsNotNone(result.source_row)

    def test_prior_inventory_from_tsv_does_not_reuse_selected_query(self) -> None:
        prior_row = {
            "span_key": "sauteed",
            "observed_surface": "sautéed",
            "decision_status": "chosen",
            "decision_reason": "selected_attribute_compatible",
            "selected_lookup_case": "morphy_a",
            "selected_query": "sauteed",
            "selected_oewn_synset": "oewn-sauteed-s",
            "selected_oewn_lexfile": "adj.all",
            "attribute_gate": "attribute_compatible",
            "all_oewn_synsets": "oewn-sauteed-s",
            "all_oewn_lexfiles": "adj.all",
            "synset_lemmas": "saute|sauteed",
            "canonical_surface": "sauteed",
            "canonical_label_key": "sauteed",
            "canonical_selection_tag": "selected_by_diacritic_folded_observed_surface",
        }
        prior_lookup = attribute_script.GpicAttributeInventoryLookup({"sauteed": prior_row})

        self.assertFalse(hasattr(prior_lookup, "lookup_selected_query"))

    def test_checkpoint_resume_continues_counts_from_record_boundary(self) -> None:
        records = [
            {
                "caption_id": "c1",
                "tokens": [
                    token(0, "black", "black", "ADJ", "amod", 1, tag="JJ"),
                    token(1, "dog", "dog", "NOUN", "ROOT", 1),
                ],
                "noun_chunks": [chunk("black dog", 1, 0, 2)],
            },
            {
                "caption_id": "c2",
                "tokens": [
                    token(0, "black", "black", "ADJ", "amod", 1, tag="JJ"),
                    token(1, "cat", "cat", "NOUN", "ROOT", 1),
                ],
                "noun_chunks": [chunk("black cat", 1, 0, 2)],
            },
        ]

        def object_lookup(surface: str):
            return FakeObjectLookupResult(surface) if surface in {"dog", "cat"} else None

        black_synset = FakeSynset("fake-black-a", "adj.all", ("black",))

        def attribute_lookup(
            surface: str,
            *,
            require_surface_query_conflict_check: bool = False,
        ):
            if surface != "black":
                return None
            return attribute_script.AttributeLookupResult(
                "exact",
                "black",
                (black_synset,),
                black_synset,
                "single_oewn_attribute_synset",
                "",
                "attribute_compatible",
                "chosen",
                "selected_attribute_compatible",
            )

        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_path = Path(tmp) / "attribute_checkpoint.json"
            metadata = {"input": "stage3.jsonl", "output": "attribute.tsv"}
            writer = attribute_script.AttributeInventoryCheckpointWriter(
                checkpoint_path,
                metadata=metadata,
                interval_records=1,
            )
            attribute_script.build_attribute_inventory_rows(
                records[:1],
                object_lookup=object_lookup,
                attribute_lookup=attribute_lookup,
                checkpoint_writer=writer,
            )
            checkpoint = attribute_script._load_checkpoint(checkpoint_path, metadata)
            self.assertIsNotNone(checkpoint)
            assert checkpoint is not None
            rows, summary = attribute_script.build_attribute_inventory_rows(
                records[1:],
                object_lookup=object_lookup,
                attribute_lookup=attribute_lookup,
                initial_inventory=checkpoint.inventory,
                initial_caption_total=checkpoint.caption_total,
                initial_noun_chunk_total=checkpoint.noun_chunk_total,
                initial_attribute_candidate_total=checkpoint.attribute_candidate_total,
            )

        self.assertEqual(summary["caption_total"], 2)
        self.assertEqual(rows[0]["span_key"], "black")
        self.assertEqual(rows[0]["count"], "2")
        self.assertEqual(rows[0]["caption_count"], "2")
        self.assertEqual(rows[0]["selected_oewn_synset"], "fake-black-a")

    def test_runtime_surface_query_conflict_requires_manual_without_prior(self) -> None:
        grounds_synset = FakeSynset("oewn-grounds-n", "noun.location", ("grounds",))
        ground_synset = FakeSynset("oewn-ground-n", "noun.object", ("ground",))

        class FakeOewn:
            def synsets(self, query: str):
                return {
                    "grounds": (grounds_synset,),
                    "ground": (ground_synset,),
                }.get(query, ())

        class FakeMorphy:
            def __call__(self, query: str, pos: str):
                if query == "grounds" and pos == "n":
                    return {"n": {"ground"}}
                return {pos: set()}

        result = attribute_script._lookup_attribute_surface(
            "grounds",
            oewn=FakeOewn(),
            morphy=FakeMorphy(),
            require_surface_query_conflict_check=True,
        )

        self.assertEqual(result.decision_status, "needs_manual")
        self.assertEqual(
            result.decision_reason,
            "manual_surface_query_conflict_required",
        )
        self.assertIsNone(result.selected_synset)
        self.assertEqual(result.query, "grounds|ground")
        self.assertEqual(
            [synset.id for synset in result.synsets],
            ["oewn-grounds-n", "oewn-ground-n"],
        )

    def test_runtime_exact_attribute_hit_skips_morphy_conflict_without_plural_flag(self) -> None:
        grounds_synset = FakeSynset("oewn-grounds-n", "noun.location", ("grounds",))
        ground_synset = FakeSynset("oewn-ground-n", "noun.object", ("ground",))

        class FakeOewn:
            def synsets(self, query: str):
                return {
                    "grounds": (grounds_synset,),
                    "ground": (ground_synset,),
                }.get(query, ())

        class FakeMorphy:
            def __call__(self, query: str, pos: str):
                if query == "grounds" and pos == "n":
                    return {"n": {"ground"}}
                return {pos: set()}

        result = attribute_script._lookup_attribute_surface(
            "grounds",
            oewn=FakeOewn(),
            morphy=FakeMorphy(),
        )

        self.assertEqual(result.decision_status, "needs_manual")
        self.assertEqual(result.decision_reason, "manual_attribute_gate_required")
        self.assertEqual(result.selected_synset, grounds_synset)
        self.assertEqual(result.query, "grounds")

    def test_automatic_surface_changed_attribute_prior_row_is_not_reusable(self) -> None:
        auto_row = {
            "span_key": "grounds",
            "observed_surface": "grounds",
            "decision_status": "chosen",
            "selected_query": "ground",
            "selected_oewn_synset": "oewn-ground-n",
            "canonical_surface": "ground",
        }
        manual_row = {
            **auto_row,
            "decision_basis": "manual_attribute_resolution",
        }

        self.assertTrue(attribute_script._is_automatic_surface_changed_prior_row(auto_row))
        self.assertFalse(attribute_script._is_automatic_surface_changed_prior_row(manual_row))

    def test_conditional_lexfile_requires_manual(self) -> None:
        synset = FakeSynset("fake-label-n", "noun.artifact", ("label",))

        lookup = attribute_script._with_selected_attribute_synset(
            "exact",
            "label",
            (synset,),
        )

        self.assertEqual(lookup.attribute_gate, "conditional")
        self.assertEqual(lookup.decision_status, "needs_manual")
        self.assertEqual(lookup.decision_reason, "manual_attribute_gate_required")

    def test_unselected_synset_candidates_are_needs_manual_not_ambiguous_status(self) -> None:
        synset = FakeSynset("fake-vague-a", "adj.all", ("vague",))

        status = attribute_script._attribute_decision_status(
            selected_synset=None,
            synsets=(synset,),
            attribute_gate="",
        )
        reason = attribute_script._attribute_decision_reason(
            selected_synset=None,
            synsets=(synset,),
            attribute_gate="",
        )

        self.assertEqual(status, "needs_manual")
        self.assertEqual(reason, "manual_synset_required")


if __name__ == "__main__":
    unittest.main()
