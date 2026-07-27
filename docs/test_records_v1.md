# V1 Test Records

This file records tests that affect future implementation decisions.

## 2026-07-13: Plural Object Exact-Vs-Base Recheck

Purpose:

- Strengthen object plural lookup so plural exact surface and base-form OEWN
  candidates are checked together.
- Rebuild the 10K object inventory after removing plural-head rows from the
  current object prior, so plural rows are re-evaluated under the new rule.

Commands:

```powershell
.\scripts\run_python.ps1 -m compileall scripts src
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_build_gpic_observed_object_inventory.py
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 -- scripts\filter_object_inventory_plural_prior.py --stage3-records outputs\real10k_mixed_formal_after_action_v3_current\stage3\stage3_records.jsonl --prior-object-inventory resources\gpic_inventory\current\inventory\object_inventory.tsv --output-filtered-prior outputs\front10000_plural_object_recheck_20260713\inventory\object_inventory_prior_without_plural_heads.tsv --removed-output outputs\front10000_plural_object_recheck_20260713\inventory\object_inventory_removed_plural_heads.tsv --summary outputs\front10000_plural_object_recheck_20260713\inventory\object_inventory_plural_prior_filter_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 1200 -- scripts\build_gpic_observed_object_inventory.py --input outputs\real10k_mixed_formal_after_action_v3_current\stage3\stage3_records.jsonl --prior-object-inventory outputs\front10000_plural_object_recheck_20260713\inventory\object_inventory_prior_without_plural_heads.tsv --output outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck.tsv --summary outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_summary.json
```

Results:

- `compileall scripts src`: passed.
- `test_build_gpic_observed_object_inventory.py`: 9 tests passed.
- `test_stage4_extract_raw.py`: 49 tests passed.
- Prior filter:
  - plural-head candidate span keys from 10K Stage 3 records: `11,256`
  - current prior object rows removed: `1,896`
  - current prior object rows kept: `4,617`
- Rebuilt object inventory:
  - total rows: `6,513`
  - `chosen`: `4,487`
  - `excluded`: `1,209`
  - `needs_manual`: `817`
- `needs_manual` reasons:
  - `manual_objectness_required`: `379`
  - `manual_synset_required`: `279`
  - `manual_surface_query_conflict_required`: `139`
  - `manual_joined_variant_required`: `20`
- Important plural conflict examples now block as intended:
  - `glasses -> glasses|glass`
  - `colors -> colors|color`
  - `pants -> pants|pant`
  - `trunks -> trunks|trunk`
  - `rings -> rings|ring`

Artifacts:

- Full rebuilt object inventory:
  `outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck.tsv`
- All pending rows:
  `outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_needs_manual.tsv`
- Plural exact-vs-base conflict subset:
  `outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_surface_base_conflicts.tsv`
- Removed prior rows:
  `outputs\front10000_plural_object_recheck_20260713\inventory\object_inventory_removed_plural_heads.tsv`

Interpretation:

- The current object lookup now avoids both earlier extremes:
  - it does not blindly use base/Morphy first for every plural, which caused
    `glasses -> glass`-style errors
  - it does not blindly trust exact plural surface when a base-form noun
    candidate also exists, which caused `colors`-style errors
- The recheck output is a review artifact and has not been published to
  `resources\gpic_inventory\current`.

## 2026-07-13: Plural Object Manual Feedback Overlay

Purpose:

- Apply user-reviewed plural object feedback to the 10K plural recheck object
  inventory.
- Keep surface rewrite-only decisions separate from synset selections while
  preserving the rule that changed object heads must have their replacement
  synset looked up.

Commands:

```powershell
.\scripts\run_python.ps1 -c "<merge gpic_observed_object_inventory_plural_recheck_v1_synset_decisions.tsv and gpic_observed_object_inventory_plural_recheck_v1_surface_rewrite_only_map.tsv>"
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 600 -- scripts\apply_object_manual_resolution.py --full-inventory outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck.tsv --resolved-subset outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v1_manual_feedback_merged.tsv --output outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v1_manual_resolved.tsv --resolved-copy outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v1_manual_resolved_subset.tsv --summary outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v1_manual_resolved_summary.json
.\scripts\run_python.ps1 -m compileall scripts src
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_apply_object_manual_resolution.py
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_build_gpic_observed_object_inventory.py
```

Results:

- Feedback rows:
  - synset decisions: `791`
  - surface rewrite-only rows: `26`
  - merged resolved feedback rows: `817`
- Initial overlay exposed an implementation bug:
  - `corn cobs -> cobs` failed because `cobs` was not present as a row in the
    full inventory.
  - Other rewrite rows such as `white lines -> lines` needed to use replacement
    decisions from the same feedback file rather than the unresolved original
    full-inventory row.
- Fixed `apply_object_manual_resolution.py` so surface rewrite replacement
  source lookup prefers non-rewrite rows from the same resolved feedback file
  and falls back to OEWN head lookup when the replacement row is absent.
- Verification:
  - `compileall scripts src`: passed.
  - `test_apply_object_manual_resolution.py`: 8 tests passed.
  - `test_build_gpic_observed_object_inventory.py`: 9 tests passed.
- Overlay result:
  - total rows: `6,513`
  - `chosen`: `5,303`
  - `excluded`: `1,209`
  - `needs_manual`: `1`
  - remaining blocker: `corn cobs -> cobs`, selected query `cob`, OEWN noun
    candidates include food and animal senses, so it still needs manual synset
    selection.

Artifacts:

- Merged feedback:
  `outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v1_manual_feedback_merged.tsv`
- Overlay output:
  `outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v1_manual_resolved.tsv`
- Remaining blocker:
  `outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v1_remaining_needs_manual.tsv`

Interpretation:

- Do not proceed to object parent/canonical enrichment from this inventory until
  the remaining `corn cobs` blocker is resolved.

## 2026-07-13: Corn Cobs Phrase-Preserved Manual Correction

Purpose:

- Correct the previous `corn cobs -> cobs` surface rewrite decision.
- Preserve `corn cobs` as a phrase-level object span and apply the reviewed
  phrase synset decision.

Commands:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 600 -- scripts\apply_object_manual_resolution.py --full-inventory outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v1_manual_resolved.tsv --resolved-subset C:\Users\rlath\Downloads\gpic_observed_object_inventory_plural_recheck_corn_cobs_phrase_preserved_resolved.tsv --output outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved.tsv --resolved-copy outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved_subset.tsv --summary outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved_summary.json
.\scripts\run_python.ps1 -c "<verify decision_status counts and corn cobs row>"
```

Results:

- Overlay input rows: `1`
- Final object inventory rows: `6,513`
- Final decision status counts:
  - `chosen`: `5,304`
  - `excluded`: `1,209`
  - `needs_manual`: `0`
- Corrected `corn cobs` row:
  - `decision_status=chosen`
  - `decision_reason=manual_phrase_preserved_synset_selected`
  - `selected_query=corn cob`
  - `selected_oewn_synset=oewn-08561700-n`
  - `selected_oewn_lexfile=noun.location`
  - `manual_action=select_synset_keep_phrase`
  - `replacement_span_key` is empty

Artifacts:

- Final phrase-preserved object inventory:
  `outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved.tsv`
- One-row resolved copy:
  `outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved_subset.tsv`
- Summary:
  `outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved_summary.json`

Interpretation:

- The object manual/synset gate for this plural recheck inventory is clear.
- The manual file's canonical fields were not treated as authoritative; object
  canonical enrichment should still be run by the pipeline's canonical step.

## 2026-07-13: Front10K Plural Recheck V2 Promotion And Formal Run

Purpose:

- Enrich the phrase-preserved plural recheck object inventory with canonical
  object surfaces.
- Continue Stage 3.5 attribute/action inventory workflow from that object
  inventory.
- Publish the completed inventory bundle as the active current inventory.
- Re-run formal mixed Stage 1-6 for the same first 10K GPIC caption input.

Commands:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 900 -- scripts\enrich_gpic_inventory_canonical.py --input outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved.tsv --output outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved_canonical.tsv --ambiguous-output outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved_canonical_ambiguous.tsv --summary outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved_canonical_summary.json --ngram-evidence resources\source_labels\google_ngram_canonical_frequency_evidence.tsv
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 1800 -- scripts\run_stage35_inventory_workflow.py --stage3-records outputs\real10k_mixed_formal_after_action_v3_current\stage3\stage3_records.jsonl --output-dir outputs\front10000_plural_object_recheck_20260713\workflow_after_object_v2 --object-inventory outputs\front10000_plural_object_recheck_20260713\inventory\gpic_observed_object_inventory_plural_recheck_v2_phrase_preserved_manual_resolved_canonical.tsv --attribute-prior-inventory resources\gpic_inventory\current\inventory\attribute_inventory.tsv --action-prior-inventory resources\gpic_inventory\current\inventory\action_inventory.tsv --base-lexicon-dir resources\gpic_inventory\current\lexicons --publish-current --snapshot-label front10000_plural_recheck_v2 --publish-summary outputs\front10000_plural_object_recheck_20260713\workflow_after_object_v2\publish_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 2400 -- scripts\run_mixed_caption_pipeline.py --input "C:\Users\rlath\OneDrive\Desktop\PILAB\0. 연구과제\기영님 연구과제(blue maze)\caption to concept\gpic-caption-concepts\data\gpic_captions_10k_train00000_00099\train\gpic_train_00000_00099_merged_10000.jsonl.gz" --output-dir outputs\front10000_plural_recheck_v2_formal_current --inventory-bundle resources\gpic_inventory\current\inventory_bundle.json --preposition-mwe-lexicon resources\lexicons\preposition_mwes.tsv --prefer-gpu --batch-size 128 --md-report outputs\front10000_plural_recheck_v2_formal_current\caption_to_concept_front10000_plural_recheck_v2.md --md-limit 100 --max-object-pairs-per-caption 40
.\scripts\run_python.ps1 -c "<verify current inventory gate counts and selected plural rows>"
```

Results:

- Object canonical enrichment:
  - rows: `6,513`
  - canonical selected rows: `5,304`
  - selected-synset missing rows: `1,209`
  - canonical ambiguous rows: `0`
- Stage 3.5 workflow:
  - status: `complete`
  - executed steps:
    `build_attribute_inventory`, `enrich_attribute_canonical`,
    `build_action_inventory`, `enrich_action_canonical`,
    `export_stage5_lexicon_bundle`
  - published current snapshot label: `front10000_plural_recheck_v2`
  - published current rows:
    - object inventory: `6,513`
    - attribute inventory: `3,531`
    - action inventory: `1,947`
    - action canonical inventory: `1,947`
- Current inventory gate verification:
  - object status counts: `chosen=5304`, `excluded=1209`, blockers `0`
  - attribute status counts: `chosen=3531`, blockers `0`
  - action status counts: `chosen=1873`, `raw_fallback=74`
  - action canonical blockers: `0`
- Formal mixed Stage 1-6:
  - status: `completed`
  - total captions: `10,000`
  - sentence captions: `9,896`
  - tag-list captions: `104`
  - Stage 3 sentence GPU enabled: `true`
  - Stage 3 tag-list GPU enabled: `true`
  - total runtime: `181.479171` seconds
  - throughput: `55.102742` captions/sec
  - Stage 6 fact total: `1,724,773`
- Spot checks:
  - `corn cobs` inventory row is phrase-preserved:
    `selected_query=corn cob`, `selected_oewn_synset=oewn-08561700-n`,
    `canonical_surface=corncob`, empty `replacement_span_key`.
  - Stage 6 object count has `corncob` with `raw_variants=corn cobs`.
  - `glasses` remains `glasses`, while singular `glass` remains separate.
  - `colors`, `pants`, `trunks`, and `rings` follow the manual selected
    canonical rows in the current inventory.

Artifacts:

- Current inventory bundle:
  `resources\gpic_inventory\current\inventory_bundle.json`
- Workflow state:
  `outputs\front10000_plural_object_recheck_20260713\workflow_after_object_v2\stage35_workflow_state.json`
- Publish summary:
  `outputs\front10000_plural_object_recheck_20260713\workflow_after_object_v2\publish_summary.json`
- Formal mixed output:
  `outputs\front10000_plural_recheck_v2_formal_current`
- Formal Markdown report:
  `outputs\front10000_plural_recheck_v2_formal_current\caption_to_concept_front10000_plural_recheck_v2.md`

Interpretation:

- The active current inventory now points to the plural recheck v2 snapshot.
- The formal 10K mixed run used the active current inventory bundle and passed
  Stage 1-6 without preview/runtime-action lookup mode.

## 2026-07-13: Stage 3.5 Publish-Current Integration

Purpose:

- Verify that a completed Stage 3.5 workflow can publish its generated
  inventory bundle to the managed current inventory path in the same workflow
  command when `--publish-current` is requested.
- Keep probe/simulation workflows unpublished unless they explicitly opt in.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_stage35_inventory_workflow.py
.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_publish_inventory_bundle.py
```

Results:

- `test_stage35_inventory_workflow.py`: 11 tests passed.
- `test_publish_inventory_bundle.py`: 3 tests passed.

Interpretation:

- The Stage 3.5 workflow now writes `inventory_bundle.json` on completion and,
  when requested, publishes that completed bundle to the current inventory
  target before reporting the workflow state.
- The workflow state records `publish_current`, `published_current_bundle`, and
  the publish summary, so an unpublished snapshot is distinguishable from an
  active current inventory update.

## 2026-07-13: Front-10000 Current Bundle Stage 4/5/6 Rerun

Purpose:

- Run formal Stage 4/5/6 from the managed current 10K inventory bundle and
  compare with the previous formal 10K output.
- Check whether the managed current 10K bundle follows the current object
  lookup policy, even when that differs from an older historical 10K run.

Commands:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 900 -- scripts\run_stage4_extract_raw.py --input outputs\real10k_mixed_formal_after_action_v3_current\stage3\stage3_records.jsonl --raw-mentions outputs\front10000_formal_stage456_using_current_20260713_rerun\stage4\raw_mentions.jsonl --raw-edges outputs\front10000_formal_stage456_using_current_20260713_rerun\stage4\raw_edges.jsonl --summary outputs\front10000_formal_stage456_using_current_20260713_rerun\stage4\summary.jsonl --object-inventory resources\gpic_inventory\current\inventory\object_inventory.tsv --action-inventory resources\gpic_inventory\current\inventory\action_inventory.tsv --preposition-mwe-lexicon resources\lexicons\preposition_mwes.tsv
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 900 -- scripts\run_stage5_canonicalize.py --raw-mentions outputs\front10000_formal_stage456_using_current_20260713_rerun\stage4\raw_mentions.jsonl --raw-edges outputs\front10000_formal_stage456_using_current_20260713_rerun\stage4\raw_edges.jsonl --lexicon-dir resources\gpic_inventory\current\lexicons --canonical-mentions outputs\front10000_formal_stage456_using_current_20260713_rerun\stage5\canonical_mentions.jsonl --canonical-edges outputs\front10000_formal_stage456_using_current_20260713_rerun\stage5\canonical_edges.jsonl --summary outputs\front10000_formal_stage456_using_current_20260713_rerun\stage5\summary.jsonl --attribute-inventory resources\gpic_inventory\current\inventory\attribute_inventory.tsv
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 1200 -- scripts\run_stage6_export_counts.py --canonical-mentions outputs\front10000_formal_stage456_using_current_20260713_rerun\stage5\canonical_mentions.jsonl --canonical-edges outputs\front10000_formal_stage456_using_current_20260713_rerun\stage5\canonical_edges.jsonl --output-dir outputs\front10000_formal_stage456_using_current_20260713_rerun\stage6 --summary outputs\front10000_formal_stage456_using_current_20260713_rerun\stage6\summary.jsonl
```

Initial comparison:

- Stage 4 and Stage 5 totals matched the previous formal 10K run.
- Stage 6 did not initially match:
  - fact total changed from `1,724,720` to `1,724,814`
  - the change came from object canonical differences:
    `pants`, `colors`, `trunks`, and `rings`.
- Corrected interpretation:
  - The initial mismatch was not evidence that manual decisions failed to
    propagate.
  - The older 100-caption run used a plural-head-lemma-first lookup path, so
    `pants -> pant`, `colors -> color`, and `trunks -> trunk` became
    `needs_manual` and were manually resolved there.
  - The current object lookup policy prefers observed exact surface lookup for
    new rows and only sends exact-vs-base conflicts to `needs_manual` when both
    sides select conflicting synsets. Under that current policy, the new 10K
    rows `pants`, `colors`, `trunks`, and `rings` can be auto-selected by OEWN
    exact-surface evidence.
  - Therefore the Stage 6 difference from the historical 10K run was a policy
    difference between historical output and current output, not a broken
    current-inventory reuse path.

Retracted correction:

- Restored the previous manual object rows for:
  `pants`, `colors`, `trunks`, `rings`, `day`, and `time` into
  `outputs\front10000_inventory_using_current_front1000_20260713_rerun\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical_metadata.tsv`.
- Republished
  `outputs\front10000_inventory_using_current_front1000_20260713_rerun\workflow_after_object_manual\inventory_bundle.json`
  to `resources\gpic_inventory\current`.
- Reran Stage 4/5/6 into
  `outputs\front10000_formal_stage456_using_current_20260713_rerun_fixed`.
- This correction was later judged wrong because it forced historical manual
  rows over rows that were correctly auto-selected by the current rule.

Historical comparison after the retracted correction:

- Stage 5 canonical outputs are byte-identical to the previous formal run:
  - `stage5/canonical_mentions.jsonl`: same SHA-256
  - `stage5/canonical_edges.jsonl`: same SHA-256
- Stage 6 final outputs are byte-identical where it matters for count export:
  - `stage6/facts.jsonl`: same SHA-256
  - key count tables including `object_counts.tsv`,
    `attribute_counts.tsv`, `action_counts.tsv`,
    `relation_triple_counts.tsv`, and
    `object_cooccurrence_pair_counts.tsv`: same SHA-256
- `stage4/raw_edges.jsonl` is byte-identical.
- `stage4/raw_mentions.jsonl` differs only in action lookup provenance metadata
  such as `decision_reason` / `synset_selection_tag`; Stage 5 canonical output
  and Stage 6 facts are unchanged by that metadata difference.

Reverted current inventory correction:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 600 -- scripts\enrich_gpic_inventory_synset_metadata.py --input outputs\front10000_inventory_using_current_front1000_20260713_rerun\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --output outputs\front10000_inventory_using_current_front1000_20260713_rerun\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical_metadata.tsv --summary outputs\front10000_inventory_using_current_front1000_20260713_rerun\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical_metadata_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 -- scripts\publish_inventory_bundle.py --source-bundle outputs\front10000_inventory_using_current_front1000_20260713_rerun\workflow_after_object_manual\inventory_bundle.json --target-dir resources\gpic_inventory\current --snapshot-label front10000 --source-stage3-records outputs\real10k_mixed_formal_after_action_v3_current\stage3\stage3_records.jsonl --summary resources\gpic_inventory\current\publish_summary.json
```

Current verification after revert:

- `resources\gpic_inventory\current\inventory\object_inventory.tsv` now has:
  - `pants -> selected_query=pants`, `decision_reason=selected_object_compatible`
  - `colors -> selected_query=colors`, `decision_reason=selected_object_compatible`
  - `trunks -> selected_query=trunks`, `decision_reason=selected_object_compatible`
  - `rings -> selected_query=rings`, `decision_reason=selected_object_compatible`
- The historical byte-identical output directory
  `outputs\front10000_formal_stage456_using_current_20260713_rerun_fixed` should
  be treated as a historical comparison artifact, not the active current-policy
  output.

## 2026-07-13: Central Current Inventory Publish And 1K Bundle Simulation

Purpose:

- Populate the managed current inventory TSVs from the first 100 captions only.
- Publish that completed 100-caption Stage 3.5 bundle to
  `resources/gpic_inventory/current/inventory_bundle.json`.
- Run the 1K mixed formal pipeline using only that central bundle, to verify
  bundle reuse before promoting a larger 10K inventory.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p "test_publish_inventory_bundle.py"
.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p "test_stage35_inventory_workflow.py"
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 -- scripts\build_gpic_observed_object_inventory.py --input outputs\front100_inventory_current\stage3\stage3_records.jsonl --prior-object-inventory outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parent_canonical_metadata.tsv --output outputs\front100_inventory_current\inventory\gpic_observed_object_inventory.tsv --summary outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 -- scripts\enrich_gpic_inventory_parents.py --input outputs\front100_inventory_current\inventory\gpic_observed_object_inventory.tsv --output outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_parents.tsv --summary outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_parents_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 -- scripts\enrich_gpic_inventory_canonical.py --input outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_parents.tsv --output outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_parent_canonical.tsv --ngram-evidence resources\source_labels\google_ngram_canonical_frequency_evidence.tsv --ambiguous-output outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_parent_canonical_ambiguous.tsv --summary outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_parent_canonical_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 -- scripts\enrich_gpic_inventory_synset_metadata.py --input outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_parent_canonical.tsv --output outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_parent_canonical_metadata.tsv --summary outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_parent_canonical_metadata_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 600 -- scripts\run_stage35_inventory_workflow.py --stage3-records outputs\front100_inventory_current\stage3\stage3_records.jsonl --output-dir outputs\front100_inventory_current --object-inventory outputs\front100_inventory_current\inventory\gpic_observed_object_inventory_parent_canonical_metadata.tsv --attribute-prior-inventory outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical.tsv --action-prior-inventory outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved.tsv --base-lexicon-dir outputs\front1000_mixed_current\lexicons_after_action_manual_resolved --preposition-mwe-lexicon resources\lexicons\preposition_mwes.tsv --ngram-evidence resources\source_labels\google_ngram_canonical_frequency_evidence.tsv --lexicon-output-dir outputs\front100_inventory_current\lexicons_after_stage35_workflow
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\publish_inventory_bundle.py --source-bundle outputs\front100_inventory_current\inventory_bundle.json --target-dir resources\gpic_inventory\current --snapshot-label front100 --source-stage3-records outputs\front100_inventory_current\stage3\stage3_records.jsonl --summary resources\gpic_inventory\current\publish_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 900 -- scripts\run_mixed_caption_pipeline.py --input outputs\front1000_current_guard_scoped_input\gpic_rows_front1000.jsonl --output-dir outputs\front1000_formal_using_front100_inventory_current --inventory-bundle resources\gpic_inventory\current\inventory_bundle.json --preposition-mwe-lexicon resources\lexicons\preposition_mwes.tsv --prefer-gpu --batch-size 128 --md-report outputs\front1000_formal_using_front100_inventory_current\caption_to_concept_front1000_using_front100_inventory.md --md-limit 100 --max-object-pairs-per-caption 40
```

Results:

- Publish unit tests: 3 tests passed.
- Stage 3.5 workflow tests: 8 tests passed.
- First-100 central inventory row counts:
  - object: 407
  - attribute: 173
  - action: 126
  - action canonical: 126
- The first publish attempt exposed a bug: complete Stage 3.5 bundles could
  point `action_inventory` at `_manual_resolved.tsv` even when no manual action
  step had created that file.
- Fixed workflow bundle writing to use the existing resolved action inventory
  when present, otherwise the raw action inventory generated by the action
  builder.
- Fixed central publish to require and copy
  `action_inventory.tsv.pipeline_state.json`, rewriting its `output` field to
  the central action TSV path.
- 1K formal mixed run using only
  `resources\gpic_inventory\current\inventory_bundle.json` completed:
  - output dir: `outputs\front1000_formal_using_front100_inventory_current`
  - preview mode: false
  - runtime action lookup preview: false
  - Stage 1 total: 1,000
  - caption shape counts: `sentence=797`, `tag_list=203`
  - Stage 6 facts: 48,560
  - total pipeline seconds: `37.107571`
  - throughput: `26.948678 captions/sec`

Interpretation:

- The central current inventory path is now a real managed input, not a run
  snapshot alias.
- A first-100 current bundle can drive a 1K formal mixed run without mutating
  inventory TSVs.
- The next promotion step should rebuild/publish the 10K inventory into the
  same central path after reviewing the 1K simulation output.

## 2026-07-13: Inventory Bundle Manifest Gate

Purpose:

- Make prior inventory reuse less dependent on manually passing four separate
  paths.
- Verify that a completed Stage 3.5 workflow writes `inventory_bundle.json`.
- Verify that the formal mixed runner can consume the bundle and rejects
  mismatched explicit per-family paths.

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast, pathlib; files=['src/gpic_concepts_v1/inventory_bundle.py','scripts/run_stage35_inventory_workflow.py','scripts/run_mixed_caption_pipeline.py','tests/test_stage35_inventory_workflow.py','tests/test_mixed_caption_pipeline.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8'), filename=f) for f in files]; print('AST_OK')"
.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_stage35_inventory_workflow.py
.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_mixed_caption_pipeline.py
.\scripts\run_python.ps1 -c "from gpic_concepts_v1.inventory_bundle import load_inventory_bundle, build_inventory_bundle_state, write_inventory_bundle; src='outputs/real10k_mixed_inventory_current/stage35_workflow_state.json'; out='outputs/real10k_mixed_inventory_current/inventory_bundle.json'; b=load_inventory_bundle(src); write_inventory_bundle(out, build_inventory_bundle_state(object_inventory=b.object_inventory, attribute_inventory=b.attribute_inventory, action_inventory=b.action_inventory, action_canonical_inventory=b.action_canonical_inventory, lexicon_dir=b.lexicon_dir, source_workflow_state=src)); print('WROTE', out)"
.\scripts\run_python.ps1 -c "from gpic_concepts_v1.inventory_bundle import load_inventory_bundle; b=load_inventory_bundle('outputs/real10k_mixed_inventory_current/inventory_bundle.json'); print('CURRENT_BUNDLE_OK', b.object_inventory, b.attribute_inventory, b.action_inventory, b.lexicon_dir)"
```

Results:

- AST check: `AST_OK`.
- `test_stage35_inventory_workflow.py`: 7 tests passed.
- `test_mixed_caption_pipeline.py`: 6 tests passed.
- Current 10K inventory bundle generated:
  `outputs\real10k_mixed_inventory_current\inventory_bundle.json`.
- Generated bundle read-back: `CURRENT_BUNDLE_OK`.

Interpretation:

- The Stage 3.5 workflow now supports `--prior-inventory-bundle` and writes a
  completed `inventory_bundle.json`.
- The formal mixed runner now supports `--inventory-bundle`.
- If a bundle and an explicit inventory/lexicon path disagree, the runner raises
  `inventory_bundle_path_mismatch` instead of silently mixing snapshots.

## 2026-07-13: Real 10K Action Manual Resolution And Formal Mixed Run

Purpose:

- Apply the 135-row action manual feedback for the real 10K mixed inventory.
- Let the Stage 3.5 workflow advance automatically through action canonical
  enrichment and Stage 5 lexicon export.
- Re-run the 10K formal mixed caption pipeline with the completed object,
  attribute, and action inventories.

Inputs:

- Action manual decisions:
  `C:\Users\rlath\Downloads\gpic_observed_action_inventory_after_attribute_v3_manual_resolved.tsv`
- Action manual audit:
  `C:\Users\rlath\Downloads\gpic_observed_action_inventory_after_attribute_v3_manual_resolution_audit.tsv`
- Stage 3.5 workflow output state:
  `outputs\real10k_mixed_inventory_current\stage35_workflow_state.json`

Commands:

```powershell
.\scripts\assert_active_workspace.ps1
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 -- scripts\run_stage35_inventory_workflow.py --stage3-records outputs\real10k_mixed_guard_scoped_timed_20260713_1240\stage3\stage3_records.jsonl --output-dir outputs\real10k_mixed_inventory_current --object-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_object_inventory_manual_resolved_v3_parent_canonical_metadata.tsv --attribute-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_attribute_inventory_after_object_v3.tsv --attribute-canonical-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_attribute_inventory_after_object_v3_manual_resolved_canonical.tsv --action-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_action_inventory_after_attribute_v3.tsv --action-prior-inventory outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved.tsv --action-manual-decisions "C:\Users\rlath\Downloads\gpic_observed_action_inventory_after_attribute_v3_manual_resolved.tsv" --base-lexicon-dir outputs\front1000_mixed_current\lexicons_after_action_manual_resolved --preposition-mwe-lexicon resources\lexicons\preposition_mwes.tsv --ngram-evidence resources\source_labels\google_ngram_canonical_frequency_evidence.tsv --lexicon-output-dir outputs\real10k_mixed_inventory_current\lexicons_after_stage35_workflow
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 2400 -- scripts\run_mixed_caption_pipeline.py --input "C:\Users\rlath\OneDrive\Desktop\PILAB\0. 연구과제\기영님 연구과제(blue maze)\caption to concept\gpic-caption-concepts\data\gpic_captions_10k_train00000_00099\train\gpic_train_00000_00099_merged_10000.jsonl.gz" --output-dir outputs\real10k_mixed_formal_after_action_v3_current --object-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_object_inventory_manual_resolved_v3_parent_canonical_metadata.tsv --attribute-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_attribute_inventory_after_object_v3_manual_resolved_canonical.tsv --action-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_action_inventory_after_attribute_v3_manual_resolved.tsv --lexicon-dir outputs\real10k_mixed_inventory_current\lexicons_after_stage35_workflow --preposition-mwe-lexicon resources\lexicons\preposition_mwes.tsv --prefer-gpu --batch-size 128
```

Results:

- Action manual overlay:
  - full rows: 1,947
  - manual decision rows: 135
  - merged status counts: `chosen=1873`, `raw_fallback=74`
- Action canonical enrichment:
  - canonical selected rows: 1,873
  - canonical ambiguous rows: 0
  - raw fallback not-applicable rows: 74
- Stage 5 lexicon bundle:
  - output dir:
    `outputs\real10k_mixed_inventory_current\lexicons_after_stage35_workflow`
  - pipeline state: `status=ready`, `action_canonical_exported=true`
  - action synonym rows: 1,914
  - action synonym rows added: 1,359
- Stage 3.5 workflow final state:
  - `status=complete`
  - `next_required_step=formal_stage4_5_6`
  - executed steps:
    `apply_action_manual_resolution`, `enrich_action_canonical`,
    `export_stage5_lexicon_bundle`
- Formal 10K mixed output:
  - output dir:
    `outputs\real10k_mixed_formal_after_action_v3_current`
  - pipeline state: `status=completed`, `preview_mode=false`
  - caption shape counts: `sentence=9896`, `tag_list=104`
  - Stage 3 GPU enabled: true
  - total pipeline seconds: `206.642167`
  - throughput: `48.392834 captions/sec`
  - Stage 4 raw mentions: 200,372
  - Stage 4 raw edges: 123,548
  - Stage 6 facts: 1,724,720
  - Stage 6 table rows:
    `object_counts=5275`, `attribute_counts=3528`, `action_counts=1079`,
    `relation_triple_counts=11831`, `object_cooccurrence_pair_counts=431714`

Interpretation:

- The action manual gate is clear for the real 10K run.
- The current formal 10K count tables should use
  `outputs\real10k_mixed_formal_after_action_v3_current`.
- The previous `blocked_action_needs_manual` workflow state is superseded by
  the current `complete` state.

## 2026-07-13: Stage 3.5 Inventory Workflow Orchestrator

Change:

- Added `scripts/run_stage35_inventory_workflow.py`.
- The workflow runner inspects object, attribute, action, canonical, and
  Stage 5 lexicon artifacts, runs the next clear offline step, and stops with a
  `stage35_workflow_state.json` blocker when manual or canonical work remains.
- The runner calls existing build/apply/canonical/export scripts instead of
  adding new extraction or canonicalization semantics.

Commands:

```powershell
.\scripts\assert_active_workspace.ps1
.\scripts\run_python.ps1 -c "import ast, pathlib; files=['scripts/run_stage35_inventory_workflow.py','tests/test_stage35_inventory_workflow.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8'), filename=f) for f in files]; print('AST_OK')"
.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_stage35_inventory_workflow.py
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\run_stage35_inventory_workflow.py --stage3-records outputs\real10k_mixed_guard_scoped_timed_20260713_1240\stage3\stage3_records.jsonl --output-dir outputs\real10k_mixed_inventory_current --object-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_object_inventory_manual_resolved_v3_parent_canonical_metadata.tsv --attribute-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_attribute_inventory_after_object_v3.tsv --attribute-canonical-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_attribute_inventory_after_object_v3_manual_resolved_canonical.tsv --action-inventory outputs\real10k_mixed_inventory_current\inventory\gpic_observed_action_inventory_after_attribute_v3.tsv --action-prior-inventory outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved.tsv --base-lexicon-dir outputs\front1000_mixed_current\lexicons_after_action_manual_resolved --preposition-mwe-lexicon resources\lexicons\preposition_mwes.tsv --ngram-evidence resources\source_labels\google_ngram_canonical_frequency_evidence.tsv --lexicon-output-dir outputs\real10k_mixed_inventory_current\lexicons_after_stage35_workflow
```

Results:

- Active workspace guard: passed.
- AST parse: passed.
- `test_stage35_inventory_workflow.py`: 5 tests passed.
- Current real-10K workflow state:
  - status: `blocked_action_needs_manual`
  - next required step: `resolve_action_manual`
  - action inventory:
    `outputs\real10k_mixed_inventory_current\inventory\gpic_observed_action_inventory_after_attribute_v3.tsv`
  - decision counts: `chosen=1738`, `needs_manual=135`, `raw_fallback=74`
  - state file:
    `outputs\real10k_mixed_inventory_current\stage35_workflow_state.json`

Interpretation:

- After object and attribute canonical inventories are clear, the new workflow
  advances to action inventory status and stops at the 135 pending action
  manual rows.
- It does not proceed to action canonical enrichment, Stage 5 lexicon export, or
  formal Stage 4 while the action manual gate is blocked.
- Canonical enrichment commands that write an ambiguous canonical artifact and
  then exit nonzero are treated as produced blocker artifacts; the workflow can
  loop once more and stop with canonical blocker state instead of crashing
  without a state file.

## 2026-07-13: Object/Attribute Selected-Query Prior Reuse And Observed-Surface Lexicon Export

Change:

- Object and attribute observed inventory builders now reuse prior final
  `chosen` decisions by exact `span_key` first, then by unique resolved
  `selected_query` when the prior selected synset is present in the current
  runtime candidates.
- `excluded` and no-synset rows remain exact-span-only.
- Attribute/action Stage 5 synonym export now emits original observed surface
  variants from `span_key`, `observed_surface`, and `example_surfaces`.

Commands:

```powershell
.\scripts\assert_active_workspace.ps1
.\scripts\run_python.ps1 -c "... ast.parse(...) ..."
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_build_gpic_observed_object_inventory.py"
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_build_gpic_observed_attribute_inventory.py"
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_export_attribute_stage5_lexicons.py"
```

Results:

- Active workspace guard: passed.
- AST parse check passed for edited builders/exporter and their tests.
- `test_build_gpic_observed_object_inventory.py`: 5 tests passed.
- `test_build_gpic_observed_attribute_inventory.py`: 10 tests passed.
- `test_export_attribute_stage5_lexicons.py`: 2 tests passed.

Interpretation:

- The duplicate manual-resolution gap for inflectional/Morphy-normalized
  object and attribute surfaces is covered without broad synonym aliasing.
- Stage 5 lexicon export now keeps pre-Morphy observed surfaces such as
  additional `example_surfaces` variants, so canonicalization can use the forms
  that actually appeared in captions.

## 2026-07-13: Action Inventory Selected-Query Prior Reuse

Change:

- Stage 3.5 action inventory generation now reuses prior resolved action
  decisions by exact `span_key` first, then by unique resolved
  `selected_query`.
- Conflicting prior decisions for the same `selected_query` are not reused.

Commands:

```powershell
.\scripts\run_python.ps1 -c "... ast.parse(...) ..."
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_build_gpic_observed_action_inventory.py"
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 -- scripts\build_gpic_observed_action_inventory.py --input outputs\front1000_mixed_current\stage3\stage3_records.jsonl --action-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_canonical.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory.tsv --needs-manual-output outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_needs_manual.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_summary.json
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_formal_inventory_gates.py"
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_stage4_extract_raw.py"
```

Results:

- AST parse check passed for the edited action lookup files.
- `test_build_gpic_observed_action_inventory.py`: 3 tests passed.
- `test_formal_inventory_gates.py`: 10 tests passed.
- `test_stage4_extract_raw.py`: 48 tests passed.
- Front 1k action inventory rebuild:
  - rows: 563
  - previous blocker count: `needs_manual=24`
  - current decision status counts: `chosen=536`, `needs_manual=19`,
    `raw_fallback=8`
  - `prior_action_selected_query_reused=106`
  - representative rows now resolved by prior selected-query reuse:
    `rides`, `mark`, `singing`, `sitting in`

Interpretation:

- The previous duplicate manual gap was caused by exact-span-only prior reuse.
- The new selected-query reuse removes duplicate manual work for inflectional
  or Morphy-normalized variants when prior action inventory evidence is unique.
- The remaining 19 action `needs_manual` rows do not have unique prior
  selected-query evidence and still require manual resolution before formal
  Stage 4.

## 2026-07-13: Formal Pipeline State Manifest Gate

Change:

- Added formal pipeline state manifests so Stage 4/5/6 no longer rely on
  conversational memory or filename conventions to know which preparation
  stages are complete.
- Action inventory artifacts now write and require
  `<artifact>.pipeline_state.json` before formal Stage 4.
- Stage 5 lexicon bundles now write and require `pipeline_state.json` before
  the mixed formal runner can use them.

Commands:

```powershell
.\scripts\assert_active_workspace.ps1
.\scripts\run_python.ps1 -c "... ast.parse(...) ..."
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_pipeline_state.py"
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_formal_inventory_gates.py"
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_mixed_caption_pipeline.py"
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_export_attribute_stage5_lexicons.py"
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_build_gpic_observed_action_inventory.py"
```

Result:

- Active workspace guard: passed.
- AST parse check: passed for 10 changed/new Python files.
- `test_pipeline_state.py`: 2 tests passed.
- `test_formal_inventory_gates.py`: 10 tests passed.
- `test_mixed_caption_pipeline.py`: 4 tests passed.
- `test_export_attribute_stage5_lexicons.py`: 2 tests passed.
- `test_build_gpic_observed_action_inventory.py`: 1 test passed.

Interpretation:

- Legacy action inventories without pipeline-state sidecars are blocked before
  formal Stage 4.
- Mixed formal runs are blocked when the Stage 5 lexicon bundle lacks a valid
  pipeline-state manifest or action canonical export.
- Existing front-1k action inventory work remains blocked until the current
  action `needs_manual` rows are resolved and the action canonical export is
  regenerated through the formal path.

## 2026-07-08: Attribute Type Deferral And Object Core-Span Consumption

Change:

- Active Stage 5/6/report no longer exports `attribute_type`.
- Stage 4 and the attribute inventory builder consume selected object core
  suffix tokens, not necessarily the full lookup span.

Commands:

```powershell
.\scripts\run_python.ps1 -c "... ast.parse(...) ..."
.\scripts\run_tests.ps1 --timeout-seconds 45 discover -s tests -p test_build_gpic_observed_attribute_inventory.py
.\scripts\run_tests.ps1 --timeout-seconds 45 discover -s tests -p test_export_attribute_stage5_lexicons.py
```

Result:

- AST parse check passed for the edited Python files.
- `test_build_gpic_observed_attribute_inventory.py`: 5 tests passed.
- `test_export_attribute_stage5_lexicons.py`: hard-timeout after 45 seconds.
  No `python.exe` process remained afterward. Cause not established from this
  run; do not report that export behavior is runtime-verified from this test.

Interpretation:

- The object core-span rule is covered by the attribute inventory test file.
- Attribute type export behavior is statically updated but the export unittest
  file was not successfully completed in this run.

## 2026-07-03: Action Parent Renamed To Action Type

Change:

- Kept object parent concepts as `parent_concepts`.
- Replaced action parent lookup with `action_type` lookup.
- Replaced `resources/lexicons/action_parents.tsv` with `resources/lexicons/action_types.tsv`.

Commands:

```powershell
.\scripts\run_python.ps1 -m unittest tests.test_stage5_canonicalize tests.test_stage6_export_counts tests.test_schema
.\scripts\run_python.ps1 -m unittest discover tests
```

Result:

- Targeted tests: 11 passed.
- Full unit test suite: 45 passed.

Interpretation:

- Stage 5 now stores action type in `canonical_detail.action_type`.
- Stage 6 `action_event` facts export `action_type`.
- Action mentions no longer receive `parent_concepts` from Stage 5.
- Object parent behavior remains unchanged.

## 2026-07-03: Prevent Accidental Full Test Runs

Problem:

- Running `.\scripts\run_tests.ps1` executed the full pytest suite by default.
- The run was interrupted after a long wall time.
- Collection also loaded the transformer model because two `skipUnless`
  decorators called `spacy.load()` at import/collection time.

Changes:

- `scripts/run_tests.ps1` now defaults to `pytest --collect-only -q`.
- Full pytest execution now requires `.\scripts\run_tests.ps1 --full`.
- Other pytest execution args are rejected unless `--collect-only` is present.
- `tests/test_stage3_annotate.py` and `tests/test_stage4_extract_raw.py` now
  check for the transformer model package with `importlib.util.find_spec()`
  during collection instead of calling `spacy.load()`.

Commands:

```powershell
.\scripts\run_tests.ps1
.\scripts\run_tests.ps1 -q
```

Result:

- Default test command collected 45 tests in 4.39 seconds.
- Accidental execution command `.\scripts\run_tests.ps1 -q` was rejected before
  pytest execution.

Interpretation:

- The default test command no longer starts the full suite.
- Transformer model loading no longer happens during pytest collection.

## 2026-07-03: Add Hard Pytest Subprocess Timeout

Problem:

- Shell-level command timeouts can stop the outer PowerShell process while
  leaving the inner `python.exe -m pytest` process alive.
- `--maxfail=1` is not a timeout and does not protect against hangs.

Change:

- Added `scripts/run_pytest_with_timeout.py`.
- `scripts/run_tests.ps1` now runs pytest through this Python wrapper.
- The wrapper executes `python -m pytest ...` with
  `subprocess.run(..., timeout=...)`.
- Default timeout is 60 seconds.
- A custom timeout can be passed with `--timeout-seconds N`.

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast, pathlib; ast.parse(pathlib.Path('scripts/run_pytest_with_timeout.py').read_text(encoding='utf-8')); print('syntax ok')"
.\scripts\run_tests.ps1 --collect-only -q --timeout-seconds 20
.\scripts\run_tests.ps1 --full --timeout-seconds 1 -q tests/test_stage3_annotate.py::Stage3AnnotateTest
Get-Process | Where-Object { $_.ProcessName -match 'python|pytest' } | Select-Object Id,ProcessName,CPU,StartTime,Path
```

Result:

- Syntax check passed.
- Collection completed: 45 tests collected in 4.53 seconds.
- The 1-second transformer test probe returned
  `PYTEST_TIMEOUT: killed pytest after 1.001s limit=1s`.
- No `python` or `pytest` process remained after the timeout probe.

Interpretation:

- `run_tests.ps1` now has a child-process-level pytest timeout.
- This does not identify the original 50-minute root cause by itself.
- It prevents the same class of lingering pytest process while that cause is
  investigated in smaller timed probes.

## 2026-07-03: Pytest Cache And Sandbox Temp Write Diagnosis

Problem:

- A pytest run could print passing tests and then fail to terminate.
- A later bounded `light` probe failed quickly with `PermissionError` while
  writing files under a Python `TemporaryDirectory`.

Verified observations:

- `tests/test_schema.py` passed its assertions but hung after completion when
  pytest cacheprovider was enabled.
- The same schema test exited normally when pytest ran with
  `-p no:cacheprovider`.
- A sandboxed `run_tests.ps1` execution of `tests/test_io_jsonl.py` failed in
  0.15 seconds with `PermissionError` when writing inside the temp directory.
- The same bounded command outside the sandbox passed in 0.04 seconds.
- The bounded `light` probe outside the sandbox passed in 8.017 seconds.

Changes:

- `scripts/run_pytest_with_timeout.py` runs pytest with
  `-p no:cacheprovider`.
- `scripts/diagnose_test_runtime.py` runs pytest with
  `-p no:cacheprovider`.
- `scripts/run_tests.ps1`, `scripts/run_pytest_with_timeout.py`, and
  `scripts/diagnose_test_runtime.py` default test temp files to
  `C:\Users\Public\Documents\ESTsoft\CreatorTemp\gpic-explainable-link-tests`
  when that directory exists.
- `scripts/diagnose_test_runtime.ps1` no longer exposes the old `all-probes`
  option.

Commands:

```powershell
.\scripts\run_python.ps1 -m compileall scripts
.\scripts\run_tests.ps1 --full --timeout-seconds 30 -q --maxfail=1 -vv tests/test_io_jsonl.py
.\scripts\diagnose_test_runtime.ps1 -Group light -TimeoutSeconds 60
```

Results:

- Script syntax check passed.
- Sandboxed `tests/test_io_jsonl.py` still failed with `PermissionError`, so
  the failure was not treated as a pipeline-code regression.
- The same bounded single-file pytest command outside the sandbox passed:
  `2 passed in 0.04s`.
- Outside-sandbox `light` probe passed:
  `elapsed_seconds=8.017`, `timeout_seconds=60`, `exit_code=0`.
- Light probe summary:
  `C:\Users\Public\Documents\ESTsoft\CreatorTemp\gpic-explainable-link-tests\test_runtime_20260703_142216.summary.tsv`

Interpretation:

- The earlier post-pass hang was caused by pytest cache finalization behavior in
  this junction/sandbox environment, not by the schema tests themselves.
- The later temp write failures are sandbox subprocess permission failures, not
  JSONL writer failures.
- Future pytest runs should use the bounded wrappers and avoid raw pytest.
- If a sandboxed pytest run reports temp-directory `PermissionError`, confirm
  with the same bounded command outside the sandbox before changing pipeline
  code.

## 2026-07-03: Make Unittest The Default Test Runner

Reason:

- Current tests are `unittest.TestCase` based.
- No pytest fixture-only test syntax was found in the test files.
- Pytest remains useful for diagnostic collection and duration reports, but it
  is not needed for the default correctness path.
- The default path should avoid pytest unless pytest-specific diagnostics are
  needed.

Changes:

- Added `scripts/run_unittest_with_timeout.py`.
- `scripts/run_tests.ps1` now runs bounded `unittest` by default.
- Pytest is now reached only with explicit `--pytest`.
- `AGENTS.md` now states the default runner policy as `unittest` first, pytest
  only for diagnostic exceptions.

Commands:

```powershell
.\scripts\run_python.ps1 -m compileall scripts
.\scripts\run_tests.ps1 --timeout-seconds 30 tests.test_io_jsonl
.\scripts\run_tests.ps1 --timeout-seconds 30 discover -s tests -p test_io_jsonl.py
```

Results:

- Script syntax passed.
- `tests.test_io_jsonl` module-name execution failed because `tests` is not a
  package. Targeted unittest execution should use discovery syntax:
  `discover -s tests -p test_io_jsonl.py`.
- Sandboxed discovery still failed with temp-directory `PermissionError`.
- The same bounded unittest discovery command outside the sandbox passed:
  `Ran 2 tests in 0.011s`, `OK`.

Interpretation:

- Switching to `unittest` is still useful because the default runner no longer
  depends on pytest or pytest cache behavior.
- It does not solve Codex sandbox temp-write restrictions for tests that create
  `TemporaryDirectory` files.
- Temp-writing tests must still be verified with the bounded runner outside the
  sandbox when the sandbox reports `PermissionError`.

## 2026-07-05: Atomic TSV Writer Unit Test Temp Directory

Reason:

- Generated TSV writers were changed to write a same-directory temp file and
  then atomically replace the final path.
- The first atomic writer unittest used Python's default
  `tempfile.TemporaryDirectory()`, which resolved to
  `C:\Users\Public\Documents\ESTsoft\CreatorTemp` in this environment.

Command:

```powershell
.\scripts\run_python.ps1 -m unittest tests.test_atomic_io -v
```

Initial result:

- Failed before exercising `atomic_text_writer`.
- Error: `PermissionError` while writing `out.tsv` inside the default temp
  directory.

Fix:

- Updated `tests/test_atomic_io.py` to create its temp directories under the
  repo-local `.tmp_tests/atomic_io` path and remove them after each test.

Interpretation:

- This failure was a test temp-directory sandbox issue, not evidence that the
  atomic writer itself was broken.
- Future tests that need filesystem writes should choose an explicit writable
  temp root instead of relying on Python's default temp directory.

## 2026-07-05: Generated TSV Write Timeout And Sandbox Permission Diagnosis

Problem:

- A raw Objects365 candidate generation command was interrupted after a long
  wait.
- A bounded `scripts/run_script_with_timeout.py` run later timed out at 90
  seconds.
- The first implementation of `atomic_text_writer` used
  `tempfile.NamedTemporaryFile(dir=target.parent, delete=False)`.

Verified observations:

- The bounded runner killed the script with `SCRIPT_TIMEOUT` and left no Python
  child process behind.
- Phase logging showed the expensive normal phase was `Morphy(oewn)`, around
  18 to 21 seconds.
- Candidate row construction took about 1 second.
- In sandboxed execution, same-directory generated TSV temp creation failed
  with `PermissionError`.
- The same bounded generation command outside the sandbox completed in about
  25 seconds.

Changes:

- `atomic_text_writer` no longer uses `NamedTemporaryFile`.
- It now creates an explicit same-directory temp path using
  `.final_name.pid.uuid.tmp`, opens it with exclusive `"x"` mode, fsyncs it,
  and replaces the final path with `os.replace`.
- `scripts/build_objects365_oewn_candidates.py` now prints coarse phase timing
  and TSV write timing so future runs show where time is spent.

Commands:

```powershell
.\scripts\run_python.ps1 -m compileall scripts src tests
.\scripts\run_python.ps1 -m unittest tests.test_atomic_io -v
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 90 scripts\build_objects365_oewn_candidates.py
```

Results:

- Compile passed.
- Atomic writer unit tests passed: 2 tests.
- Sandboxed generated TSV write failed with `PermissionError`.
- Outside-sandbox bounded generation succeeded.
- Objects365 summary after successful generation:
  `rows=365`, `selected_rows=230`, `reused_selected_rows=68`,
  `rejected_rows=6`, `ambiguous_like_rows=0`, `unresolved_like_rows=61`.

Interpretation:

- The long wait was not a lingering Python child after the bounded runner was
  added.
- The main recurring issue is that generated artifact writes under this
  junction/OneDrive-backed repo path can be denied inside the Codex sandbox.
- `require_escalated` is not a permission fix; it is the correct execution mode
  for this generated artifact command when the sandbox denies the write.
- Long generated artifact scripts must still use the bounded runner.

## 2026-07-06: Stage 2 Tokenizer Source Alignment Test

Change under test:

- `make_stage2_nlp()` now loads `en_core_web_trf` in tokenizer-only mode instead
  of `spacy.blank("en")`.
- Stage 2 still runs only `nlp.make_doc(caption)` plus span protection.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_stage2_preprocess.py
.\scripts\run_python.ps1 -c "import ast, pathlib; files=['src/gpic_concepts_v1/stage2_preprocess.py','tests/test_stage2_preprocess.py','scripts/run_unittest_with_timeout.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8')) for f in files]; print('ast ok')"
```

Results:

- Stage 2 tests passed: 6 tests.
- Changed-file syntax check passed: `ast ok`.

Related test runner adjustment:

- `scripts/run_unittest_with_timeout.py` now uses
  `root.parent/.gpic_tmp/gpic-explainable-link-tests` as its default temp root
  instead of Public CreatorTemp.
- `tests/test_stage2_preprocess.py` now writes unique temp files under the
  runner-provided temp root instead of creating a fresh `TemporaryDirectory`.

Compile note:

- `.\scripts\run_python.ps1 -m compileall src tests scripts` failed because
  `compileall` writes `.pyc` files into `__pycache__` under the
  junction/OneDrive-backed repo path.
- This failure is a write-permission issue, not a syntax error in the changed
  files.

## 2026-07-06: Stage 2/3/4/5 Object Span Pipeline Update

Change under test:

- Stage 2 no longer merges object MWE spans.
- Stage 3 no longer runs object-MWE POS correction.
- Stage 4 now selects object spans inside noun chunks via OEWN noun lookup
  semantics and maps all selected-span tokens to the object mention.
- Stage 5 now uses raw surface labels for object fallback and does not attach
  action types.

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast, pathlib; files=[...]; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8')) for f in files]; print('ast ok')"
.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_stage2_preprocess.py
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage3_annotate.py
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage5_canonicalize.py
```

Results:

- Syntax check passed: `ast ok`.
- Stage 2 tests passed: 7 tests.
- Stage 3 tests passed: 5 tests.
- Stage 4 tests passed: 6 tests.
- Stage 5 tests passed: 4 tests.
- Full stage test bundle passed:
  - command: `.\scripts\run_tests.ps1 --timeout-seconds 240 discover -s tests -p "test_stage*_*.py"`
  - result: 25 tests passed.
- Runtime OEWN sanity check:
  - command: synthetic Stage 3 record for `A dog.` through
    `extract_raw_concepts_from_stage3_record`
  - result: one object mention, `text=dog`,
    `selected_oewn_synset=oewn-02086723-n`.

Test environment note:

- Several tests no longer rely on Python's default temp directory because this
  Codex sandbox can deny writes under AppData or the OneDrive-backed repo
  junction. Tests now probe candidate temp roots before writing.

## 2026-07-06: Object MWE Dead Path Cleanup

Change under test:

- Removed the remaining Stage 2 object MWE loader, PhraseMatcher merge code,
  token extension, and Stage 3 object-MWE compatibility arguments.
- Removed `--object-mwes` from Stage 2, Stage 3, and fast benchmark CLIs.
- Removed the stale `object_mwe` token column from caption concept Markdown
  rendering.
- Updated IO/atomic tests to use the same writable-temp probing policy as the
  Stage tests.

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast, pathlib; files=[...]; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8-sig'), filename=f) for f in files]; print('ast ok', len(files))"
.\scripts\run_tests.ps1 --timeout-seconds 240 discover -s tests -p "test_*.py"
```

Results:

- Syntax check passed: `ast ok 12`.
- Full bounded unittest discovery passed:
  - command: `.\scripts\run_tests.ps1 --timeout-seconds 240 discover -s tests -p "test_*.py"`
  - result: 48 tests passed.

Compile note:

- `compileall src scripts tests` still fails in this sandbox because it writes
  `.pyc` files under the junction/OneDrive-backed repo path. Use AST parsing for
  syntax checks here unless pycache write permissions are fixed.

## 2026-07-06: Stage 4 Ambiguous Synset Gate

Issue found:

- The 20-caption sample output contained object mentions whose OEWN lookup had
  noun synsets but no selected synset:
  - `ambiguous_wn30_all_zero`: 21 mentions
  - `ambiguous_wn30_tie`: 2 mentions
- Stage 4 had accepted those spans because it checked `lookup.synsets` but did
  not require `lookup.selected_synset`.
- Stage 5/6 then continued through raw fallback, which violated the current
  object-synset policy.

Fix under test:

- Stage 4 now raises `Stage4SynsetAmbiguityError` when an object span has OEWN
  noun candidates but no selected synset.
- Missing OEWN lookup still means "do not create an object mention"; ambiguous
  lookup means "stop and resolve offline first."

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast,pathlib; files=[...]; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8-sig'), filename=f) for f in files]; print('ast ok', len(files))"
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_python.ps1 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --raw-mentions outputs\case_reports_sentence20_current_after_ambiguous_gate\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current_after_ambiguous_gate\raw_edges.jsonl --summary outputs\case_reports_sentence20_current_after_ambiguous_gate\stage4_summary.jsonl
```

Results:

- Syntax check passed: `ast ok 2`.
- Stage 4 tests passed: 7 tests.
- Runtime 20-caption Stage 4 run now stops at the first unresolved ambiguity:
  - caption_id: `c90e89252ab6c4dde38fddfe360d0ce85dd31790e7ae838dc610bebb349f2b5f`
  - surface/query: `graphics`
  - tag: `ambiguous_wn30_all_zero`
  - candidate synsets: `oewn-07011408-n`, `oewn-03458929-n`

## 2026-07-06: GPIC Observed Object Inventory Runtime Boundary

Issue found:

- The active pipeline boundary was still easy to confuse with the earlier
  external source-label inventory work.
- COCO/LVIS/Objects365/OpenImages/Visual Genome source-label inventories are
  not active runtime input for GPIC caption extraction.
- Stage 4 must consume a GPIC observed object inventory built from Stage 3 GPIC
  records.

Fix under test:

- Added `scripts/build_gpic_observed_object_inventory.py`.
- Added `load_gpic_object_inventory()` and `GpicObjectInventoryLookup`.
- Added human-facing inventory queue fields:
  - `decision_status`
  - `decision_reason`
  - `objectness_gate` as evidence, not as the main status
- `scripts/run_stage4_extract_raw.py` and `scripts/benchmark_fast_pipeline.py`
  now require `--object-inventory` unless `--allow-runtime-oewn-lookup` is
  explicitly passed for probe/debug runs.
- Updated Stage 4 tests to verify inventory-driven object span selection.

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast,pathlib; files=[...]; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8-sig'), filename=f) for f in files]; print('ast ok', len(files))"
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 -- scripts\build_gpic_observed_object_inventory.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --output outputs\case_reports_sentence20_current\gpic_observed_object_inventory.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_object_inventory_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 90 -- scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory.tsv --raw-mentions outputs\case_reports_sentence20_current_after_gpic_inventory\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current_after_gpic_inventory\raw_edges.jsonl --summary outputs\case_reports_sentence20_current_after_gpic_inventory\stage4_summary.jsonl
```

Results:

- Syntax check passed: `ast ok 3`.
- Stage 4 tests passed: 9 tests.
- GPIC observed inventory builder over 20 captions:
  - caption_total: 20
  - noun_chunk_total: 263
  - inventory_rows: 194
  - decision_status_counts:
    - chosen: 105
    - needs_manual: 81
    - excluded: 8
  - decision_reason_counts:
    - selected_object_compatible: 105
    - manual_objectness_required: 66
    - manual_synset_required: 15
    - no_oewn_noun_synset: 8
  - plural common noun lookup examples:
    - `men`: observed surface `men`, selected query `man`, chosen
    - `windows`: observed surface `windows`, selected query `window`, chosen
    - `leaves`: observed surface `leaves`, selected query `leaf`, chosen
- Stage 4 with the generated GPIC inventory stopped as intended on the first
  row that is not chosen:
  - caption_id: `c90e89252ab6c4dde38fddfe360d0ce85dd31790e7ae838dc610bebb349f2b5f`
  - surface/query: `front`
  - decision_status: `needs_manual`
  - objectness_gate: `conditional`
  - tag: `selected_by_wn30_lemma_count`

Environment note:

- The active repo path is a junction to the OneDrive-backed repository.
- The sandboxed generated TSV write failed with `PermissionError` while opening
  a same-directory atomic temp file.
- The successful generated-artifact run used the same narrow bounded command
  with `require_escalated`, following the generated artifact policy in
  `AGENTS.md`.

## 2026-07-07: Joined Variant False Positive Guard

Issue found:

- Separator removal during OEWN lookup can create unrelated joined words:
  - `black shirt -> blackshirt`
  - `black top -> blacktop`
  - `A man -> aman`
- These should not be automatically counted as chosen object spans.

Fix under test:

- Multiword spans starting with function words such as `DET`, `ADP`, or `PRON`
  are skipped before OEWN probe. This makes `A man` fall through to `man`.
- If a span is found only through `joined_variant` or
  `last_word_morphy_after_joined_variant`, it is kept as `needs_manual` with
  `decision_reason=manual_joined_variant_required`.
- Exact and space-preserving MWE lookup can still become `chosen`.

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast,pathlib; files=[...]; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8-sig'), filename=f) for f in files]; print('ast ok', len(files))"
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 -- scripts\build_gpic_observed_object_inventory.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --output outputs\case_reports_sentence20_current\gpic_observed_object_inventory.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_object_inventory_summary.json
```

Results:

- Syntax check passed: `ast ok 3`.
- `git diff --check` passed for changed files.
- Stage 4 unit tests: 13 passed.
- Regenerated 20-caption GPIC observed object inventory:
  - `chosen`: 103
  - `needs_manual`: 83
  - `excluded`: 8
  - `manual_joined_variant_required`: 3
- Confirmed joined-variant manual rows:
  - `black shirt -> blackshirt`
  - `black top -> blacktop`
  - `seed pods -> seedpod`
- Confirmed determiner-start span behavior:
  - `man -> man`, chosen
  - `men -> man`, chosen

## 2026-07-07: Excluded Inventory Rows Counted As Status-Tagged Objects

Issue found:

- `excluded` rows were being treated like dropped rows during runtime object span
  selection.
- This made no-synset or non-object-status labels disappear from count output,
  even though the row had already been explicitly tagged as `excluded`.

Decision:

- `decision_status=excluded` is a quality/status tag, not a count gate.
- `decision_status=chosen` and `decision_status=excluded` both create object
  mentions.
- `decision_status=needs_manual` stops Stage 4 extraction.
- A row with a selected synset but unresolved canonical surface also stops
  Stage 4 extraction.
- Missing inventory rows are still not counted.

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast,pathlib; files=['src/gpic_concepts_v1/stage4_extract_raw.py','tests/test_stage4_extract_raw.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8-sig'), filename=f) for f in files]; print('ast ok', len(files))"
git diff --check -- src\gpic_concepts_v1\stage4_extract_raw.py tests\test_stage4_extract_raw.py docs\rules_v1.md docs\implementation_plan_v1.md docs\output_schema_v1.md
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_python.ps1 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --raw-mentions C:\Users\Public\Documents\ESTsoft\CreatorTemp\gpic_excluded_count_stage4_probe\raw_mentions.jsonl --raw-edges C:\Users\Public\Documents\ESTsoft\CreatorTemp\gpic_excluded_count_stage4_probe\raw_edges.jsonl --summary C:\Users\Public\Documents\ESTsoft\CreatorTemp\gpic_excluded_count_stage4_probe\stage4_summary.jsonl
```

Results:

- Syntax check passed: `ast ok 2`.
- `git diff --check` passed for changed files.
- Stage 4 unit tests: 14 passed.
- Added regression coverage for an `excluded` inventory row with no OEWN synset:
  - object mention is created
  - `decision_status=excluded` is preserved in `source_detail`
  - `has_oewn_noun_synset=false` is preserved in `source_detail`
- 20-caption Stage 4 probe with the redecided inventory completed:
  - raw mentions: 510
  - object mentions: 263

## 2026-07-07: Canonical Ambiguity Gate

Issue found:

- Canonical enrichment could be run even when `decision_status=needs_manual`
  rows were still present, which violated the intended order:
  synset/objectness manual resolution first, canonical surface selection second.
- Canonical enrichment could leave unresolved canonical rows in the ambiguous
  TSV while still exiting successfully.
- Stage 4 also needed to reject rows with a selected synset but empty
  `canonical_surface`.

Fix:

- `enrich_gpic_inventory_canonical.py` now exits before OEWN loading if any
  `needs_manual` row remains.
- `enrich_gpic_inventory_canonical.py` now writes output, ambiguous TSV, and
  summary first, then exits nonzero if `canonical_ambiguous_rows > 0`.
- Stage 4 raises `Stage4SynsetAmbiguityError` for selected-synset rows whose
  canonical surface is unresolved.

Verification:

- Syntax check passed for:
  - `src/gpic_concepts_v1/stage4_extract_raw.py`
  - `scripts/enrich_gpic_inventory_canonical.py`
  - `tests/test_stage4_extract_raw.py`
- `git diff --check` passed for changed gate files and docs.
- `test_enrich_gpic_inventory_canonical.py`: 3 tests passed.
- Real sentence 101-200 inventory canonical precondition check:
  - status: `blocked_needs_manual_before_canonical`
  - needs_manual_rows: 212
  - confirmed no output TSV was written before failure
- `test_stage4_extract_raw.py`: 15 tests passed.
  - object decision status counts:
    - `chosen`: 244
    - `excluded`: 19
  - first excluded examples counted as object mentions:
    - `them`
    - `another`
    - `center-right`
    - `hours`
    - `"J.B. HUNT Intermodal."`

## 2026-07-07: Selected OEWN Parent Evidence Propagation

Decision under test:

- Once offline/manual synset resolution produces final `selected_oewn_synset`,
  parent evidence must be filled from that selected synset.
- Parent evidence means every immediate OEWN hypernym synset ID, not one chosen
  parent lemma.
- Stage 5 should attach those parent synset IDs as object `parent_concepts`.
- Stage 6 should expose parent columns in object, role, relation, and object
  co-occurrence count tables.

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast,pathlib; files=[...]; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8-sig'), filename=f) for f in files]; print('ast ok', len(files))"
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage5_canonicalize.py
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage6_export_counts.py
.\scripts\run_python.ps1 scripts\enrich_gpic_inventory_parents.py --input outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --output outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_object_inventory_parent_summary.json
.\scripts\run_python.ps1 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --raw-mentions outputs\case_reports_sentence20_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges.jsonl --summary outputs\case_reports_sentence20_current\stage4_summary.jsonl
.\scripts\run_python.ps1 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence20_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges.jsonl --lexicon-dir resources\lexicons --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges.jsonl --summary outputs\case_reports_sentence20_current\stage5_summary.jsonl
.\scripts\run_python.ps1 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges.jsonl --output-dir outputs\case_reports_sentence20_current\stage6 --summary outputs\case_reports_sentence20_current\stage6_summary.jsonl
```

Results:

- Syntax check passed: `ast ok 9`.
- Stage 4 tests: 14 passed.
- Stage 5 tests: 4 passed.
- Stage 6 tests: 2 passed.
- Parent enrichment summary:
  - rows: 194
  - selected_synset_missing_rows: 10
  - parent_filled_rows: 184
  - parent_empty_rows: 0
- Re-run summary:
  - Stage 4 object mentions: 263
  - Stage 5 object mentions with parent: 251
  - Stage 6 `object_counts.tsv` includes `parent_concepts`.
  - Stage 6 relation/object co-occurrence count tables include source/target
    parent concept columns.

## 2026-07-07: GPIC Observed Canonical Surface Propagation

Decision under test:

- Selected synset alone is not enough; object canonical surface must also be
  chosen by the offline canonical rule.
- Stage 5 should use inventory `canonical_surface` as object canonical label
  and mark `canonical_source=gpic_observed_inventory`.
- If canonical remains ambiguous, the inventory row keeps blank
  `canonical_surface` and the row appears in the canonical ambiguous TSV.

Commands:

```powershell
.\scripts\run_python.ps1 scripts\enrich_gpic_inventory_canonical.py --input outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --output outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --ambiguous-output outputs\case_reports_sentence20_current\gpic_observed_object_inventory_canonical_ambiguous.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_object_inventory_canonical_summary.json
.\scripts\run_python.ps1 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --raw-mentions outputs\case_reports_sentence20_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges.jsonl --summary outputs\case_reports_sentence20_current\stage4_summary.jsonl
.\scripts\run_python.ps1 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence20_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges.jsonl --lexicon-dir resources\lexicons --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges.jsonl --summary outputs\case_reports_sentence20_current\stage5_summary.jsonl
.\scripts\run_python.ps1 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges.jsonl --output-dir outputs\case_reports_sentence20_current\stage6 --summary outputs\case_reports_sentence20_current\stage6_summary.jsonl
```

Results:

- Initial canonical enrichment exposed one bug:
  - `sun` and `Sun` tied on WN3 count.
  - The implementation used case-insensitive observed-surface matching, leaving
    `sun` ambiguous.
  - Fixed to use exact observed surface display match for this rule step.
- Current canonical enrichment:
  - rows: 194
  - selected_synset_missing_rows: 10
  - canonical_selected_rows: 184
  - canonical_ambiguous_rows: 0
- Stage 5 after re-run:
  - `canonical_source_counts`: `gpic_observed_inventory=251`, `raw_fallback=259`
- Bounded unit tests:
  - `test_atomic_io.py`: 3 passed
  - `test_stage4_extract_raw.py`: 14 passed
  - `test_stage5_canonicalize.py`: 4 passed

## 2026-07-07: Sentence 101-200 Manual Resolution End-to-End Check

Decision under test:

- Canonical enrichment must not run before `needs_manual` rows are resolved.
- After manual resolution removes `needs_manual`, parent/canonical enrichment should run.
- Stage 4 should accept `chosen` and `excluded` inventory rows, but still block `needs_manual`.
- Stage 5/6 should produce count tables from the manual-resolved inventory.

Inputs:

- `outputs/case_reports_sentence100_0101_0200_current/stage3_records.jsonl`
- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_object_inventory_manual_resolved.tsv`

Manual resolved inventory validation:

- rows: 567
- `chosen`: 514
- `excluded`: 53
- `needs_manual`: 0
- corrected invalid manual helmet synset:
  - `oewn-03521675-n` -> `oewn-03518281-n`
- corrected missing selected synset for `white feathers`:
  - selected query `white feather` -> `feather`
  - selected synset blank -> `oewn-89570581-n`

Parent enrichment result:

- output: `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_object_inventory_manual_resolved_parents.tsv`
- parent_filled_rows: 514
- parent_lookup_error_rows: 0
- selected_synset_missing_rows: 53

Canonical enrichment result:

- output: `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv`
- canonical_selected_rows: 514
- canonical_ambiguous_rows: 0
- canonical_lookup_error_rows: 0
- selected_synset_missing_rows: 53

Stage 4 result:

- raw mentions: 2242
- raw edges: 1224
- objects: 1169
- attributes: 604
- actions: 432
- quantities: 37
- relations: 197

Stage 5 result:

- canonical mentions: 2242
- canonical edges: 1224
- canonical_source_counts:
  - `gpic_observed_inventory`: 1065
  - `raw_fallback`: 1177

Stage 6 result:

- output: `outputs/case_reports_sentence100_0101_0200_current/counts_manual_resolved`
- fact_total: 21833
- table row counts:
  - object_counts.tsv: 504
  - attribute_counts.tsv: 253
  - object_attribute_pair_counts.tsv: 537
  - action_counts.tsv: 200
  - agent_patient_pair_counts.tsv: 345
  - relation_triple_counts.tsv: 182
  - object_cooccurrence_pair_counts.tsv: 16324

## 2026-07-07: Manual Resolution Gate Regression

Decision under test:

- A final manual-resolved object inventory row must be either `chosen` or
  `excluded`.
- Any other explicit decision status is treated as pending manual work.
- If a `chosen` row changes the surface/head form but has no
  `selected_oewn_synset`, parent/canonical enrichment must stop before OEWN
  loading.

Representative case:

- `white feathers`
  - bad pending state:
    - `decision_status=chosen`
    - `selected_query=white feather`
    - `selected_oewn_synset=` blank
    - `canonical_surface=feather`
  - expected gate:
    - `surface_correction_requires_synset_lookup`

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast,pathlib; files=[...]; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8-sig'), filename=f) for f in files]; print('ast ok', len(files))"
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_inventory_validation.py
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_enrich_gpic_inventory_canonical.py
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_enrich_gpic_inventory_parents.py
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage4_extract_raw.py
```

Results:

- AST parse: `ast ok 7`
- `test_inventory_validation.py`: 4 passed
- `test_enrich_gpic_inventory_canonical.py`: 4 passed
- `test_enrich_gpic_inventory_parents.py`: 1 passed
- `test_stage4_extract_raw.py`: 15 passed

## 2026-07-08: Attribute Manual No-Synset Chosen Normalization

Decision under test:

- Attribute manual feedback with `decision_status=chosen` but blank
  `selected_oewn_synset` is not a valid chosen row.
- Before attribute canonical enrichment, that row is normalized to
  `decision_status=excluded`.
- `excluded` is a resolved manual status, but it is not a canonical decision.
- Every `excluded` row clears canonical columns and receives
  `canonical_selection_tag=not_applicable_excluded`.
- Feedback-provided `canonical_surface` and `manual_*` canonical tags are ignored
  for non-excluded selected-synset rows; canonical enrichment recomputes them.

Representative case:

- `TYR`
  - previous state:
    - `decision_status=chosen`
    - `selected_oewn_synset=` blank
    - `canonical_surface=tyr`
    - `canonical_selection_tag=manual_surface_canonical`
  - expected state:
    - `decision_status=excluded`
    - `decision_reason=manual_excluded_oewn_false_positive_brand_modifier_no_synset`
    - `canonical_surface=` blank
    - `canonical_selection_tag=not_applicable_excluded`

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_enrich_gpic_attribute_inventory_canonical.py
.\scripts\run_python.ps1 scripts\enrich_gpic_attribute_inventory_canonical.py --input outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_manual_resolved.tsv --output outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_canonical.tsv --ambiguous-output outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_canonical_ambiguous.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_canonical_summary.json
```

Results:

- `test_enrich_gpic_attribute_inventory_canonical.py`: 6 passed
- 20-caption attribute canonical enrichment:
  - rows: 101
  - excluded_not_applicable_rows: 4
  - selected_synset_missing_rows: 0
  - canonical_selected_rows: 97
  - canonical_ambiguous_rows: 0
  - manual_surface_canonical rows: 0
  - canonical tag counts:
    - `selected_single_observed_variant_matched_synset_lemma`: 95
    - `selected_by_wn30_lemma_count_unique_positive_max`: 2
    - `not_applicable_excluded`: 4
  - status counts: `chosen=97`, `excluded=4`
  - all excluded rows: `canonical_selection_tag=not_applicable_excluded`

## 2026-07-08: Attribute Type Lexicon Export and Count Propagation

Decision under test:

- Typed attribute inventory is converted into a Stage 5 lexicon bundle.
- `excluded` rows never export canonical synonyms, even if feedback supplied a
  canonical value.
- `excluded` rows may still export `attribute_type` against the raw-fallback
  key for audit/filtering.
- Stage 6 object-attribute pair counts should carry `attribute_type`.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_export_attribute_stage5_lexicons.py
.\scripts\run_python.ps1 scripts\export_attribute_stage5_lexicons.py --attribute-inventory outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_typed.tsv --output-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_typed --base-lexicon-dir resources\lexicons --summary outputs\case_reports_sentence20_current\attribute_stage5_lexicon_export_summary.json
.\scripts\run_python.ps1 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence20_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges.jsonl --lexicon-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_typed --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attr_typed.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attr_typed.jsonl --summary outputs\case_reports_sentence20_current\stage5_attr_typed_summary.jsonl
.\scripts\run_python.ps1 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attr_typed.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attr_typed.jsonl --output-dir outputs\case_reports_sentence20_current\stage6_attr_typed --summary outputs\case_reports_sentence20_current\stage6_attr_typed_summary.jsonl
```

Results:

- `test_export_attribute_stage5_lexicons.py`: 1 passed.
- Attribute lexicon export:
  - inventory rows: 101
  - chosen synonym rows added: 97
  - attribute type rows: 99
  - excluded type rows: 4
  - ignored excluded canonical rows: 0
- Stage 5 typed run:
  - canonical mentions: 510
  - canonical edges: 289
  - canonical source counts: `gpic_observed_inventory=251`, `lexicon=128`, `raw_fallback=131`
- Stage 6 typed run:
  - fact total: 4830
  - object-attribute pair rows: 129
  - object-attribute pair rows with `attribute_type`: 129
  - excluded attributes remain raw fallback:
    - `Several -> several`, `canonical_source=raw_fallback`
    - `entire -> entire`, `canonical_source=raw_fallback`
    - `overall -> overall`, `canonical_source=raw_fallback`
    - `TYR -> tyr`, `canonical_source=raw_fallback`

## 2026-07-08: Invalidated Attribute Export Test Runner Attempt

Decision under test:

- Attribute inventory `decision_status` should use only `chosen`, `excluded`,
  and `needs_manual`.
- OEWN lookup failure should be represented by reason/metadata, not by a fourth
  `decision_status=no_synset`.

Invalid command:

```powershell
.\scripts\run_python.ps1 -m unittest tests.test_export_attribute_stage5_lexicons
```

Result:

- Invalidated. This command was the wrong runner for repository validation.
- It repeated the temp/PermissionError failure pattern and was interrupted by
  the user after it remained visible for many minutes.
- The result must not be used as pass/fail evidence.

Root-cause record:

- See `docs/test_runner_incident_log_v1.md`.

## 2026-07-08: 20 Caption Attribute-Current Report Regeneration

Decision under test:

- Attribute type export is deferred: Stage 5 lexicon bundle should carry
  attribute canonical synonyms but no `attribute_type` rows.
- Stage 4 object span selection should not consume modifier tokens when the
  selected object core is the head, so `black top`, `black shirt`, and
  `blue wall` still produce color attributes.

Commands:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --raw-mentions outputs\case_reports_sentence20_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges.jsonl --summary outputs\case_reports_sentence20_current\stage4_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\export_attribute_stage5_lexicons.py --attribute-inventory outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_typed.tsv --output-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_current --base-lexicon-dir resources\lexicons --summary outputs\case_reports_sentence20_current\attribute_stage5_lexicon_export_summary_current.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence20_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges.jsonl --lexicon-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_current --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_current.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_current.jsonl --summary outputs\case_reports_sentence20_current\stage5_attribute_current_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_current.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_current.jsonl --output-dir outputs\case_reports_sentence20_current\stage6_attribute_current --summary outputs\case_reports_sentence20_current\stage6_attribute_current_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\build_caption_concept_md.py --sentence-rows outputs\benchmark_real10k_train\sentence_rows_9896.jsonl.gz --stage3-records outputs\case_reports_sentence20_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_current.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_current.jsonl --facts outputs\case_reports_sentence20_current\stage6_attribute_current\facts.jsonl --output outputs\case_reports_sentence20_current\caption_to_concept_cases_0001_0020_attribute_current.md --start 0 --limit 20 --max-object-pairs-per-caption 40
```

Execution note:

- The first sandboxed attempts hit `PermissionError` because
  `C:\Users\rlath\Documents\Codex\gpic-explainable-link` is a junction to the
  OneDrive repo path. The successful rerun used the bounded
  `run_script_with_timeout.py` wrapper with sandbox escalation.

Results:

- Stage 4:
  - mentions: 513
  - edges: 292
  - `has_attribute`: 138
- Attribute lexicon export:
  - `attribute_synonym_rows`: 97
  - `attribute_type_rows`: 0
  - `attribute_type_rows_deferred`: 101
- Stage 5:
  - canonical mentions: 513
  - canonical edges: 292
  - canonical source counts:
    `gpic_observed_inventory=251`, `lexicon=131`, `raw_fallback=131`
- Stage 6:
  - fact total: 4833
  - `has_attribute`: 138
  - object-attribute pair rows: 132
- Markdown report:
  - `outputs/case_reports_sentence20_current/caption_to_concept_cases_0001_0020_attribute_current.md`

Validation:

- `outputs/case_reports_sentence20_current/stage5_lexicons_attribute_current/attribute_types.tsv`
  contains only the header row.
- `rg attribute_type` over the current Markdown, Stage 5 JSONL, and Stage 6
  output returned no matches.
- Expected restored pairs are present:
  - `top` + `black`
  - `shirt` + `black`
  - `headphone` + `black`
  - `wall` + `blue`

## 2026-07-08: Attribute Modifier `nmod` Recall Expansion

Decision under test:

- R11.1 and R13 attribute modifier dependencies include `nmod` in addition to
  `amod` and `compound`.
- `conj` is intentionally not included in this change.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_build_gpic_observed_attribute_inventory.py
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --raw-mentions outputs\case_reports_sentence20_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges.jsonl --summary outputs\case_reports_sentence20_current\stage4_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence20_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges.jsonl --lexicon-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_current --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_current.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_current.jsonl --summary outputs\case_reports_sentence20_current\stage5_attribute_current_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_current.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_current.jsonl --output-dir outputs\case_reports_sentence20_current\stage6_attribute_current --summary outputs\case_reports_sentence20_current\stage6_attribute_current_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\build_caption_concept_md.py --sentence-rows outputs\benchmark_real10k_train\sentence_rows_9896.jsonl.gz --stage3-records outputs\case_reports_sentence20_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_current.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_current.jsonl --facts outputs\case_reports_sentence20_current\stage6_attribute_current\facts.jsonl --output outputs\case_reports_sentence20_current\caption_to_concept_cases_0001_0020_attribute_current.md --start 0 --limit 20 --max-object-pairs-per-caption 40
```

Results:

- `test_stage4_extract_raw.py`: 17 passed.
- `test_build_gpic_observed_attribute_inventory.py`: 6 passed.

## 2026-07-09: 20-Caption Rerun With OEWN-Based Phrasal Action Selection

Decision under test:

- R15 can select OEWN-backed phrasal action spans.
- R17 can use the direct `pobj` of a consumed phrasal-action ADP as patient.
- R18 excludes ADP tokens consumed by selected phrasal actions.
- Corrected on 2026-07-09: action synset ambiguity is not pass-through
  metadata. The generated `attribute_action_current` files from this run are
  invalid as formal output because Stage 4 should have stopped on action
  `needs_manual`.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --raw-mentions outputs\case_reports_sentence20_current\raw_mentions_action_current.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges_action_current.jsonl --summary outputs\case_reports_sentence20_current\stage4_action_current_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence20_current\raw_mentions_action_current.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges_action_current.jsonl --lexicon-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_current --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_current.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_current.jsonl --summary outputs\case_reports_sentence20_current\stage5_attribute_action_current_summary.jsonl --attribute-inventory outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_typed.tsv
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_current.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_current.jsonl --output-dir outputs\case_reports_sentence20_current\stage6_attribute_action_current --summary outputs\case_reports_sentence20_current\stage6_attribute_action_current_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\build_caption_concept_md.py --sentence-rows outputs\benchmark_real10k_train\sentence_rows_9896.jsonl.gz --stage3-records outputs\case_reports_sentence20_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_current.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_current.jsonl --facts outputs\case_reports_sentence20_current\stage6_attribute_action_current\facts.jsonl --output outputs\case_reports_sentence20_current\caption_to_concept_cases_0001_0020_attribute_action_current.md --start 0 --limit 20 --max-object-pairs-per-caption 40
```

Results:

- `test_stage4_extract_raw.py`: 19 passed.
- Stage 4:
  - mentions: 516
  - edges: 307
  - actions: 104
  - event roles: 108
  - relations: 50
- Stage 5:
  - canonical mentions: 516
  - canonical edges: 307
  - canonical source counts:
    `gpic_observed_inventory=251`, `lexicon=131`, `raw_fallback=134`
- Stage 6:
  - fact total: 4848
  - action events: 104
  - event roles: 108
  - relation facts: 50
  - action count rows: 82
  - agent/patient pair rows: 105
- Phrasal action spans selected: 13.
- Markdown report:
  - `outputs/case_reports_sentence20_current/caption_to_concept_cases_0001_0020_attribute_action_current.md`

Inspection note:

- Some selected phrasal action spans look useful, e.g. `stand in`, `run on`,
  `cascade down`, `cling to`.
- Some spans need review as possible false positives, e.g. `frame In`.
- Stage 4:
  - mentions: 516
  - edges: 295
  - `has_attribute`: 141
- Stage 5:
  - canonical mentions: 516
  - canonical edges: 295
  - canonical source counts:
    `gpic_observed_inventory=251`, `lexicon=131`, `raw_fallback=134`
- Stage 6:
  - fact total: 4836
  - `has_attribute`: 141
  - object-attribute pair rows: 135

Validation:

- `outputs/case_reports_sentence20_current/stage6_attribute_current/object_attribute_pair_counts.tsv`
  contains `object_attribute_pair:jersey:maroon`.
- The 20-caption Markdown contains `jersey (jerseys) has_attribute maroon`.
- `yellow` in `maroon and yellow jerseys` is still not attached to `jersey`
  because `yellow.dep_ == conj`, and `conj` is outside this approved change.

## 2026-07-08: Sentence 101-200 Attribute-Current Report Regeneration

Decision under test:

- Re-run the 100-caption sentence 101-200 report with the current Stage 4
  attribute modifier dependency set: `amod`, `compound`, `nmod`.
- Do not rebuild the 100-caption attribute manual inventory yet.
- Use the existing manual-resolved object inventory for object
  canonicalization and parent concepts.
- Attribute canonicalization remains raw fallback for this 100-caption run
  because the base `resources/lexicons/attribute_synonyms.tsv` has only a
  header row.

Commands:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --raw-mentions outputs\case_reports_sentence100_0101_0200_current\raw_mentions_attribute_current.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_current\raw_edges_attribute_current.jsonl --summary outputs\case_reports_sentence100_0101_0200_current\stage4_attribute_current_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence100_0101_0200_current\raw_mentions_attribute_current.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_current\raw_edges_attribute_current.jsonl --lexicon-dir resources\lexicons --canonical-mentions outputs\case_reports_sentence100_0101_0200_current\canonical_mentions_attribute_current.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_current\canonical_edges_attribute_current.jsonl --summary outputs\case_reports_sentence100_0101_0200_current\stage5_attribute_current_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence100_0101_0200_current\canonical_mentions_attribute_current.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_current\canonical_edges_attribute_current.jsonl --output-dir outputs\case_reports_sentence100_0101_0200_current\stage6_attribute_current --summary outputs\case_reports_sentence100_0101_0200_current\stage6_attribute_current_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\build_caption_concept_md.py --sentence-rows outputs\case_reports_sentence100_0101_0200_current\sentence_rows_0101_0200.jsonl --stage3-records outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence100_0101_0200_current\canonical_mentions_attribute_current.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_current\canonical_edges_attribute_current.jsonl --facts outputs\case_reports_sentence100_0101_0200_current\stage6_attribute_current\facts.jsonl --output outputs\case_reports_sentence100_0101_0200_current\caption_to_concept_cases_0101_0200_attribute_current.md --start 0 --limit 100 --max-object-pairs-per-caption 40
```

Results:

- Stage 4:
  - mentions: 2252
  - edges: 1234
  - `has_attribute`: 614
- Stage 5:
  - canonical mentions: 2252
  - canonical edges: 1234
  - canonical source counts:
    `gpic_observed_inventory=1065`, `raw_fallback=1187`
- Stage 6:
  - fact total: 21843
  - `has_attribute`: 614
  - object-attribute pair rows: 547
  - object co-occurrence pair facts: 19008
- Markdown report:
  - `outputs/case_reports_sentence100_0101_0200_current/caption_to_concept_cases_0101_0200_attribute_current.md`

Validation:

- `rg attribute_type` over the 100-caption Markdown, Stage 5 JSONL, and Stage 6
  output returned no matches.

## 2026-07-08: Sentence 101-200 Attribute Inventory Status Refresh

Issue:

- The existing 100-caption attribute inventory summary still contained the
  legacy status `decision_status=no_synset`.
- Current rules use only `chosen`, `needs_manual`, and `excluded` as the main
  queue status. Missing OEWN attribute synsets are represented as
  `decision_status=chosen` with
  `decision_reason=no_oewn_attribute_synset`.

Command:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\build_gpic_observed_attribute_inventory.py --input outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --output C:\Users\Public\Documents\ESTsoft\CreatorTemp\gpic_attr_inventory_probe_100.tsv --summary C:\Users\Public\Documents\ESTsoft\CreatorTemp\gpic_attr_inventory_probe_100_summary.json
```

Result:

- Probe regenerated successfully outside the sandbox after the sandboxed run
  failed to open the OEWN sqlite database.
- Current status counts:
  - `chosen`: 161
  - `needs_manual`: 96
  - `no_synset`: 0
- Current copied outputs:
  - `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_attribute_inventory_current.tsv`
  - `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_attribute_inventory_current_needs_manual.tsv`

Interpretation:

- The old `gpic_observed_attribute_inventory.tsv` in this folder is a stale
  snapshot for status naming.
- Use the `_current` files for the next manual attribute-resolution pass.

## 2026-07-08: Formal Stage4 and Stage5 Inventory Gate Enforcement

Decision under test:

- Stage 4 runner must block before raw extraction when the object inventory has
  pending rows or a selected synset without canonical surface.
- Stage 5 runner must require a resolved attribute inventory for formal output.
- Stage 5 runner must block before canonicalization when the attribute inventory
  has pending rows or a selected synset without canonical surface.
- Stage 5 unresolved runs must be explicit preview runs.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_inventory_validation.py
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_formal_inventory_gates.py
.\scripts\run_python.ps1 -c "import ast, pathlib; files=['src/gpic_concepts_v1/inventory_validation.py','scripts/run_stage4_extract_raw.py','scripts/run_stage5_canonicalize.py','tests/test_inventory_validation.py','tests/test_formal_inventory_gates.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8'), filename=f) for f in files]; print('ast ok', len(files))"
```

Results:

- `test_inventory_validation.py`: 5 passed.
- `test_formal_inventory_gates.py`: 6 passed.
- AST parse: `ast ok 5`.
- Actual 100-caption Stage 5 gate probe with
  `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_attribute_inventory_current.tsv`
  stopped before canonicalization:
  - status: `blocked_attribute_inventory_before_stage5`
  - rows: 257
  - blocked_rows: 231
  - first blocker reasons include `selected_synset_missing_canonical_surface`
  - note: this was a formal Stage 5 readiness gate, not the earlier
    attribute synset/manual inventory gate.

Interpretation:

- Formal Stage 4/5 runners now stop before incomplete inventories can be
  promoted into formal Stage 5/6/Markdown outputs.
- Stage 5 preview output is still possible only with
  `--allow-unresolved-attribute-preview`.

## 2026-07-08: Sentence 101-200 Attribute Manual Resolution Applied

Decision under test:

- The user-provided 96-row attribute manual resolution file should replace the
  96 pending rows in the full 257-row sentence 101-200 attribute inventory.
- After canonical enrichment, the full attribute inventory should have no
  pending manual rows and no selected synset rows missing canonical surface.
- The resulting Stage 5 run should pass the formal attribute inventory gate.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_apply_attribute_manual_resolution.py
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 60 scripts\apply_attribute_manual_resolution.py --full-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current.tsv --resolved-subset C:\Users\rlath\Downloads\gpic_observed_attribute_inventory_current_resolved.tsv --manual-decisions C:\Users\rlath\Downloads\gpic_observed_attribute_inventory_current_manual_decisions.tsv --output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved.tsv --resolved-copy outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_subset.tsv --manual-decisions-copy outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_decisions.tsv --summary outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolution_apply_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\enrich_gpic_attribute_inventory_canonical.py --input outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved.tsv --output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv --ambiguous-output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical_ambiguous.tsv --summary outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\export_attribute_stage5_lexicons.py --attribute-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv --output-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_current_manual_resolved --base-lexicon-dir resources\lexicons --summary outputs\case_reports_sentence100_0101_0200_current\attribute_stage5_lexicon_export_summary_current_manual_resolved.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence100_0101_0200_current\raw_mentions_attribute_current.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_current\raw_edges_attribute_current.jsonl --lexicon-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_current_manual_resolved --canonical-mentions outputs\case_reports_sentence100_0101_0200_current\canonical_mentions_attribute_current_manual_resolved.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_current\canonical_edges_attribute_current_manual_resolved.jsonl --summary outputs\case_reports_sentence100_0101_0200_current\stage5_attribute_current_manual_resolved_summary.jsonl --attribute-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence100_0101_0200_current\canonical_mentions_attribute_current_manual_resolved.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_current\canonical_edges_attribute_current_manual_resolved.jsonl --output-dir outputs\case_reports_sentence100_0101_0200_current\stage6_attribute_current_manual_resolved --summary outputs\case_reports_sentence100_0101_0200_current\stage6_attribute_current_manual_resolved_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\build_caption_concept_md.py --sentence-rows outputs\case_reports_sentence100_0101_0200_current\sentence_rows_0101_0200.jsonl --stage3-records outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence100_0101_0200_current\canonical_mentions_attribute_current_manual_resolved.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_current\canonical_edges_attribute_current_manual_resolved.jsonl --facts outputs\case_reports_sentence100_0101_0200_current\stage6_attribute_current_manual_resolved\facts.jsonl --output outputs\case_reports_sentence100_0101_0200_current\caption_to_concept_cases_0101_0200_attribute_current_manual_resolved.md --start 0 --limit 100 --max-object-pairs-per-caption 40
.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_enrich_gpic_attribute_inventory_canonical.py
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_export_attribute_stage5_lexicons.py
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_formal_inventory_gates.py
```

Results:

- Manual resolution overlay:
  - full rows: 257
  - resolved rows: 96
  - merged status counts: `chosen=257`
  - merged selected synset rows: 231
  - merged empty canonical surface rows before enrichment: 257
  - manual canonical fields from the user-provided TSV are not copied into the
    full resolved inventory.
- Canonical enrichment:
  - rows: 257
  - canonical selected rows: 231
  - selected synset missing rows: 26
  - canonical ambiguous rows: 0
  - canonical lookup error rows: 0
- Stage 5 lexicon export:
  - attribute synonym rows: 231
  - attribute type rows: 0
- Stage 5 formal output:
  - `formal_attribute_inventory_gate=True`
  - canonical mentions: 2252
  - canonical edges: 1234
  - canonical source counts:
    `gpic_observed_inventory=1065`, `lexicon=587`, `raw_fallback=600`
- Stage 6:
  - fact total: 21843
  - `has_attribute`: 614
  - object-attribute pair rows: 547
  - object co-occurrence pair rows: 16324
- Markdown report:
  - `outputs/case_reports_sentence100_0101_0200_current/caption_to_concept_cases_0101_0200_attribute_current_manual_resolved.md`
- Tests:
  - `test_apply_attribute_manual_resolution.py`: 2 passed.
  - `test_enrich_gpic_attribute_inventory_canonical.py`: 6 passed.
  - `test_export_attribute_stage5_lexicons.py`: 1 passed.
  - `test_formal_inventory_gates.py`: 6 passed.
  - AST parse: `ast ok 5`.
- Additional verification after clearing manual canonical overlay:
  - `manual_surface_preserved` count is 0 in:
    - `gpic_observed_attribute_inventory_current_manual_resolved.tsv`
    - `gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv`
    - exported `attribute_synonyms.tsv`
  - selected synset rows missing canonical surface after enrichment: 0

Interpretation:

- The provided manual decisions are now applied to the full 100-caption
  attribute inventory.
- User-provided canonical fields are ignored at overlay time; canonical surfaces
  are recomputed by `enrich_gpic_attribute_inventory_canonical.py`.
- The formal Stage 5 gate now passes for this 100-caption run.
- Attribute type remains inactive in Stage 5/6 by current v1 rule.

## 2026-07-09: Action `needs_manual` Gate Correction

Decision under test:

- A selected R15 action span with `decision_status=needs_manual` must stop
  Stage 4.
- The previous 20-caption `attribute_action_current` outputs were produced
  before this correction and must not be treated as formal caption-to-concept
  output.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --raw-mentions outputs\case_reports_sentence20_current\raw_mentions_action_gate_probe.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges_action_gate_probe.jsonl --summary outputs\case_reports_sentence20_current\stage4_action_gate_probe_summary.jsonl
```

Results:

- `test_stage4_extract_raw.py`: 19 tests passed.
- The 20-caption Stage 4 probe stopped before writing raw output files.
- First blocker:
  - action surface: `marked`
  - lookup query: `mark`
  - `decision_status`: `needs_manual`
  - selection tag: `ambiguous_wn30_tie`

Interpretation:

- Stage 4 no longer lets unresolved action synset decisions pass into raw
  mentions, Stage 5, Stage 6, or Markdown reports.
- The failed probe did not create:
  - `raw_mentions_action_gate_probe.jsonl`
  - `raw_edges_action_gate_probe.jsonl`
  - `stage4_action_gate_probe_summary.jsonl`

## 2026-07-09: 20-Caption Action Inventory Manual File Generated

Decision under test:

- Build the offline action inventory file needed before formal Stage 4 can
  proceed with OEWN-backed action spans.
- Provide a `needs_manual` subset TSV for manual resolution.

Commands:

```powershell
.\scripts\run_python.ps1 -c "import ast, pathlib; path=pathlib.Path('scripts/build_gpic_observed_action_inventory.py'); ast.parse(path.read_text(encoding='utf-8'), filename=str(path)); print('ast ok')"
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\build_gpic_observed_action_inventory.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --output outputs\case_reports_sentence20_current\gpic_observed_action_inventory.tsv --needs-manual-output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_needs_manual.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_action_inventory_summary.json

## 2026-07-09: Sentence-20 Action Manual Resolution And Formal Stage 4 Gate

Scope:

- Applied the 8 user-provided action `needs_manual` decisions for the
  sentence-20 current sample.
- Added a resolved action inventory input to Stage 4.
- Regenerated Stage 4/5/6 and Markdown report under `action_manual_resolved`
  output names.

Manual decisions:

- `deepening -> oewn-00226992-v`
- `depicts -> oewn-01690851-v`
- `marked -> oewn-01591414-v`
- `shimmering -> oewn-02769408-v`
- `shining -> oewn-02771882-v`, selected query `shine`
- `sits in -> oewn-02619175-v`, known false positive note preserved
- `slopes -> oewn-02040935-v`, selected query `slope`
- `stands out -> oewn-02680375-v`

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_apply_action_manual_resolution.py"
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_stage4_extract_raw.py"
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_formal_inventory_gates.py"
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\apply_action_manual_resolution.py --full-inventory outputs\case_reports_sentence20_current\gpic_observed_action_inventory.tsv --manual-decisions outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_decisions.tsv --output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolved.tsv --resolved-output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolved_subset.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolution_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --action-inventory outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolved.tsv --raw-mentions outputs\case_reports_sentence20_current\raw_mentions_action_manual_resolved.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges_action_manual_resolved.jsonl --summary outputs\case_reports_sentence20_current\stage4_action_manual_resolved_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence20_current\raw_mentions_action_manual_resolved.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges_action_manual_resolved.jsonl --lexicon-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_current --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_manual_resolved.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_manual_resolved.jsonl --summary outputs\case_reports_sentence20_current\stage5_attribute_action_manual_resolved_summary.jsonl --attribute-inventory outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_typed.tsv
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_manual_resolved.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_manual_resolved.jsonl --output-dir outputs\case_reports_sentence20_current\stage6_attribute_action_manual_resolved --summary outputs\case_reports_sentence20_current\stage6_attribute_action_manual_resolved_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\build_caption_concept_md.py --sentence-rows outputs\benchmark_real10k_train\sentence_rows_9896.jsonl.gz --stage3-records outputs\case_reports_sentence20_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_manual_resolved.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_manual_resolved.jsonl --facts outputs\case_reports_sentence20_current\stage6_attribute_action_manual_resolved\facts.jsonl --output outputs\case_reports_sentence20_current\caption_to_concept_cases_0001_0020_attribute_action_manual_resolved.md --start 0 --limit 20 --max-object-pairs-per-caption 40
.\scripts\run_python.ps1 -m compileall scripts src tests
```

Results:

- `test_apply_action_manual_resolution.py`: 2 passed.
- `test_stage4_extract_raw.py`: 22 passed.
- `test_formal_inventory_gates.py`: 8 passed.
- compileall passed for changed scripts/src/tests.
- Manual overlay status counts:
  - before: `chosen=71`, `needs_manual=8`, `raw_fallback=3`
  - after: `chosen=79`, `raw_fallback=3`
- Stage 4 manual-resolved summary:
  - `raw_mention_total=516`
  - `raw_edge_total=307`
  - mention counts: `action=104`, `attribute=141`, `object=263`, `quantity=8`
- Stage 5 manual-resolved summary:
  - `canonical_mention_total=516`
  - `canonical_edge_total=307`
  - `formal_attribute_inventory_gate=True`
- Stage 6 manual-resolved summary:
  - `fact_total=4848`
  - `action_counts.tsv` rows: 82
- Sanity note:
  - Stage 4 action mentions carry the manual `selected_query` in `lemma` and
    source metadata.
  - Stage 5 action counts still use the existing R22 action synonym path. The
    action inventory canonical export is not implemented in this snapshot, so
    several action count labels remain surface forms such as `deepening` and
    `depicts`.

Generated artifacts:

- `outputs/case_reports_sentence20_current/gpic_observed_action_inventory_manual_decisions.tsv`
- `outputs/case_reports_sentence20_current/gpic_observed_action_inventory_manual_resolved.tsv`
- `outputs/case_reports_sentence20_current/gpic_observed_action_inventory_manual_resolved_subset.tsv`
- `outputs/case_reports_sentence20_current/raw_mentions_action_manual_resolved.jsonl`
- `outputs/case_reports_sentence20_current/raw_edges_action_manual_resolved.jsonl`
- `outputs/case_reports_sentence20_current/canonical_mentions_attribute_action_manual_resolved.jsonl`
- `outputs/case_reports_sentence20_current/canonical_edges_attribute_action_manual_resolved.jsonl`
- `outputs/case_reports_sentence20_current/stage6_attribute_action_manual_resolved/`
- `outputs/case_reports_sentence20_current/caption_to_concept_cases_0001_0020_attribute_action_manual_resolved.md`
```

Results:

- AST parse: `ast ok`.
- Full action inventory:
  - `outputs/case_reports_sentence20_current/gpic_observed_action_inventory.tsv`
  - rows: 82
- Manual subset:
  - `outputs/case_reports_sentence20_current/gpic_observed_action_inventory_needs_manual.tsv`
  - rows: 14
- Summary:
  - `outputs/case_reports_sentence20_current/gpic_observed_action_inventory_summary.json`
  - caption_total: 20
  - verb_token_total: 104
  - decision_status_counts: `chosen=68`, `needs_manual=14`
  - decision_reason_counts:
    - `selected_verb_synset=65`
    - `manual_action_synset_required=14`
    - `no_oewn_verb_synset=3`

Interpretation:

- The generated `needs_manual` TSV is the file to resolve before rerunning
  formal Stage 4 with action inventory support.
- `compileall` was not used as validation because it attempted to write
  `scripts/__pycache__` and hit a local `PermissionError`; the AST check avoids
  bytecode writes.

Correction:

- The first generated action inventory incorrectly wrote no-synset raw fallback
  action rows as `decision_status=chosen`.
- Correct status for no-synset action fallback is `decision_status=raw_fallback`
  because selected synset is absent.

## 2026-07-09: Action Verb Exact Surface Filter And Morphy Ambiguity

Decision under test:

- R15 action lookup must not treat OEWN's internal morphology as an exact
  surface hit.
- R15 action lookup must not auto-select the first Morphy candidate when
  multiple Morphy queries produce OEWN verb hits.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage4_extract_raw.py
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\build_gpic_observed_action_inventory.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --output outputs\case_reports_sentence20_current\gpic_observed_action_inventory.tsv --needs-manual-output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_needs_manual.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_action_inventory_summary.json
```

Results:

- `test_stage4_extract_raw.py`: 21 tests passed in 2.414 seconds.
- Regenerated 20-caption action inventory summary:
  - inventory_rows: 82
  - verb_token_total: 104
  - decision_status_counts: `chosen=71`, `needs_manual=8`,
    `raw_fallback=3`
  - decision_reason_counts:
    - `selected_verb_synset=71`
    - `manual_action_synset_required=6`
    - `manual_action_morphy_required=2`
    - `no_oewn_verb_synset=3`

Representative row check:

- `lit -> light`, `lying -> lie`, `made -> make`, `sitting -> sit`,
  `splitting -> split`, and `worn -> wear` now use
  `selected_lookup_case=verb_head_morphy`.
- `shining -> shin|shine` and `slopes -> slop|slope` now use
  `selected_lookup_case=verb_head_morphy_ambiguous` and
  `decision_status=needs_manual`.

Interpretation:

- Inflected action surfaces no longer become artificial raw-surface
  `needs_manual` rows when OEWN internally returns base-lemma verb synsets.
- Multiple Morphy verb-hit queries are now explicit manual decisions rather
  than hidden automatic choices.

## 2026-07-10: Sentence-20 Action Canonical Inventory Build

Decision under test:

- After action synset `needs_manual` rows are resolved, the next offline step is
  action canonical inventory build, not runtime Stage 4/5 execution.
- Canonical inventory must stop if canonical selection creates new manual
  blockers.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_enrich_gpic_action_inventory_canonical.py"
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\enrich_gpic_action_inventory_canonical.py --input outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolved.tsv --output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_canonical.tsv --ambiguous-output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_canonical_ambiguous.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_action_inventory_canonical_summary.json
.\scripts\run_python.ps1 -c "import ast, pathlib; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8')) for p in ['scripts/enrich_gpic_action_inventory_canonical.py','tests/test_enrich_gpic_action_inventory_canonical.py']]; print('ast ok')"
```

Results:

- `test_enrich_gpic_action_inventory_canonical.py`: 3 tests passed.
- AST parse: `ast ok`.
- Canonical summary:
  - rows: 82
  - canonical_selected_rows: 79
  - raw_fallback_not_applicable_rows: 3
  - canonical_ambiguous_rows: 0
  - canonical_lookup_error_rows: 0

Representative row check:

- `deepening -> deepen`
- `depicts -> depict`
- `marked -> mark`
- `shimmering -> shimmer`
- `shining -> shine`
- `sits in -> sit in`
- `slopes -> slope`
- `stands out -> stand out`

Generated artifacts:

- `outputs/case_reports_sentence20_current/gpic_observed_action_inventory_canonical.tsv`
- `outputs/case_reports_sentence20_current/gpic_observed_action_inventory_canonical_ambiguous.tsv`
- `outputs/case_reports_sentence20_current/gpic_observed_action_inventory_canonical_summary.json`

Interpretation:

- Sentence-20 action canonical inventory has no remaining canonical manual
  blockers.
- The 3 raw fallback rows have no selected synset, so canonical selection is
  intentionally not applicable for those rows.

## 2026-07-10: Sentence-20 Stage 4/5/6 Rerun With Action Canonical Export

Decision under test:

- Completed action canonical inventory should feed Stage 5 R22 through
  `action_synonyms.tsv`.
- Stage 4 extraction graph should remain the same shape, while Stage 5/6 action
  labels should use canonical action surfaces where available.

Commands:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_export_attribute_stage5_lexicons.py"
.\scripts\run_python.ps1 -c "import ast, pathlib; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8')) for p in ['scripts/export_attribute_stage5_lexicons.py','tests/test_export_attribute_stage5_lexicons.py']]; print('ast ok')"
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\export_attribute_stage5_lexicons.py --attribute-inventory outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_typed.tsv --action-canonical-inventory outputs\case_reports_sentence20_current\gpic_observed_action_inventory_canonical.tsv --output-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_action_canonical --summary outputs\case_reports_sentence20_current\attribute_action_stage5_lexicon_export_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --action-inventory outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolved.tsv --raw-mentions outputs\case_reports_sentence20_current\raw_mentions_attribute_action_canonical.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges_attribute_action_canonical.jsonl --summary outputs\case_reports_sentence20_current\stage4_attribute_action_canonical_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence20_current\raw_mentions_attribute_action_canonical.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges_attribute_action_canonical.jsonl --lexicon-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_action_canonical --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_canonical.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_canonical.jsonl --summary outputs\case_reports_sentence20_current\stage5_attribute_action_canonical_summary.jsonl --attribute-inventory outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_typed.tsv
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_canonical.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_canonical.jsonl --output-dir outputs\case_reports_sentence20_current\stage6_attribute_action_canonical --summary outputs\case_reports_sentence20_current\stage6_attribute_action_canonical_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\build_caption_concept_md.py --sentence-rows outputs\benchmark_real10k_train\sentence_rows_9896.jsonl.gz --stage3-records outputs\case_reports_sentence20_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_canonical.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_canonical.jsonl --facts outputs\case_reports_sentence20_current\stage6_attribute_action_canonical\facts.jsonl --output outputs\case_reports_sentence20_current\caption_to_concept_cases_0001_0020_attribute_action_canonical.md --start 0 --limit 20 --max-object-pairs-per-caption 40
```

Results:

- `test_export_attribute_stage5_lexicons.py`: 2 tests passed.
- AST parse: `ast ok`.
- Stage 5 lexicon export:
  - action_inventory_rows: 82
  - action_synonym_rows_added: 79
  - action_raw_fallback_rows_skipped: 3
  - attribute synonym rows added: 97
- Stage 4:
  - raw mentions: 516
  - raw edges: 307
  - mention counts: `action=104`, `attribute=141`, `object=263`,
    `quantity=8`
- Stage 5:
  - canonical mentions: 516
  - canonical edges: 307
  - canonical source counts:
    `gpic_observed_inventory=251`, `lexicon=232`, `raw_fallback=33`
- Stage 6:
  - facts: 4848
  - action events: 104
  - action count rows: 71

Representative action count check:

- `deepening -> deepen`
- `depicts -> depict`
- `shimmering -> shimmer`
- `shining -> shine`
- `sits in -> sit in`
- `slopes -> slope`
- `stands out -> stand out`

Generated artifacts:

- `outputs/case_reports_sentence20_current/stage5_lexicons_attribute_action_canonical/`
- `outputs/case_reports_sentence20_current/raw_mentions_attribute_action_canonical.jsonl`
- `outputs/case_reports_sentence20_current/raw_edges_attribute_action_canonical.jsonl`
- `outputs/case_reports_sentence20_current/canonical_mentions_attribute_action_canonical.jsonl`
- `outputs/case_reports_sentence20_current/canonical_edges_attribute_action_canonical.jsonl`
- `outputs/case_reports_sentence20_current/stage6_attribute_action_canonical/`
- `outputs/case_reports_sentence20_current/caption_to_concept_cases_0001_0020_attribute_action_canonical.md`

Interpretation:

- The action canonical inventory is now connected to active R22 via generated
  `action_synonyms.tsv`.
- Stage 6 action counts now use canonical action keys where action canonical
  evidence exists.
- Raw fallback actions remain raw-surface counted.

## 2026-07-10: R15 Fronted Preposition Rejection Regression

Decision under test:

- R15 should not build a phrasal action candidate from a preposition token that
  appears before the VERB head.
- Following prepositions such as `look at` should still be valid phrasal action
  candidates.

Command:

```powershell
.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage4_extract_raw.py
```

Result:

- 23 tests passed in 1.652 seconds.

Interpretation:

- The new fronted-PP regression test confirms `In ... frame` does not become
  `frame in` even when `frame in` exists in action lookup.
- The existing `look at` regression test confirms following-preposition phrasal
  action behavior still works.

## 2026-07-10: Sentence-20 Rerun After R15 Fronted Preposition Filter

Decision under test:

- The generated 20-caption action inventory and report should reflect the R15
  `prep.i > verb.i` constraint.
- Previously observed `frame In` and `frames In` action spans should become
  single-verb `frame` and `frames` actions.

Commands:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\build_gpic_observed_action_inventory.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --output outputs\case_reports_sentence20_current\gpic_observed_action_inventory.tsv --needs-manual-output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_needs_manual.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_action_inventory_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\apply_action_manual_resolution.py --full-inventory outputs\case_reports_sentence20_current\gpic_observed_action_inventory.tsv --manual-decisions outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_decisions.tsv --output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolved.tsv --resolved-output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolved_subset.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolution_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\enrich_gpic_action_inventory_canonical.py --input outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolved.tsv --output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_canonical.tsv --ambiguous-output outputs\case_reports_sentence20_current\gpic_observed_action_inventory_canonical_ambiguous.tsv --summary outputs\case_reports_sentence20_current\gpic_observed_action_inventory_canonical_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\export_attribute_stage5_lexicons.py --attribute-inventory outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_typed.tsv --action-canonical-inventory outputs\case_reports_sentence20_current\gpic_observed_action_inventory_canonical.tsv --output-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_action_canonical --summary outputs\case_reports_sentence20_current\attribute_action_stage5_lexicon_export_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence20_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence20_current\gpic_observed_object_inventory_redecided_from_manual_review.tsv --action-inventory outputs\case_reports_sentence20_current\gpic_observed_action_inventory_manual_resolved.tsv --raw-mentions outputs\case_reports_sentence20_current\raw_mentions_attribute_action_canonical.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges_attribute_action_canonical.jsonl --summary outputs\case_reports_sentence20_current\stage4_attribute_action_canonical_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence20_current\raw_mentions_attribute_action_canonical.jsonl --raw-edges outputs\case_reports_sentence20_current\raw_edges_attribute_action_canonical.jsonl --lexicon-dir outputs\case_reports_sentence20_current\stage5_lexicons_attribute_action_canonical --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_canonical.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_canonical.jsonl --summary outputs\case_reports_sentence20_current\stage5_attribute_action_canonical_summary.jsonl --attribute-inventory outputs\case_reports_sentence20_current\gpic_observed_attribute_inventory_typed.tsv
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_canonical.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_canonical.jsonl --output-dir outputs\case_reports_sentence20_current\stage6_attribute_action_canonical --summary outputs\case_reports_sentence20_current\stage6_attribute_action_canonical_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\build_caption_concept_md.py --sentence-rows outputs\benchmark_real10k_train\sentence_rows_9896.jsonl.gz --stage3-records outputs\case_reports_sentence20_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence20_current\canonical_mentions_attribute_action_canonical.jsonl --canonical-edges outputs\case_reports_sentence20_current\canonical_edges_attribute_action_canonical.jsonl --facts outputs\case_reports_sentence20_current\stage6_attribute_action_canonical\facts.jsonl --output outputs\case_reports_sentence20_current\caption_to_concept_cases_0001_0020_attribute_action_canonical.md --start 0 --limit 20 --max-object-pairs-per-caption 40
```

Results:

- Action inventory:
  - rows: 82
  - chosen: 71
  - needs_manual: 8
  - raw_fallback: 3
  - candidate types: `verb=71`, `verb_prep=10`, `verb_prt=1`
- Manual action resolution:
  - overlaid rows: 8
  - merged status: `chosen=79`, `raw_fallback=3`
- Action canonical inventory:
  - selected rows: 79
  - ambiguous rows: 0
  - raw fallback not applicable rows: 3
- Stage 4:
  - raw mentions: 516
  - raw edges: 305
  - edge types: `event_role=106`, `has_attribute=141`, `has_quantity=8`,
    `relation=50`
- Stage 6:
  - facts: 4846
  - action events: 104
  - action count rows: 71
  - agent/patient pair rows: 103
  - relation triple rows: 50

Verification:

- `frame In` and `frames In` no longer appear in the regenerated raw mentions,
  canonical mentions, action count table, or Markdown report.
- `action:frame in` and `action:frames in` no longer appear in regenerated
  Stage 6 action counts.
- The regenerated report shows `frames` and `frame` as single-verb actions for
  the two former fronted-PP cases.

## 2026-07-10: Sentence-100 Action Inventory Gate Probe

Decision under test:

- Expand the latest R15 action inventory flow from 20 sentence captions to the
  existing 100-caption `0101_0200` sample.
- Stop before Stage 4 if unresolved action `needs_manual` rows remain.

Command:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 scripts\build_gpic_observed_action_inventory.py --input outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory.tsv --needs-manual-output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_needs_manual.tsv --summary outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_summary.json
```

Result:

- caption_total: 100
- verb_token_total: 432
- inventory_rows: 221
- decision_status_counts:
  - chosen: 206
  - needs_manual: 13
  - raw_fallback: 2
- candidate_type_counts:
  - verb: 189
  - verb_prep: 30
  - verb_prt: 2
- decision_reason_counts:
  - selected_verb_synset: 206
  - manual_action_synset_required: 12
  - manual_action_morphy_required: 1
  - no_oewn_verb_synset: 2

Generated artifacts:

- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_action_inventory.tsv`
- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_action_inventory_needs_manual.tsv`
- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_action_inventory_summary.json`

Interpretation:

- Formal Stage 4/5/6 100-caption regeneration did not proceed because 13
  action rows still require manual action synset resolution.
- The next step is to resolve
  `gpic_observed_action_inventory_needs_manual.tsv`, then rerun action manual
  resolution, action canonical enrichment, Stage 5 lexicon export, and Stage
  4/5/6.

## 2026-07-10: Sentence-100 Full Rerun With Action Manual Decisions

Decision under test:

- Apply the 13 user-provided action synset manual decisions for the 100-caption
  `0101_0200` sample.
- Continue through action canonical enrichment, Stage 5 lexicon export, Stage
  4/5/6, and Markdown report generation after the action gate is clear.

Manual decisions:

```text
sit in -> oewn-02619175-v
sits in -> oewn-02619175-v
holding in -> oewn-02716988-v
hold in -> oewn-02716988-v
holds in -> oewn-02716988-v
combed -> oewn-00038078-v
drawn in -> oewn-01509215-v
marked -> oewn-01591414-v
marking -> oewn-01591414-v
neighboring -> oewn-02614211-v
silhouetting -> oewn-01684516-v
stands out -> oewn-02680375-v
striped -> oewn-01275827-v
```

Commands:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\apply_action_manual_resolution.py --full-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory.tsv --manual-decisions outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_decisions.tsv --output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_resolved.tsv --resolved-output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_resolved_subset.tsv --summary outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_resolution_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\enrich_gpic_action_inventory_canonical.py --input outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_resolved.tsv --output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_canonical.tsv --ambiguous-output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_canonical_ambiguous.tsv --summary outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_canonical_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\export_attribute_stage5_lexicons.py --attribute-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv --action-canonical-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_canonical.tsv --output-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_action_canonical --summary outputs\case_reports_sentence100_0101_0200_current\attribute_action_stage5_lexicon_export_summary.json
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --action-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_resolved.tsv --raw-mentions outputs\case_reports_sentence100_0101_0200_current\raw_mentions_attribute_action_canonical.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_current\raw_edges_attribute_action_canonical.jsonl --summary outputs\case_reports_sentence100_0101_0200_current\stage4_attribute_action_canonical_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence100_0101_0200_current\raw_mentions_attribute_action_canonical.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_current\raw_edges_attribute_action_canonical.jsonl --lexicon-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_action_canonical --canonical-mentions outputs\case_reports_sentence100_0101_0200_current\canonical_mentions_attribute_action_canonical.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_current\canonical_edges_attribute_action_canonical.jsonl --summary outputs\case_reports_sentence100_0101_0200_current\stage5_attribute_action_canonical_summary.jsonl --attribute-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence100_0101_0200_current\canonical_mentions_attribute_action_canonical.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_current\canonical_edges_attribute_action_canonical.jsonl --output-dir outputs\case_reports_sentence100_0101_0200_current\stage6_attribute_action_canonical --summary outputs\case_reports_sentence100_0101_0200_current\stage6_attribute_action_canonical_summary.jsonl
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\build_caption_concept_md.py --sentence-rows outputs\case_reports_sentence100_0101_0200_current\sentence_rows_0101_0200.jsonl --stage3-records outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence100_0101_0200_current\canonical_mentions_attribute_action_canonical.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_current\canonical_edges_attribute_action_canonical.jsonl --facts outputs\case_reports_sentence100_0101_0200_current\stage6_attribute_action_canonical\facts.jsonl --output outputs\case_reports_sentence100_0101_0200_current\caption_to_concept_cases_0101_0200_attribute_action_canonical.md --start 0 --limit 100 --max-object-pairs-per-caption 40
```

Results:

- Action manual resolution:
  - overlaid rows: 13
  - merged status: `chosen=219`, `raw_fallback=2`
- Action canonical inventory:
  - rows: 221
  - canonical selected rows: 219
  - canonical ambiguous rows: 0
  - raw fallback not applicable rows: 2
- Stage 4:
  - raw mentions: 2252
  - raw edges: 1278
  - mention counts: `action=432`, `attribute=614`, `object=1169`,
    `quantity=37`
  - edge counts: `event_role=430`, `has_attribute=614`, `has_quantity=37`,
    `relation=197`
- Stage 5:
  - canonical mentions: 2252
  - canonical edges: 1278
  - canonical source counts:
    `gpic_observed_inventory=1065`, `lexicon=1016`, `raw_fallback=171`
- Stage 6:
  - facts: 21887
  - action events: 432
  - entity exists: 1169
  - event roles: 430
  - relations: 197
  - object pair facts: 19008
  - table row counts:
    - action_counts.tsv: 163
    - agent_patient_pair_counts.tsv: 377
    - attribute_counts.tsv: 255
    - object_attribute_pair_counts.tsv: 547
    - object_cooccurrence_pair_counts.tsv: 16324
    - object_counts.tsv: 504
    - relation_triple_counts.tsv: 182

Validation:

- `gpic_observed_action_inventory_manual_resolved.tsv` has no remaining
  `needs_manual` rows.
- Action canonical summary has `canonical_ambiguous_rows=0`.
- The regenerated action counts and Markdown report do not contain
  `action:frame in`, `frame In`, or `frames In`.

## 2026-07-10: Wiktionary Preposition MWE Candidate Probe

Decision under test:

- Build an offline preposition-form MWE candidate inventory from
  Wiktionary/Wiktextract evidence.
- Keep only English entries with `pos == "prep"` and at least two
  whitespace-delimited surface tokens.
- Do not promote the result into active Stage 4 relation MWE behavior.

Command:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\build_wiktionary_prep_mwe_candidates.py --output-dir outputs\wiktionary_prep_mwe_candidates
```

Result:

- JSONL entries read: 870
- English prep entries: 870
- single-token prep entries excluded: 592
- MWE prep entries: 278
- MWE prep senses: 389
- unique MWE surfaces: 278
- generated candidate rows have `min_token_count=2`, `max_token_count=5`,
  and no single-token rows.

Generated artifacts:

- `outputs/wiktionary_prep_mwe_candidates/wiktionary_prep_mwe_candidates.tsv`
- `outputs/wiktionary_prep_mwe_candidates/wiktionary_prep_mwe_senses.tsv`
- `outputs/wiktionary_prep_mwe_candidates/wiktionary_prep_mwe_summary.json`

Interpretation:

- The probe produced a source candidate inventory only.
- Active relation extraction remains raw-preserving under R18/R24.

## 2026-07-10: External Preposition Source Candidate Probe

Decision under test:

- Pull and summarize TPP/PDEP/STREUSLE/PASTRIE-related preposition sources for
  manual relation-MWE candidate review.
- Keep the result as offline evidence only.
- Do not promote candidates into active Stage 4 relation MWE behavior.

Source pulls completed:

- `git clone --depth 1 https://github.com/nert-nlp/streusle.git outputs\external_preposition_sources\streusle`
- `git clone --depth 1 https://github.com/nert-nlp/pastrie.git outputs\external_preposition_sources\pastrie`
- `git clone --depth 1 https://github.com/kenclr/ca4pdep.git outputs\external_preposition_sources\pdep_ca4pdep`

Generation command:

```powershell
.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\build_external_preposition_source_candidates.py
```

Result:

- combined MWE candidate rows: 1,014
- STREUSLE preposition-related MWE occurrences: 1,177
- STREUSLE unique candidates: 653
- STREUSLE `lexcat == P` occurrences: 137
- STREUSLE `lexcat == P` unique candidates: 50
- PASTRIE MWE occurrences: 329
- PASTRIE unique candidates: 210
- PASTRIE `lexcat == P` occurrences: 74
- PASTRIE `lexcat == P` unique candidates: 34
- combined STREUSLE/PASTRIE `lexcat == P` source rows: 84
- combined STREUSLE/PASTRIE `lexcat == P` unique surface keys: 62
- clean STREUSLE/PASTRIE `lexcat == P` source rows: 82
- clean STREUSLE/PASTRIE `lexcat == P` unique surface keys: 60
- excluded STREUSLE/PASTRIE `lexcat == P` artifact rows: 2
- clean STREUSLE/PASTRIE `lexcat == P` rows flagged for unknown SNACS supersense: 3
- PDEP preposition entries: 304
- PDEP multiword preposition entries: 166
- PDEP sense rows: 1,039
- TPP feature-summary prepositions: 44
- TPP feature-summary multiword prepositions: 0
- TPP appendix prepositions: 373
- TPP appendix multiword prepositions: 222
- TPP appendix final curation KEEP rows: 199
- TPP appendix final curation DROP rows: 23
- TPP appendix final curation REVIEW rows: 0
- TPP appendix final curation extraction corrections: 5
- combined preposition MWE source rows: 699
- combined preposition MWE unique entries: 365
- combined non-preposition-MWE source rows: 190
- combined non-preposition-MWE unique entries: 179
- combined source disagreement entries after manual drop: 0

Generated artifacts:

- `outputs/external_preposition_sources/candidate_tables/external_preposition_mwe_candidates_combined.tsv`
- `outputs/external_preposition_sources/candidate_tables/streusle_preposition_mwe_candidates.tsv`
- `outputs/external_preposition_sources/candidate_tables/streusle_preposition_mwe_occurrences.tsv`
- `outputs/external_preposition_sources/candidate_tables/pastrie_preposition_mwe_candidates.tsv`
- `outputs/external_preposition_sources/candidate_tables/pastrie_preposition_mwe_occurrences.tsv`
- `outputs/external_preposition_sources/candidate_tables/streusle_p_lexcat_preposition_mwe_candidates.tsv`
- `outputs/external_preposition_sources/candidate_tables/streusle_p_lexcat_preposition_mwe_occurrences.tsv`
- `outputs/external_preposition_sources/candidate_tables/pastrie_p_lexcat_preposition_mwe_candidates.tsv`
- `outputs/external_preposition_sources/candidate_tables/pastrie_p_lexcat_preposition_mwe_occurrences.tsv`
- `outputs/external_preposition_sources/candidate_tables/streusle_pastrie_p_lexcat_preposition_mwe_candidates.tsv`
- `outputs/external_preposition_sources/candidate_tables/streusle_pastrie_p_lexcat_preposition_mwe_candidates_clean.tsv`
- `outputs/external_preposition_sources/candidate_tables/streusle_pastrie_p_lexcat_preposition_mwe_candidates_excluded.tsv`
- `outputs/external_preposition_sources/candidate_tables/pdep_preposition_inventory.tsv`
- `outputs/external_preposition_sources/candidate_tables/pdep_sense_substitutes.tsv`
- `outputs/external_preposition_sources/candidate_tables/tpp_feature_preposition_summary.tsv`
- `outputs/external_preposition_sources/candidate_tables/tpp_litkowski_2002_appendix_preposition_inventory.tsv`
- `outputs/external_preposition_sources/candidate_tables/tpp_litkowski_2002_appendix_preposition_mwe_inventory.tsv`
- `outputs/external_preposition_sources/candidate_tables/tpp_litkowski_2002_appendix_preposition_mwe_manual_reaudit.tsv`
- `outputs/external_preposition_sources/candidate_tables/tpp_litkowski_2002_appendix_preposition_mwe_inventory_clean.tsv`
- `outputs/external_preposition_sources/candidate_tables/tpp_litkowski_2002_appendix_preposition_mwe_inventory_excluded.tsv`
- `outputs/external_preposition_sources/candidate_tables/combined_preposition_mwe_inventory.tsv`
- `outputs/external_preposition_sources/candidate_tables/combined_preposition_mwe_source_rows.tsv`
- `outputs/external_preposition_sources/candidate_tables/combined_non_preposition_mwe_inventory.tsv`
- `outputs/external_preposition_sources/candidate_tables/combined_non_preposition_mwe_source_rows.tsv`
- `outputs/external_preposition_sources/candidate_tables/combined_preposition_mwe_conflicts.tsv`
- `outputs/external_preposition_sources/candidate_tables/external_preposition_source_manifest.tsv`
- `outputs/external_preposition_sources/candidate_tables/external_preposition_source_summary.json`

Validation:

- The builder was run through `scripts\run_script_with_timeout.py`.
- Final generation completed within the 120 second script timeout.
- `compileall` succeeded for
  `scripts\build_external_preposition_source_candidates.py` when run outside
  the sandbox. A sandboxed `compileall` attempt failed only because it could not
  write `scripts\__pycache__` under the project path.

Interpretation:

- STREUSLE, PASTRIE, and PDEP-derived candidate tables are available for manual
  review.
- The original combined STREUSLE/PASTRIE rows are intentionally broad because
  they include `contains_adp_token` and `p_supersense` evidence. A stricter
  follow-up subset keeps only occurrence rows whose holistic MWE lexical
  category is exactly `P`.
- The stricter STREUSLE/PASTRIE `lexcat == P` subset has 84 source rows and 62
  unique surface keys. It excludes `contains_adp_token`-only rows, `PP`
  idiomatic prepositional phrases, and verbal MWE rows.
- A follow-up review-clean step removes two single-token lexical artifacts:
  `into` with surface `In To`, and `within` with surface `win in`. The clean
  STREUSLE/PASTRIE `lexcat == P` inventory therefore has 82 source rows and 60
  unique surface keys.
- The clean STREUSLE/PASTRIE source table still records three `lexcat == P`
  candidates with unknown SNACS supersense evidence: `at hand`, `in my hand`
  (surface `in my hands`), and `in this day`. In the combined prep-MWE
  inventory these are manually dropped because they are idiomatic or ordinary
  PP expressions, not preposition MWEs that take an NP complement.
- The PDEP inventory extraction did perform the intended inventory filter:
  `prepcnts.csv` has 304 preposition entries, and 166 of them have at least two
  whitespace-delimited tokens.
- The TPP artifact in this probe is limited to the `ca4pdep` TPP feature
  summary, where the 44 retrieved preposition labels are all single-token
  labels. This is not the full original TPP database or the original TPP
  phrasal-preposition inventory.
- A follow-up check found the original NODE/TPP appendix inventory in Litkowski
  (2002), Table A-2. It was extracted from the ACL Anthology PDF with
  coordinate-based column reconstruction. The extracted table has exactly 373
  entries, including 222 multiword entries.
- Therefore `TPP feature-summary multiword prepositions: 0` must not be read as
  evidence that original TPP has no phrasal prepositions.
- The archived `clres.com/prepositions.html` TPP page confirms that Online TPP
  once linked `tppdata.zip` for downloading the full database. The exact zip was
  not retrievable from Wayback CDX during this probe, so the appendix PDF table
  is the retrieved original inventory source.
- The current historical `clres.com/prepositions.html` host was checked on
  2026-07-10 and redirects to unrelated casino content, so the live host was not
  used as a data source.
- The TPP appendix multiword candidates were then curated from
  `C:\Users\rlath\Downloads\tpp_preposition_mwe_final (1).xlsx`. The final
  clean inventory keeps 199 rows, excludes 23 rows, and leaves 0 rows in review.
- The final curation records five extraction corrections: `#à la -> à la`,
  `head and shoulders -> head and shoulders above`, `above inside -> inside`,
  `not someone's idea -> not someone's idea of`, and
  `of this side of -> this side of`.
- The curated source rows were combined with PDEP multiword preposition rows,
  Wiktionary English `pos=prep` MWE rows, and clean STREUSLE/PASTRIE
  `lexcat == P` rows. After the user-approved conflict drop decision and the
  Wiktionary misspelling/manual-drop filters, the combined preposition MWE
  inventory has 699 source rows and 365 deduplicated entries.
- The combined non-preposition-MWE inventory has 190 source rows and 179
  deduplicated entries. It includes TPP final DROP rows, STREUSLE/PASTRIE
  artifact exclusions, PDEP single-token prepositions, Wiktionary
  `misspelling` rows, manual-drop rows, and source rows moved by the
  `manual_conflict_drop` decision.
- STREUSLE/PASTRIE `lexcat == P` rows now use observed corpus surfaces as
  matcher/lookup `entry` and `lookup_forms`; their corpus MWE `lexlemma` is
  retained separately as `canonical_lemma` evidence. This prevents source
  lemmas such as `accord to`, `in term of`, `see as`, and `when it come to`
  from becoming direct lookup entries when only inflected observed surfaces
  were attested.
- Wiktionary `misspelling` rows such as `as oppose to`, `incase of`, and
  `infront of` are retained only in the non-preposition-MWE inventory.
- `a matter of`, `as if`, `at hand`, `for example`, `from the ground up`,
  `in my hands`, `in this day`, `seeing as`, and `the dickens` are manually
  dropped from the prep-MWE inventory.
- `d t`, `out ta`, and `rather then` are not standalone prep-MWE entries; they
  are retained only as surface-variant evidence under `due to`, `out of`, and
  `rather than`.
- The originally reviewed eight source-disagreement entries were all dropped
  from the prep-MWE inventory by explicit user decision: `a cut above`,
  `bare of`, `in memoriam`, `little short of`, `nothing short of`,
  `preparatory to`, `short for`, and `shot through with`. The same
  conflict-drop rule is applied to the current combined source audit, and the
  regenerated `combined_preposition_mwe_conflicts.tsv` has 0 unresolved rows.
- Active relation extraction remains raw-preserving under R18/R24.

Generated artifacts:

- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_action_inventory_manual_decisions.tsv`
- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_action_inventory_manual_resolved.tsv`
- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_action_inventory_canonical.tsv`
- `outputs/case_reports_sentence100_0101_0200_current/stage5_lexicons_attribute_action_canonical/`
- `outputs/case_reports_sentence100_0101_0200_current/raw_mentions_attribute_action_canonical.jsonl`
- `outputs/case_reports_sentence100_0101_0200_current/raw_edges_attribute_action_canonical.jsonl`
- `outputs/case_reports_sentence100_0101_0200_current/canonical_mentions_attribute_action_canonical.jsonl`
- `outputs/case_reports_sentence100_0101_0200_current/canonical_edges_attribute_action_canonical.jsonl`
- `outputs/case_reports_sentence100_0101_0200_current/stage6_attribute_action_canonical/`
- `outputs/case_reports_sentence100_0101_0200_current/caption_to_concept_cases_0101_0200_attribute_action_canonical.md`

## 2026-07-11: Active preposition MWE relation implementation tests

Change under test:

- Stage 4 now loads active `resources/lexicons/preposition_mwes.tsv` rows.
- Stage 4 detects contiguous preposition MWE spans, marks their tokens as
  `relation_mwe_consumed`, creates R18.1 relation edges only when source/target
  dependency evidence is present, and suppresses consumed single-ADP relation
  extraction.
- Stage 5 preserves relation MWE edge metadata.
- Stage 6 emits `attribute_exists`, `quantity_exists`, `object_parent`, and
  `relation_component` facts/counts.

Generated artifact:

- `resources/lexicons/preposition_mwes.tsv`

Validation commands:

- `.\scripts\run_python.ps1 -c "import ast, pathlib; ..."`
  - Result: `ast parse ok 9`
  - Interpretation: syntax parse passed without writing `__pycache__`.
- `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_stage4_extract_raw.py`
  - Result: 25 tests passed in 2.247 seconds.
- `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_stage5_canonicalize.py`
  - Result: 5 tests passed in 0.096 seconds.
- `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_stage6_export_counts.py`
  - Result: 2 tests passed in 0.099 seconds.
- `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_schema.py`
  - Result: 6 tests passed.

Permission note:

- A direct `compileall src scripts tests` attempt failed because Python tried to
  write `__pycache__` files under the project path. This was not a syntax
  failure.
- `scripts\run_tests.ps1` now sets `PYTHONDONTWRITEBYTECODE=1` so bounded test
  runs do not depend on project `__pycache__` write permission.

## 2026-07-11: Preposition MWE matcher index optimization

Change under test:

- Stage 4 preposition MWE span matching was changed from scanning every lexicon
  entry against every caption position to building a token-sequence index once
  and matching caption n-grams by dictionary lookup.
- The semantic rule is unchanged: exact contiguous token span matching,
  longest overlapping span first, and earlier span as the tie-breaker.

Validation commands:

- `.\scripts\run_python.ps1 -c "import ast, pathlib; ..."`
  - Result: `ast ok`
  - Interpretation: `stage4_extract_raw.py` and `test_stage4_extract_raw.py`
    parsed successfully without writing bytecode.
- `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_stage4_extract_raw.py`
  - Result: 26 tests passed in 1.484 seconds.
  - Interpretation: Stage 4 raw extraction behavior, including the indexed
    preposition MWE lookup path and longest-overlap policy, still passes the
    focused regression suite.

Invocation note:

- `.\scripts\run_tests.ps1 --timeout-seconds 60 tests.test_stage4_extract_raw`
  failed because `tests` is not a Python package. The corrected bounded unittest
  invocation is the `discover -s tests -p test_stage4_extract_raw.py` command
  above.

## 2026-07-11: Action-Attached Preposition MWE Relation Candidate Preservation

Change under test:

- R18.1 now creates a normal relation edge when a preposition MWE is attached to
  a VERB head and that VERB has exactly one direct object-mapped source
  child candidate.
- If multiple direct object-mapped source candidates exist, R18.1 creates
  `ambiguous_relation_candidate` edges instead of normal relation triples.
- Stage 6 exports those candidate edges to
  `ambiguous_relation_candidate_counts.tsv`.

Validation commands:

- `.\scripts\run_python.ps1 -c "import ast, pathlib; ..."`
  - First attempt using `encoding='utf-8'` failed on an existing BOM in one file
    because `ast.parse()` received the BOM character directly.
  - Re-run with `encoding='utf-8-sig'` succeeded: `ast ok`.
- `.\scripts\run_python.ps1 -m compileall -q src\gpic_concepts_v1 scripts tests`
  - Failed with `PermissionError` while writing `__pycache__`; this was not a
    syntax failure.
- `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p "test_stage4_extract_raw.py"`
  - Result: 28 tests passed.
- `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p "test_stage5_canonicalize.py"`
  - Result: 6 tests passed.
- `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p "test_stage6_export_counts.py"`
  - Result: 2 tests passed.
- `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p "test_schema.py"`
  - Result: 6 tests passed.

20-caption rerun:

- Output directory:
  `outputs/case_reports_sentence20_preposition_mwe_current`
- Stage 4:
  - raw mentions: 515
  - raw edges: 304
  - edge type counts: `event_role=105`, `has_attribute=141`,
    `has_quantity=8`, `relation=50`
  - R18.1 hit count: 1
  - R18.1 hit:
    `woman --in front of--> screen`, source resolution
    `head_direct_object_child`, source dep `nsubj`
- Stage 6:
  - fact total: 5254
  - relation facts: 50
  - relation component facts: 3
  - ambiguous relation candidate table rows: 0
  - new output table exists:
    `stage6/ambiguous_relation_candidate_counts.tsv`
- Markdown report regenerated:
  `outputs/case_reports_sentence20_preposition_mwe_current/caption_to_concept_cases_0001_0020_preposition_mwe_current.md`

Interpretation:

- The original first-caption case now produces the intended normal relation
  row: `relation:woman:in front of:screen`.
- No ambiguous relation candidate case happened in this 20-caption sample, but
  unit tests cover the multiple-source candidate path.

## 2026-07-11: 100-Caption Preposition MWE Relation Rerun

Purpose:

- Re-run the 0101-0200 100-caption sample after R18.1 action-attached
  preposition MWE handling, including `ambiguous_relation_candidate` export.

Output directory:

- `outputs/case_reports_sentence100_0101_0200_preposition_mwe_current`

Validation command group:

- Stage 4 raw extraction, Stage 5 canonicalization, Stage 6 count export, and
  Markdown report generation were run with bounded 240-second wrappers.

Results:

- Stage 4:
  - raw mentions: 2246
  - raw edges: 1280
  - edge type counts: `event_role=428`, `has_attribute=614`,
    `has_quantity=37`, `relation=197`,
    `ambiguous_relation_candidate=4`
- Stage 5:
  - canonical mentions: 2246
  - canonical edges: 1280
  - `formal_attribute_inventory_gate=true`
- Stage 6:
  - fact total: 23567
  - relation facts: 197
  - relation component facts: 19
  - ambiguous relation candidate facts: 4
  - ambiguous relation candidate table rows: 4
- Markdown report:
  `outputs/case_reports_sentence100_0101_0200_preposition_mwe_current/caption_to_concept_cases_0101_0200_preposition_mwe_current.md`

Observed relation MWE checks:

- `in front of` relation triples appeared in 3 captions.
- `in front of` relation components appeared as 3 component tokens across those
  3 captions.
- The 4 ambiguous relation candidates all came from an `along with` case where
  multiple object-mapped source/target candidates were preserved for review.

## 2026-07-11: 100-Caption R18.1 Rerun After VERB/AUX Head Source Rule

Purpose:

- Re-run the same 0101-0200 100-caption sample after extending R18.1 source
  candidates from `VERB` heads to `VERB`/`AUX` heads.

Output directory:

- `outputs/case_reports_sentence100_0101_0200_preposition_mwe_aux_head`

Validation commands:

- `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_stage4_extract_raw.py`
  - Result: 32 tests passed.
- `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_stage5_canonicalize.py`
  - Result: 6 tests passed.
- `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_stage6_export_counts.py`
  - Result: 3 tests passed.
- `git diff --check`
  - Result: passed. Git warned that existing `AGENTS_ko.md` line endings will
    be normalized when Git touches that file.

Rerun results:

- Stage 4:
  - raw mentions: 2246
  - raw edges: 1289
  - edge type counts: `event_role=428`, `has_attribute=614`,
    `has_quantity=37`, `relation=206`,
    `ambiguous_relation_candidate=4`
- Stage 5:
  - canonical mentions: 2246
  - canonical edges: 1289
  - `formal_attribute_inventory_gate=true`
- Stage 6:
  - fact total: 23593
  - relation facts: 206
  - relation component facts: 39
  - ambiguous relation candidate facts: 1
  - relation triple table rows: 192
- Markdown report:
  `outputs/case_reports_sentence100_0101_0200_preposition_mwe_aux_head/caption_to_concept_cases_0101_0200_preposition_mwe_aux_head.md`

Previously discussed relation MWE checks:

- `0015`: `leg --out of--> focus` now appears.
- `0025`: still emits `scene --next to--> wall`; the desired `area --next to--> wall`
  is not recovered because the current rule still does not do semantic source
  disambiguation.
- `0048`: `building --along with--> sign` now appears.
- `0050`: `bowl --next to--> it` now appears.
- `0076`: `van --in front of--> building` now appears.
- `0076`: `along with contact details` is still not semantically recovered as
  `markings --along with--> details`; current dependency evidence gives
  `"Marc Sovaerts" --along with--> detail`.
- `0090`: `screen --in front of--> building` now appears.
- `0100`: still missing. In the parse, `man` is `nsubj` of `speaks`, while
  `in front of` is attached to `standing`; `man` is not a direct child of the
  MWE head, and R18.1 still does not climb to sibling/ancestor event roles.

## 2026-07-11: 100-Caption R18.1 Rerun After Missing Endpoint Preservation

Purpose:

- Re-run the same 0101-0200 100-caption sample after preserving matched R18.1
  MWE occurrences as `ambiguous_relation_candidate` rows when source or target
  candidates are missing.

Output directory:

- `outputs/case_reports_sentence100_0101_0200_preposition_mwe_missing_endpoint`

Validation commands:

- `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_schema.py`
  - Result: 7 tests passed.
- `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_stage4_extract_raw.py`
  - Result: 33 tests passed.
- `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_stage5_canonicalize.py`
  - Result: 7 tests passed.
- `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_stage6_export_counts.py`
  - Result: 4 tests passed.

Rerun results:

- Stage 4:
  - raw mentions: 2246
  - raw edges: 1291
  - edge type counts: `event_role=428`, `has_attribute=614`,
    `has_quantity=37`, `relation=206`,
    `ambiguous_relation_candidate=6`
- Stage 5:
  - canonical mentions: 2246
  - canonical edges: 1291
  - `formal_attribute_inventory_gate=true`
- Stage 6:
  - fact total: 23595
  - relation facts: 206
  - relation component facts: 39
  - ambiguous relation candidate facts: 3
  - relation triple table rows: 192
  - ambiguous relation candidate table rows: 3
- Markdown report:
  `outputs/case_reports_sentence100_0101_0200_preposition_mwe_missing_endpoint/caption_to_concept_cases_0101_0200_preposition_mwe_missing_endpoint.md`

Observed ambiguous relation occurrences:

- `0015`: existing `along with` candidate remains
  `source_ambiguous / along with / target_ambiguous`.
- `0048`: previously dropped `such as` occurrence is now visible as
  `source_missing / such as / target_resolved`.
- `0100`: previously dropped `standing in front of a brick wall` occurrence is
  now visible as `source_missing / in front of / target_resolved`, with target
  `wall`.

Interpretation:

- Missing-endpoint occurrences no longer disappear silently.
- Normal relation facts stayed at 206, so missing endpoint candidates are not
  mixed into confirmed relation triples.
- Object count stayed at 1163, because missing endpoints do not create object
  mentions.

## 2026-07-11: Broad Preposition MWE Lexicon Merge Speed Probe

Purpose:

- Compare 100-caption runtime before and after merging the user-approved Google
  Ngram ADP...of relation pattern rows into the active Stage 4 preposition MWE
  lexicon.

Lexicon change:

- Baseline active lexicon: 370 rows.
- Broad active lexicon: 5021 rows.
- Broad rows by source:
  - `GOOGLE_NGRAM_RELATION_PATTERN`: 4651
  - reviewed external preposition MWE rows: 370
- Broad lexicon widths:
  - 2 tokens: 129
  - 3 tokens: 1322
  - 4 tokens: 2243
  - 5 tokens: 1327

Validation commands:

- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_stage4_extract_raw.py"`
  - Result: 33 tests passed.
- `.\scripts\run_python.ps1 -c "import ast; from pathlib import Path; ast.parse(Path('scripts/export_preposition_mwe_stage4_lexicon.py').read_text(encoding='utf-8'))"`
  - Result: passed.
- `.\scripts\run_python.ps1 -m compileall scripts\export_preposition_mwe_stage4_lexicon.py`
  - Result: failed with `PermissionError` while writing `scripts\__pycache__`.
  - Interpretation: bytecode write permission failure, not a syntax failure.

Benchmark command:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 scripts\benchmark_fast_pipeline.py --input outputs\case_reports_sentence100_0101_0200_current\sentence_rows_0101_0200.jsonl --object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --lexicon-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_action_canonical --batch-size 512 --summary outputs\benchmark_preposition_mwe_broad\baseline_370_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 scripts\benchmark_fast_pipeline.py --input outputs\case_reports_sentence100_0101_0200_current\sentence_rows_0101_0200.jsonl --object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --lexicon-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_action_canonical --batch-size 512 --summary outputs\benchmark_preposition_mwe_broad\broad_5021_summary.json`

Benchmark scope:

- Input: 100 sentence captions, `0101-0200`.
- Model: `en_core_web_trf`.
- Batch size: 512.
- Length bucketing: disabled.
- Raw extraction mode: `stage3-record`.
- GPU mode: none; latest run reported `gpu_enabled=false`.
- GPU metadata was recorded in both summary JSON files.
- This is a speed probe through `benchmark_fast_pipeline.py`; the benchmark
  path does not write formal Stage 4/5/6 JSONL artifacts.

Benchmark result:

| metric | baseline 370 | broad 5021 | delta |
|---|---:|---:|---:|
| processing seconds | 3.7338 | 3.9036 | +0.1698 |
| processing captions/sec | 26.78 | 25.62 | -1.17 |
| total seconds | 5.1837 | 5.2059 | +0.0221 |
| total captions/sec | 19.29 | 19.21 | -0.08 |
| Stage 3 seconds | 3.0634 | 3.0984 | +0.0350 |
| Stage 4 seconds | 0.1783 | 0.2146 | +0.0362 |
| Stage 6 seconds | 0.3999 | 0.4936 | +0.0937 |

Count-impact observation from benchmark output:

- `relation_component` facts increased from 39 to 74.
- `ambiguous_relation_candidate` facts increased from 3 to 4.
- `relation` facts changed from 206 to 205.
- `entity_exists` facts changed from 1163 to 1155.
- The object/fact-total changes mean broad preposition MWE consumption affects
  more than relation labels in this sample, so formal report review is still
  needed before treating the broad lexicon output as the new report baseline.

Interpretation:

- The active lexicon grew from 370 to 5021 rows.
- Stage 4 time increased by about 0.036 seconds on 100 captions in this run.
- The dominant runtime remains Stage 3 transformer parsing in this 100-caption
  benchmark.

## 2026-07-12: 100-Caption R13 Attribute Conj Chain Rerun

Purpose:

- Re-run the same `0101-0200` 100-caption sample after adding R13 attribute
  conjunct expansion.
- During self-review, direct-conj-only expansion missed `yellow` in
  `blue, white, and yellow planes`, because the parse attached it as a chained
  conjunct (`blue -> white -> yellow`). The rule and implementation were
  adjusted to same-noun-chunk conj-chain expansion rooted at an accepted
  attribute modifier.

Output directory:

- `outputs/case_reports_sentence100_0101_0200_attribute_conj_current`

Validation commands:

- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage4_extract_raw.py`
  - Result: 35 tests passed.
- `.\scripts\run_python.ps1 -c "import ast, pathlib; files=[pathlib.Path('src/gpic_concepts_v1/stage4_extract_raw.py'), pathlib.Path('tests/test_stage4_extract_raw.py')]; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print('syntax ok: stage4 + test')"`
  - Result: passed.

Rerun commands:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --action-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_resolved.tsv --raw-mentions outputs\case_reports_sentence100_0101_0200_attribute_conj_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_attribute_conj_current\raw_edges.jsonl --summary outputs\case_reports_sentence100_0101_0200_attribute_conj_current\stage4_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence100_0101_0200_attribute_conj_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_attribute_conj_current\raw_edges.jsonl --lexicon-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_action_canonical --canonical-mentions outputs\case_reports_sentence100_0101_0200_attribute_conj_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_attribute_conj_current\canonical_edges.jsonl --summary outputs\case_reports_sentence100_0101_0200_attribute_conj_current\stage5_summary.jsonl --attribute-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence100_0101_0200_attribute_conj_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_attribute_conj_current\canonical_edges.jsonl --output-dir outputs\case_reports_sentence100_0101_0200_attribute_conj_current\stage6 --summary outputs\case_reports_sentence100_0101_0200_attribute_conj_current\stage6_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\build_caption_concept_md.py --sentence-rows outputs\case_reports_sentence100_0101_0200_current\sentence_rows_0101_0200.jsonl --stage3-records outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence100_0101_0200_attribute_conj_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_attribute_conj_current\canonical_edges.jsonl --facts outputs\case_reports_sentence100_0101_0200_attribute_conj_current\stage6\facts.jsonl --output outputs\case_reports_sentence100_0101_0200_attribute_conj_current\caption_to_concept_cases_0101_0200_attribute_conj_current.md --start 0 --limit 100 --max-object-pairs-per-caption 40`

Rerun results:

- Stage 4:
  - raw mentions: 2255
  - raw edges: 1307
  - mention type counts: `action=432`, `attribute=631`, `object=1155`,
    `quantity=37`
  - edge type counts: `ambiguous_relation_candidate=7`, `event_role=427`,
    `has_attribute=631`, `has_quantity=37`, `relation=205`
- Stage 5:
  - canonical mentions: 2255
  - canonical edges: 1307
  - `formal_attribute_inventory_gate=true`
  - canonical source counts: `gpic_observed_inventory=1051`, `lexicon=1032`,
    `raw_fallback=172`
- Stage 6:
  - fact total: 23153
  - `attribute_exists=631`
  - `has_attribute=631`
  - `object_attribute_pair_counts.tsv` rows: 562
- Markdown report:
  `outputs/case_reports_sentence100_0101_0200_attribute_conj_current/caption_to_concept_cases_0101_0200_attribute_conj_current.md`

Self-review notes:

- 21 attribute mentions have
  `source_detail.modifier_source=conj_of_attribute_modifier`.
- Examples recovered as expected:
  - `red and purple lights`: `purple -> lights`
  - `blue and white uniform`: `white -> uniform`
  - `blue, white, and yellow planes`: `white -> planes` and `yellow -> planes`
  - `gold and silver details`: `silver -> details`
- No obvious false-positive conjunct attribute was found in the 21-row manual
  spot check.
- Compared with the older
  `case_reports_sentence100_0101_0200_preposition_mwe_missing_endpoint`
  artifact, attributes increased by 17 and `has_attribute` edges increased by
  17. Object/relation counts are not an apples-to-apples R13 comparison because
  the active preposition MWE lexicon is now the 5021-row broad lexicon recorded
  in the previous speed probe.

## 2026-07-12: R18/R18.1 Relation Target Conj Regression

Purpose:

- Verify the target-side relation conjunction rule before and after
  implementation.
- Keep R18.1 multiple-independent-target behavior ambiguous while allowing one
  base target with coordinated target conjuncts to produce normal relation
  edges.

Validation commands:

- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 60 -q tests\test_stage4_extract_raw.py -k "target_conj"`
  - Before implementation: 2 failed, both expected missing target-conj
    relation edges.
  - After implementation: 2 passed, 35 deselected.
- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 120 -q tests\test_stage4_extract_raw.py`
  - Result: 37 passed, 1 warning from third-party `torch.jit.script`
    deprecation.
- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 120 -q tests\test_stage6_export_counts.py`
  - Result: 4 passed.
- `.\scripts\run_python.ps1 -m compileall src\gpic_concepts_v1\stage4_extract_raw.py`
  - Result: failed with `PermissionError` writing `__pycache__`; this is the
    known bytecode-write failure mode, not a syntax failure.
- `.\scripts\run_python.ps1 -c "import ast, pathlib; ast.parse(pathlib.Path('src/gpic_concepts_v1/stage4_extract_raw.py').read_text(encoding='utf-8'))"`
  - Result: passed. Used as bytecode-free syntax check.

Implementation notes:

- R18 now expands a direct `pobj` target through object-mapped target-side
  `conj` chains.
- R18.1 now distinguishes `candidate_target_count` from
  `candidate_target_base_count`.
- R18.1 normal relation edge selection uses one source candidate plus one
  independent target base; coordinated targets from that base are emitted as
  multiple normal relation edges.
- Multiple independent target bases still produce
  `ambiguous_relation_candidate` edges.

## 2026-07-12: 100-Caption R18/R18.1 Relation Target Conj Rerun

Purpose:

- Re-run the same `0101-0200` 100-caption sample after R18/R18.1 target-side
  relation conjunction expansion.
- Compare against `case_reports_sentence100_0101_0200_attribute_conj_current`
  as the prior 100-caption baseline.

Output directory:

- `outputs/case_reports_sentence100_0101_0200_relation_target_conj_current`

Rerun commands:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --action-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_resolved.tsv --raw-mentions outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\raw_edges.jsonl --summary outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\stage4_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\raw_edges.jsonl --lexicon-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_action_canonical --canonical-mentions outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\canonical_edges.jsonl --summary outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\stage5_summary.jsonl --attribute-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\canonical_edges.jsonl --output-dir outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\stage6 --summary outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\stage6_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\build_caption_concept_md.py --sentence-rows outputs\case_reports_sentence100_0101_0200_current\sentence_rows_0101_0200.jsonl --stage3-records outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\canonical_edges.jsonl --facts outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\stage6\facts.jsonl --output outputs\case_reports_sentence100_0101_0200_relation_target_conj_current\caption_to_concept_cases_0101_0200_relation_target_conj_current.md --start 0 --limit 100 --max-object-pairs-per-caption 40`

Rerun results:

- Stage 4:
  - raw mentions: 2255
  - raw edges: 1350
  - mention type counts: `action=432`, `attribute=631`, `object=1155`,
    `quantity=37`
  - edge type counts: `ambiguous_relation_candidate=8`, `event_role=427`,
    `has_attribute=631`, `has_quantity=37`, `relation=247`
- Stage 5:
  - canonical mentions: 2255
  - canonical edges: 1350
  - canonical source counts: `gpic_observed_inventory=1051`, `lexicon=1032`,
    `raw_fallback=172`
- Stage 6:
  - fact total: 23199
  - fact type counts include `relation=247`,
    `ambiguous_relation_candidate=4`, `relation_component=78`
  - `relation_triple_counts.tsv` rows: 233
- Markdown report:
  `outputs/case_reports_sentence100_0101_0200_relation_target_conj_current/caption_to_concept_cases_0101_0200_relation_target_conj_current.md`

Comparison with previous 100-caption baseline:

- Raw relation-like edges:
  - Previous: `relation=205`, `ambiguous_relation_candidate=7`
  - New: `relation=247`, `ambiguous_relation_candidate=8`
- New relation-like edges: 43 total.
  - `relation`: 42
  - `ambiguous_relation_candidate`: 1
  - `R18`: 40
  - `R18.1`: 3
  - all 43 have target resolution `conj_of_pobj` or `conj_of_final_pobj`
- `relation_triple_counts.tsv`:
  - previous rows: 192
  - new rows: 233
  - new-only rows: 41
  - existing rows with increased count: 1

Self-review notes:

- Every new relation-like edge has a direct base-target counterpart.
- Duplicate relation-like edge keys: 0.
- New-only non-conj relation-like edges: 0.
- Good recovered examples include:
  - `seagull --with--> wings`
  - `pasta --with--> chicken`
  - `van --with--> logo`
  - `building --with--> window frames`
  - `scene --next to--> door`
- Expected noisy examples also appear because the current policy counts any
  object-mapped target conjunct, including objects that are visually/property-
  like in context:
  - `artificial flowers --in--> yellow`
  - `artificial flowers --in--> red`
  - `materials --like--> roofing`
- The one new ambiguous R18.1 edge is target-conj recovery in an already
  source-missing/ambiguous `in front of ... wall and banner` style case, so it
  remains out of normal relation triple count.

## 2026-07-12: R16.1 Action Conj Agent Inheritance Regression

Purpose:

- Add agent-only inheritance for coordinated actions.
- Verify patient roles are not inherited.
- Verify passive-like conjunct targets do not inherit active agents.

Validation commands:

- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 60 -q tests\test_stage4_extract_raw.py -k "conjunct_action"`
  - Before implementation: 3 failed, all due missing R16.1 agent inheritance.
  - After implementation and passive-like safety gate: 4 passed, 37 deselected.
- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 120 -q tests\test_stage4_extract_raw.py`
  - Result: 41 passed, 1 warning from third-party `torch.jit.script`
    deprecation.
- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 120 -q tests\test_stage6_export_counts.py`
  - Result: 4 passed.
- `.\scripts\run_python.ps1 -c "import ast, pathlib; ast.parse(pathlib.Path('src/gpic_concepts_v1/stage4_extract_raw.py').read_text(encoding='utf-8')); ast.parse(pathlib.Path('tests/test_stage4_extract_raw.py').read_text(encoding='utf-8')); print('ast ok')"`
  - Result: passed.

Implementation notes:

- R16.1 runs after direct R16/R17 event role extraction and before R18/R18.1
  relation extraction.
- R16.1 copies an agent only when the target action is a `conj` action, the
  target action has no existing agent, and the source action has exactly one
  agent.
- R16.1 uses fixed-point propagation, so chained coordination can inherit from
  an action that itself just inherited an agent.
- R16.1 does not inherit patients.
- Self-review of the first 100-caption run found false positives where active
  agents were inherited into passive-like `parked/framed` targets. The final
  rule excludes target actions with direct `nsubjpass`, `auxpass`, or `agent`
  children.

## 2026-07-12: 100-Caption R16.1 Action Conj Agent Rerun

Purpose:

- Re-run the same `0101-0200` 100-caption sample after R16.1.
- Compare against `case_reports_sentence100_0101_0200_relation_target_conj_current`
  as the previous 100-caption baseline.

Output directory:

- `outputs/case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current`

Rerun commands:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --action-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_resolved.tsv --raw-mentions outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\raw_edges.jsonl --summary outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\stage4_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\raw_edges.jsonl --lexicon-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_action_canonical --canonical-mentions outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\canonical_edges.jsonl --summary outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\stage5_summary.jsonl --attribute-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\canonical_edges.jsonl --output-dir outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\stage6 --summary outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\stage6_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\build_caption_concept_md.py --sentence-rows outputs\case_reports_sentence100_0101_0200_current\sentence_rows_0101_0200.jsonl --stage3-records outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\canonical_edges.jsonl --facts outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\stage6\facts.jsonl --output outputs\case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current\caption_to_concept_cases_0101_0200_action_agent_conj_passive_safe_current.md --limit 100 --start 0`

Rerun results:

- Stage 4:
  - raw mentions: 2255
  - raw edges: 1361
  - mention type counts: `action=432`, `attribute=631`, `object=1155`,
    `quantity=37`
  - edge type counts: `ambiguous_relation_candidate=8`, `event_role=438`,
    `has_attribute=631`, `has_quantity=37`, `relation=247`
- Stage 5:
  - canonical mentions: 2255
  - canonical edges: 1361
  - canonical source counts: `gpic_observed_inventory=1051`, `lexicon=1032`,
    `raw_fallback=172`
- Stage 6:
  - fact total: 23210
  - fact type counts include `event_role=438`, `relation=247`,
    `ambiguous_relation_candidate=4`, `relation_component=78`
  - `agent_patient_pair_counts.tsv` rows: 383
  - `relation_triple_counts.tsv` rows: 233
- Markdown report:
  `outputs/case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current/caption_to_concept_cases_0101_0200_action_agent_conj_passive_safe_current.md`

Comparison with previous 100-caption baseline:

- Previous raw edge types:
  `has_quantity=37`, `has_attribute=631`, `event_role=427`,
  `relation=247`, `ambiguous_relation_candidate=8`
- New raw edge types:
  `has_quantity=37`, `has_attribute=631`, `event_role=438`,
  `relation=247`, `ambiguous_relation_candidate=8`
- R16.1 edges: 11 total.
- R16.1 labels: `agent=11`.
- R16.1 patient edges: 0.
- Passive-like false positives found in the first self-review (`parked` and
  `framed`) are not present as R16.1 edges after the safety gate.

Additional self-review:

- R16.1 generated 8 new `agent_patient_pair_counts.tsv` rows and increased 2
  existing rows.
- New rows:
  - `event_role:carry:agent:individual`, count 2
  - `event_role:have on:agent:he`, count 1
  - `event_role:have on:agent:headset`, count 1
  - `event_role:have:agent:he`, count 1
  - `event_role:mention:agent:banner`, count 1
  - `event_role:sing:agent:man`, count 1
  - `event_role:talk:agent:people`, count 1
  - `event_role:wrap:agent:that`, count 1
- Increased rows:
  - `event_role:stand:agent:he`, 2 -> 3
  - `event_role:wear:agent:he`, 2 -> 3
- Residual risk: `event_role:wrap:agent:that` comes from the source action in
  `ivy that climbs ... and wraps ...`; R16.1 correctly copied the source
  action's single agent, but the source agent itself is a pronoun/reference-like
  object because this pipeline still does not do coreference resolution.

## 2026-07-12: R16.2/R17.1 Passive Voice Regression

Purpose:

- Add passive subject and passive by-phrase handling without moving repair
  logic into Stage 5.
- Verify active `by` phrases do not become passive agents.
- Verify passive metadata survives into Stage 6 count facts/tables.

Commands:

- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 60 -q tests\test_stage4_extract_raw.py -k "passive or active_by"`
  - Before implementation: 2 failed, 2 passed, 39 deselected.
  - After implementation: 4 passed, 39 deselected.
- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 120 -q tests\test_stage4_extract_raw.py`
  - 43 passed, 1 warning.
- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 120 -q tests\test_stage6_export_counts.py`
  - 5 passed.
- `.\scripts\run_python.ps1 -m compileall src scripts tests`
  - Failed with `PermissionError` while writing repo-local `__pycache__`
    files. This is the known sandbox/OneDrive pycache write issue, not a
    syntax failure.
- `.\scripts\run_python.ps1 -c "... ast.parse(... encoding='utf-8-sig') ..."`
  - `ast ok` for:
    `src/gpic_concepts_v1/stage4_extract_raw.py`,
    `src/gpic_concepts_v1/stage6_export_counts.py`,
    `scripts/build_caption_concept_md.py`.

Implemented behavior:

- R17.1 creates `event_role:patient` from direct `nsubjpass`/`csubjpass`
  object children.
- R16.2 creates `event_role:agent` from direct passive `by + pobj` only when
  the same action already has an R17.1 passive subject edge.
- Stage 6 event_role facts and `agent_patient_pair_counts.tsv` now include
  `raw_role` and `voice_normalization` explanatory fields.

## 2026-07-12: 100-Caption Passive Voice Rerun

Purpose:

- Re-run the same `0101-0200` 100-caption sample after R16.2/R17.1.
- Compare against
  `outputs/case_reports_sentence100_0101_0200_action_agent_conj_passive_safe_current`.

Output directory:

- `outputs/case_reports_sentence100_0101_0200_passive_voice_current`

Commands:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --action-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_resolved.tsv --raw-mentions outputs\case_reports_sentence100_0101_0200_passive_voice_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_passive_voice_current\raw_edges.jsonl --summary outputs\case_reports_sentence100_0101_0200_passive_voice_current\stage4_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence100_0101_0200_passive_voice_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_passive_voice_current\raw_edges.jsonl --lexicon-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_action_canonical --canonical-mentions outputs\case_reports_sentence100_0101_0200_passive_voice_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_passive_voice_current\canonical_edges.jsonl --summary outputs\case_reports_sentence100_0101_0200_passive_voice_current\stage5_summary.jsonl --attribute-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence100_0101_0200_passive_voice_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_passive_voice_current\canonical_edges.jsonl --output-dir outputs\case_reports_sentence100_0101_0200_passive_voice_current\stage6 --summary outputs\case_reports_sentence100_0101_0200_passive_voice_current\stage6_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\build_caption_concept_md.py --sentence-rows outputs\case_reports_sentence100_0101_0200_current\sentence_rows_0101_0200.jsonl --stage3-records outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence100_0101_0200_passive_voice_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_passive_voice_current\canonical_edges.jsonl --facts outputs\case_reports_sentence100_0101_0200_passive_voice_current\stage6\facts.jsonl --output outputs\case_reports_sentence100_0101_0200_passive_voice_current\caption_to_concept_cases_0101_0200_passive_voice_current.md --limit 100 --start 0`

Results:

- Stage 4/5 edge type counts:
  `ambiguous_relation_candidate=8`, `event_role=479`,
  `has_attribute=631`, `has_quantity=37`, `relation=247`.
- Stage 6 fact type counts include:
  `event_role=479`, `relation=247`, `ambiguous_relation_candidate=4`.
- Stage 6 table rows:
  `agent_patient_pair_counts.tsv=423`,
  `relation_triple_counts.tsv=233`,
  `object_counts.tsv=504`.
- MD report:
  `outputs/case_reports_sentence100_0101_0200_passive_voice_current/caption_to_concept_cases_0101_0200_passive_voice_current.md`

Passive edge audit:

- R17.1 edges: 40 total, all `patient`.
- R16.2 edges: 1 total, `agent`.
- All 41 passive edges have `voice_normalization=passive_to_active`.
- Baseline R16.1-safe run had `event_role=438`; passive run has
  `event_role=479`, so the net increase is 41 event roles.
- Diff against baseline produced 40 changed/new count rows because one passive
  row, `event_role:mount:patient:sign`, has count 2.
- Example new rows:
  - `event_role:mount:patient:sign`, count 2
  - `event_role:illuminate:agent:lighting`, count 1
  - `event_role:park:patient:van`, count 1
  - `event_role:see:patient:bus`, count 1
  - `event_role:display:patient:airplane`, count 1

Residual risk:

- The rule inherits existing action canonicalization quality. Existing false
  positives such as phrasal action choices remain possible, but passive role
  extraction itself is limited to direct passive dependency evidence.

Additional self-review:

- Passive raw edge audit:
  - passive edges: 41
  - `R17.1`: 40
  - `R16.2`: 1
  - duplicate passive exact keys: 0
  - R16.2 actions without same-action R17.1 passive subject: 0
  - all passive edges have `voice_normalization=passive_to_active`
- Conj interaction audit:
  - R16.1 edges: 11
  - R16.1 edges on passive actions: 0
- Count-table audit:
  - `agent_patient_pair_counts.tsv` has `raw_role` and
    `voice_normalization` columns.
  - aggregate rows with `voice_normalization=passive_to_active`: 40
    because 41 passive facts collapse into 40 count rows.
- Baseline comparison:
  - R16: unchanged at 247
  - R16.1: unchanged at 11
  - R17: unchanged at 180
  - R17.1: +40
  - R16.2: +1
- Sample review showed expected passive conversions such as:
  - `parked -> fire truck`, `nsubjpass`, `theme`
  - `seen -> bus`, `nsubjpass`, `theme`
  - `mounted -> signs`, `nsubjpass`, `theme`
- Residual non-passive-rule issues observed:
  - Existing object/reference limitations can still produce targets like
    `They` or `both`.
  - Existing action canonicalization can still produce phrasal labels such as
    `set in` or `set on`.
  - These are not introduced by R16.2/R17.1; they remain separate object/action
    inventory or coreference issues.

## 2026-07-12: R16.3 ACL Action Head-Object Agent Regression

Purpose:

- Add the narrow `acl` agent recovery rule without reviving the old broad
  inheritance behavior.
- Verify the rule handles active participial `acl` only and does not treat VBN
  reduced passive/adjectival modifiers as agents.

Commands:

- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 120 -q tests\test_stage4_extract_raw.py -k "acl"`
  - After implementation: 4 passed, 43 deselected.
- `.\scripts\run_tests.ps1 --pytest --timeout-seconds 180 -q tests\test_stage4_extract_raw.py`
  - After implementation: 47 passed, 1 warning.

Implementation notes:

- R16.3 runs after direct R16/R17/R17.1/R16.2 event role extraction and before
  R16.1 conjunct action agent inheritance.
- R16.3 creates only `event_role:agent`.
- R16.3 requires:
  - action head token is selected as an action head
  - `dep == "acl"`
  - `tag == "VBG"`
  - action has no existing agent edge
  - acl dependency head token is already selected as an object
  - action is not passive-like by direct child dep `nsubjpass`, `auxpass`, or
    `agent`
- R16.3 does not handle `relcl` relative-pronoun resolution.
- R16.3 does not inherit patients.

Self-review adjustment:

- The first 100-caption run without the VBG gate produced 25 R16.3 edges and
  incorrectly included VBN reduced passive/adjectival cases such as
  `bicycles parked`, `plaque mounted`, `screens placed`, and
  `advertisement painted`.
- The rule was narrowed to `tag == "VBG"` and a VBN negative regression test
  was added.

## 2026-07-12: 100-Caption R16.3 ACL Agent Rerun

Purpose:

- Re-run the same `0101-0200` 100-caption sample after R16.3.
- Compare against `outputs/case_reports_sentence100_0101_0200_passive_voice_current`.

Output directory:

- `outputs/case_reports_sentence100_0101_0200_acl_agent_current`

Commands:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage4_extract_raw.py --input outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --action-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_manual_resolved.tsv --raw-mentions outputs\case_reports_sentence100_0101_0200_acl_agent_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_acl_agent_current\raw_edges.jsonl --summary outputs\case_reports_sentence100_0101_0200_acl_agent_current\stage4_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage5_canonicalize.py --raw-mentions outputs\case_reports_sentence100_0101_0200_acl_agent_current\raw_mentions.jsonl --raw-edges outputs\case_reports_sentence100_0101_0200_acl_agent_current\raw_edges.jsonl --lexicon-dir outputs\case_reports_sentence100_0101_0200_current\stage5_lexicons_attribute_action_canonical --canonical-mentions outputs\case_reports_sentence100_0101_0200_acl_agent_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_acl_agent_current\canonical_edges.jsonl --summary outputs\case_reports_sentence100_0101_0200_acl_agent_current\stage5_summary.jsonl --attribute-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage6_export_counts.py --canonical-mentions outputs\case_reports_sentence100_0101_0200_acl_agent_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_acl_agent_current\canonical_edges.jsonl --output-dir outputs\case_reports_sentence100_0101_0200_acl_agent_current\stage6 --summary outputs\case_reports_sentence100_0101_0200_acl_agent_current\stage6_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\build_caption_concept_md.py --sentence-rows outputs\case_reports_sentence100_0101_0200_current\sentence_rows_0101_0200.jsonl --stage3-records outputs\case_reports_sentence100_0101_0200_current\stage3_records.jsonl --canonical-mentions outputs\case_reports_sentence100_0101_0200_acl_agent_current\canonical_mentions.jsonl --canonical-edges outputs\case_reports_sentence100_0101_0200_acl_agent_current\canonical_edges.jsonl --facts outputs\case_reports_sentence100_0101_0200_acl_agent_current\stage6\facts.jsonl --output outputs\case_reports_sentence100_0101_0200_acl_agent_current\caption_to_concept_cases_0101_0200_acl_agent_current.md --limit 100 --start 0`

Results:

- Stage 4/5 edge type counts:
  `ambiguous_relation_candidate=8`, `event_role=486`,
  `has_attribute=631`, `has_quantity=37`, `relation=247`.
- Stage 6 fact type counts include:
  `event_role=486`, `relation=247`, `ambiguous_relation_candidate=4`.
- Stage 6 table rows:
  `agent_patient_pair_counts.tsv=428`,
  `relation_triple_counts.tsv=233`,
  `object_counts.tsv=504`.
- MD report:
  `outputs/case_reports_sentence100_0101_0200_acl_agent_current/caption_to_concept_cases_0101_0200_acl_agent_current.md`

R16.3 audit:

- R16.3 edges: 7 total.
- R16.3 labels: `agent=7`.
- R16.1 edges remained 11 after the VBG gate.
- R16.1 edges whose source agent came from R16.3: 0.
- Event-role rule counts:
  - R16: 247
  - R16.1: 11
  - R16.2: 1
  - R16.3: 7
  - R17: 180
  - R17.1: 40

Sample R16.3 edges:

- `holding -> supports`
- `marking -> line`
- `wearing -> man`
- `carrying -> woman`
- `showing -> sign`
- `reading -> sign`
- `wearing -> man`

Residual risk:

- R16.3 still inherits existing parser/action quality. If the parser labels a
  gerund-like modifier as `acl/VBG` where the head is not a semantic agent, the
  rule can still add a questionable agent.
- VBN passive/adjectival false positives observed in the first run were removed
  by the VBG gate.
- `relcl` relative-pronoun cases remain intentionally unresolved.

## 2026-07-12: Tag-list Segment Extraction Tests

Purpose:

- Accept the R1.1 tag-list route change and the Stage 3/4 tag-list object,
  attribute, and quantity branch.
- Verify tag-list rows no longer depend on `tag_list_deferred` skip semantics.
- Verify tag-list Stage 4 does not enter the sentence action/relation path.

Validation notes:

- A direct `.\scripts\run_python.ps1 -m compileall src scripts tests` attempt
  failed with `PermissionError` while writing repo-local `__pycache__` files.
  This is the known pycache write issue, not evidence of a syntax error.
- Syntax validation was repeated with `ast.parse`, which does not write
  `__pycache__`.

Commands:

- `.\scripts\run_python.ps1 -c "import ast, pathlib; files=['src/gpic_concepts_v1/schema.py','src/gpic_concepts_v1/stage1.py','src/gpic_concepts_v1/stage1_loader.py','src/gpic_concepts_v1/stage3_annotate.py','src/gpic_concepts_v1/stage4_extract_raw.py','scripts/run_stage1_records.py','scripts/run_stage3_annotate.py','tests/test_schema.py','tests/test_stage1.py','tests/test_stage1_loader.py','tests/test_stage3_annotate.py','tests/test_stage4_extract_raw.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8')) for f in files]; print('ast ok')"`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_schema.py`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage1.py`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage1_loader.py`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage4_extract_raw.py -k tag_list`
- `.\scripts\run_tests.ps1 --timeout-seconds 240 discover -s tests -p test_stage3_annotate.py -k tag_list`
- `.\scripts\run_tests.ps1 --timeout-seconds 240 discover -s tests -p test_stage3_annotate.py`
- `.\scripts\run_tests.ps1 --timeout-seconds 240 discover -s tests -p test_stage4_extract_raw.py`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage2_preprocess.py -k tag_list`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_stage1*.py`
- `git diff --check`

Results:

- AST parse: `ast ok`.
- `test_schema.py`: 7 passed.
- `test_stage1.py`: 9 passed.
- `test_stage1_loader.py`: 2 passed.
- Stage 4 tag-list focused test: 1 passed.
- Stage 3 tag-list focused tests: 3 passed.
- Full `test_stage3_annotate.py`: 7 passed.
- Full `test_stage4_extract_raw.py`: 48 passed.
- Stage 2 tag-list boundary test: 1 passed.
- Combined Stage 1 pattern: 11 passed.
- `git diff --check`: only existing CRLF/LF warnings for
  `docs/rule_change_review_log_v1.md` and `scripts/build_caption_concept_md.py`.

Residual risk:

- Tag-list action, event-role, and relation extraction remain intentionally
  unimplemented.
- Tag-list cross-segment grouping remains intentionally unimplemented.
- Only object-bearing segment noun chunks and single-token floating
  attribute-like segments are extracted in this first tag-list branch.

## 2026-07-12: GPIC Object Inventory Prior Reuse Tests

Purpose:

- Verify that already resolved GPIC observed object inventory rows can be reused by exact `span_key` when building a new object inventory batch.
- Prevent tag-list object inventory builds from re-queuing sentence-batch object spans that already have selected synset/canonical/parent evidence.

Commands:

- `.\scripts\run_python.ps1 -c "import ast, pathlib; ast.parse(pathlib.Path('scripts/build_gpic_observed_object_inventory.py').read_text(encoding='utf-8')); ast.parse(pathlib.Path('tests/test_build_gpic_observed_object_inventory.py').read_text(encoding='utf-8')); print('ast_ok')"`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_build_gpic_observed_object_inventory.py`

Results:

- AST parse: `ast_ok`.
- `test_build_gpic_observed_object_inventory.py`: 3 passed.

Residual risk:

- Prior reuse is exact `span_key` only. Surface variants still need normal lookup/manual resolution.
- Reused prior rows can carry a stale prior mistake; `decision_basis` records the reuse path for audit.

Tag-list prior-reuse verification:

- Command:
  - `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 -- scripts\build_gpic_observed_object_inventory.py --input outputs\tag_list_current_run\stage3_tag_list\stage3_records.jsonl --prior-object-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --output outputs\tag_list_current_run\inventory_prior\gpic_tag_list_object_inventory.tsv --summary outputs\tag_list_current_run\inventory_prior\gpic_tag_list_object_inventory_summary.json`
- Result:
  - inventory_rows: 85
  - prior_reusable_rows: 567
  - prior_reused_rows: 37
  - decision_status_counts: `chosen=69`, `excluded=1`, `needs_manual=15`
  - previous no-prior tag-list run had `needs_manual=25`
- Old needs_manual rows resolved by prior reuse:
  - `book`, `text`, `background`, `court`, `forest`, `jersey`, `night`, `person`, `sign`, `waves`
- Remaining needs_manual subset:
  - `outputs/tag_list_current_run/inventory_prior/gpic_tag_list_object_inventory_needs_manual.tsv`

## 2026-07-12: Tag-list Attribute Inventory Prior Reuse Correction

Purpose:

- Correct the tag-list attribute inventory build so it follows the same
  prior-inventory reuse workflow as object inventory.
- Prevent tag-list attribute rows from being rebuilt in isolation when a
  resolved/canonical sentence attribute inventory already exists for the same
  `span_key`.
- Preserve prior canonical fields from the reused attribute inventory instead
  of dropping them during the observed-inventory rebuild.

Code change:

- `scripts/build_gpic_observed_attribute_inventory.py` now:
  - reuses final prior rows with `decision_status=chosen` or `excluded`
  - preserves non-manual prior canonical fields
  - reports `prior_reused_rows`, `prior_selected_synset_reused_rows`, and
    `prior_canonical_reused_rows` in the summary

Commands:

- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_build_gpic_observed_attribute_inventory.py`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\build_gpic_observed_attribute_inventory.py --input outputs\tag_list_current_run\stage3_tag_list\stage3_records.jsonl --object-inventory outputs\tag_list_current_run\inventory_prior\gpic_tag_list_object_inventory_manual_resolved_parent_canonical.tsv --attribute-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_attribute_inventory_current_manual_resolved_canonical.tsv --output outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory.tsv --summary outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_summary.json`

Results:

- Attribute inventory tests: 8 passed.
- Tag-list attribute inventory rows: 28.
- Prior reused rows: 16.
- Prior selected-synset reused rows: 16.
- Prior canonical reused rows: 16.
- Decision status counts after prior reuse:
  - `chosen=18`
  - `needs_manual=10`
- Synset/manual gate status:
  - `blocked_attribute_synset_inventory_before_canonical`
  - blockers: `pending_manual_decision_status=10`
- Needs-manual subset:
  - `outputs/tag_list_current_run/inventory_prior/gpic_tag_list_attribute_inventory_needs_manual.tsv`

Interpretation:

- The previous tag-list attribute inventory build ignored the available
  sentence attribute prior. That was not a formal pipeline result.
- The corrected build now uses the same cross-caption-shape inventory namespace
  as object inventory.
- Canonical missing rows are not part of this current synset/manual gate.
  Canonical checks start only after the 10 `needs_manual` attribute rows are
  resolved and the offline canonical inventory build is run.
- Existing files under `outputs/tag_list_current_run/manual_resolved` include
  Stage 5/6 preview output created before attribute manual/canonical completion.
  They are not formal tag-list caption-to-concept output and are marked with
  `PREVIEW_NOT_FORMAL.md`.

## 2026-07-12: Tag-list Attribute Manual Resolution And Formal Output

Purpose:

- Apply the user-provided 10-row tag-list attribute manual resolution file.
- Follow the same sentence workflow order:
  needs-manual resolution, canonical enrichment, Stage 5, Stage 6.
- Replace the earlier preview-only Stage 5/6 result with a formal output
  folder that passes the attribute inventory gate.

Inputs:

- `outputs/tag_list_current_run/inventory_prior/gpic_tag_list_attribute_inventory.tsv`
- `C:\Users\rlath\Downloads\gpic_tag_list_attribute_inventory_resolved.tsv`
- `C:\Users\rlath\Downloads\gpic_tag_list_attribute_inventory_manual_decisions.tsv`

Commands:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\apply_attribute_manual_resolution.py --full-inventory outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory.tsv --resolved-subset C:\Users\rlath\Downloads\gpic_tag_list_attribute_inventory_resolved.tsv --manual-decisions C:\Users\rlath\Downloads\gpic_tag_list_attribute_inventory_manual_decisions.tsv --output outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_manual_resolved.tsv --resolved-copy outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_manual_resolved_subset.tsv --manual-decisions-copy outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_manual_decisions.tsv --summary outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_manual_resolution_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\enrich_gpic_attribute_inventory_canonical.py --input outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_manual_resolved.tsv --output outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_manual_resolved_canonical.tsv --ambiguous-output outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_manual_resolved_canonical_ambiguous.tsv --summary outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_manual_resolved_canonical_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\export_attribute_stage5_lexicons.py --attribute-inventory outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_manual_resolved_canonical.tsv --output-dir outputs\tag_list_current_run\stage5_lexicons_attribute_manual_resolved --base-lexicon-dir resources\lexicons --summary outputs\tag_list_current_run\stage5_lexicons_attribute_manual_resolved_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\run_stage4_extract_raw.py --input outputs\tag_list_current_run\stage3_tag_list\stage3_records.jsonl --object-inventory outputs\tag_list_current_run\inventory_prior\gpic_tag_list_object_inventory_manual_resolved_parent_canonical.tsv --raw-mentions outputs\tag_list_current_run\formal_attribute_resolved\raw_mentions.jsonl --raw-edges outputs\tag_list_current_run\formal_attribute_resolved\raw_edges.jsonl --summary outputs\tag_list_current_run\formal_attribute_resolved\stage4_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\run_stage5_canonicalize.py --raw-mentions outputs\tag_list_current_run\formal_attribute_resolved\raw_mentions.jsonl --raw-edges outputs\tag_list_current_run\formal_attribute_resolved\raw_edges.jsonl --lexicon-dir outputs\tag_list_current_run\stage5_lexicons_attribute_manual_resolved --canonical-mentions outputs\tag_list_current_run\formal_attribute_resolved\canonical_mentions.jsonl --canonical-edges outputs\tag_list_current_run\formal_attribute_resolved\canonical_edges.jsonl --summary outputs\tag_list_current_run\formal_attribute_resolved\stage5_summary.jsonl --attribute-inventory outputs\tag_list_current_run\inventory_prior\gpic_tag_list_attribute_inventory_manual_resolved_canonical.tsv`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\run_stage6_export_counts.py --canonical-mentions outputs\tag_list_current_run\formal_attribute_resolved\canonical_mentions.jsonl --canonical-edges outputs\tag_list_current_run\formal_attribute_resolved\canonical_edges.jsonl --output-dir outputs\tag_list_current_run\formal_attribute_resolved\stage6 --summary outputs\tag_list_current_run\formal_attribute_resolved\stage6_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\build_caption_concept_md.py --sentence-rows outputs\tag_list_current_run\stage1\tag_rows.jsonl --stage3-records outputs\tag_list_current_run\stage3_tag_list\stage3_records.jsonl --canonical-mentions outputs\tag_list_current_run\formal_attribute_resolved\canonical_mentions.jsonl --canonical-edges outputs\tag_list_current_run\formal_attribute_resolved\canonical_edges.jsonl --facts outputs\tag_list_current_run\formal_attribute_resolved\stage6\facts.jsonl --output outputs\tag_list_current_run\formal_attribute_resolved\caption_to_concept_tag_list_formal_attribute_resolved.md --start 0 --limit 21 --max-object-pairs-per-caption 40`

Results:

- Manual overlay:
  - full rows: 28
  - overlaid rows: 10
  - merged decision status counts: `chosen=28`
- Attribute canonical enrichment:
  - rows: 28
  - canonical selected rows: 28
  - canonical ambiguous rows: 0
  - selected synset missing rows: 0
- Stage 5 lexicon export:
  - attribute synonym rows: 28
  - attribute type rows: 0
- Formal Stage 4:
  - raw mentions: 137
  - raw edges: 37
  - edge type counts: `has_attribute=37`
- Formal Stage 5:
  - `formal_attribute_inventory_gate=True`
  - canonical mentions: 137
  - canonical edges: 37
  - canonical source counts:
    `gpic_observed_inventory=93`, `lexicon=37`, `raw_fallback=7`
- Formal Stage 6:
  - fact total: 611
  - fact type counts:
    `entity_exists=95`, `attribute_exists=42`, `has_attribute=37`,
    `object_parent=97`, `object_pair_in_caption=340`
- Markdown report:
  - `outputs/tag_list_current_run/formal_attribute_resolved/caption_to_concept_tag_list_formal_attribute_resolved.md`

Interpretation:

- The tag-list attribute branch now follows the sentence workflow order.
- The previous preview Stage 5/6 output remains explicitly marked as preview
  and should not be used as formal output.

## 2026-07-12: Combined Sentence And Tag-list Report

Purpose:

- Present the existing sentence 100-caption formal output and the tag-list
  21-caption formal output in one report instead of keeping them as separate
  user-facing artifacts.
- Recompute Stage 6 counts over the combined canonical mention/edge JSONL
  rather than concatenating old count tables.

Inputs:

- Sentence rows:
  `outputs/case_reports_sentence100_0101_0200_current/sentence_rows_0101_0200.jsonl`
- Sentence canonical output:
  `outputs/case_reports_sentence100_0101_0200_acl_agent_current/canonical_mentions.jsonl`
  and `canonical_edges.jsonl`
- Tag-list rows:
  `outputs/tag_list_current_run/stage1/tag_rows.jsonl`
- Tag-list canonical output:
  `outputs/tag_list_current_run/formal_attribute_resolved/canonical_mentions.jsonl`
  and `canonical_edges.jsonl`

Generated combined inputs:

- `outputs/combined_sentence100_taglist21_current/caption_rows.jsonl`
- `outputs/combined_sentence100_taglist21_current/stage3_records.jsonl`
- `outputs/combined_sentence100_taglist21_current/canonical_mentions.jsonl`
- `outputs/combined_sentence100_taglist21_current/canonical_edges.jsonl`

Commands:

- `.\scripts\run_python.ps1 -c "... combine sentence/tag-list JSONL files ..."`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\run_stage6_export_counts.py --canonical-mentions outputs\combined_sentence100_taglist21_current\canonical_mentions.jsonl --canonical-edges outputs\combined_sentence100_taglist21_current\canonical_edges.jsonl --output-dir outputs\combined_sentence100_taglist21_current\stage6 --summary outputs\combined_sentence100_taglist21_current\stage6_summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\build_caption_concept_md.py --sentence-rows outputs\combined_sentence100_taglist21_current\caption_rows.jsonl --stage3-records outputs\combined_sentence100_taglist21_current\stage3_records.jsonl --canonical-mentions outputs\combined_sentence100_taglist21_current\canonical_mentions.jsonl --canonical-edges outputs\combined_sentence100_taglist21_current\canonical_edges.jsonl --facts outputs\combined_sentence100_taglist21_current\stage6\facts.jsonl --output outputs\combined_sentence100_taglist21_current\caption_to_concept_cases_sentence100_taglist21_combined.md --start 0 --limit 121 --max-object-pairs-per-caption 40`

Results:

- Combined caption rows: 121.
- Caption type counts:
  - `short=47`
  - `medium=38`
  - `long=15`
  - `tag=21`
- Stage 3 records: 121.
- Canonical mentions: 2392.
- Canonical edges: 1446.
- Combined Stage 6 facts: 23869.
- Combined Stage 6 fact type counts:
  - `entity_exists=1250`
  - `attribute_exists=673`
  - `action_event=432`
  - `event_role=486`
  - `relation=247`
  - `has_attribute=668`
  - `has_quantity=37`
  - `quantity_exists=37`
  - `object_parent=1207`
  - `relation_component=78`
  - `ambiguous_relation_candidate=4`
  - `object_pair_in_caption=18750`
- Combined report:
  - `outputs/combined_sentence100_taglist21_current/caption_to_concept_cases_sentence100_taglist21_combined.md`

Interpretation:

- This report combines already-formal sentence and tag-list outputs into one
  user-facing report.
- No new extraction rule was applied during the merge.
- Stage 6 count tables were recomputed from the combined canonical JSONL files,
  so count tables are not stale concatenations.

## 2026-07-12: Local Repo Copy And Copied `.mamba` Smoke Check

Purpose:

- Decide whether to keep the copied `.mamba` environment after moving the repo
  from the OneDrive-backed junction path to a real local Documents\Codex path.
- Verify that the new path does not keep reparse/junction state and that
  `scripts\run_python.ps1` uses the copied environment.

Paths:

- Source workspace path:
  `C:\Users\rlath\Documents\Codex\gpic-explainable-link`
- New local workspace path:
  `C:\Users\rlath\Documents\Codex\gpic-caption-concepts-explainable`
- Copy log:
  `C:\Users\rlath\Documents\Codex\gpic_repo_copy_20260712.log`

Commands:

- `robocopy $src $dst /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /MT:16 /NP /TEE /LOG:$log`
- `Get-ChildItem -Force -Recurse -Attributes ReparsePoint -LiteralPath $dst`
- `.\scripts\run_python.ps1 -c "import sys, os; print(sys.executable); print(os.getcwd())"`
- `.\scripts\run_python.ps1 scripts\check_runtime_env.py --spacy-model en_core_web_trf --require-spacy-gpu`
- `.\scripts\run_python.ps1 -c "from pathlib import Path; import nltk, wn; from nltk.corpus import wordnet as wn30; from wn.morphy import Morphy; root=Path.cwd(); wn.config.data_directory=str(root/'resources'/'wn_data'); nltk.data.path.insert(0, str(root/'resources'/'nltk_data')); oewn=wn.Wordnet('oewn:2025+', expand=''); print(oewn.synsets('dog', pos='n')[0].id); print(Morphy(oewn)('dogs', pos='n')); print(wn30.synsets('dog', pos=wn30.NOUN)[0].name())"`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_build_gpic_observed_object_inventory.py`
- `.\scripts\run_python.ps1 -m compileall scripts src`
- `git diff --check`

Results:

- Robocopy summary: copied 3,548 dirs, 37,672 files, 5.635 GB, failed 0.
- New local workspace reparse count: 0.
- Initial copied wrapper check showed `run_python.ps1` still preferred the old
  `gpic-explainable-link` fallback when that path existed.
- `scripts\run_python.ps1` was changed so the current repo root is used when it
  already contains `.mamba\env\python.exe` and `src`; legacy ASCII fallback is
  used only when the current root lacks a usable environment.
- After the wrapper fix, `run_python.ps1` printed:
  `C:\Users\rlath\Documents\Codex\gpic-caption-concepts-explainable\.mamba\env\python.exe`
- Runtime smoke:
  - Python 3.11.15 from copied `.mamba`
  - spaCy 3.8.14
  - `en_core_web_trf` loaded with pipes
    `transformer, tagger, parser, attribute_ruler, lemmatizer`
  - PyTorch CUDA available: true
  - CuPy installed and CUDA runtime device count: 1
  - GPU reported by runtime: NVIDIA GeForce RTX 5080 Laptop GPU
  - OEWN `oewn:2025+` lookup worked for `dog`
  - OEWN Morphy worked for `dogs -> dog`
  - NLTK WordNet 3.0 lookup worked for `dog.n.01`
- A first `check_runtime_env.py --require-spacy-gpu` run returned a transient
  `PermissionError` during model load, but direct GPU spaCy load and a repeated
  `check_runtime_env.py --require-spacy-gpu` run both succeeded.
- `test_build_gpic_observed_object_inventory.py`: 3 passed.
- `compileall scripts src`: succeeded.
- `git diff --check`: only existing CRLF/LF warnings; no whitespace errors.

Interpretation:

- The copied `.mamba` environment is usable from the new local repo path.
- There is no current need to create a fresh environment as the next step.

Residual risk:

- The transient first spaCy GPU model-load `PermissionError` was not root-caused.
  If it reappears, investigate GPU/CuPy/spaCy load ordering or Windows file
  access before recreating the environment.

## 2026-07-12: Formal Mixed Sentence/Tag-list Pipeline Runner

Purpose:

- Replace the manual post-hoc sentence/tag-list merge with a formal runner that
  produces one shared Stage 6 count set.
- Preserve the existing shape-specific extraction rules: sentence rows still
  use the sentence Stage 3 path, and tag-list rows still use the tag-list
  segment Stage 3 path.

Changed files:

- `scripts/run_mixed_caption_pipeline.py`
- `tests/test_mixed_caption_pipeline.py`
- `docs/rules_v1.md`

Implementation:

- Stage 1 writes `caption_records.jsonl`, `sentence_rows.jsonl`,
  `tag_rows.jsonl`, and `caption_rows_mixed.jsonl`.
- Stage 3 annotates sentence and tag-list rows through their existing
  shape-specific paths, then combines the resulting Stage 3 records back into
  original `caption_records` order.
- Stage 4, Stage 5, and Stage 6 run once over the combined Stage 3 / raw /
  canonical files.
- Stage 6 count tables are recomputed from combined canonical mentions/edges;
  the runner does not append old count TSV files.

Verification:

- `.\scripts\run_python.ps1 -m compileall scripts\run_mixed_caption_pipeline.py tests\test_mixed_caption_pipeline.py`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_mixed_caption_pipeline.py`

Results:

- `compileall`: succeeded.
- `test_mixed_caption_pipeline.py`: 2 passed in 5.359 seconds.

Interpretation:

- The new runner has a tested ordering guard for combining sentence/tag-list
  rows and Stage 3 records.
- This is an execution wrapper only; it does not add a new extraction,
  canonicalization, or count rule.

## 2026-07-12: Front 1k Object Manual Resolution Applied

Purpose:

- Apply the user-provided 1k object manual feedback to the mixed-runner object
  inventory.
- Keep canonical selection owned by the pipeline, not by the feedback sheet.

Inputs:

- `C:\Users\rlath\Downloads\gpic_observed_object_inventory_manual_processed_synset_rule.tsv`
- `outputs/front1000_mixed_current/inventory/gpic_observed_object_inventory.tsv`

Changed files:

- `scripts/apply_object_manual_resolution.py`
- `tests/test_apply_object_manual_resolution.py`

Implementation:

- Added an object manual overlay script matching the attribute/action overlay
  pattern.
- The feedback file uses `decision_status=accepted`; the script normalizes that
  to pipeline `decision_status=chosen`.
- Canonical columns from the feedback file are ignored/cleared. Object canonical
  is recalculated by `enrich_gpic_inventory_canonical.py`.
- Extra feedback/provenance columns are preserved in the output TSV.

Commands:

- `.\scripts\run_python.ps1 -m compileall scripts\apply_object_manual_resolution.py tests\test_apply_object_manual_resolution.py`
- `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_apply_object_manual_resolution.py`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\apply_object_manual_resolution.py --full-inventory outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory.tsv --resolved-subset C:\Users\rlath\Downloads\gpic_observed_object_inventory_manual_processed_synset_rule.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved.tsv --resolved-copy outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_processed_synset_rule_normalized.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolution_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\enrich_gpic_inventory_parents.py --input outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parents.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parents_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\enrich_gpic_inventory_canonical.py --input outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parents.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --ngram-evidence resources\source_labels\google_ngram_canonical_frequency_evidence.tsv --ambiguous-output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical_ambiguous.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\build_gpic_observed_attribute_inventory.py --input outputs\front1000_mixed_current\stage3\stage3_records.jsonl --object-inventory outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --attribute-inventory outputs\front1000_mixed_current\prior\gpic_observed_attribute_inventory_prior_merged.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_summary.json`

Results:

- `compileall`: failed with `PermissionError` writing `__pycache__` `.pyc`
  files. This was not used as the formal verification signal.
- `test_apply_object_manual_resolution.py`: 2 passed in 0.040 seconds.
- Object manual overlay:
  - full rows: 1788
  - overlaid rows: 440
  - original status counts: `chosen=1223`, `excluded=125`,
    `needs_manual=440`
  - merged status counts: `chosen=1663`, `excluded=125`
- Object parent enrichment:
  - parent filled rows: 1663
  - selected synset missing rows: 125
  - parent lookup errors: 0
- Object canonical enrichment:
  - canonical selected rows: 1663
  - canonical ambiguous rows: 0
  - selected synset missing rows: 125
- Attribute inventory for the same 1k run:
  - inventory rows: 771
  - status counts: `chosen=450`, `needs_manual=321`
  - needs-manual subset written to
    `outputs/front1000_mixed_current/inventory/gpic_observed_attribute_inventory_needs_manual.tsv`

Interpretation:

- The 1k object inventory is now resolved through parent and canonical.
- The next formal blocker is the 1k attribute inventory `needs_manual=321`.

## 2026-07-12: Front 1k Attribute Manual Resolution Applied

Purpose:

- Apply the user-provided 1k attribute manual synset feedback.
- Keep attribute canonical selection owned by the pipeline, not by the feedback
  sheet.

Inputs:

- `C:\Users\rlath\Downloads\gpic_observed_attribute_inventory_manual_processed_synset_rule.tsv`
- `outputs/front1000_mixed_current/inventory/gpic_observed_attribute_inventory.tsv`

Changed files:

- `scripts/apply_attribute_manual_resolution.py`
- `tests/test_apply_attribute_manual_resolution.py`

Implementation:

- `apply_attribute_manual_resolution.py` now normalizes feedback
  `decision_status=accepted` to pipeline `decision_status=chosen`.
- Extra feedback/provenance columns are preserved in the merged inventory.
- Canonical columns from the feedback file are still cleared before merge, so
  `enrich_gpic_attribute_inventory_canonical.py` remains the only canonical
  decision step.

Commands:

- `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_apply_attribute_manual_resolution.py`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\apply_attribute_manual_resolution.py --full-inventory outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory.tsv --resolved-subset C:\Users\rlath\Downloads\gpic_observed_attribute_inventory_manual_processed_synset_rule.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved.tsv --resolved-copy outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_processed_synset_rule_normalized.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolution_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 scripts\enrich_gpic_attribute_inventory_canonical.py --input outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved_canonical.tsv --ambiguous-output outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved_canonical_ambiguous.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved_canonical_summary.json`

Results:

- `test_apply_attribute_manual_resolution.py`: 2 passed in 0.120 seconds.
- Attribute manual overlay:
  - full rows: 771
  - overlaid rows: 321
  - original status counts: `chosen=450`, `needs_manual=321`
  - merged status counts: `chosen=771`
  - selected synset rows: 692
- Attribute canonical enrichment:
  - canonical selected rows: 688
  - selected synset missing rows: 79
  - canonical ambiguous rows: 4
  - ambiguous output:
    `outputs/front1000_mixed_current/inventory/gpic_observed_attribute_inventory_manual_resolved_canonical_ambiguous.tsv`

Canonical blockers:

- `E`
- `N`
- `S`
- `sautéed`

Interpretation:

- The 1k attribute synset inventory is resolved.
- The next formal blocker is attribute canonical ambiguity for 4 rows.
- Stage 4/5/6 must not run for this 1k batch until those 4 canonical rows are
  resolved.

## 2026-07-12 - Front 1k Object Corrected Manual Overlay

Purpose:

- Re-apply the corrected object manual feedback after the earlier joined-variant
  false positives such as `black shirt`, `blue jacket`, and `white cap`.
- Preserve the rule that a manual surface/head correction must be checked
  against OEWN again before later stages.

Inputs:

- `C:\Users\rlath\Downloads\gpic_observed_object_inventory_manual_resolved_corrected.tsv`
- `outputs/front1000_mixed_current/inventory/gpic_observed_object_inventory.tsv`

Changed files:

- `scripts/apply_object_manual_resolution.py`
- `tests/test_apply_object_manual_resolution.py`

Implementation:

- `apply_object_manual_resolution.py` now detects corrected manual rows such as
  `manual_resolution_type=canonical_head_no_selected_synset`.
- For those rows, it runs OEWN lookup on the corrected `selected_query` instead
  of silently accepting a blank `selected_oewn_synset`.
- If the corrected head query still cannot be auto-selected safely, the row is
  kept as `needs_manual`.

Commands:

- `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_apply_object_manual_resolution.py`
- `.\scripts\run_python.ps1 -m compileall scripts\apply_object_manual_resolution.py tests\test_apply_object_manual_resolution.py`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\apply_object_manual_resolution.py --full-inventory outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory.tsv --resolved-subset C:\Users\rlath\Downloads\gpic_observed_object_inventory_manual_resolved_corrected.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved.tsv --resolved-copy outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_corrected_normalized.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolution_summary.json`

Results:

- `test_apply_object_manual_resolution.py`: 4 passed in 0.090 seconds.
- Object manual overlay:
  - full rows: 1788
  - overlaid rows: 440
  - head relookup rows: 16
  - head relookup rows still needing manual: 2
  - merged status counts: `chosen=1661`, `excluded=125`, `needs_manual=2`
- Remaining object manual output:
  `outputs/front1000_mixed_current/inventory/gpic_observed_object_inventory_corrected_remaining_needs_manual.tsv`

Remaining blockers:

- `round table` -> `table`: OEWN auto-selected `noun.group`, still
  conditional/manual.
- `star sign` -> `sign`: OEWN auto-selected `noun.communication`, still
  conditional/manual.

Interpretation:

- Joined false positives such as `black shirt`, `blue jacket`, and `white cap`
  are no longer accepted as `blackshirt`, `bluejacket`, or `whitecap`.
- Parent/canonical enrichment and Stage 4+ must wait until the two remaining
  object manual rows are resolved.

## 2026-07-12 - Previous Object Inventory Joined/Head Audit

Purpose:

- Check whether the same joined-variant/head-correction mistake existed in
  earlier 20-caption, 100-caption, or tag-list object inventories.

Files checked:

- `outputs/case_reports_sentence20_current/gpic_observed_object_inventory_redecided_from_manual_review.tsv`
- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv`
- `outputs/tag_list_current_run/inventory_prior/gpic_tag_list_object_inventory_manual_resolved_parent_canonical.tsv`
- `outputs/tag_list_current_run/inventory/gpic_tag_list_object_inventory.tsv`

Audit criteria:

- Final/chosen rows with `selected_lookup_case` containing `joined_variant`.
- Rows whose decision reason mentions joined/head/surface mismatch correction.
- Final rows where a corrected surface/head has no selected synset.

Findings:

- 20-caption inventory:
  - `seed pods -> seedpod`: likely legitimate lexicalized joined/Morphy case.
- Tag-list inventory:
  - `street light -> streetlight`: likely legitimate lexicalized MWE.
  - `wine glasses -> wineglass`: likely legitimate lexicalized/Morphy case.
- 100-caption inventory:
  - `court house -> courthouse`: likely legitimate lexicalized MWE.
  - `black cap -> cap`: selected synset ID is the cap artifact sense; metadata
    still shows stale `selected_lookup_case=joined_variant` /
    `selected_query=blackcap`.
  - `long legs -> leg`: selected synset ID is the body-part `leg` sense;
    metadata still has stale joined/head evidence.
  - `white feathers -> feather`: selected synset ID is `feather`; this follows
    the head-correction rule.
  - `white line -> line`: selected synset ID is the shape `line` sense; review
    if the intended object policy should keep line-like shapes.
  - `black top -> top`: selected synset ID is `top`, but OEWN definition is
    "platform surrounding the head of a lower mast"; this looks like a real
    remaining manual/sense issue, not just metadata.

Interpretation:

- The same severe `blackshirt`/`bluejacket`/`whitecap` false-positive pattern is
  not broadly present in the earlier tag-list or 20-caption outputs.
- The 100-caption output has one likely semantic object issue (`black top`) and
  several stale-provenance rows where the selected synset is already the head
  sense but `selected_query`/`selected_lookup_case` still reflect the old joined
  candidate.

## 2026-07-12 - Reopened Ambiguous Previous Object Rows

Purpose:

- Reopen only the ambiguous/problematic rows found in the previous object
  inventory audit.
- Do not reopen lexicalized joined forms such as `street light -> streetlight`,
  `wine glasses -> wineglass`, `court house -> courthouse`, or
  `seed pods -> seedpod`.

Changed files:

- `scripts/reopen_inventory_rows.py`

Updated inventory files:

- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_object_inventory_manual_resolved.tsv`
- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_object_inventory_manual_resolved_parents.tsv`
- `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv`

Command:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 60 -- scripts\reopen_inventory_rows.py --inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved.tsv --inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parents.tsv --inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --decision "black top=reopened_after_joined_head_audit_selected_top_synset_is_mast_platform" --decision "white line=reopened_after_joined_head_audit_line_shape_policy_needs_manual" --audit-output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_reopened_needs_manual.tsv --source 2026-07-12_joined_head_audit`

Results:

- Reopened rows:
  - `black top`
  - `white line`
- Audit output:
  `outputs/case_reports_sentence100_0101_0200_current/gpic_observed_object_inventory_reopened_needs_manual.tsv`
- Final selected/query/canonical/parent fields were cleared on reopened rows.
- Previous selected values were preserved in `previous_*` fields.
- Readiness gate on
  `gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv` now
  reports exactly two blockers, both with
  `blocker_reason=pending_manual_decision_status`.

Interpretation:

- The older 100-caption Stage 4+ outputs derived from this inventory should be
  treated as stale until these two object rows are manually resolved and the
  downstream stages are rerun.

## 2026-07-12 - Final Object Manual Decisions For Reopened Rows

Purpose:

- Apply the final user decisions for the remaining corrected object rows.
- Keep explicit no-synset head fallback valid only when the manual decision says
  to remove a modifier and keep the head surface without a selected OEWN synset.

Inputs:

- `round table -> table`, `oewn-04386330-n`, physical furniture table sense.
- `star sign -> sign`, `oewn-04224949-n`, physical signboard/artifact sense.
- `black top -> top`, no selected synset; discard the `blacktop` asphalt sense.
- `white line -> white line`, `oewn-13886392-n`, exact shape sense.

Changed files:

- `scripts/resolve_object_inventory_rows.py`
- `src/gpic_concepts_v1/inventory_validation.py`
- `scripts/enrich_gpic_inventory_canonical.py`
- `tests/test_inventory_validation.py`
- `tests/test_enrich_gpic_inventory_canonical.py`

Implementation:

- Added a small explicit manual-resolution path for no-synset head fallbacks.
- `black top` can now remain `decision_status=chosen` with
  `selected_query=top`, blank `selected_oewn_synset`, and
  `canonical_surface=top`.
- Canonical enrichment preserves that explicit fallback instead of clearing the
  canonical surface as an unresolved no-synset row.
- General unresolved/no-synset rows are still blocked or marked not applicable;
  this path applies only to explicit manual head fallback rows.

Commands:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\resolve_object_inventory_rows.py --inventory outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved.tsv --decision "round table=>table=>oewn-04386330-n=>table=>manual_select_physical_table_furniture_sense" --decision "star sign=>sign=>oewn-04224949-n=>sign=>manual_select_physical_signboard_artifact_sense" --audit-output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_final_manual_decisions.tsv --source 2026-07-12_user_feedback`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 -- scripts\enrich_gpic_inventory_parents.py --input outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parents.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parents_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 -- scripts\enrich_gpic_inventory_canonical.py --input outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parents.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --ngram-evidence resources\source_labels\google_ngram_canonical_frequency_evidence.tsv --ambiguous-output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical_ambiguous.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\resolve_object_inventory_rows.py --inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved.tsv --inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parents.tsv --inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --decision "black top=>top=>=>top=>manual_head_fallback_blacktop_synset_discarded" --decision "white line=>white line=>oewn-13886392-n=>white line=>manual_keep_exact_white_line_shape_synset" --audit-output outputs\case_reports_sentence100_0101_0200_current\gpic_observed_object_inventory_reopened_manual_resolved.tsv --source 2026-07-12_user_feedback`

Results:

- Front 1k object inventory:
  - `round table`: chosen, `selected_query=table`,
    `selected_oewn_synset=oewn-04386330-n`, `noun.artifact`,
    `canonical_surface=table`.
  - `star sign`: chosen, `selected_query=sign`,
    `selected_oewn_synset=oewn-04224949-n`, `noun.artifact`,
    `canonical_surface=sign`.
  - Parent enrichment: `parent_filled_rows=1663`,
    `selected_synset_missing_rows=125`, `parent_lookup_error_rows=0`.
  - Canonical enrichment: `canonical_selected_rows=1663`,
    `canonical_ambiguous_rows=0`, `selected_synset_missing_rows=125`.
- Previous 100-caption object inventory:
  - `black top`: chosen, `selected_query=top`, blank selected synset,
    `canonical_surface=top`,
    `manual_resolution_type=canonical_head_no_selected_synset`.
  - `white line`: chosen, `selected_query=white line`,
    `selected_oewn_synset=oewn-13886392-n`, `noun.shape`,
    `canonical_surface=white line`.
- Readiness checks:
  - Front 1k parent/canonical inventory blockers: 0.
  - Previous 100-caption parent/canonical inventory blockers: 0.

Verification:

- AST parse for changed Python files passed.
- `test_inventory_validation.py`: 6 passed.
- `test_enrich_gpic_inventory_canonical.py`: 5 passed.
- `compileall` was attempted, but failed with the known `__pycache__`
  `PermissionError` in this Windows/junction workspace. This was not treated as
  a syntax failure because AST parse and targeted tests passed.

Interpretation:

- The front 1k object inventory is no longer blocked by `round table` or
  `star sign`.
- The previous 100-caption object inventory is no longer blocked by `black top`
  or `white line`.
- Downstream reruns should use these updated inventory files rather than the
  stale pre-resolution outputs.

## 2026-07-12 - Front 1k Attribute Canonical Reprocessing

Purpose:

- Reprocess the front 1k attribute inventory after the object correction pass.
- Fix canonical ambiguity caused by treating `selected_query` as an observed
  exact surface and by not applying the existing diacritic-folded canonical key
  at the exact-surface tie-break step.

Changed files:

- `scripts/enrich_gpic_inventory_canonical.py`
- `tests/test_enrich_gpic_inventory_canonical.py`
- `docs/rules_v1.md`
- `docs/rule_change_review_log_v1.md`

Implementation:

- Exact observed-surface tie breaking now uses actual observed caption surfaces
  only: `observed_surface` and `example_surfaces`.
- Lookup-only `selected_query` still helps candidate discovery, but it is no
  longer treated as an observed exact surface.
- If display exact surface does not choose one lemma, the canonical helper can
  choose the unique selected-synset lemma whose diacritic-folded matching key
  equals the raw observed surface key.

Commands:

- `.\scripts\run_python.ps1 -c "import ast, pathlib; files=['scripts/enrich_gpic_inventory_canonical.py','scripts/enrich_gpic_attribute_inventory_canonical.py','tests/test_enrich_gpic_inventory_canonical.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8'), filename=f) for f in files]; print('ast ok', len(files))"`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_enrich_gpic_inventory_canonical.py`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p test_enrich_gpic_attribute_inventory_canonical.py`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\apply_attribute_manual_resolution.py --full-inventory outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory.tsv --resolved-subset C:\Users\rlath\Downloads\gpic_observed_attribute_inventory_manual_processed_synset_rule.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved.tsv --resolved-copy outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_processed_synset_rule_normalized.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolution_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 -- scripts\enrich_gpic_attribute_inventory_canonical.py --input outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved_canonical.tsv --ngram-evidence resources\source_labels\google_ngram_canonical_frequency_evidence.tsv --ambiguous-output outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved_canonical_ambiguous.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved_canonical_summary.json`
- `.\scripts\run_python.ps1 -c "from pathlib import Path; from scripts.run_stage5_canonicalize import _raise_if_attribute_inventory_not_ready; p=Path('outputs/front1000_mixed_current/inventory/gpic_observed_attribute_inventory_manual_resolved_canonical.tsv'); _raise_if_attribute_inventory_not_ready(p); print('attribute stage5 gate ok')"`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\export_attribute_stage5_lexicons.py --attribute-inventory outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_manual_resolved_canonical.tsv --output-dir outputs\front1000_mixed_current\lexicons --base-lexicon-dir resources\lexicons --summary outputs\front1000_mixed_current\inventory\attribute_stage5_lexicon_export_summary.json`

Results:

- AST parse passed: `ast ok 3`.
- `test_enrich_gpic_inventory_canonical.py`: 7 passed.
- `test_enrich_gpic_attribute_inventory_canonical.py`: 6 passed.
- Manual overlay:
  - full rows: 771
  - original status counts: `chosen=450`, `needs_manual=321`
  - merged status counts: `chosen=771`
  - selected synset rows: 692
- Canonical enrichment:
  - rows: 771
  - canonical selected rows: 692
  - selected synset missing rows: 79
  - canonical ambiguous rows: 0
  - canonical lookup errors: 0
  - `E`, `N`, and `S` now keep uppercase canonical surfaces.
  - `sautéed` now canonicalizes to `sauteed` through the diacritic-folded
    observed surface key.
- Stage 5 attribute inventory gate: passed.
- Stage 5 lexicon export:
  - output dir: `outputs/front1000_mixed_current/lexicons`
  - `attribute_synonym_rows=692`
  - `chosen_synonym_rows_added=692`
  - `attribute_type_rows=0`

Interpretation:

- The front 1k attribute inventory is now resolved through canonical enrichment.
- The current blocker is no longer attribute canonical ambiguity.
- The run has a Stage 5 lexicon bundle ready for the next formal pipeline step.

## 2026-07-12 - Front 1k Attribute Rebuild After Object Corrections

Purpose:

- Rebuild the front 1k attribute inventory after corrected object decisions
  changed consumed object spans and exposed additional modifiers.
- Apply the final one-row manual attribute decision for `star`.
- Produce a corrected-object Stage 5 attribute lexicon bundle without treating
  the older pre-object-rebuild attribute lexicon as current.

Manual decision:

- `star`: select `oewn-13904301-n`, `noun.shape`; parent taxonomy label
  `shape`.
- The manual subset records this as an explicit user-approved decision, while
  canonical surface is recomputed by the canonical enrichment script.

Commands:

- `.\scripts\run_python.ps1 -c "... write gpic_observed_attribute_inventory_rebuilt_after_object_star_manual.tsv ..."`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\apply_attribute_manual_resolution.py --full-inventory outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object.tsv --resolved-subset outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_star_manual.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved.tsv --resolved-copy outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_star_manual_normalized.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolution_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 240 -- scripts\enrich_gpic_attribute_inventory_canonical.py --input outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical.tsv --ngram-evidence resources\source_labels\google_ngram_canonical_frequency_evidence.tsv --ambiguous-output outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical_ambiguous.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical_summary.json`
- `.\scripts\run_python.ps1 -c "from pathlib import Path; from scripts.run_stage5_canonicalize import _raise_if_attribute_inventory_not_ready; p=Path('outputs/front1000_mixed_current/inventory/gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical.tsv'); _raise_if_attribute_inventory_not_ready(p); print('attribute stage5 gate ok')"`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\export_attribute_stage5_lexicons.py --attribute-inventory outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical.tsv --output-dir outputs\front1000_mixed_current\lexicons_after_object_rebuild --base-lexicon-dir resources\lexicons --summary outputs\front1000_mixed_current\inventory\attribute_stage5_lexicon_export_after_object_rebuild_summary.json`

Results:

- Rebuilt inventory before overlay:
  - rows: 773
  - original status counts: `chosen=772`, `needs_manual=1`
  - only pending row: `star`
- Manual overlay:
  - overlaid rows: 1
  - merged status counts: `chosen=773`
  - selected synset rows: 694
- Canonical enrichment:
  - rows: 773
  - canonical selected rows: 694
  - selected synset missing rows: 79
  - canonical ambiguous rows: 0
  - canonical lookup errors: 0
  - `star` canonical surface: `star`
- Stage 5 attribute inventory gate: passed.
- Stage 5 lexicon export:
  - output dir: `outputs/front1000_mixed_current/lexicons_after_object_rebuild`
  - `attribute_synonym_rows=694`
  - `chosen_synonym_rows_added=694`
  - `attribute_type_rows=0`

Interpretation:

- The corrected-object front 1k attribute inventory has no remaining manual or
  canonical blockers.
- Downstream formal runs that depend on the object-corrected attribute
  inventory should use
  `outputs/front1000_mixed_current/lexicons_after_object_rebuild`, not the older
  `outputs/front1000_mixed_current/lexicons` bundle from the pre-rebuild
  attribute inventory.

## 2026-07-12 - Front 1k Preposition-MWE-Aware Action Inventory

Purpose:

- Continue from corrected-object/attribute Stage 3.5 into the pre-Stage4
  action inventory preparation for the front 1k mixed caption run.
- Apply active preposition MWE span detection before action candidate
  generation, matching the Stage 4 R18.1/R15 ordering.
- Reuse the latest resolved sentence-100 action canonical inventory where exact
  span keys match, then queue only unresolved action decisions.

Implementation correction:

- `scripts/build_gpic_observed_action_inventory.py` first failed after the R15
  helper signature changed to require `excluded_token_indices`.
- The correct fix is not an empty set: the action inventory builder now detects
  active preposition MWE spans first and passes their consumed token ids to
  `_action_candidates_from_token_record()`.
- This aligns the offline action inventory builder with the documented
  Stage 4 ordering: preposition MWE span detection before phrasal action
  candidate selection.

Commands:

- `.\scripts\run_python.ps1 -c "import ast, pathlib; path=pathlib.Path('scripts/build_gpic_observed_action_inventory.py'); ast.parse(path.read_text(encoding='utf-8'), filename=str(path)); print('ast ok')"`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_build_gpic_observed_action_inventory.py"`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_formal_inventory_gates.py"`
- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_mixed_caption_pipeline.py"`
- `.\scripts\run_python.ps1 -c "import ast, pathlib; files=['scripts/build_gpic_observed_action_inventory.py','scripts/run_stage4_extract_raw.py','scripts/run_mixed_caption_pipeline.py','tests/test_build_gpic_observed_action_inventory.py','tests/test_formal_inventory_gates.py','tests/test_mixed_caption_pipeline.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8'), filename=f) for f in files]; print('ast ok', len(files))"`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 -- scripts\build_gpic_observed_action_inventory.py --input outputs\front1000_mixed_current\stage3\stage3_records.jsonl --action-inventory outputs\case_reports_sentence100_0101_0200_current\gpic_observed_action_inventory_canonical.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory.tsv --needs-manual-output outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_needs_manual.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_summary.json`

Results:

- AST parse: passed for 6 files.
- `test_build_gpic_observed_action_inventory.py`: 1 passed.
- `test_formal_inventory_gates.py`: 9 passed.
- `test_mixed_caption_pipeline.py`: 3 passed.
- Action inventory:
  - caption total: 1000
  - verb token total: 2464
  - preposition MWE matches before action candidate selection: 114
  - preposition MWE consumed token ids before action candidate selection: 336
  - inventory rows: 563
  - decision status counts: `chosen=531`, `needs_manual=24`,
    `raw_fallback=8`
  - candidate type counts: `verb=474`, `verb_prep=73`, `verb_prt=15`,
    `verb_prt_prep=1`
  - needs-manual output:
    `outputs/front1000_mixed_current/inventory/gpic_observed_action_inventory_needs_manual.tsv`

Interpretation:

- The front 1k run is now blocked at action manual resolution.
- Formal Stage 4/5/6 should not run until the 24 action `needs_manual` rows are
  resolved and action canonical enrichment/export is complete.

## 2026-07-13 - Front 1k Action Manual Resolution To Stage 6

Purpose:

- Apply the user-provided action manual decision file for the current front 1k
  mixed caption run.
- Confirm the action manual gate and action canonical gate are clear.
- Re-run formal Stage 4, Stage 5, and Stage 6 with the corrected action
  inventory and action Stage 5 lexicon.

Commands:

- `.\scripts\run_tests.ps1 --timeout-seconds 120 discover -s tests -p "test_apply_action_manual_resolution.py"`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\apply_action_manual_resolution.py --full-inventory outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory.tsv --manual-decisions "C:\Users\rlath\Downloads\gpic_observed_action_inventory_manual_decisions(4).tsv" --output outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved.tsv --resolved-output outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved_subset.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolution_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 -- scripts\enrich_gpic_action_inventory_canonical.py --input outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved_canonical.tsv --ambiguous-output outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved_canonical_ambiguous.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved_canonical_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\export_attribute_stage5_lexicons.py --attribute-inventory outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical.tsv --action-canonical-inventory outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved_canonical.tsv --output-dir outputs\front1000_mixed_current\lexicons_after_action_manual_resolved --base-lexicon-dir outputs\front1000_mixed_current\lexicons_after_object_rebuild --summary outputs\front1000_mixed_current\inventory\attribute_action_stage5_lexicon_export_after_action_manual_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 -- scripts\run_stage4_extract_raw.py --input outputs\front1000_mixed_current\stage3\stage3_records.jsonl --object-inventory outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --action-inventory outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved.tsv --raw-mentions outputs\front1000_mixed_current\stage4_after_action_manual_resolved\raw_mentions.jsonl --raw-edges outputs\front1000_mixed_current\stage4_after_action_manual_resolved\raw_edges.jsonl --summary outputs\front1000_mixed_current\stage4_after_action_manual_resolved\summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 -- scripts\run_stage5_canonicalize.py --raw-mentions outputs\front1000_mixed_current\stage4_after_action_manual_resolved\raw_mentions.jsonl --raw-edges outputs\front1000_mixed_current\stage4_after_action_manual_resolved\raw_edges.jsonl --lexicon-dir outputs\front1000_mixed_current\lexicons_after_action_manual_resolved --attribute-inventory outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical.tsv --canonical-mentions outputs\front1000_mixed_current\stage5_after_action_manual_resolved\canonical_mentions.jsonl --canonical-edges outputs\front1000_mixed_current\stage5_after_action_manual_resolved\canonical_edges.jsonl --summary outputs\front1000_mixed_current\stage5_after_action_manual_resolved\summary.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 -- scripts\run_stage6_export_counts.py --canonical-mentions outputs\front1000_mixed_current\stage5_after_action_manual_resolved\canonical_mentions.jsonl --canonical-edges outputs\front1000_mixed_current\stage5_after_action_manual_resolved\canonical_edges.jsonl --output-dir outputs\front1000_mixed_current\stage6_after_action_manual_resolved --summary outputs\front1000_mixed_current\stage6_after_action_manual_resolved\summary.jsonl`

Results:

- `test_apply_action_manual_resolution.py`: 3 passed.
- Action manual overlay:
  - full rows: 563
  - overlaid rows: 19
  - original status counts: `chosen=536`, `needs_manual=19`,
    `raw_fallback=8`
  - merged status counts: `chosen=555`, `raw_fallback=8`
  - action inventory sidecar: `status=resolved`, `needs_manual_rows=0`,
    `action_inventory_preposition_mwe_aware=true`
- Action canonical enrichment:
  - rows: 563
  - canonical selected rows: 555
  - raw fallback not-applicable rows: 8
  - canonical ambiguous rows: 0
  - canonical lookup errors: 0
- Stage 5 lexicon export:
  - output dir:
    `outputs/front1000_mixed_current/lexicons_after_action_manual_resolved`
  - action synonym rows added: 555
  - action raw fallback rows skipped: 8
  - attribute synonym rows: 694
- Stage 4:
  - total captions: 1000
  - raw mentions: 14317
  - raw edges: 8357
  - mention type counts: `object=7619`, `attribute=4044`,
    `action=2422`, `quantity=232`
  - edge type counts: `has_attribute=3998`, `event_role=2745`,
    `relation=1352`, `has_quantity=232`,
    `ambiguous_relation_candidate=30`
- Stage 5:
  - canonical mentions: 14317
  - canonical edges: 8357
  - `formal_attribute_inventory_gate=true`
  - canonical source counts: `gpic_observed_inventory=7059`,
    `lexicon=6334`, `raw_fallback=924`
- Stage 6:
  - fact total: 87500
  - action count rows: 359
  - object count rows: 1474
  - attribute count rows: 794
  - relation triple rows: 1058
  - ambiguous relation candidate rows: 9
  - object co-occurrence pair rows: 39076

Spot checks:

- `shining` and `shines` count under canonical action `shine`.
- `installed` counts under `install`.
- `taped` counts under `tape`.
- `sitting in`, `sits in`, and `sit in` count under `sit in`.

Interpretation:

- The current front 1k action manual blocker is clear.
- The front 1k mixed caption run now has formal Stage 4/5/6 artifacts after
  action manual resolution.

## 2026-07-13 - Object/Attribute Surface-Changing Lookup Guard

Purpose:

- Remove automatic object/attribute prior reuse by `selected_query`.
- Prevent observed exact surface and lemma/Morphy/normalization lookup from
  silently choosing different synsets.
- Rebuild the front-1000 object inventory far enough to identify new object
  manual blockers before any new Stage 4/5/6 run.

Commands:

- `.\scripts\run_python.ps1 -c "import ast, pathlib; files=['src/gpic_concepts_v1/stage4_extract_raw.py','scripts/build_gpic_observed_object_inventory.py','scripts/build_gpic_observed_attribute_inventory.py','tests/test_build_gpic_observed_object_inventory.py','tests/test_build_gpic_observed_attribute_inventory.py','tests/test_stage4_extract_raw.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8'), filename=f) for f in files]; print('AST_OK')"`
- `.\scripts\run_python.ps1 -m unittest tests.test_build_gpic_observed_object_inventory tests.test_build_gpic_observed_attribute_inventory tests.test_stage4_extract_raw tests.test_formal_inventory_gates`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 300 -- scripts\build_gpic_observed_object_inventory.py --input outputs\front1000_mixed_current\stage3\stage3_records.jsonl --prior-object-inventory outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_surface_conflict_guard.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_surface_conflict_guard_summary.json`

Results:

- AST parse: passed.
- Focused regression tests: 75 passed.
- Rebuilt object inventory:
  - output:
    `outputs/front1000_mixed_current/inventory/gpic_observed_object_inventory_surface_conflict_guard.tsv`
  - `chosen=1626`, `excluded=125`, `needs_manual=37`
  - all 37 pending rows have
    `decision_reason=manual_surface_query_conflict_required`
  - needs-manual subset:
    `outputs/front1000_mixed_current/inventory/gpic_observed_object_inventory_surface_conflict_guard_needs_manual.tsv`

Interpretation:

- The previous front-1000 Stage 4/5/6 artifacts generated after action manual
  resolution are invalid for object/attribute analysis under the current rule.
- Do not run attribute rebuild, Stage 4, Stage 5, or Stage 6 until the 37 object
  `needs_manual` rows are resolved and object canonical/parent enrichment is
  regenerated.

## 2026-07-13 - Corrected Front-1000 Object Inventory Source

Purpose:

- Replace the stale front-1000 object source inventory that contained bad
  automatic surface-changing selections, such as `glasses -> glass`.
- Apply the manually resolved 37 object surface-query conflict rows, then
  regenerate parent, canonical, and selected synset metadata evidence.

Commands:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\apply_object_manual_resolution.py --full-inventory outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_surface_conflict_guard.tsv --resolved-subset "C:\Users\rlath\Downloads\gpic_observed_object_inventory_surface_conflict_guard_manual_resolved.tsv" --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_surface_conflict_guard_manual_resolved.tsv --resolved-copy outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_surface_conflict_guard_manual_resolved_subset.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_surface_conflict_guard_manual_resolution_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\enrich_gpic_inventory_parents.py --input outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_surface_conflict_guard_manual_resolved.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parents.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parents_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 180 scripts\enrich_gpic_inventory_canonical.py --input outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parents.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parent_canonical.tsv --ngram-evidence resources\source_labels\google_ngram_canonical_frequency_evidence.tsv --ambiguous-output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parent_canonical_ambiguous.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parent_canonical_summary.json`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 scripts\enrich_gpic_inventory_synset_metadata.py --input outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parent_canonical.tsv --output outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parent_canonical_metadata.tsv --summary outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parent_canonical_metadata_summary.json`
- `.\scripts\run_python.ps1 -c "... m._raise_if_object_inventory_not_ready(Path('outputs/front1000_mixed_current/inventory/gpic_observed_object_inventory_corrected_source_parent_canonical_metadata.tsv')); print('OBJECT_INVENTORY_STAGE4_GATE_OK')"`
- `.\scripts\run_tests.ps1 --timeout-seconds 30 -- discover -s tests -p test_enrich_gpic_inventory_synset_metadata.py`

Results:

- Manual overlay:
  - `chosen=1663`, `excluded=125`, `needs_manual=0`
  - overlaid rows: 37
- Parent enrichment:
  - parent filled rows: 1663
  - selected synset missing rows: 125
  - lookup errors: 0
- Canonical enrichment:
  - canonical selected rows: 1663
  - canonical ambiguous rows: 0
  - selected synset missing rows: 125
- Selected synset metadata refresh:
  - selected synset rows: 1663
  - changed cells: 543
  - lookup errors: 0
- Final corrected object inventory:
  - `outputs/front1000_mixed_current/inventory/gpic_observed_object_inventory_corrected_source_parent_canonical_metadata.tsv`
  - Stage 4 object inventory gate: passed
  - `glasses` is now `selected_query=glasses`,
    `selected_oewn_synset=oewn-04279164-n`,
    `selected_oewn_lexfile=noun.artifact`, `canonical_surface=glasses`
- Metadata regression test:
  - `test_enrich_gpic_inventory_synset_metadata.py`: 1 passed

Interpretation:

- The stale `gpic_observed_object_inventory_manual_resolved_parent_canonical.tsv`
  should not be used as the front-1000 object source for new formal runs.
- Use the corrected metadata inventory above for the next attribute rebuild and
  any following formal Stage 4/5/6 run.

## 2026-07-13 - Front-1000 Mixed Pipeline Timed Rerun With Scoped Surface-Conflict Guard

Purpose:

- Rerun the first 1,000 GPIC captions with the current scoped object/attribute
  surface-conflict rule.
- Record GPU mode and per-stage mixed pipeline timing in the output summary.

GPU/runtime evidence:

- `nvidia-smi` before the run: RTX 5080 Laptop GPU, driver 592.01, CUDA 13.1,
  P8 idle, about 4W / 73W, 21 MiB used.
- Runtime env: CuPy installed, Torch CUDA available, spaCy `prefer_gpu=true`.
- Mixed pipeline Stage 3 summaries reported `gpu_enabled=true`.

Commands:

- Rehydrated the original front-1000 Stage 1 caption records into GPIC row
  input:
  `outputs/front1000_current_guard_scoped_input/gpic_rows_front1000.jsonl`
- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 900 -- scripts\run_mixed_caption_pipeline.py --input outputs\front1000_current_guard_scoped_input\gpic_rows_front1000.jsonl --output-dir outputs\front1000_guard_scoped_timed_20260713_0300 --object-inventory outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parent_canonical_metadata.tsv --attribute-inventory outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical.tsv --action-inventory outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved.tsv --lexicon-dir outputs\front1000_mixed_current\lexicons_after_action_manual_resolved --preposition-mwe-lexicon resources\lexicons\preposition_mwes.tsv --prefer-gpu --batch-size 128`

Results:

- Output dir:
  `outputs/front1000_guard_scoped_timed_20260713_0300`
- Captions: 1,000 total = 797 sentence + 203 tag-list.
- Wrapper wall time: 25.635 seconds.
- Internal mixed pipeline time: 18.533390 seconds.
- Internal throughput: 53.956670 captions/sec.
- Stage timing:
  - Stage 1 records: 0.045627 sec
  - Stage 1 mixed caption rows: 0.052518 sec
  - Stage 3 model load: 1.640845 sec
  - Stage 3 sentence: 4.918622 sec
  - Stage 3 tag-list: 3.482393 sec
  - Stage 3 combine: 0.305102 sec
  - Stage 4 lookup load: 0.057841 sec
  - Stage 4 raw extraction: 1.965609 sec
  - Stage 5 canonicalize: 1.496635 sec
  - Stage 6 count export: 4.552036 sec
- Stage 6 facts: 87,506 total.

Notes:

- The first attempted run used the canonical action inventory as the Stage 4
  action inventory and was correctly blocked by pipeline state validation.
  The successful run used
  `gpic_observed_action_inventory_manual_resolved.tsv` for Stage 4 and the
  action-canonical exported lexicon bundle for Stage 5.
- `scripts/run_mixed_caption_pipeline.py` now records `timing_seconds` and
  `timing_throughput` in `mixed_pipeline_summary.jsonl` and `pipeline_state.json`.

## 2026-07-13 - Real 10K Mixed Formal Pipeline Run

Purpose:

- Process the GPIC train 10K shard with the current formal mixed
  sentence/tag-list runner.
- Confirm that tag-list captions are not accidentally excluded.

Input correction:

- The first 10K attempt used
  `outputs/benchmark_real10k_train/sentence_rows_9896.jsonl.gz`.
- That file is a sentence-only benchmark input, so Stage 1 reported
  `sentence=9896` and `tag_list=0`.
- The corrected run used the original mixed GPIC row shard:
  `C:\Users\rlath\OneDrive\Desktop\PILAB\0. 연구과제\기영님 연구과제(blue maze)\caption to concept\gpic-caption-concepts\data\gpic_captions_10k_train00000_00099\train\gpic_train_00000_00099_merged_10000.jsonl.gz`.

Preflight:

- Stage 1 check over the corrected shard:
  - total rows: 10,000
  - sentence captions: 9,896
  - tag-list captions: 104
  - skipped rows: 0
- `nvidia-smi` before the corrected run:
  - RTX 5080 Laptop GPU
  - P5
  - about 20.34 W
  - 890 MiB / 16,303 MiB used
  - GPU util about 23%
- `nvidia-smi` after the corrected run:
  - P5
  - about 20.94 W
  - 897 MiB / 16,303 MiB used
  - GPU util about 23%

Command:

- `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 2400 -- scripts\run_mixed_caption_pipeline.py --input "C:\Users\rlath\OneDrive\Desktop\PILAB\0. 연구과제\기영님 연구과제(blue maze)\caption to concept\gpic-caption-concepts\data\gpic_captions_10k_train00000_00099\train\gpic_train_00000_00099_merged_10000.jsonl.gz" --output-dir outputs\real10k_mixed_guard_scoped_timed_20260713_1240 --object-inventory outputs\front1000_mixed_current\inventory\gpic_observed_object_inventory_corrected_source_parent_canonical_metadata.tsv --attribute-inventory outputs\front1000_mixed_current\inventory\gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical.tsv --action-inventory outputs\front1000_mixed_current\inventory\gpic_observed_action_inventory_manual_resolved.tsv --lexicon-dir outputs\front1000_mixed_current\lexicons_after_action_manual_resolved --preposition-mwe-lexicon resources\lexicons\preposition_mwes.tsv --prefer-gpu --batch-size 128`

Results:

- Output dir:
  `outputs/real10k_mixed_guard_scoped_timed_20260713_1240`
- Pipeline status: completed.
- Wrapper wall time: 218.999 seconds.
- Internal mixed pipeline time: 210.686974 seconds.
- Internal throughput: 47.463779 captions/sec.
- Stage 1:
  - total: 10,000
  - sentence: 9,896
  - tag-list: 104
- Stage 3:
  - sentence records written: 9,896
  - tag-list records written: 104
  - tag-list segments: 510
  - Stage 3 summaries reported `gpu_enabled=true`.
- Stage 4:
  - raw mentions: 184,400
  - raw edges: 109,392
- Stage 5:
  - canonical mentions: 184,400
  - canonical edges: 109,392
  - canonical sources:
    - GPIC observed inventory: 85,420
    - lexicon: 77,651
    - raw fallback: 21,329
- Stage 6:
  - facts: 1,401,962
  - object counts rows: 1,251
  - attribute counts rows: 3,058
  - action counts rows: 1,451
  - relation triple rows: 8,112
  - object co-occurrence pair rows: 209,902

Notes:

- The corrected 10K run includes tag-list captions in the same Stage 6 count
  set as sentence captions.
- The input file lives outside the current writable repo, but outputs were
  written under the active local workspace.

## 2026-07-13 - Real 10K Object Inventory Manual Resolution V3

Purpose:

- Apply the 1,791-row object manual feedback for the real 10K mixed inventory.
- Preserve surface-rewrite rows as observed-span rows while copying the
  replacement head span's selected synset evidence.
- Recompute parent, canonical, and synset metadata before Stage 4 use.

Manual resolution:

- Input full inventory:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_object_inventory.tsv`
- Manual patch:
  `C:\Users\rlath\Downloads\gpic_observed_object_inventory_needs_manual_v3_mixed_patch.tsv`
- Output:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_object_inventory_manual_resolved_v3.tsv`
- Summary:
  - full rows: 6,513
  - resolved rows: 1,791
  - original status counts: `chosen=3513`, `excluded=1209`, `needs_manual=1791`
  - merged status counts: `chosen=5304`, `excluded=1209`
  - surface rewrite rows: 26
  - remaining surface rewrite `needs_manual` rows: 0

Parent enrichment:

- Output:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_object_inventory_manual_resolved_v3_parents.tsv`
- Summary:
  - rows: 6,513
  - selected synset missing rows: 1,209
  - parent filled rows: 5,303
  - parent lookup error rows: 0

Canonical enrichment:

- Initial canonical enrichment reached Google Ngram evidence for two rows whose
  saved evidence was missing:
  - `corn cobs`: candidates `corncob` and `corn cob`
  - `golf clubs`: candidates `golfclub` and `golf club`
- The correct handling is to query Google Ngram evidence, record the evidence
  row, and rerun canonical enrichment. Do not manually guess canonical values
  when the required Ngram evidence is absent.
- Added Google Ngram evidence rows to
  `resources/source_labels/google_ngram_canonical_frequency_evidence.tsv`
  using corpus `26`, smoothing `0`, case-insensitive `true`, years 2000-2019.
- Evidence result:
  - `corncob`: mean `7.9700879977e-08`
  - `corn cob`: mean `2.85911695753e-08`
  - `golfclub`: mean `4.06227038136e-09`
  - `golf club`: mean `7.6806290314e-07`
- Rerun output:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_object_inventory_manual_resolved_v3_parent_canonical.tsv`
- Rerun summary:
  - rows: 6,513
  - canonical selected rows: 5,304
  - selected synset missing rows: 1,209
  - canonical ambiguous rows: 0
  - canonical lookup error rows: 0
- Verified rows:
  - `corn cobs -> corncob`, tag `selected_by_google_ngram_frequency_unique_max`
  - `golf clubs -> golf club`, tag `selected_by_google_ngram_frequency_unique_max`

Metadata refresh and gate:

- Metadata output:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_object_inventory_manual_resolved_v3_parent_canonical_metadata.tsv`
- Metadata summary:
  - rows: 6,513
  - selected synset rows: 5,304
  - lookup error rows: 0
- Stage 4 object inventory gate:
  `OBJECT_INVENTORY_STAGE4_GATE_OK`

Regression guard:

- Added a guard to `scripts/resolve_object_inventory_rows.py` so a row whose
  `canonical_selection_tag` contains `google_ngram_evidence_missing` cannot be
  manually assigned a canonical surface.
- This prevents repeating the mistake where missing saved Google Ngram evidence
  was bypassed with a manual canonical guess.
- Verification:
  `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_resolve_object_inventory_rows.py`
  passed.

Attribute inventory rebuild after object V3:

- Rebuilt the real 10K attribute inventory from the same mixed Stage 3 records
  using the corrected object V3 inventory as the consumed-object span source.
- Prior attribute inventory:
  `outputs/front1000_mixed_current/inventory/gpic_observed_attribute_inventory_rebuilt_after_object_manual_resolved_canonical.tsv`
- Output:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_attribute_inventory_after_object_v3.tsv`
- Needs-manual subset:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_attribute_inventory_after_object_v3_needs_manual.tsv`
- Summary:
  - rows: 3,531
  - attribute candidate total: 55,364
  - prior reused rows: 649
  - prior selected synset reused rows: 612
  - status counts: `chosen=2037`, `needs_manual=1494`
  - needs-manual reasons:
    - `manual_attribute_gate_required=823`
    - `manual_synset_required=510`
    - `manual_surface_query_conflict_required=161`
- Formal state:
  - Stop here. Attribute canonical enrichment must not run until the 1,494
    `needs_manual` attribute rows are resolved.

Attribute manual resolution after object V3:

- User-provided resolved subset:
  `C:\Users\rlath\Downloads\gpic_observed_attribute_inventory_after_object_v3_manual_resolved.tsv`
- User-provided audit:
  `C:\Users\rlath\Downloads\gpic_observed_attribute_inventory_after_object_v3_manual_resolution_audit.tsv`
- Overlay output:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_attribute_inventory_after_object_v3_manual_resolved.tsv`
- Summary:
  - full rows: 3,531
  - resolved rows: 1,494
  - overlaid rows: 1,494
  - original status counts: `chosen=2037`, `needs_manual=1494`
  - merged status counts: `chosen=3531`

Attribute canonical enrichment after manual resolution:

- Output:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_attribute_inventory_after_object_v3_manual_resolved_canonical.tsv`
- Summary:
  - rows: 3,531
  - selected synset missing rows: 843
  - canonical selected rows: 2,688
  - canonical ambiguous rows: 0
  - canonical lookup error rows: 0
  - selected-synset rows missing canonical after enrichment: 0

Attribute Stage 5 lexicon export:

- Base lexicon bundle:
  `outputs/front1000_mixed_current/lexicons_after_action_manual_resolved`
- Output dir:
  `outputs/real10k_mixed_inventory_current/lexicons_after_attribute_v3_manual_resolved`
- Summary:
  - inventory rows: 3,531
  - attribute synonym rows: 2,767
  - chosen synonym rows added: 2,073
  - action synonym rows preserved from base bundle: 555

Action inventory rebuild after 10K attribute resolution:

- Input Stage 3 records:
  `outputs/real10k_mixed_guard_scoped_timed_20260713_1240/stage3/stage3_records.jsonl`
- Prior action inventory:
  `outputs/front1000_mixed_current/inventory/gpic_observed_action_inventory_manual_resolved.tsv`
- Preposition MWE lexicon:
  `resources/lexicons/preposition_mwes.tsv`
- Output:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_action_inventory_after_attribute_v3.tsv`
- Needs-manual subset:
  `outputs/real10k_mixed_inventory_current/inventory/gpic_observed_action_inventory_after_attribute_v3_needs_manual.tsv`
- Summary:
  - rows: 1,947
  - verb tokens scanned: 38,373
  - relation MWE matches: 2,358
  - relation MWE consumed tokens: 7,461
  - candidate types: `verb=1617`, `verb_prep=273`, `verb_prt=62`, `verb_prt_prep=1`
  - status counts: `chosen=1738`, `needs_manual=135`, `raw_fallback=74`
  - needs-manual reasons:
    - `manual_action_synset_required=128`
    - `manual_action_morphy_required=7`
- Formal state:
  - Stop here. Action canonical enrichment and Stage 4/5/6 rerun must not
    proceed until the 135 `needs_manual` action rows are resolved.

Object inventory prior-bundle guard, 2026-07-13:

- Change:
  - `scripts/build_gpic_observed_object_inventory.py` now accepts
    `--prior-inventory-bundle` and derives the reusable prior object inventory
    from the bundle.
  - If both `--prior-inventory-bundle` and `--prior-object-inventory` are
    passed and they point to different object inventory paths, the command
    fails with `inventory_bundle_path_mismatch`.
- Reason:
  - A 10K expansion was accidentally started from
    `resources/gpic_inventory/current`, which was still the front100 published
    bundle, while the intended prior was the verified front1000 bundle under
    `outputs/front1000_inventory_using_front100_current/...`.
  - The fix reduces manual path extraction and makes object prior selection
    follow the same bundle mismatch gate used by Stage 3.5 workflow commands.
- Verification:
  - Command:
    `.\scripts\run_tests.ps1 --timeout-seconds 90 discover -s tests -p test_build_gpic_observed_object_inventory.py`
  - Result:
    8 tests passed.
  - Probe command:
    `.\scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 120 -- scripts\build_gpic_observed_object_inventory.py --input outputs\real10k_mixed_formal_after_action_v3_current\stage3\stage3_records.jsonl --prior-inventory-bundle outputs\front1000_inventory_using_front100_current\simulated_workflow_after_action_manual\inventory_bundle.json --output outputs\front10000_inventory_using_front1000_simulated_20260713\guard_probe\gpic_observed_object_inventory_limit5.tsv --summary outputs\front10000_inventory_using_front1000_simulated_20260713\guard_probe\gpic_observed_object_inventory_limit5_summary.json --limit 5`
  - Probe result:
    summary recorded `prior_inventory_bundle` and resolved `prior_object_inventory`
    from that bundle, with 21 prior-reused rows in the 5-caption probe.
- Publish state:
  - The verified front1000 bundle was published to
    `resources/gpic_inventory/current/inventory_bundle.json` with
    `snapshot_label=front1000`.
  - Published row counts:
    `object=1788`, `attribute=773`, `action=563`,
    `action_canonical=563`.
## 2026-07-26: Attribute MWE Prefix Lexicon Replacement Regression

- Failure:
  - The 1M Attribute MWE lexicon export stopped on
    `wood grain -> woodgrain / wood grain`.
- Root cause:
  - The 10K base lexicon retained an older
    `gpic_observed_attribute_inventory` MWE mapping.
  - The approved 1M MWE rescan recomputed the same raw key under the current
    no-joined-separator canonical rule, but the exporter treated the stale
    same-source row as immutable base data.
- Guard:
  - For raw keys present in the current MWE inventory, Stage 5 export removes
    prior `gpic_observed_attribute_inventory` synonym rows and regenerates them
    from the current inventory.
  - Manual or external source conflicts still block export.
- Verification:
  - Command:
    `.\scripts\run_tests.ps1 discover -s tests -p test_export_attribute_stage5_lexicons.py`
  - Result: `5 tests passed`.
  - Added regressions cover same-source stale MWE replacement and preservation
    of the external-source conflict gate.

## 2026-07-26: Attribute MWE Exact Surface Precedes Morphy Alias

- The 1M Stage 4/5/6 candidate stopped before extraction because
  `red carpeted` advertised the Morphy alias `red carpet`, while a distinct
  exact `red carpet` MWE row owned that same form.
- Root cause: the runtime matcher registered exact observed forms and generated
  aliases in one namespace with equal precedence.
- Structural fix: build the matcher in two passes. Exact observed forms are
  registered first; aliases may fill only unowned keys. Alias-to-alias
  conflicts remain fatal.
- Added tests for exact ownership and for a genuine unresolved alias conflict.

## 2026-07-26: Streaming Attribute MWE Rollout Verifier

- Added sharded-path and streaming caption-group regressions to
  `tests/test_verify_attribute_mwe_rollout.py`.
- Targeted command:
  `.\scripts\run_tests.ps1 --pytest --timeout-seconds 600
  tests\test_verify_attribute_mwe_rollout.py
  tests\test_attribute_units.py
  tests\test_export_attribute_stage5_lexicons.py
  tests\test_stage4_extract_raw.py`
- Result: `67 passed`.
- The verifier reads direct and sharded Stage 4/5 artifacts without loading
  the full JSONL corpus into memory.

## 2026-07-26: Attribute MWE 1M Verification Failure Guards

- The first 1M candidate verification correctly blocked publication.
- Diagnosed failures:
  - raw mention text was compared with inventory `span_key`;
  - the legacy baseline stored quantity in separate Stage 6 tables;
  - Stage 4 separator-equivalent matching was broader than Stage 5 synonym
    export;
  - resolved variants added 19 valid Stage 4 occurrences beyond discovery
    counts, requiring a post-resolution evidence recount.
- Added regressions for:
  - separator-equivalent Stage 5 MWE synonym export;
  - Stage 4 recount keyed by `inventory_span_key`;
  - unknown-key recount blocking;
  - unified/legacy quantity row normalization;
  - sharded streaming verification.
- Targeted result after the fixes: `70 passed`.
