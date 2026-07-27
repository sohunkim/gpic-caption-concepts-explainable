import argparse
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


def _load_canonical_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "enrich_gpic_inventory_canonical.py"
    spec = importlib.util.spec_from_file_location("enrich_gpic_inventory_canonical", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


canonical_script = _load_canonical_script()


class _FakeSense:
    id = ""


class _FakeSynset:
    def __init__(self, lemmas):
        self._lemmas = lemmas

    def lemmas(self):
        return self._lemmas

    def senses(self):
        return [_FakeSense()]


class _FakeMorphy:
    def __init__(self, results=None):
        self._results = results or {}

    def __call__(self, key, pos):
        return {pos: self._results.get((key, pos), set())}


class EnrichGpicInventoryCanonicalTest(unittest.TestCase):
    def test_surface_key_folds_diacritics_for_canonical_matching(self) -> None:
        self.assertEqual(canonical_script._surface_key("café"), "cafe")

    def test_selected_query_is_not_observed_exact_surface(self) -> None:
        decision = canonical_script._decide_canonical(
            {
                "observed_surface": "E",
                "selected_query": "e",
                "selected_oewn_synset": "fake-letter-e-n",
            },
            synset=_FakeSynset(["E", "e"]),
            morphy=_FakeMorphy(),
            ngram_evidence={},
        )

        self.assertEqual(decision["canonical_surface"], "E")
        self.assertEqual(
            decision["canonical_selection_tag"],
            "selected_by_unique_observed_span_surface",
        )

    def test_diacritic_folded_raw_surface_can_break_morphy_tie(self) -> None:
        decision = canonical_script._decide_canonical(
            {
                "observed_surface": "sautéed",
                "selected_query": "sautéed",
                "selected_oewn_synset": "fake-sauteed-a",
            },
            synset=_FakeSynset(["saute", "sauteed"]),
            morphy=_FakeMorphy({("sauteed", "v"): {"saute"}}),
            ngram_evidence={},
            morphy_pos=("a", "n", "v", "r"),
        )

        self.assertEqual(decision["canonical_surface"], "sauteed")
        self.assertEqual(
            decision["canonical_selection_tag"],
            "selected_by_unique_observed_span_surface_key",
        )

    def test_attribute_mwe_variants_do_not_include_separator_removal(self) -> None:
        keys = canonical_script._observed_surface_variant_keys(
            {"observed_surface": "dark brown"},
            _FakeMorphy({("dark brown", "a"): {"darkbrown"}}),
            morphy_pos=("a",),
            attribute_mwe_mode=True,
        )

        self.assertIn("dark brown", keys)
        self.assertIn("dark-brown", keys)
        self.assertNotIn("darkbrown", keys)

    def test_attribute_mwe_morphy_applies_to_anchor_only(self) -> None:
        keys = canonical_script._observed_surface_variant_keys(
            {"observed_surface": "cube shaped"},
            _FakeMorphy({("shaped", "v"): {"shape"}}),
            morphy_pos=("v",),
            attribute_mwe_mode=True,
        )

        self.assertIn("cube shape", keys)
        self.assertIn("cube-shape", keys)
        self.assertNotIn("cubeshape", keys)

    def test_main_exits_before_canonical_when_needs_manual_rows_remain(self) -> None:
        args = argparse.Namespace(
            input="input.tsv",
            output="output.tsv",
            ngram_evidence=None,
            ambiguous_output="ambiguous.tsv",
            summary=None,
        )
        rows = [
            {
                "observed_surface": "jersey",
                "span_key": "jersey",
                "decision_status": "needs_manual",
                "decision_reason": "manual_synset_required",
                "selected_query": "jersey",
                "selected_oewn_synset": "",
                "synset_selection_tag": "ambiguous_wn30_all_zero",
            }
        ]

        with (
            patch.object(canonical_script, "parse_args", return_value=args),
            patch.object(
                canonical_script,
                "_read_tsv",
                return_value=(rows, ["decision_status"]),
            ),
            patch.object(canonical_script.wn, "Wordnet") as wordnet,
            patch("builtins.print"),
        ):
            with self.assertRaises(SystemExit) as caught:
                canonical_script.main()

        self.assertIn("blocked_rows=1", str(caught.exception))
        wordnet.assert_not_called()

    def test_main_exits_when_surface_correction_has_no_selected_synset(self) -> None:
        args = argparse.Namespace(
            input="input.tsv",
            output="output.tsv",
            ngram_evidence=None,
            ambiguous_output="ambiguous.tsv",
            summary=None,
        )
        rows = [
            {
                "observed_surface": "white feathers",
                "span_key": "white feathers",
                "decision_status": "chosen",
                "decision_reason": "manual_joined_head_correction_synset_missing",
                "selected_query": "white feather",
                "selected_oewn_synset": "",
                "canonical_surface": "feather",
            }
        ]

        with (
            patch.object(canonical_script, "parse_args", return_value=args),
            patch.object(
                canonical_script,
                "_read_tsv",
                return_value=(rows, ["decision_status"]),
            ),
            patch.object(canonical_script.wn, "Wordnet") as wordnet,
            patch("builtins.print"),
        ):
            with self.assertRaises(SystemExit) as caught:
                canonical_script.main()

        self.assertIn("blocked_rows=1", str(caught.exception))
        wordnet.assert_not_called()

    def test_manual_no_synset_head_fallback_keeps_canonical_surface(self) -> None:
        args = argparse.Namespace(
            input="input.tsv",
            output="output.tsv",
            ngram_evidence=None,
            ambiguous_output="ambiguous.tsv",
            summary=None,
        )
        rows = [
            {
                "observed_surface": "black top",
                "span_key": "black top",
                "decision_status": "chosen",
                "decision_reason": "manual_accept_canonical_head_modifier_removed",
                "selected_query": "top",
                "selected_oewn_synset": "",
                "canonical_surface": "top",
                "canonical_selection_tag": "manual_no_synset_head_canonical",
                "manual_resolution_type": "canonical_head_no_selected_synset",
            }
        ]

        with (
            patch.object(canonical_script, "parse_args", return_value=args),
            patch.object(
                canonical_script,
                "_read_tsv",
                return_value=(rows, ["canonical_surface", "canonical_selection_tag"]),
            ),
            patch.object(canonical_script, "_write_tsv") as write_tsv,
            patch.object(canonical_script.wn, "Wordnet", return_value=Mock()),
            patch.object(canonical_script, "Morphy", return_value=object()),
            patch("builtins.print"),
        ):
            canonical_script.main()

        self.assertEqual(rows[0]["canonical_surface"], "top")
        self.assertEqual(rows[0]["canonical_label_key"], "top")
        self.assertEqual(
            rows[0]["canonical_selection_tag"],
            "manual_no_synset_head_canonical",
        )
        self.assertEqual(write_tsv.call_count, 2)

    def test_main_exits_nonzero_when_canonical_ambiguity_remains(self) -> None:
        args = argparse.Namespace(
            input="input.tsv",
            output="output.tsv",
            ngram_evidence=None,
            ambiguous_output="ambiguous.tsv",
            summary=None,
        )
        rows = [{"decision_status": "chosen", "selected_oewn_synset": "fake-synset-n"}]
        fake_oewn = Mock()
        fake_oewn.synset.return_value = object()

        with (
            patch.object(canonical_script, "parse_args", return_value=args),
            patch.object(
                canonical_script,
                "_read_tsv",
                return_value=(rows, ["selected_oewn_synset"]),
            ),
            patch.object(canonical_script, "_write_tsv") as write_tsv,
            patch.object(canonical_script.wn, "Wordnet", return_value=fake_oewn),
            patch.object(canonical_script, "Morphy", return_value=object()),
            patch("builtins.print"),
            patch.object(
                canonical_script,
                "_decide_canonical",
                return_value={
                    "canonical_surface": "",
                    "canonical_label_key": "",
                    "canonical_selection_tag": "ambiguous_google_ngram_tie",
                    "canonical_candidate_lemmas": "sun|Sun",
                    "canonical_candidate_lemma_counts": "sun:1|Sun:1",
                    "google_ngram_candidate_surfaces": "sun|Sun",
                    "google_ngram_candidate_mean_frequencies": "sun:1|Sun:1",
                },
            ),
        ):
            with self.assertRaises(SystemExit) as caught:
                canonical_script.main()

        self.assertIn("canonical_ambiguous_rows=1", str(caught.exception))
        self.assertEqual(write_tsv.call_count, 2)


if __name__ == "__main__":
    unittest.main()
