# V1 Rule Change Review Log

이 문서는 v1 pipeline에서 rule 또는 lexicon을 추가하기 전에 부작용을 검토한 기록을 남기는 곳이다.

## 2026-07-13: Stage 3.5 Workflow Publish-Current Integration

Proposed rule or lexicon change:

- Let `scripts/run_stage35_inventory_workflow.py` publish a completed workflow
  bundle to the managed current inventory path when `--publish-current` is
  explicitly requested.
- Keep guard/probe/simulation runs unpublished unless they opt in.

Rule generality classification:

- General process/orchestration rule.
- This is not a caption-specific rescue, semantic fallback, or lexicon entry.

Target stage and rule id:

- Stage 3.5-6 R27.2 current inventory publish from complete workflow.

Existing rules affected:

- Extends R27 Stage 3.5 workflow orchestration and R27.1 inventory bundle
  manifest gate.
- Does not change object, attribute, action, relation, canonical, or count
  semantics.

Expected count-table impact:

- No direct count change for the same formal inventory bundle.
- Reduces stale-inventory mistakes by promoting the completed bundle to the
  active current location in the same workflow command.

False positive risk:

- None for extraction.
- Operational risk is accidental overwrite of current by a probe run; mitigated
  by requiring explicit `--publish-current`.

False negative risk:

- None for extraction.

Reversibility:

- Reversible by republishing another completed bundle to the same current path.
- Historical output snapshots remain unchanged.

Verification plan:

- Add a workflow unit test that reaches complete and publishes to a temp current
  directory.
- Run bounded Stage 3.5 workflow and publish tests.

Decision status:

- Approved by user request on 2026-07-13: "캡션 돌려서 needs_manual 해결하고 canonical gate까지 통과해서 새로운 lexicon 추출하면 당연히 통합 inventory에 업데이트 해야하는거 아니야?"

## 2026-07-13: Central Current Inventory Publish Path

Proposed rule or lexicon change:

- Add a stable current inventory publish step at
  `resources/gpic_inventory/current/inventory_bundle.json`.
- The publish step copies the completed object, attribute, action, optional
  action-canonical inventories, Stage 5 lexicon bundle, and action inventory
  pipeline-state sidecar from a completed Stage 3.5 snapshot.
- The current bundle is the intended input for later inventory reuse and formal
  mixed runs. Output snapshot paths remain run history, not the managed current
  inventory location.

Rule generality classification:

- General process/orchestration rule.
- This is not a caption-specific rescue, semantic fallback, or source-specific
  lexicon rule.

Target stage and rule id:

- Stage 3.5-6 R27.2 central current inventory publish path.

Existing rules affected:

- Extends R27.1 inventory bundle manifest gate.
- Does not change object, attribute, action, relation, canonical, or count
  semantics.

Expected count-table impact:

- No direct count change for a fixed set of inventory inputs.
- Prevents accidental reuse of stale output-snapshot paths when moving from
  100-caption inventory checks to 1K/10K formal runs.

False positive risk:

- None for extraction.
- Operationally, publish now fails when the action inventory sidecar is missing
  instead of producing a formally unusable current bundle.

False negative risk:

- None for extraction.
- A central bundle built from only 100 captions will naturally cover only that
  inventory scope; larger formal runs may fall back for unseen terms until a
  larger inventory is promoted.

Reversibility:

- Reversible by publishing a different completed bundle to the same central
  path or by using an explicit snapshot bundle for a one-off run.

Verification plan:

- Add publish-script unit tests.
- Publish a first-100-caption bundle into the central current path.
- Run a 1K formal mixed pipeline using only the central current bundle and
  confirm it completes with no preview mode.

Decision status:

- Approved by user request on 2026-07-13: "똑바로 보완해라. 그리고 일단 tsv 파일 내부는 처음 100 caption 기준으로만 채워놔."

## 2026-07-13: Inventory Bundle Manifest Gate

Proposed rule or lexicon change:

- Add a formal inventory bundle manifest that groups the completed object,
  attribute, action, and Stage 5 lexicon paths from a Stage 3.5 workflow.
- Allow the Stage 3.5 workflow runner and formal mixed pipeline runner to accept
  this bundle as a single input.
- If a bundle and a per-family path are both supplied, fail when the paths
  disagree instead of silently mixing inventory snapshots.

Rule generality classification:

- General process/orchestration rule.
- This is not a source-specific evidence rule, one-off patch, or semantic
  fallback.

Target stage and rule id:

- Stage 3.5-6 R27.1 inventory bundle manifest gate.

Existing rules affected:

- Complements R26 formal pipeline state manifest gate.
- Complements R27 Stage 3.5 inventory workflow orchestration.
- Does not change R11.1-R25 extraction, canonicalization, or count semantics.

Expected count-table impact:

- No direct count change.
- Reduces the chance that a later large run accidentally uses stale object,
  attribute, action, or lexicon inputs from different inventory snapshots.

False positive risk:

- None for concept extraction.
- Operationally, a command can now fail if it supplies a bundle and a mismatched
  explicit path. This is intended because mixed inventory snapshots are unsafe.

False negative risk:

- None for concept extraction.
- Legacy per-path commands still work, but they do not get the same mismatch
  protection unless a bundle is supplied.

Reversibility:

- Reversible by running legacy per-path arguments without a bundle.
- The bundle is sidecar metadata and does not change inventory TSV contents.

Verification plan:

- Add unit tests for bundle loading and mismatch detection.
- Add workflow tests confirming a complete workflow writes `inventory_bundle.json`.
- Add mixed runner tests confirming bundle paths are applied.

Decision status:

- Approved by user request on 2026-07-13: "겁나 불안하게 하고 있었네; 보완해."

## 2026-07-13: Stage 3.5 Inventory Workflow Orchestrator

Proposed rule or lexicon change:

- Add an explicit Stage 3.5 workflow runner that inspects inventory artifacts,
  writes a workflow state file, and advances to the next offline preparation
  step when the previous step is clear.
- The runner must stop at object, attribute, action, or canonical blockers and
  record the exact next required manual action instead of relying on chat memory.
- The runner calls existing build/apply/canonical/export scripts; it does not
  add new extraction, synset, canonical, or count semantics.

Rule generality classification:

- General process/orchestration rule.
- This is not a one-off patch, source-label rescue, or semantic fallback.

Target stage and rule id:

- Stage 3.5-5 R27 inventory workflow orchestration.

Existing rules affected:

- Complements R26 formal pipeline state manifests.
- Does not change R11.1-R11.5, R12-R25 extraction/canonical/count behavior.
- Turns the existing negative gates into an explicit positive next-step runner.

Expected count-table impact:

- No direct count change.
- Prevents formal Stage 4/5/6 from being run from stale or half-prepared
  inventory artifacts.
- Reduces repeated assistant/operator mistakes where a cleared Stage 3.5 phase
  did not automatically proceed to the next required phase.

False positive risk:

- None for concept extraction.
- Operationally, the runner may block if required paths are missing or state is
  incomplete; this is preferable to silently skipping a Stage 3.5 phase.

False negative risk:

- None for concept extraction.
- Operationally, a correctly prepared legacy artifact may need to be passed as
  an explicit path if it was not created by the workflow's default naming.

Reversibility:

- Reversible by not using the workflow runner. Generated workflow state is
  sidecar metadata and does not alter inventory TSV semantics.

Verification plan:

- Add unit tests for workflow next-step decisions.
- Run bounded tests for the workflow planner.
- Run the workflow on the current 10K inventory directory and confirm it stops
  at the existing action `needs_manual` blocker rather than moving to canonical
  or Stage 4.

Decision status:

- Approved by user request on 2026-07-13: "이전 단계가 clear되면 내가 따로 지시 안해도 다음 단계로 넘어가도록 해놔".

## 2026-07-13: Scope Object/Attribute Surface-Conflict Guard To New Runtime Lookup

Proposed rule or lexicon change:

- Remove the overly broad behavior where exact user/manual inventory decisions
  could be reopened merely because a lemma/Morphy/surface-changing query selected
  a different synset.
- Keep a scoped safety guard for fresh runtime lookup: if there is no reusable
  prior/manual inventory row and observed exact surface and lemma/Morphy/base-form
  query select different synsets, leave the row `needs_manual` with
  `decision_reason=manual_surface_query_conflict_required`.
- Object and attribute runtime lookup still prefers observed exact surface when
  there is no conflicting selected fallback synset, and falls back to
  lemma/Morphy/normalization only when observed exact lookup has no hit.
- Keep the stale-prior hygiene rule that refuses to reuse old automatic
  surface-changing prior rows without explicit manual evidence.

Rule generality classification:

- General lookup-order clarification.
- This is not a surface-specific rescue mapping; it defines when manual
  inventory authority wins and when a new unreviewed lookup conflict must be
  manually reviewed.

Target stage and rule id:

- Stage 3.5 GPIC observed object inventory.
- Stage 3.5 GPIC observed attribute inventory.
- Stage 4 R12 object extraction runtime lookup helper.

Existing rules affected:

- Narrows the earlier object/attribute surface-changing conflict guard.
- Does not change explicit manual decision authority: user-provided manual TSV
  decisions remain authoritative.
- Does not change joined-variant manual handling.

Expected count-table impact:

- User-approved object/attribute rows should not be reopened solely because exact
  surface and Morphy/base-form candidates differ.
- New, unreviewed object/attribute rows with exact-vs-Morphy/base-form selected
  synset conflict still stop in the manual queue.
- Existing user-approved manual rows remain valid unless the user explicitly
  reopens them.

False positive risk:

- Exact surface OEWN senses may be accepted for manual inventory rows even when a
  base-form/Morphy sense would be better in a particular caption.
- This is accepted for v1 because exact observed surface is less surprising
  than hidden surface-changing conflict blockers after the user has reviewed the
  row.

False negative risk:

- Some runtime conflicts may still require manual review even when the exact
  surface would have been acceptable.

Reversibility:

- Remove the scoped conflict helper functions and tests if the user later wants
  exact surface to always win even for new unreviewed rows.

Verification plan:

- Update object, attribute, and Stage 4 regression tests so prior/manual
  inventory rows win, but new runtime exact-vs-Morphy/base-form conflicts become
  `needs_manual`.
- Delete stale `surface_conflict_guard*` generated artifacts from the current
  front-1000 inventory directory.
- Run bounded unit tests for the changed lookup behavior.

Decision status:

- Revised by user in chat on 2026-07-13: the broad guard was patchy, but a scoped
  guard is reasonable when no lexicon/manual row exists.

## 2026-07-13: Object/Attribute Selected-Query Prior Reuse And Observed-Surface Lexicon Export

Proposed rule or lexicon change:

- Extend GPIC observed object and attribute prior reuse from exact `span_key`
  only to exact `span_key` first, then unique final `chosen` `selected_query`.
- Reuse by `selected_query` only after the current runtime OEWN lookup confirms
  that the prior selected synset is still one of the current candidates.
- Keep `excluded` and no-synset rows exact-span-only; do not propagate them by
  `selected_query`.
- Stage 5 synonym lexicon export must include original observed caption
  surfaces from `span_key`, `observed_surface`, and `example_surfaces`, not only
  Morphy/lookup-normalized query strings.

Rule generality classification:

- General inventory reuse and lexicon export rule.

Target stage and rule id:

- Stage 3.5 GPIC observed object inventory.
- Stage 3.5 GPIC observed attribute inventory.
- Stage 3.5-to-5 attribute/action synonym lexicon export.

Existing rules affected:

- Object and attribute inventory builders now avoid repeated manual decisions
  for inflectional or Morphy-normalized variants whose selected query was
  already resolved uniquely.
- R20/R22 active synonym lexicons get observed pre-Morphy surface variants so
  Stage 5 can canonicalize the forms that actually appeared in captions.

Expected count-table impact:

- No new extraction pattern is added.
- Object/attribute inventory `needs_manual` rows may decrease when the same
  normalized selected query was already resolved.
- Attribute/action canonical count keys can become more stable because raw
  observed variants such as inflected or diacritic surfaces are exported as
  synonyms.

False positive risk:

- Low to moderate. `selected_query` reuse can propagate one prior sense to a
  new observed surface. The current-candidate membership check and unique prior
  selected synset guard reduce this risk.
- Excluded/no-synset rows intentionally remain exact-only to avoid broad
  negative propagation.

False negative risk:

- Low. Rows remain unresolved or `needs_manual` if prior selected-query evidence
  is absent, conflicting, or not present in the current runtime candidate list.

Reversibility:

- Remove selected-query prior indexes from object/attribute builders.
- Revert synonym export to one raw key per inventory row.

Verification plan:

- Add unit tests for object and attribute selected-query reuse and conflict/no
  propagation behavior.
- Add export tests proving `example_surfaces` variants are emitted as synonym
  raw keys for attribute and action canonical rows.

Decision status:

- Approved by user request: "그래 그렇게 해. 그리고 매번 이렇게 다시 찾지 않도록 Morphy 전 원본 surface는 lexicon에 추가하도록 해."

## 2026-07-13: Action Inventory Selected-Query Prior Reuse

Proposed rule or lexicon change:

- Extend Stage 3.5 action inventory prior reuse from exact `span_key` only to
  exact `span_key` first, then resolved `selected_query`.
- If a new observed action surface normalizes through OEWN/Morphy to a
  `selected_query` that already has a unique final chosen synset in the prior
  action inventory, reuse that synset instead of queuing the row as
  `needs_manual`.
- If prior rows for the same `selected_query` disagree, keep the new row
  `needs_manual`.

Rule generality classification:

- General rule.

Target stage and rule id:

- Stage 3.5 action inventory preparation feeding R15.

Existing rules affected:

- R15 action span selection and Stage 3.5 action inventory lookup.
- R11.4/R11.5 downstream action canonical inventory/export benefit from fewer
  duplicate manual decisions.

Expected count-table impact:

- No new action extraction pattern is added.
- Some inflectional variants that already resolve to a previously selected
  action query, such as `sitting in -> sit in` or `mark -> mark`, can now become
  `chosen` automatically.
- Final action count keys may change only after the formal action canonical
  export is regenerated from the resolved inventory.

False positive risk:

- Low to moderate. Reusing by `selected_query` can propagate a prior manual
  sense to a new surface form. The conflict guard prevents reuse when prior
  rows for that query disagree.

False negative risk:

- Low. Rows remain `needs_manual` when there is no unique prior chosen synset.

Reversibility:

- Revert the selected-query reuse index and lookup fallback. Exact `span_key`
  reuse remains unchanged.

Verification plan:

- Add a unit test where prior `marked -> selected_query=mark` resolves a new
  `mark` row by selected-query reuse.
- Add a unit test where conflicting prior `selected_query=mark` rows do not get
  reused.
- Run the action inventory builder unit tests and relevant formal gate tests.

Decision status:

- Approved by user request: "보완해".

## 2026-07-13: R26 Formal Pipeline State Manifest Gate

Proposed rule or lexicon change:

- Add artifact-level pipeline state manifests so formal runners no longer infer
  stage readiness from filenames, chat memory, or partial summaries.
- The action inventory builder writes a sidecar state file next to the generated
  action inventory TSV.
- Formal Stage 4 requires that action inventory sidecar state to prove action
  candidates were built after active preposition MWE span detection.
- The mixed formal runner writes an output-directory `pipeline_state.json`
  recording preview/formal mode and stage completion state.

Rule generality classification:

- General formal pipeline execution gate.
- This is not a semantic extraction rule, lexicon expansion, or one-off rescue
  mapping.

Target stage and rule id:

- Stage 3.5-6 R26 formal pipeline state manifest gate.

Existing rules affected:

- R15/R18.1 extraction order is unchanged.
- R11.4/R15 action inventory and extraction now require manifest evidence that
  preposition MWE detection happened before action candidate generation.
- Formal Stage 4 action inventory readiness now checks both TSV row status and
  sidecar manifest state.
- Preview/debug runtime action lookup remains possible only through explicit
  preview flags.

Expected count-table impact:

- No direct count change.
- Prevents count tables from being generated from stale, preview, or
  out-of-order artifacts.

False positive risk:

- None for concept extraction, because no extraction behavior changes.
- Operationally, a valid legacy artifact without sidecar state will be rejected
  as formal input and must be regenerated with the current runner.

False negative risk:

- None for concept extraction.
- Operationally, a missing or stale sidecar can block a run until regenerated.

Reversibility:

- Reversible by removing the R26 manifest gate and sidecar state checks.
- Sidecar state files are non-destructive metadata and can be ignored by older
  scripts.

Verification plan:

- Add unit tests for reading/writing pipeline state and rejecting missing or
  stale action-inventory sidecars.
- Update Stage 4 gate tests so formal Stage 4 rejects action inventory without
  preposition-MWE-aware sidecar state.
- Update mixed runner tests so a successful stubbed run writes formal
  `pipeline_state.json`.
- Run bounded tests for pipeline state, formal gates, mixed pipeline, and action
  inventory builder.

Decision status:

- Approved by user in chat on 2026-07-13 after repeated stage-order mistakes
  showed that chat memory and filename conventions were insufficient.

## 2026-07-12: R16.3 ACL Action Head-Object Agent Inheritance

Proposed rule or lexicon change:

- Add agent-only event-role inheritance for active `acl` actions.
- After direct R16/R17/R17.1/R16.2 event role extraction, if an action head has
  `dep == "acl"` and `tag == "VBG"`, has no existing agent, and its dependency
  head is already a selected object, create an agent edge from the action to
  that head object.
- Do not apply this rule to `relcl`, `advcl`, `xcomp`, `ccomp`, `acomp`, or
  generic VBG/VBN actions.
- Do not inherit patients.

Rule generality classification:

- General dependency-evidence recall rule.
- This is not coreference or relative-pronoun resolution. It only uses the
  direct `acl -> head noun` dependency already present in the parse.

Target stage and rule id:

- Stage 4 R16.3 ACL action head-object agent inheritance.

Existing rules affected:

- R16 direct `nsubj` agent extraction remains unchanged.
- R16.2/R17.1 passive handling remains unchanged.
- R16.3 runs after direct event role extraction and before R16.1 conjunct
  action agent inheritance, so a recovered acl agent may serve as the source
  for a following conjunct action.
- R16.1 remains limited to `conj` actions.

Expected count-table impact:

- Agent/patient pair count may increase for reduced relative/participial noun
  modifiers such as `a man holding a bat`.
- Action count should not change.
- Patient count should not change from this rule.

False positive risk:

- Low to medium. Parser errors can attach an action as `acl` to the wrong noun.
- The risk is bounded by requiring the acl head to already be an object and by
  refusing to add an agent when the action already has one.
- VBN acl actions are excluded because reduced past-participle modifiers often
  have passive/adjectival readings such as `bicycles parked` or `sign mounted`.
- Passive-like acl actions are also excluded when direct children include
  `nsubjpass`, `auxpass`, or `agent`.

False negative risk:

- Still misses `relcl` cases such as `a man who is holding a bat`.
- Still misses acl cases where the head noun is not selected as an object.
- Does not recover patient/object roles beyond existing R17.

Rejected scope:

- `relcl` relative-pronoun subject replacement is intentionally excluded.
- Broad old-code inheritance over `advcl`, `xcomp`, `ccomp`, `acomp`, and
  VBG/VBN fallback is intentionally excluded.

Reversibility:

- Reversible by removing the R16.3 pass after direct event role extraction.
- Added edges preserve `role_source=acl_head_object_agent` and `acl_head_i`
  metadata.

Verification plan:

- Add a Stage 4 regression test where an acl action receives its head object as
  agent.
- Add a negative regression test proving R16.3 does not add an agent when a
  direct R16 agent already exists.
- Add a negative regression test proving passive-like acl actions do not get
  an R16.3 agent.
- Add a negative regression test proving VBN acl modifiers do not get an R16.3
  agent even without an explicit `by` phrase.
- Run bounded Stage 4 tests, then re-run the current 100-caption sample and
  inspect added R16.3 edges.

Decision status:

- Approved by user in chat on 2026-07-12.

## 2026-07-12: Tag-list Segment Object/Attribute/Quantity Extraction

Proposed rule or lexicon change:

- Change tag-list handling from `tag_list_deferred` skip to a separate tag-list
  path.
- Stage 1 writes tag-list GPIC rows separately from sentence rows.
- Stage 3 annotates each comma-separated tag segment as its own spaCy Doc and
  preserves segment token/noun_chunk evidence.
- Stage 4 extracts only object, attribute, and quantity mentions/edges from
  tag-list segment noun chunks using the same GPIC object inventory and R12/R13/R14
  rules as sentence captions.
- If a tag-list segment has no extracted object and is a single attribute-like
  token, Stage 4 preserves it as an unattached attribute mention.
- Stage 4 does not create tag-list actions, event roles, relations, or
  cross-segment semantic links.

Rule generality classification:

- General input-shape routing and segment extraction rule.
- Not a source-specific rescue mapping or one-off patch.

Target stage and rule id:

- Stage 1 R1.1 tag-list route.
- Stage 3 R6-R11 segment annotation evidence.
- Stage 4 R12/R13/R14 tag-list object/attribute/quantity extraction.

Existing rules affected:

- R1.1 no longer means skip; it means tag-list route.
- R12/R13/R14 are reused on tag-list segment noun chunks.
- R15-R18 are not run for tag-list rows.
- Stage 5/6 consume tag-list raw mentions/edges without new interpretation.

Expected count-table impact:

- Tag-list rows can now add object, object parent, attribute, quantity,
  object-attribute, object-quantity, and object co-occurrence facts.
- Tag-list rows do not add action, event-role, relation, relation-component, or
  ambiguous-relation facts.

False positive risk:

- Medium. Short comma segments can be visually meaningful labels but may also be
  context/noise. The risk is bounded by requiring the same GPIC observed object
  inventory for object extraction.
- Unattached single-token attribute mentions may include non-visual labels, but
  they do not attach to objects.

False negative risk:

- Tag-list multi-token floating attributes and context phrases may be missed.
- Tag-list action/relation information is intentionally not extracted.
- Segment-to-segment grouping such as `red, shirt` is intentionally not inferred.

Reversibility:

- Reversible by routing tag-list rows back to `tag_list_deferred` and removing
  the tag-list branch in Stage 3/4.
- Tag-list output rows preserve `caption_shape=tag_list`,
  `tag_segment_id`, and segment offsets in metadata.

Verification plan:

- Update schema/Stage 1 tests for non-skipped tag-list records and tag rows.
- Add Stage 3 test for comma segment annotation metadata.
- Add Stage 4 tests for tag-list object/attribute/quantity extraction and
  single-token floating attribute preservation.
- Run bounded tests for schema, Stage 1, Stage 3, and Stage 4.

Decision status:

- Approved by user in chat on 2026-07-12.

## 2026-07-11: R18.1 Missing Endpoint Ambiguous Occurrences

Proposed rule or lexicon change:

- Preserve a matched R18.1 preposition MWE occurrence as an
  `ambiguous_relation_candidate` even when source candidates or target
  candidates are empty.
- Use audit-only sentinel edge endpoints for the missing side:
  `__missing_source__` and `__missing_target__`.
- Do not create object mentions for missing endpoints, and do not count missing
  endpoints as objects.

Rule generality classification:

- General audit-preservation rule.
- This is not semantic source/target recovery. It records that a reviewed MWE
  matched and that the source or target could not be resolved by the current
  dependency/object-mapping rule.

Target stage and rule id:

- Stage 4 R18.1 preposition MWE relation extraction.
- Stage 5 R24 relation metadata preservation.
- Stage 6 R25 ambiguous relation occurrence count.

Existing rules affected:

- R18.1 no longer drops matched MWE occurrences just because source or target
  candidate lists are empty.
- Normal `relation` edges are still emitted only when both source and target
  are exactly one real mention.
- Missing endpoint candidates are never promoted to normal relation triples.

Expected count-table impact:

- Ambiguous relation occurrence count may increase for matched MWE occurrences
  whose source or target is missing under the current rule.
- Relation triple count is unchanged for missing-endpoint cases.
- Object, attribute, action, quantity, and object-pair counts are not affected
  because no missing endpoint mention is created.

False positive risk:

- Low for confirmed relation triples, because no missing-endpoint occurrence is
  counted as a normal relation.
- Medium for audit volume, because any matched preposition MWE with unresolved
  endpoints is now visible as an ambiguous candidate occurrence.

False negative risk:

- Lower for relation-MWE auditing, because matched MWE occurrences are no
  longer silently lost when an endpoint is unresolved.
- Still medium for confirmed relations, because the rule still does not infer
  source or target through ancestor/sibling/event-role semantics.

Reversibility:

- Reversible by restoring the previous `continue` behavior when either
  candidate list is empty and removing missing endpoint sentinel support from
  the schema/canonical/count path.

Verification plan:

- Add a Stage 4 test for a matched `in front of` MWE with no direct source
  candidate and one resolved target; it should create one
  `ambiguous_relation_candidate`.
- Add Stage 5/6 tests showing the missing endpoint survives canonicalization
  and exports one ambiguous occurrence fact with `source_status=source_missing`.
- Re-run the same 0101-0200 100-caption sample and check that the previously
  missed `standing in front of a brick wall` occurrence is visible as
  ambiguous.

Decision status:

- Approved by user in chat on 2026-07-11.

## 2026-07-11: R18.1 Broad Google Ngram Relation Pattern Lexicon Merge

Proposed rule or lexicon change:

- Merge the user-approved Google Ngram-filtered ADP...of relation pattern rows
  into the active Stage 4 preposition MWE lexicon alongside the reviewed
  external preposition MWE inventory.
- Use only rows where `ngram_found == yes` and `ngram_status == ok`.
- For these rows, use the row `term` as both the matching token sequence and
  the canonical relation label.

Rule generality classification:

- Explicit user-approved manual lexicon expansion.
- This is a broad coverage lexicon source, not a one-off rescue mapping.

Target stage and rule id:

- Stage 3.5 preposition MWE lexicon bundle.
- Stage 4 R18.1 preposition MWE relation extraction.
- Stage 6 R25 relation component and ambiguous relation occurrence counts may
  receive more rows through R18.1 metadata.

Existing rules affected:

- R18.1 receives more active lexicon spans to match.
- R18.1 source/target evidence rules are unchanged.
- Stage 5 relation canonicalization remains raw-preserving for single ADP
  relations and preserves preposition MWE labels from Stage 4.

Expected count-table impact:

- Relation triple count may increase when newly added broad patterns have a
  single source and target candidate.
- Ambiguous relation occurrence count may increase when a newly added broad
  pattern has missing or multiple source/target candidates.
- Relation component count may increase for confirmed relation MWE edges.

False positive risk:

- High by design. The user explicitly accepts false positives for the broad
  ADP...of pattern lexicon.
- The risk is bounded by preserving unresolved source/target cases as
  `ambiguous_relation_candidate` rows rather than forcing every occurrence into
  a normal relation triple.

False negative risk:

- Lower than the curated-only lexicon for generated ADP...of spatial relation
  patterns.
- Expressions outside the generated pattern family are still limited to the
  reviewed external inventory.

Reversibility:

- Reversible by rerunning `scripts/export_preposition_mwe_stage4_lexicon.py`
  with `--ngram-input ""` or by removing the default Ngram input from the
  export script.
- Lexicon rows preserve `source=GOOGLE_NGRAM_RELATION_PATTERN` and Ngram
  evidence in `notes`, so broad-source rows can be filtered downstream.

Verification plan:

- Regenerate `resources/lexicons/preposition_mwes.tsv`.
- Compare lexicon row counts before and after merge.
- Run a 100-caption benchmark before and after merge with the same input,
  object inventory, Stage 5 lexicon directory, batch size, and model.
- Inspect Stage 4/Stage 6 summary changes, especially relation,
  relation_component, and ambiguous_relation_candidate facts.

Decision status:

- Approved by user in chat on 2026-07-11.

## 2026-07-11: R18.1 VERB/AUX Head Source Candidates

Proposed rule or lexicon change:

- Extend the R18.1 head-attached source candidate rule from `VERB` heads to
  `VERB` or `AUX` heads.
- When the initial preposition MWE token's head is not itself an object mention
  but has POS `VERB` or `AUX`, all direct children already mapped to object
  mentions are preserved as source candidates regardless of dependency label.

Rule generality classification:

- General syntax-preserving candidate rule.
- This is not semantic PP source disambiguation. It only removes the accidental
  `VERB`-only POS gate for copular/AUX-attached relation MWEs.

Target stage and rule id:

- Stage 4 R18.1 preposition MWE relation extraction.
- Stage 6 R25 ambiguous relation occurrence count remains unchanged.

Existing rules affected:

- R18.1 no longer says AUX/copular source recovery is out of scope.
- R18.1 still requires either a direct object head or direct object-mapped child
  candidates under the `VERB`/`AUX` head.
- R18.1 still does not climb ancestors, inherit agents, or use semantic source
  scoring.

Expected count-table impact:

- Relation triple count may gain rows for copular/AUX-attached preposition MWEs
  such as `legs are out of focus`, `bowls are next to it`, or `building is
  visible along with sign`.
- Ambiguous relation occurrence count may gain rows if an AUX head has multiple
  object-mapped child source candidates.

False positive risk:

- Medium. A direct object-mapped child under an AUX head may still not be the
  semantic source of the preposition MWE.
- The risk is bounded by preserving multi-source cases as ambiguous candidates
  and counting them once per matched MWE occurrence.

False negative risk:

- Medium. This still misses cases where the source is an ancestor, conjunct,
  inherited event role, or semantically implied object rather than a direct
  object-mapped child.

Reversibility:

- Reversible by changing `RELATION_MWE_SOURCE_HEAD_POS` back to `{"VERB"}`.

Verification plan:

- Add a Stage 4 test where an AUX-attached `out of` MWE uses an `nsubj` object
  child as relation source.
- Re-run bounded Stage 4 and Stage 5 tests.
- Re-run the same 0101-0200 100-caption sample and compare the previously
  discussed missed relation MWE cases.

Decision status:

- Approved by user in chat on 2026-07-11.

## 2026-07-11: R18.1 Ambiguous Relation Occurrence Count Unit

Proposed rule or lexicon change:

- Change R18.1 ambiguous preposition MWE counting from candidate-pair count to
  matched-MWE-occurrence count.
- If source candidates or target candidates are multiple, Stage 4 may preserve
  candidate-pair edges for audit, but Stage 6 groups those edges by
  `caption_id + matched_token_indices + relation` and emits one
  `ambiguous_relation_candidate` fact for the occurrence.
- If the final ADP has multiple direct `pobj` object targets, treat that as
  target ambiguity rather than multiple confirmed normal relations.

Rule generality classification:

- general rule.
- This is a count-unit correction for all R18.1 ambiguous relation cases, not a
  caption-specific rescue rule.

Target stage and rule id:

- Stage 4 R18.1 preposition MWE relation extraction.
- Stage 6 R25 count export.

Existing rules affected:

- R18.1 previously made one `ambiguous_relation_candidate` edge per source
  candidate and did not treat multiple target candidates as ambiguous when the
  source was unique.
- R25 previously counted each ambiguous candidate edge as one fact.

Expected count-table impact:

- Ambiguous relation candidate counts decrease when one matched MWE occurrence
  expands into multiple source-target candidate pairs.
- Normal relation triples no longer count cases where a preposition MWE has a
  unique source but multiple target candidates.
- Confirmed one-source/one-target relation triples are unchanged.

False positive risk:

- Lower for count interpretation, because one ambiguous occurrence no longer
  inflates count by the Cartesian product of candidates.
- Candidate-pair audit edges still exist, so downstream reviewers can inspect
  all possible source-target pairs.

False negative risk:

- Some previously counted normal relation triples with multiple target
  candidates move to ambiguous occurrence count. This is intended because the
  target was not uniquely determined.

Reversibility:

- Reversible by changing Stage 6 back to edge-level candidate facts and Stage 4
  back to source-only ambiguity.
- Candidate-pair edges preserve matched token indices, source candidates, and
  target candidates for audit.

Verification plan:

- Add Stage 4 test where one source and two final `pobj` targets produce
  ambiguous candidate edges, not normal relation edges.
- Add Stage 6 test where four candidate-pair edges from one matched MWE
  occurrence produce exactly one ambiguous relation occurrence fact/count row.
- Run bounded Stage 4 and Stage 6 unit tests.

Decision status:

- Approved by user in chat on 2026-07-11: ambiguous source/target cases should
  count once per relation occurrence, not once per candidate pair.

## 2026-07-10 - Action Canonical Inventory Export To R22

### Proposed change

- 변경 대상:
  - completed GPIC observed action canonical inventory rows를 Stage 5
    `action_synonyms.tsv` lexicon bundle로 export한다.
  - `decision_status=chosen`이고 `selected_oewn_synset`, `canonical_surface`가
    모두 있는 row만 export한다.
  - `raw_fallback` action row는 selected synset과 canonical action surface가
    없으므로 export하지 않는다.
- target stage: Stage 3.5 export preparation, Stage 5 canonicalization.
- target rule id: R11.5, R22.
- rule generality classification:
  - general rule.

### Existing rules affected

- R11.4 action canonical inventory build 결과를 active Stage 5 R22 입력으로
  연결한다.
- R22 자체는 기존 explicit TSV lexicon lookup 방식을 그대로 쓴다.

### Expected count-table impact

- Stage 4 mention/edge count는 변하지 않는다.
- Stage 5 action canonical label과 Stage 6 action count key가 observed surface
  기준에서 canonical action 기준으로 바뀔 수 있다.

### False positive risk

- 낮음. 이미 selected synset과 canonical surface가 결정된 offline inventory
  row만 export한다.

### False negative risk

- `raw_fallback` action은 canonical evidence가 없으므로 raw surface count로
  남는다.

### Reversibility

- generated Stage 5 lexicon bundle에서 action synonym export를 제거하거나,
  action canonical inventory 없이 bundle을 재생성하면 되돌릴 수 있다.
- export row에는 `source=gpic_observed_action_inventory`를 남긴다.

### Verification plan

- action canonical inventory row가 `action_synonyms.tsv`로 export되고
  `raw_fallback` row가 skip되는 unit test를 추가한다.
- sentence-20 Stage 5 lexicon bundle을 action canonical inventory 포함해서
  재생성한다.
- Stage 4/5/6/Markdown을 재실행하고 action count key가 canonical으로 바뀌었는지
  확인한다.

### Decision status

- 2026-07-10 사용자가 action canonical inventory 이후 Stage 4/5/6 재실행을
  요청해 승인됨.

## 2026-07-08 - Object Parent Display Label With Synset Evidence

### Proposed change

- 변경 대상:
  - Stage 5 object `parent_concepts`는 synset ID가 아니라 parent lemma display label을 먼저 담는다.
  - parent synset IDs는 `canonical_detail.parent_oewn_synsets`와 Stage 6 `*_parent_synset_ids` evidence column에 보존한다.
- target stage: Stage 5, Stage 6, Markdown report rendering.
- target rule id: R23, R25.
- rule generality classification:
  - general rule.

### Existing rules affected

- R23은 기존에 `parent_concepts` 자체를 `parent_oewn_synsets`로 채웠다.
- R25는 parent 관련 extra field를 그대로 count table에 노출했으므로 사람이 보는 table도 synset ID 중심이었다.

### Expected count-table impact

- object parent 관련 count table의 parent 표시값이 `oewn-*` ID에서 readable lemma label로 바뀐다.
- parent synset ID는 새 `parent_synset_ids` 계열 evidence column에 남는다.
- object/action/attribute/relation extraction 자체는 변하지 않는다.

### False positive risk

- 낮음. parent 선택은 바꾸지 않고 같은 selected OEWN hypernym evidence의 display만 바꾼다.
- 같은 display lemma가 여러 synset에 걸칠 수 있으므로 synset ID evidence column을 함께 유지한다.

### False negative risk

- 없음. parent가 새로 추가되거나 제거되지 않는다.
- parent lemma evidence가 없으면 기존 synset ID로 fallback하여 빈 표시값을 만들지 않는다.

### Reversibility

- Stage 5 `canonical_detail.parent_oewn_synsets`에 기존 ID evidence가 그대로 남는다.
- Reversible by switching `parent_concepts` back to `parent_oewn_synsets`.

### Verification plan

- Stage 5 unit test: parent_lemmas가 있으면 `parent_concepts`가 lemma display로 채워지고 ID는 detail에 남는지 확인한다.
- Stage 6 unit test: count table에 `parent_concepts`와 `parent_synset_ids`가 모두 나오는지 확인한다.
- Markdown renderer: parent display 옆에 synset ID column을 보여주는지 확인한다.

### Decision status

- Approved by user in chat on 2026-07-08: "반영해".

원칙:

- 말로만 rule impact를 약속하지 않는다.
- 구현 전에 이 문서에 검토를 먼저 남긴다.
- 사용자가 승인하기 전에는 extraction, canonicalization, count에 영향을 주는 변경을 구현하지 않는다.

## 2026-07-08 - Active Attribute Type 보류와 Object Core Span 소비 분리

### Proposed change

- 변경 대상:
  - active Stage 5, Stage 6, Markdown report에서 `attribute_type`을 붙이지 않는다.
  - object lookup은 긴 noun chunk span으로 할 수 있지만, Stage 4에서 실제 consumed 처리와 object token mapping은 selected object의 core suffix span만 사용한다.
- target stage: Stage 3.5, Stage 4, Stage 5, Stage 6.
- target rule id: R11.1, R11.3, R12, R13, R14, R20, R25.
- rule generality classification:
  - general rule

### Existing rules affected

- R11.1은 기존에 selected object lookup span 전체를 consumed token evidence로 썼다.
- R11.3은 attribute type taxonomy를 Stage 5용 lexicon export 대상으로 봤다.
- R12는 selected lookup span 전체를 object mention/token mapping으로 썼다.
- R13/R14는 selected lookup span 전체를 attribute/quantity 후보에서 제외했다.
- R20은 attribute canonicalization 중 `attribute_types.tsv` lookup을 붙였다.
- R25는 object-attribute pair extra field에 `attribute_type`을 넣었다.

### Expected count-table impact

- `black top`, `black shirt`, `blue wall`처럼 lookup span은 modifier+head지만 object core가 head인 경우, modifier가 다시 attribute 후보가 되므로 attribute count와 object-attribute pair count가 늘 수 있다.
- `trash can`, `cell phone`처럼 object core가 full phrase인 경우에는 기존처럼 full phrase가 consumed된다.
- active count table과 detailed Markdown report에서 `attribute_type`이 사라진다.

### False positive risk

- true lexicalized MWE인데 canonical/core evidence가 head suffix로만 잡히면 modifier가 attribute로 남을 수 있다.
- full lookup span, lookup query, lookup token indices를 source detail에 남기므로 audit과 rollback이 가능하다.

### False negative risk

- canonical/core evidence가 없으면 full lookup span을 consumed하기 때문에 일부 modifier가 여전히 빠질 수 있다.
- attribute type 분석은 보류되므로 color/material/size 같은 type별 count는 이번 active pipeline에서 나오지 않는다.

### Reversibility

- Stage 4 source detail에 `lookup_span_surface`, `lookup_token_indices`, `selected_token_indices`를 함께 남긴다.
- attribute type TSV는 offline audit artifact로 남길 수 있지만, active Stage 5/6/report에서 쓰지 않는다.

### Verification plan

- Stage 5가 `attribute_type`을 canonical detail에 넣지 않는지 확인한다.
- Stage 6 fact/count export가 `attribute_type`을 내보내지 않는지 확인한다.
- `black top`은 object `top` + attribute `black`으로, `black trash can`은 object `trash can` + attribute `black`으로 유지되는지 좁은 테스트로 확인한다.

### Decision status

- approved by user: "반영해"

## 2026-07-08 - GPIC Observed Attribute Inventory v1

### Proposed change

- 변경 대상: GPIC Stage 3 records에서 관측된 noun chunk modifier attribute inventory를 만든다.
- target stage: Stage 3.5 offline inventory preparation.
- target rule id: R11.1, R11.2, R11.3.
- rule generality classification:
  - general rule over GPIC observed captions

### Existing rules affected

- 직접 영향:
  - active Stage 4 raw extraction은 변경하지 않는다.
  - active Stage 5 runtime attribute lexicon lookup은 변경하지 않는다.
- 간접 영향:
  - Stage 3.5 object inventory의 selected object span을 consumed token evidence로 사용한다.
  - consumed token 밖 `amod`/`compound` token만 attribute inventory 후보가 된다.
  - attribute canonical/type parent 준비를 위한 offline TSV가 추가된다.

### Count-table impact

- object count: 없음.
- attribute count: 현재 runtime count 변화 없음. offline attribute inventory output만 추가한다.
- object-attribute pair count: 현재 runtime count 변화 없음.
- action count: 없음.
- agent/patient pair count: 없음.
- relation triple count: 없음.
- object co-occurrence pair count: 없음.

### Risk review

- false positive risk:
  - `amod`/`compound` token은 attribute 후보일 뿐, conditional/hard_conflict lexfile은 `needs_manual`로 보낸다.
  - POS/TAG hard filter를 추가하지 않아 품사 기반 false negative를 피한다.
  - no-synset attribute는 synset/canonical 적용 없이 count 후보로만 남긴다.
- false negative risk:
  - noun chunk 밖 modifier는 이번 inventory에 포함하지 않는다.
  - consumed object span 안 token은 attribute 후보에서 제외하므로 MWE 내부 단어 attribute count는 사라진다.
- lost information:
  - raw observed surface, selected query, all OEWN candidate synsets, lexfiles, WN3 count evidence를 TSV에 보존한다.
- interaction with existing rules:
  - Stage 4 extraction용 object ambiguity raise 경로를 쓰지 않고, object span consumption만 참조한다.
  - attribute parent는 OEWN hypernym으로 자동 생성하지 않고 manual taxonomy로 둔다.

### Reversibility

- source column:
  - `span_key`
  - `observed_surface`
  - `selected_query`
  - `selected_oewn_synset`
  - `attribute_gate`
  - `decision_status`
  - `canonical_surface`
- metadata:
  - `decision_basis=gpic_observed_attribute_inventory`
- rollback path:
  - 새 attribute inventory/canonical scripts와 generated TSV를 제거하면 active pipeline에는 영향이 없다.

### Verification plan

- unit test:
  - consumed object span 밖 attribute만 inventory row가 되는지 확인한다.
  - conditional/hard_conflict gate가 manual status를 만드는지 확인한다.
  - no-synset row가 canonical enrichment를 막지 않고 not-applicable로 남는지 확인한다.
- small sample inspection:
  - 20-caption 또는 100-caption Stage 3 records에서 observed attribute inventory를 생성해 status 분포를 확인한다.
- regression comparison:
  - active Stage 4/5/6 output은 이번 변경만으로 바뀌지 않아야 한다.

### Decision

- status: implemented
- rationale:
  - 사용자가 attribute도 object와 같은 방식으로 synset/canonical inventory를 만들되, parent는 manual taxonomy로 정하자고 승인했다.
  - implementation:
    - `scripts/build_gpic_observed_attribute_inventory.py`
    - `scripts/enrich_gpic_attribute_inventory_canonical.py`
  - verification:
    - AST check passed for 5 changed/new Python files.
    - `test_build_gpic_observed_attribute_inventory.py`: 3 passed.
    - `test_enrich_gpic_attribute_inventory_canonical.py`: 3 passed.
    - Existing `test_enrich_gpic_inventory_canonical.py`: 4 passed.
    - `git diff --check`: no whitespace errors; only existing CRLF warnings.
  - status naming correction:
    - Attribute inventory `decision_status` now uses `needs_manual` for both unresolved synset selection and manual lexfile-gate review.
    - The more specific ambiguity cause remains in `decision_reason` and `synset_selection_tag`.
  - manual feedback handling:
    - Attribute canonical enrichment treats `excluded` as a resolved status, not a blocker.
    - Input `canonical_surface` and `manual_*` canonical tags from feedback files are ignored; canonical fields are recomputed from selected synset evidence.

## 2026-07-05 - OpenImages Source-Label Synset Candidate Build v1

### Proposed change

- 변경 대상: OpenImages boxable class labels를 OEWN 2025+ noun synset 후보로 변환하고 ambiguous/unresolved report를 생성한다.
- target stage: source-label candidate generation only.
- target rule id: active extraction/canonicalization/count rule 없음. future R4/R19/R23 input evidence 후보.
- rule generality classification:
  - source-specific evidence rule

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - `resources/source_labels/object_source_label_synset_inventory.tsv`에 OpenImages semantic source-label rows가 추가될 수 있다.
  - prior integrated inventory와 exact normalized label key가 같은 OpenImages row는 duplicate로 분리하고 OEWN lookup을 하지 않는다.
  - canonical decision TSV는 이번 요청 범위가 "synset까지"이므로 자동 재생성 대상이 아니다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.
- active lexicon에 반영하지 않으므로 Stage 1~6 output count에는 영향 없음.

### Risk review

- false positive risk:
  - OpenImages display name은 visual object label이지만 OEWN 다의어가 있을 수 있다.
  - OpenImages MID/hierarchy를 semantic alias나 WordNet synset rescue로 쓰지 않으므로, hierarchy 기반 오해석은 줄인다.
  - object-compatible 후보 중 WN3.0 count 단독 최대값이 있으면 하나를 선택한다. 이는 source object label의 대표 noun sense를 고정하는 기준이지 실제 image object sense 검증은 아니다.
- false negative risk:
  - OpenImages hierarchy parent가 objectness를 암시하더라도 direct WordNet/OEWN synset metadata가 없으면 자동 선택에 쓰지 않는다.
  - lookup recovery는 형태 기반 variant와 Morphy까지만 허용하므로 label 일부는 unresolved로 남을 수 있다.
  - object-compatible 후보군 안에서 count가 모두 0 또는 동률이면 ambiguous로 남긴다.
- lost information:
  - OpenImages MID, source label, hierarchy parent/child MID와 label은 TSV에 보존한다.
  - all OEWN candidate synsets와 WN3.0 count evidence를 보존한다.
- interaction with existing rules:
  - COCO/Objects365 builder를 직접 참조해서 reuse하지 않고 prior integrated inventory만 본다.
  - duplicate row는 semantic inventory에 넣지 않고 duplicate TSV로 분리한다.

### Reversibility

- source column:
  - `dataset`
  - `category_id`
  - `label`
  - `label_key`
  - `openimages_parent_mids`
  - `openimages_parent_labels`
  - `selected_oewn_synset`
  - `selection_status`
  - `synset_selection_tag`
- metadata:
  - `wordnet_version=2025-plus`
  - `wordnet_lexicon_id=oewn:2025+`
  - `decision_basis`
- rollback path:
  - OpenImages generated TSV files를 제거하고 integrated inventory builder input에서 OpenImages candidate TSV를 제거하면 active pipeline에는 영향이 없다.

### Verification plan

- syntax check:
  - pycache를 쓰지 않는 `compile()` 방식으로 OpenImages builder와 inventory builder를 확인한다.
- bounded generation:
  - `scripts/run_script_with_timeout.py --timeout-seconds 120 scripts/build_openimages_oewn_candidates.py`
  - `scripts/run_script_with_timeout.py --timeout-seconds 60 scripts/build_object_source_label_inventory.py`
- inspection:
  - OpenImages rows, duplicate rows, selected/ambiguous/unresolved rows를 확인한다.
  - ambiguous TSV 상위 row를 확인한다.
  - active `resources/lexicons/*` 파일을 수정하지 않았는지 확인한다.

### Decision

- status: implemented
- rationale:
  - 사용자가 다음 dataset으로 OpenImages를 처리하고 ambiguous를 알려달라고 명시했다.
  - output:
    - `resources/source_labels/openimages_boxable_classes.tsv`
    - `resources/source_labels/openimages_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/openimages_oewn2025plus_ambiguous.tsv`
    - `resources/source_labels/openimages_oewn2025plus_unresolved.tsv`
    - `resources/source_labels/object_source_label_synset_inventory.tsv`
    - `resources/source_labels/object_source_label_duplicates.tsv`
    - `resources/source_labels/object_source_label_synset_conflicts.tsv`
  - result:
    - OpenImages rows=601
    - duplicate_existing_label_key_rows=180
    - OEWN lookup rows=421
    - selected rows=297
    - ambiguous rows=64
    - unresolved rows=60
    - MWE candidate rows=168
    - integrated semantic inventory rows=797
    - integrated duplicate rows=249
    - integrated source occurrence rows=1046
    - integrated conflict label keys=0
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않았다.
  - execution note:
    - OpenImages source download and candidate TSV generation required `require_escalated` because sandboxed network access failed.
    - Integrated inventory generation required `require_escalated` because sandboxed same-directory temp file creation failed.
    - This was outside-sandbox execution for the narrow commands, not a permission fix.

## 2026-07-05 - OpenImages Ambiguous Label Manual Decisions v1

### Proposed change

- 변경 대상: OpenImages OEWN 2025+ source-label candidate generation에서 사용자가 명시한 ambiguous label을 manual select 또는 reject로 처리한다.
- target stage: source-label candidate generation only.
- target rule id: active extraction/canonicalization rule 없음. future R4/R19/R23 input evidence 후보.
- rule generality classification:
  - explicit user-approved manual decision

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - OpenImages 후보 TSV의 일부 ambiguous row가 selected 또는 rejected로 바뀐다.
  - OpenImages hierarchy parent label은 자동 rule로 쓰지 않고, 사용자가 승인한 manual decision의 note/evidence로만 남긴다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.
- active lexicon에 반영하지 않으므로 Stage 1~6 output count에는 영향 없음.

### Risk review

- false positive risk:
  - 선택은 사용자가 승인한 OpenImages ambiguous label에만 적용된다.
  - 같은 surface가 다른 dataset이나 GPIC caption token에 등장해도 이 결정이 자동 rule로 확장되지는 않는다.
  - `first-order fallback` row 중 OpenImages parent와 후보 order가 충돌하는 label은 parent-based manual note로 정확한 synset을 지정한다.
- false negative risk:
  - 사용자가 list에 포함하지 않은 ambiguous row는 그대로 ambiguous로 남는다.
  - typo correction, semantic alias, head fallback, label-specific query replacement는 추가하지 않는다.
- lost information:
  - 없음. all OEWN candidates, WN3.0 count evidence, OpenImages MID/hierarchy metadata는 TSV에 유지한다.
- interaction with existing rules:
  - lookup query를 바꾸지 않는다.
  - manual select는 selected synset만 지정하고 `objectness_gate=manual_override`로 기록한다.
  - manual reject는 selected synset을 비우고 `selection_status=rejected`로 남긴다.

### Reversibility

- source column:
  - `manual_decision`
  - `manual_decision_note`
  - `selected_oewn_synset`
  - `selection_status`
  - `synset_selection_tag`
  - `decision_basis`
- metadata:
  - selected rows: `manual_decision=select:{synset_id}`
  - rejected rows: `manual_decision=reject:{synset_id}` 또는 `reject`
- rollback path:
  - OpenImages manual decision table에서 해당 label을 제거하고 후보 TSV를 재생성하면 된다.

### Verification plan

- syntax check:
  - pycache를 쓰지 않는 `compile()` 방식으로 OpenImages builder를 확인한다.
- bounded generation:
  - `scripts/run_script_with_timeout.py --timeout-seconds 120 scripts/build_openimages_oewn_candidates.py`
  - `scripts/run_script_with_timeout.py --timeout-seconds 60 scripts/build_object_source_label_inventory.py`
- inspection:
  - selected/rejected/ambiguous/unresolved count 변화를 확인한다.
  - 사용자가 명시한 labels의 `manual_decision`, `selected_oewn_synset`, `selection_status`를 확인한다.
  - active `resources/lexicons/*` 파일을 수정하지 않았는지 확인한다.

### Decision

- status: implemented
- rationale:
  - 사용자가 OpenImages ambiguous labels에 대한 reject/select/fallback 결정을 명시했다.
  - 이 변경은 automatic rule이 아니라 source-label 후보 파일의 explicit manual decision이다.
  - output:
    - `resources/source_labels/openimages_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/openimages_oewn2025plus_ambiguous.tsv`
    - `resources/source_labels/openimages_oewn2025plus_unresolved.tsv`
    - `resources/source_labels/object_source_label_synset_inventory.tsv`
    - `resources/source_labels/object_source_label_duplicates.tsv`
    - `resources/source_labels/object_source_label_synset_conflicts.tsv`
  - result:
    - OpenImages rows=601
    - duplicate_existing_label_key_rows=180
    - OEWN lookup rows=421
    - selected rows=343
    - manual selected rows=46
    - manual rejected rows=2
    - ambiguous rows=16
    - unresolved rows=60
    - integrated selected rows=651
    - integrated ambiguous rows=16
    - integrated rejected rows=9
    - integrated unresolved rows=121
    - integrated conflict label keys=0
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않았다.
  - execution note:
    - compile check는 sandbox 안에서 통과했다.
    - TSV regeneration은 sandbox `PermissionError` 후 같은 좁은 bounded command를 `require_escalated`로 실행해 생성했다.

## 2026-07-05 - Integrated Source Inventory Canonical Decision v1

### Proposed change

- 변경 대상: `object_source_label_synset_inventory.tsv`의 selected synset group을 기준으로 canonical lemma decision TSV를 만든다.
- target stage: source-label candidate analysis only.
- target rule id: active Stage 1~6 rule 없음. future R19/R23 input evidence 후보.
- rule generality classification:
  - general rule

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - dataset label들이 같은 OEWN synset으로 묶였을 때 count 대표 surface 후보를 별도 TSV로 기록한다.
  - 추후 사용자가 active lexicon 생성을 지시하면 이 decision TSV가 입력 evidence가 될 수 있다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - active lexicon에 반영하지 않으므로 현재 pipeline count false positive는 없다.
  - canonical decision 자체에서는 서로 다른 OEWN lemma를 같은 것으로 collapse하지 않도록 `lowercase + whitespace normalize`만 사용한다.
  - WordNet/OEWN underscore는 display space로만 해석한다.
- false negative risk:
  - source label 또는 Morphy lookup query와 surface가 맞지 않는 synset lemma는 canonical 후보에서 빠질 수 있다.
  - WN3.0 count가 없거나 동률이면 ambiguous로 남는다.
- lost information:
  - 없음. source labels, candidate lemmas, WN3.0 counts, selection tag를 TSV에 남긴다.
- interaction with existing rules:
  - integrated semantic inventory만 읽는다.
  - duplicate label rows는 inventory에 포함되지 않으므로 canonical decision에는 들어가지 않는다.

### Reversibility

- source column:
  - `selected_oewn_synset`
  - `synset_lemmas`
  - `source_label`
  - `selected_lookup_case`
  - `selected_query`
  - `wn30_lemma_counts`
- output:
  - `resources/source_labels/object_synset_canonical_decisions.tsv`
  - `resources/source_labels/object_synset_canonical_ambiguous.tsv`
- rollback path:
  - 위 output TSV를 삭제하거나 script를 되돌리면 active pipeline에는 영향이 없다.

### Verification plan

- compile check:
  - `python -m compileall scripts/build_object_synset_canonical_decisions.py`
- bounded generation:
  - `scripts/run_script_with_timeout.py --timeout-seconds 60 scripts/build_object_synset_canonical_decisions.py`
- inspection:
  - selected synset group 수, canonical selected/ambiguous 수, selection tag count를 확인한다.
  - active lexicon 파일이 수정되지 않았는지 확인한다.

### Decision

- status: implemented
- rationale:
  - 사용자가 통합 inventory 기준 canonical 결정을 지시했다.
  - 사용자가 active lexicon 생성은 dataset 정리 완료 후 별도 지시 전까지 하지 말라고 명시했다.
  - output:
    - `resources/source_labels/object_synset_canonical_decisions.tsv`
    - `resources/source_labels/object_synset_canonical_ambiguous.tsv`
  - result: selected synset groups=304, canonical selected rows=300, canonical ambiguous rows=4.
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않았다.
  - execution note:
    - pycache를 쓰지 않는 syntax check는 sandbox 안에서 통과했다.
    - TSV generation은 sandbox `PermissionError` 후 같은 bounded command를 `require_escalated`로 실행해 생성했다.

## 2026-07-05 - Google Ngram Canonical Frequency Fallback v1

### Proposed change

- 변경 대상: 통합 source-label canonical decision에서 official surface 또는 WN3.0 count로 결정되지 않는 synset group에 Google Books Ngram frequency fallback을 적용한다.
- target stage: source-label candidate analysis only.
- target rule id: active Stage 1~6 rule 없음. future R19/R23 input evidence 후보.
- rule generality classification:
  - general rule

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - `object_synset_canonical_decisions.tsv`의 ambiguous row 일부가 canonical selected row로 바뀔 수 있다.
  - Google Books Ngram evidence TSV가 source-label analysis file로 추가된다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - Ngram 후보군을 모든 synset lemma로 열면 `cell`, `board` 같은 generic lemma가 잘못 이길 수 있다.
  - 이를 막기 위해 후보군은 selected OEWN synset lemma 중 source label formal surface variants 또는 formal lookup selected query와 형태적으로 맞은 lemma로 제한한다.
  - source official label surface와 selected query는 support key일 뿐이며, 그 자체가 OEWN lemma가 아니면 canonical 또는 Ngram 비교 후보가 아니다.
- false negative risk:
  - Google Ngram에 없는 phrase는 missing 또는 zero로 남아 ambiguous가 유지될 수 있다.
  - Google Books corpus frequency는 image-caption frequency와 다르다.
- lost information:
  - 없음. term별 mean frequency, max frequency, nonzero year count, query URL을 TSV에 보존한다.
- interaction with existing rules:
  - semantic alias나 head fallback은 추가하지 않는다.
  - Ngram은 synset 선택이 아니라 canonical surface tie-break에만 쓴다.

### Reversibility

- source column:
  - `google_ngram_candidate_surfaces`
  - `google_ngram_candidate_mean_frequencies`
  - `canonical_selection_tag`
- output:
  - `resources/source_labels/google_ngram_canonical_frequency_evidence.tsv`
  - `resources/source_labels/object_synset_canonical_decisions.tsv`
  - `resources/source_labels/object_synset_canonical_ambiguous.tsv`
- rollback path:
  - Ngram evidence TSV를 삭제하고 canonical decision script를 재실행하면 Ngram fallback 없는 상태로 돌아간다.

### Verification plan

- syntax check:
  - pycache를 쓰지 않는 `compile()` 방식으로 두 script를 확인한다.
- bounded generation:
  - `scripts/run_script_with_timeout.py --timeout-seconds 60 scripts/build_google_ngram_canonical_frequency_evidence.py`
  - `scripts/run_script_with_timeout.py --timeout-seconds 60 scripts/build_object_synset_canonical_decisions.py`
- inspection:
  - Ngram evidence row count, status, canonical ambiguous count를 확인한다.
  - active lexicon 파일이 수정되지 않았는지 확인한다.

### Decision

- status: implemented
- rationale:
  - 사용자가 "동일한 lemma가 없거나 2개 이상이 남으면 Google Ngram 기준 frequency 비교" rule을 적용해야 한다고 명시했다.
  - active lexicon 생성은 여전히 보류한다.
  - 이후 사용자 지적에 따라 `cell phone`/`game board` 같은 source surface를 Ngram 후보로 직접 비교하지 않고, selected OEWN lemma 후보만 Ngram 후보로 쓰도록 수정했다.
  - result:
    - Ngram evidence rows=4.
    - Ngram fallback synset groups=2.
    - canonical selected rows=304.
    - canonical ambiguous rows=0.

## Review Template

```markdown
## YYYY-MM-DD - 짧은 제목

### Proposed change

- 변경 대상:
- target stage:
- target rule id:
- rule generality classification:
  - general rule | source-specific evidence rule | explicit user-approved manual decision | one-off patch / rescue mapping

### Existing rules affected

- 직접 영향:
- 간접 영향:

### Count-table impact

- object count:
- attribute count:
- object-attribute pair count:
- action count:
- agent/patient pair count:
- relation triple count:
- object co-occurrence pair count:

### Risk review

- false positive risk:
- false negative risk:
- lost information:
- interaction with existing rules:

### Reversibility

- source column:
- rule_id:
- metadata:
- rollback path:

### Verification plan

- unit test:
- small sample inspection:
- regression comparison:

### Decision

- status: proposed | approved | rejected | implemented | deferred
- rationale:
```

## 2026-07-03 - Object MWE Lexicon Review 준비

### Proposed change

- 변경 대상: `resources/lexicons/object_mwes.tsv`에 object MWE entries 추가
- target stage: Stage 2 spaCy preprocessing, Stage 3 POS correction
- target rule id: R4, R7

### Existing rules affected

- 직접 영향:
  - R4 Object MWE merge
  - R7 Object MWE POS correction
  - R11 Noun chunking
  - R12 Noun chunk root to object
  - R13 Noun chunk modifier to attribute
- 간접 영향:
  - R19 Object synonym canonicalization
  - R23 Object parent concept mapping
  - R25 Count export

### Count-table impact

- object count: merged MWE가 object root로 잡히면 object label이 바뀐다.
- attribute count: compound modifier가 object MWE 안으로 흡수되면 attribute count가 줄 수 있다.
- object-attribute pair count: 위 attribute 감소가 pair count 감소로 이어질 수 있다.
- action count: 직접 영향 없음.
- agent/patient pair count: action role target object label이 바뀔 수 있다.
- relation triple count: relation source 또는 target object label이 바뀔 수 있다.
- object co-occurrence pair count: object label 변경으로 pair key가 바뀔 수 있다.

### Risk review

- false positive risk: compositional phrase를 object MWE로 merge하면 attribute 정보를 잃는다.
- false negative risk: 필요한 object MWE를 누락하면 spaCy가 POS/dependency를 틀릴 수 있다.
- lost information: `color/material/size + noun` 같은 modifier 정보가 사라질 수 있다.
- interaction with existing rules: R7은 R4에서 merge된 token을 무조건 `NOUN/NN`으로 보정하므로 lexicon precision이 중요하다.

### Reversibility

- source column: `object_mwes.tsv`의 `source`
- rule_id: protected span metadata의 R4, Stage 3 rule_ids의 R7
- metadata: protected span `kind=object_mwe`, `canonical`, `source`
- rollback path: 해당 lexicon row 삭제 후 Stage 2부터 재실행

### Verification plan

- unit test: 대표 phrase가 merge되고 header-only가 empty로 동작하는 기존 Stage 2 test 유지
- small sample inspection: object MWE 적용 전후 20~100개 caption token/noun chunk/raw concept 비교
- regression comparison: object count, attribute count, object-attribute pair count 변화량 확인

### Decision

- status: proposed
- rationale: object MWE lexicon을 채우기 전 부작용 검토 기준을 고정한다.

## 2026-07-03 - Object MWE Lexicon Side-Effect Review

### Proposed change

- 변경 대상: `resources/lexicons/object_mwes.tsv`에 실제 object MWE entries를 추가하기 전 부작용 검토
- target stage: Stage 2 spaCy preprocessing, Stage 3 POS correction, Stage 4 raw concept extraction, Stage 5 canonicalization, Stage 6 count export
- target rule id: R4, R7, R11, R12, R13, R19, R23, R25

### Existing rules affected

- 직접 영향:
  - R4 Object MWE merge: lexicon phrase가 발견되면 여러 token을 하나의 token으로 merge한다.
  - R7 Object MWE POS correction: R4에서 merge된 token을 `POS=NOUN`, `TAG=NN`으로 강제한다.
  - R11 Noun chunking: merge와 POS correction 이후 noun chunk root가 달라질 수 있다.
  - R12 Noun chunk root to object: merged phrase 전체가 object mention이 될 수 있다.
  - R13 Noun chunk modifier to attribute: MWE 내부 modifier token이 사라지면 attribute extraction이 줄어든다.
- 간접 영향:
  - R16/R17 event role: agent/patient target label이 기존 head noun에서 merged phrase로 바뀔 수 있다.
  - R18 relation: relation source/target label이 기존 head noun에서 merged phrase로 바뀔 수 있다.
  - R19 object synonym canonicalization: 현재 Stage 5는 `object_mwes.tsv`의 `canonical` metadata를 직접 사용하지 않고 raw object lemma를 lookup한다.
  - R23 object parent concept mapping: parent lookup도 Stage 5 canonical object 기준이므로 object synonym/parent lexicon과 동기화가 필요하다.
  - R25 count export: object, object-attribute pair, agent/patient pair, relation triple, object co-occurrence key가 바뀔 수 있다.

### Count-table impact

- object count:
  - 좋은 경우: `traffic light`가 `light + traffic attribute`가 아니라 `traffic light` object로 세어진다.
  - 주의점: source 교집합에 들어온 phrase는 기존 head noun object를 full phrase object로 바꾼다. 예: `stone wall`이 후보에 들어오면 기존 `wall` count가 `stone wall` count로 바뀐다.
- attribute count:
  - MWE 내부의 `amod` 또는 `compound` token은 Stage 4에서 modifier로 보이지 않게 된다.
  - probe 결과 `stone wall`을 MWE로 넣으면 `stone` attribute가 사라졌고, `young woman`을 MWE로 넣으면 `young` attribute가 사라졌다.
- object-attribute pair count:
  - attribute count 감소와 함께 `wall-stone`, `woman-young`, `phone-cell` 같은 pair가 사라질 수 있다.
- action count:
  - action mention 자체는 직접 영향이 없다.
- agent/patient pair count:
  - action target이 `phone`에서 `cell phone`, `woman`에서 `young woman`처럼 바뀔 수 있다.
- relation triple count:
  - relation source 또는 target이 MWE object label로 바뀔 수 있다.
- object co-occurrence pair count:
  - canonical object label이 달라지면 pair key가 달라진다.

### Risk review

- false positive risk:
  - object dataset과 WordNet noun MWE의 교집합에도 compositional-looking phrase가 포함될 수 있다.
  - v1에서는 `attribute + head noun` 같은 추가 semantic guard를 만들지 않는다.
  - R7이 merged token을 무조건 noun으로 보정하므로, source 교집합에 들어온 phrase는 parser와 count에 강하게 반영된다.
- false negative risk:
  - 진짜 lexicalized object MWE가 빠지면 head noun으로 축소되고 modifier attribute가 생긴다.
  - probe 결과 `traffic light`를 넣지 않으면 `light` object와 `traffic` attribute로 나뉜다.
- lost information:
  - MWE merge는 내부 token을 하나로 합치기 때문에 내부 modifier의 attribute evidence가 사라진다.
  - 이 손실은 별도 guard rule로 막지 않고, source-based object MWE 정책의 known side effect로 기록하고 전후 count 변화로 측정한다.
- interaction with existing rules:
  - quote merge가 object MWE merge보다 먼저 실행된다. quote span 내부의 MWE는 quote token으로 먼저 merge되므로 object MWE matching 대상에서 빠질 수 있다.
  - object MWE merge가 hyphen merge보다 먼저 실행된다. hyphenated object가 object MWE lexicon에 없으면 R5 hyphen merge는 되더라도 R7 object MWE POS correction은 받지 않는다.
  - spaCy `filter_spans`가 overlapping span 중 긴 span을 우선 보존하므로 nested MWE entry가 있으면 긴 phrase가 우선된다.
  - 현재 `object_mwes.tsv`의 `canonical` 컬럼은 Stage 2 protected span metadata에만 남고, Stage 4 raw object lemma나 Stage 5 canonicalization에는 직접 연결되지 않는다.

### Probe result

- command:
  - `.\scripts\run_python.ps1 -c ...`로 `annotate_text()`와 `extract_raw_concepts_from_stage3_record()`를 직접 호출했다.
- probe cases:
  - `A traffic light stands near the road.`
  - `A stone wall stands beside a road.`
  - `A young woman holds a cell phone.`
- observed:
  - no MWE `traffic light`: object=`light`, attribute=`traffic`.
  - with MWE `traffic light`: object=`traffic light`, `traffic` attribute 없음.
  - no MWE `stone wall`: object=`wall`, attribute=`stone`.
  - with MWE `stone wall`: object=`stone wall`, `stone` attribute 없음.
  - no MWE `young woman` and `cell phone`: object=`woman`, attribute=`young`; object=`phone`, attribute=`cell`.
  - with MWE `young woman` and `cell phone`: object=`young woman`, object=`cell phone`; `young`, `cell` attribute 없음.
- interpretation:
  - object MWE merge는 head noun object와 internal modifier attribute를 full phrase object로 바꾸는 강한 선택이다.
  - 이 선택은 GPIC caption별 땜빵 rule이나 `attribute + head noun` guard가 아니라 외부 source 교집합 기준으로만 결정한다.
  - `object_mwes.tsv`의 `canonical` 값을 실제 canonical count에 반영하려면 Stage 5 `object_synonyms.tsv` 또는 parent lexicon과 별도 동기화가 필요하다.

### Reversibility

- source column:
  - `object_mwes.tsv`의 `source`를 반드시 채워서 어떤 외부 source 또는 audit 기준에서 온 entry인지 보존한다.
- rule_id:
  - Stage 2 protected span metadata에는 `R4`.
  - Stage 3 rule ids에는 object MWE가 있을 때 `R7`.
  - Stage 4 object mention은 여전히 `R12`.
- metadata:
  - protected span metadata에 `kind=object_mwe`, `text`, `canonical`, `source`, char/token span이 남는다.
  - 단, Stage 4 raw mention에는 현재 이 metadata가 직접 복사되지 않는다.
- rollback path:
  - 문제가 되는 lexicon row를 삭제하고 Stage 2부터 재실행하면 된다.
  - 이미 생성된 Stage 3~6 산출물은 해당 lexicon 상태에 종속되므로 함께 재생성해야 한다.

### Verification plan

- unit test:
  - object MWE: source-approved phrase가 하나의 object token으로 merge되는지 확인.
  - side effect fixture: `stone wall` 같은 phrase를 lexicon에 넣으면 internal attribute가 사라진다는 현재 side effect를 명시.
  - canonical metadata limitation: object MWE protected span canonical이 Stage 5 canonical object로 자동 반영되지 않는 현재 동작을 명시.
- small sample inspection:
  - object MWE lexicon 후보를 넣기 전후로 20~100 caption의 token, noun chunk, raw mentions, raw edges를 비교한다.
- regression comparison:
  - object count, attribute count, object-attribute pair count 변화량을 먼저 본다.
  - agent/patient pair와 relation triple은 target/source label drift를 확인한다.

### Decision

- status: proposed
- rationale:
  - object MWE는 필요하지만, 넣는 순간 attribute count와 object label을 강하게 바꾼다.
  - v1에서는 사람이 만든 `attribute + head noun` 예외 rule을 추가하지 않는다.
  - 후보 선정은 외부 visual object dataset과 WordNet noun MWE 교집합 같은 source-based 기준으로 한다.
  - source-based 기준 때문에 생기는 modifier attribute 손실은 hidden repair로 막지 않고 count 변화로 드러낸다.
  - object MWE canonical과 Stage 5 object canonical/parent lexicon은 동기화해야 한다.

## 2026-07-03 - COCO WordNet Candidate Build

### Proposed change

- 변경 대상: COCO instances 2017 category labels를 WordNet noun synset과 매칭한 후보 TSV 생성
- target stage: candidate generation only
- target rule id: active extraction rule 없음

### Existing rules affected

- 직접 영향:
  - 없음. active `resources/lexicons/object_mwes.tsv`, `object_synonyms.tsv`, `object_parents.tsv`는 수정하지 않았다.
- 간접 영향:
  - 이후 승인되면 R4, R7, R19, R23 입력 후보가 될 수 있다.

### Count-table impact

- object count: 현재 없음. 후보 파일만 생성했다.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - COCO label만으로 WordNet synset을 고르면 다의어 문제가 생긴다.
  - 예: `hot dog`의 exact WordNet query는 여러 noun synset을 반환하며, 첫 synset은 음식이 아니다.
- false negative risk:
  - exact WordNet noun synset만 인정하면 `cell phone`, `wine glass`, `stop sign`처럼 실제 object MWE지만 exact query가 없는 label이 빠진다.
- lost information:
  - 후보 생성 단계에서는 없음. active lexicon에 반영하지 않았다.
- interaction with existing rules:
  - 생성된 후보가 나중에 active object MWE로 들어가면 R4/R7 side effect가 발생한다.
  - 이 초기 실험에서 `synset.lemma_names()[0]`을 canonical처럼 기록한 것은 deprecated 처리한다.
  - OEWN surface 후보는 `synset_lemmas` 전체 목록으로 보존한다.
  - canonical probe rule은 이후 `2026-07-04 - COCO Canonical Surface Probe v1`에서 별도로 정의했다.
  - parent는 이 후보 생성 단계에서 active parent로 확정하지 않는다.

### Reversibility

- source column:
  - `resources/source_labels/coco_instances_2017_categories.tsv`
  - `resources/source_labels/coco_wordnet_candidates.tsv`
  - note: 이번 `coco_instances_2017_categories.tsv`는 표준 COCO instances 2017 80 detection category list를 repo source label 파일로 만든 것이다. official annotation JSON을 이 turn에서 직접 parse하지는 않았다.
- rule_id:
  - 없음. 후보 파일 생성 단계다.
- metadata:
  - `wordnet_query`, `wordnet_synset_count`, `all_wordnet_synsets`, `decision_basis`를 남긴다.
- rollback path:
  - 후보 파일과 스크립트를 삭제하면 active pipeline 결과에는 영향이 없다.

### Verification plan

- unit test:
  - 아직 없음. 후보 생성 스크립트는 compile check와 실행 결과로 확인했다.
- small sample inspection:
  - COCO 80 rows 전체를 생성하고 multiword 후보와 ambiguous/no-match rows를 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1~6 regression은 아직 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - COCO 80 category label source를 생성했다.
  - official COCO annotation JSON parse는 아직 하지 않았다.
  - `resources/source_labels/coco_wordnet_candidates.tsv`를 생성했다.
  - 결과: rows=80, exact WordNet noun matched rows=75, unambiguous rows=31, ambiguous rows=44, multiword rows=15, object MWE candidates=9.
  - WordNet synset이 여러 개인 label은 자동 canonical/parent를 채우지 않고 ambiguity로 남긴다.

## 2026-07-04 - COCO Supercategory + WordNet Lexname Disambiguation

### Proposed change

- Change target: improve `resources/source_labels/coco_wordnet_candidates.tsv` candidate generation only.
- Add COCO `supercategory` to `resources/source_labels/coco_instances_2017_categories.tsv`.
- When a COCO label has multiple exact WordNet noun synsets, filter candidate synsets by a documented mapping from COCO supercategory to WordNet lexicographer file category, exposed by `Synset.lexname()`.
- If exactly one synset remains after this filter, select it and tag the selection as `selected_by_coco_supercategory_wordnet_lexname`.
- If multiple synsets remain, leave the row ambiguous and tag it as `ambiguous_after_coco_supercategory_wordnet_lexname`.
- target stage: candidate generation only
- target rule id: active extraction/canonicalization rule 없음

### Existing rules affected

- Direct impact: none. Active Stage 2 object MWE lexicon and Stage 5 object synonym/parent lexicons are not updated.
- Indirect impact: if these candidates are later promoted into active lexicons, they can affect R4, R7, R19, and R23.

### Count-table impact

- object count: no current impact.
- attribute count: no current impact.
- object-attribute pair count: no current impact.
- action count: none.
- agent/patient pair count: no current impact.
- relation triple count: no current impact.
- object co-occurrence pair count: no current impact.

### Risk review

- false positive risk:
  - COCO supercategory and WordNet lexname are both coarse categories. They can reduce ambiguity but do not prove the exact intended sense.
  - `noun.artifact` can still contain several plausible artifact senses, so rows with multiple matched artifact synsets must stay ambiguous.
- false negative risk:
  - A correct visual synset may be excluded if the COCO supercategory-to-lexname mapping is too narrow.
  - For v1 candidate generation, ambiguous rows are retained instead of forced.
- lost information:
  - None in active pipeline. Candidate TSV preserves `all_wordnet_synsets`, `matched_wordnet_lexnames`, and `synset_selection_tag`.
- interaction with existing rules:
  - No active extraction rule changes.
  - Later promotion must still pass a separate R4/R7/R19/R23 review.

### Reversibility

- source column:
  - `supercategory`
  - `target_wordnet_lexnames`
  - `matched_wordnet_lexnames`
  - `synset_selection_tag`
- rule_id: none, candidate-generation only.
- metadata: candidate rows retain all candidate synsets and decision tags.
- rollback path:
  - Revert `scripts/build_coco_wordnet_candidates.py`.
  - Revert generated `resources/source_labels/coco_wordnet_candidates.tsv`.
  - Revert the added `supercategory` column if needed.

### Verification plan

- unit test: not required for active pipeline because no active extraction/canonicalization behavior changes.
- small sample inspection:
  - Regenerate COCO candidates.
  - Confirm previously ambiguous rows are either selected by lexname or left ambiguous.
  - Confirm no active lexicon files under `resources/lexicons` changed.
- regression comparison:
  - No Stage 1-6 regression needed until candidates are promoted to active lexicons.

### Decision

- status: approved
- rationale:
  - The user approved using WordNet lexicographer file category as a disambiguation signal.
  - Ambiguous rows must remain ambiguous.
  - Rows resolved by this method must keep an explicit tag explaining the resolution.

## 2026-07-04 - COCO OEWN 2025 Synset Probe

### Proposed change

- 변경 대상: COCO instances 2017 category labels를 OEWN 2025 core noun synset과 매칭한 후보 TSV 생성
- target stage: candidate generation only
- target rule id: active extraction/canonicalization rule 없음

### Existing rules affected

- 직접 영향:
  - 없음. `resources/lexicons` 아래 active lexicon은 수정하지 않았다.
- 간접 영향:
  - 이후 승인되면 R4, R7, R19, R23 입력 후보가 될 수 있다.

### Count-table impact

- object count: 현재 없음. 후보 파일만 생성했다.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - COCO supercategory + OEWN lexfile로도 같은 lexfile 안에 여러 synset이 남을 수 있다.
  - 예: `hot dog`는 `noun.food` synset이 2개 남는다.
- false negative risk:
  - exact OEWN label lookup만 쓰면 `cell phone`, `wine glass`, `sports ball` 같은 COCO label이 no-synset으로 남는다.
- lost information:
  - active pipeline에는 없음. 후보 TSV만 생성했다.
- interaction with existing rules:
  - 없음. active Stage 1~6 output은 변하지 않는다.

### Reversibility

- source column:
  - `wordnet_source=oewn`
  - `wordnet_version=2025`
  - `synset_selection_tag`
- rule_id:
  - 없음. 후보 생성 단계다.
- metadata:
  - `all_oewn_synsets`
  - `all_oewn_lexfiles`
  - `matched_oewn_lexfiles`
- rollback path:
  - `resources/source_labels/coco_oewn2025_synset_candidates.tsv`와 `scripts/build_coco_oewn_candidates.py`를 제거하면 active pipeline에는 영향이 없다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 turn에서는 추가하지 않는다.
- small sample inspection:
  - COCO 80 rows 전체를 생성하고 summary, ambiguous rows, no-synset rows, `hot dog` row를 확인했다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1~6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - `wn==1.1.0`을 설치하고 OEWN 2025 core를 `resources/wn_data`에 설치했다.
  - output: `resources/source_labels/coco_oewn2025_synset_candidates.tsv`
  - report: `docs/coco_oewn2025_synset_probe.md`
  - summary: rows=80, OEWN noun matched rows=75, selected rows=51, ambiguous rows=24, no-synset rows=5.
  - `hot dog`는 OEWN 2025에서도 ambiguous로 남는다.
  - Wikidata P8814는 `07692347-n`, `07713282-n`을 제공하며 OEWN 2025의 `oewn-07692347-n`, `oewn-07713282-n`에 매핑된다. 하지만 둘 다 `noun.food`라 단일 선택 근거로는 부족하다.

## 2026-07-04 - COCO Object MWE 후보 + OEWN 2025+ Lookup Recovery

### Proposed change

- 변경 대상: COCO instances 2017 category labels를 OEWN 2025+ 기준으로 다시 조회하고, Object MWE 후보 관리용 lookup recovery 및 synset selection evidence를 TSV로 생성한다.
- target stage: candidate generation only
- target rule id: active extraction/canonicalization rule 없음

### Existing rules affected

- 직접 영향:
  - 없음. `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - 이후 승인되면 R4 Object MWE merge, R7 Object MWE POS correction, R19 Object synonym canonicalization, R23 Object parent concept mapping의 입력 후보가 될 수 있다.

### Count-table impact

- object count: 현재 없음. 후보 파일만 생성한다.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - separator 제거는 `cell phone -> cellphone`, `wine glass -> wineglass`처럼 orthographic variant를 복구할 수 있지만, 일반적으로는 다른 표면형을 만들 수 있다.
  - 이 위험을 줄이기 위해 첫 성공 lookup case와 실제 selected query를 TSV에 기록하고, active lexicon에는 자동 반영하지 않는다.
- false negative risk:
  - `sports ball`처럼 head fallback이 필요한 label은 unresolved로 남는다.
  - `potted plant -> pot plant` 같은 semantic alias는 이번 safe recovery에 포함하지 않는다.
- lost information:
  - 후보 생성 단계에서는 없음.
  - active Object MWE lexicon으로 승격할 때는 별도 R4/R7 side-effect review가 필요하다.
- interaction with existing rules:
  - 없음. candidate TSV만 생성한다.

### Reversibility

- source column:
  - `selected_lookup_case`
  - `selected_query`
  - `synset_selection_tag`
  - `mwe_candidate_status`
- rule_id:
  - 없음. 후보 생성 단계다.
- metadata:
  - `all_oewn_synsets`
  - `matched_oewn_lexfiles`
  - `wn30_lemma_counts`
- rollback path:
  - 생성된 `resources/source_labels/coco_oewn2025plus_synset_candidates.tsv`와 스크립트 변경을 되돌리면 active pipeline에는 영향이 없다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - COCO 80 rows 전체를 생성한다.
  - `skis`, `cell phone`, `wine glass`, `potted plant`, `sports ball`, `hot dog` row를 확인한다.
  - summary에서 selected, ambiguous, unresolved count를 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1-6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - 사용자가 COCO부터 해당 규칙으로 다시 진행하라고 승인했다.
  - 이번 변경은 active extraction/canonicalization/count rule이 아니라 source label candidate generation이다.
- output: `resources/source_labels/coco_oewn2025plus_synset_candidates.tsv`
- report: `docs/coco_oewn2025plus_synset_probe.md`
- summary: rows=80, OEWN noun matched rows=79, selected rows=77, ambiguous rows=2, unresolved rows=1.
- key result: `cell phone` and `wine glass` were recovered by joined variant; `skis` by Morphy; sense-key WN3.0 count fallback selected 22 more rows; `potted plant` unresolved; `hot dog` and `cake` ambiguous.
  - lookup은 fixed-priority sequential 방식으로 수행하고, 첫 성공 case만 선택하되 그 case와 query는 반드시 기록한다.
## 2026-07-04 - COCO OEWN Objectness Lexfile Gate

### Proposed change

- 변경 대상: COCO OEWN 2025+ source label candidate generation에 objectness lexfile gate 추가
- target stage: candidate generation only
- target rule id: active extraction/canonicalization rule 없음

### Existing rules affected

- 직접 영향: active pipeline 없음. 후보 TSV만 재생성한다.
- 간접 영향: 이후 active object MWE/synonym/parent 후보 승격 시 conditional/hard-conflict 후보를 manual-check 대상으로 분리한다.

### Risk review

- false positive risk: `sportsball` 같은 단일 synset hard-conflict 항목이 selected로 들어가는 것을 줄인다.
- false negative risk: `traffic light`, `stop sign`처럼 conditional lexfile이지만 실제 physical object인 label도 manual-check로 빠진다.
- lost information: 없음. 후보 row에는 all synsets와 objectness gate를 보존한다.

### Decision

- status: implemented
- rationale: 사용자가 object-compatible, conditional, hard-conflict lexfile 기준을 승인했다.
- result: rows=80, matched=79, selected=67, ambiguous=12, unresolved=1, manual_check=6.

## 2026-07-04 - COCO Manual Label Synset Decisions

### Proposed change

- 변경 대상: `resources/source_labels/coco_oewn2025plus_synset_candidates.tsv` 생성 단계에 COCO label-level manual decision 추가
- target stage: candidate generation only
- target rule id: active extraction/canonicalization rule 없음

### Existing rules affected

- 직접 영향:
  - active pipeline 없음. `resources/lexicons` 아래 active lexicon은 수정하지 않았다.
- 간접 영향:
  - 이후 active object MWE, object synonym, object parent 후보로 승격할 때 manual decision source를 근거로 사용할 수 있다.

### Count-table impact

- object count: 현재 없음. 후보 파일만 재생성했다.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - manual label decision은 COCO label에만 적용된다. 일반 caption token이나 다른 dataset label에는 확장하지 않는다.
  - `book`은 content sense가 아니라 artifact sense로 고정했으므로, 이 결정은 COCO object category 맥락에서만 타당하다.
- false negative risk:
  - `sports ball`은 COCO physical object지만 OEWN 2025+에 적절한 physical ball synset이 없어 selected synset을 비웠다.
- lost information:
  - 없음. rejected row에도 original lookup result와 manual note를 보존한다.
- interaction with existing rules:
  - manual select는 objectness gate보다 우선한다.
  - manual reject는 selected synset을 비우고 `manual_rejected_bad_oewn_synset`으로 태깅한다.

### Reversibility

- source column:
  - `manual_decision`
  - `manual_decision_note`
  - `synset_selection_tag`
- rule_id:
  - 없음. 후보 생성 단계다.
- metadata:
  - `selected_lookup_case`
  - `selected_query`
  - `selected_oewn_synset`
  - `selected_oewn_lexfile`
- rollback path:
  - `MANUAL_COCO_LABEL_DECISIONS` entry를 제거하고 후보 파일을 재생성하면 된다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - manual decision rows 13개를 확인한다.
  - `book`이 `oewn-02873453-n` artifact로 선택됐는지 확인한다.
  - `sports ball`이 selected 없이 reject로 남았는지 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1-6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - 사용자가 COCO ambiguous/manual 항목은 제안한 해석대로 진행하고, `book`은 artifact sense로 고정하라고 승인했다.
  - output: `resources/source_labels/coco_oewn2025plus_synset_candidates.tsv`
  - report: `docs/coco_oewn2025plus_synset_probe.md`
  - result: rows=80, selected=78, manual_selected=11, manual_rejected=1, unresolved=1.
  - master log: `docs/lexicon_build_history_v1.md`
  - correction: `potted plant -> pot plant` semantic alias는 땜빵룰이므로 제거했다. `potted plant`는 자동 lookup rule로 unresolved로 둔다.

## 2026-07-04 - Lexicon Build Master Log

### Proposed change

- 변경 대상: lexicon 후보 생성 이력과 dataset별 rule을 한 파일에 모은 master log 추가
- target stage: documentation only
- target rule id: active extraction/canonicalization rule 없음

### Existing rules affected

- 직접 영향: 없음.
- 간접 영향: 이후 dataset별 lexicon 후보 생성 시 COCO 전용 rule을 다른 dataset에 잘못 재사용하지 않도록 한다.

### Decision

- status: implemented
- rationale:
  - COCO supercategory evidence는 COCO에서만 쓰는 rule임을 명시했다.
  - Open Images, LVIS, Visual Genome, Objects365, V3Det, ImageNet 계열은 각 dataset metadata에 맞는 evidence를 따로 써야 한다고 기록했다.
  - master log: `docs/lexicon_build_history_v1.md`

## 2026-07-04 - Canonical Naming Clarification

### Proposed change

- 변경 대상: COCO/OEWN 후보 생성 단계에서 final canonical처럼 보이는 이름 제거
- target stage: candidate generation documentation and TSV schema
- target rule id: active extraction/canonicalization rule 없음

### Existing rules affected

- 직접 영향:
  - active pipeline 없음.
- 간접 영향:
  - 후보 TSV에서 final canonical처럼 보이는 컬럼을 제거한다. OEWN surface 후보는 `synset_lemmas` 전체 목록으로만 보존한다.

### Risk review

- false positive risk:
  - 사전 lemma 목록의 첫 표기 또는 COCO label을 final canonical로 오해하면 `cellular telephone`, `television receiver`, `pot plant` 같은 사전식 표현이 count 대표어로 고정될 수 있다.
- interaction with existing rules:
  - synset selection과 canonical selection은 다른 단계다.
  - source label과 OEWN lemma는 canonical 후보 surface일 뿐이다.

### Decision

- status: implemented
- rationale:
  - 이 결정은 `COCO Canonical Surface Probe v1` 이전의 naming cleanup 결정이다.
  - 현재 canonical probe는 source label과 형태 매칭되는 OEWN lemma만 후보로 두고, WN3.0 lemma count 단독 최대일 때만 선택한다.
  - `wordfreq`와 `SUBTLEX`는 현재 rule에서 쓰지 않는다.

## 2026-07-04 - COCO Canonical Surface Probe v1

### Proposed change

- 변경 대상: COCO OEWN 2025+ source-label candidate TSV에 canonical probe 컬럼 추가
- target stage: source-label candidate generation
- target rule id: active extraction/canonicalization rule 없음

### Rule

1. `selected_oewn_synset`이 있는 row만 대상으로 한다.
2. selected synset의 OEWN lemma 중 source label 또는 Morphy selected query와 형태적으로 연결되는 lemma만 canonical 후보로 둔다.
3. 형태 연결은 lowercase와 space, hyphen, underscore separator 차이만 무시한다.
   - `morphy`로 lookup이 성공한 경우 selected query도 canonical 후보 근거로 쓴다.
   - dataset별 semantic alias는 자동 canonical 후보 근거로 쓰지 않는다.
4. canonical 후보가 하나뿐이면 WN3.0 `lemma.count()`가 0이어도 `canonical_surface`로 선택한다.
   - 이 경우 count는 선택 기준이 아니라 기록값이다.
   - WN3.0 count mapping이 없어서 `-1`로 기록되는 경우도 후보가 하나뿐이면 선택한다.
5. canonical 후보가 둘 이상이면 WN3.0 `lemma.count()`를 비교한다.
6. count가 0보다 큰 단독 최대값이면 `canonical_surface`로 선택한다.
7. count가 모두 0이거나 동률이면 ambiguous로 둔다.
8. source label과 형태 매칭되는 WordNet lemma가 없으면 ambiguous로 둔다.
9. `wordfreq`와 `SUBTLEX`는 MWE/phrase count coverage가 불안정해서 쓰지 않는다.

### Risk review

- false positive risk:
  - 같은 synset 안의 unrelated lemma가 canonical으로 튀어나오는 것을 막기 위해 source label과 형태 매칭되는 lemma만 후보로 둔다.
- false negative risk:
  - `skis -> ski`처럼 Morphy로 연결되는 항목은 selected query를 canonical 후보 근거로 쓸 수 있다.
  - `potted plant -> pot plant` 같은 dataset-specific semantic alias는 쓰지 않으므로 unresolved로 남는다.
- interaction with existing rules:
  - active lexicon에는 아직 반영하지 않는다.
  - ambiguous row는 active canonical lexicon 승격 대상이 아니다.

### Decision

- status: implemented
- output: `resources/source_labels/coco_oewn2025plus_synset_candidates.tsv`
- report: `docs/coco_oewn2025plus_canonical_probe.md`
- result:
  - rows=80
  - selected_oewn_rows=78
  - canonical_selected_rows=76
  - canonical_ambiguous_rows=2
  - not_applicable_no_selected_synset=2

## 2026-07-04 - COCO Immediate Hypernym Parent Evidence

### Proposed change

- 변경 대상: `resources/source_labels/coco_oewn2025plus_synset_candidates.tsv`에 selected OEWN synset의 1-hop hypernym parent evidence를 추가한다.
- target stage: source-label candidate generation
- target rule id: active extraction/canonicalization rule 없음. future R23 input evidence 후보.
- rule generality classification:
  - general rule

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons/object_parents.tsv` 또는 active R23 lookup은 수정하지 않는다.
- 간접 영향:
  - 이후 source-label 후보를 active object parent lexicon으로 승격할 때 R23 입력 evidence로 쓸 수 있다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.
- future parent count:
  - selected synset 하나가 1개 이상의 immediate hypernym을 가질 수 있으므로 parent count는 object count와 1:1일 필요가 없다.

### Risk review

- false positive risk:
  - OEWN hypernym이 너무 세부적이거나 WordNet식 taxonomic label일 수 있다.
  - parent display label 하나를 임의 선택하면 오해가 생길 수 있으므로 synset id와 lemma 목록을 함께 저장한다.
- false negative risk:
  - unresolved 또는 rejected row는 parent evidence가 비어 있다.
  - broad parent category까지 올리지 않으므로 `animal`, `artifact` 같은 상위 category가 바로 나오지 않을 수 있다.
- lost information:
  - 없음. 모든 immediate hypernym을 보존한다.
- interaction with existing rules:
  - canonical surface 선택과 parent evidence 생성을 분리한다.
  - parent는 selected OEWN synset 기준이며, canonical surface string 기준으로 다시 조회하지 않는다.

### Reversibility

- source column:
  - `parent_oewn_synsets`
  - `parent_oewn_lexfiles`
  - `parent_lemmas`
  - `parent_selection_tag`
- rule_id:
  - 없음. 후보 생성 단계다.
- metadata:
  - parent synset id, parent lexfile, parent lemma list, parent selection tag.
- rollback path:
  - 해당 컬럼 생성 코드를 제거하고 후보 TSV를 재생성하면 된다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - COCO 80 rows 전체를 재생성한다.
  - `dog`, `car`, `person`, `hot dog`, `sports ball`, `potted plant`의 parent evidence를 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1~6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - 사용자가 parent를 하나로 선정하지 않고 해당 canonical/synset의 모든 1차 hypernym을 parent로 두는 방향을 승인했다.
  - 이 변경은 active R23 변경이 아니라 COCO source-label 후보 TSV에 parent evidence를 추가하는 것이다.

## 2026-07-05 - COCO Canonical Official Label Surface Tie-Break

### Proposed change

- 변경 대상: COCO OEWN 2025+ source-label candidate TSV의 canonical surface 선택 rule.
- target stage: source-label candidate generation.
- target rule id: active extraction/canonicalization rule 없음. future R19 input evidence 후보.
- rule generality classification:
  - source-specific evidence rule

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons/object_synonyms.tsv` 또는 active R19 lookup은 수정하지 않는다.
- 간접 영향:
  - 이후 source-label 후보를 active object synonym/canonical lexicon으로 승격할 때 canonical 후보 evidence로 쓸 수 있다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.
- future canonical object count:
  - WN3.0 lemma count로 정하지 못한 canonical 후보 중 COCO official label surface와 정확히 일치하는 후보가 selected로 바뀐다.

### Risk review

- false positive risk:
  - source official label을 final canonical로 오해할 수 있다. 이 rule은 selected synset 내부 canonical 후보 사이의 tie-break로만 사용한다.
  - exact surface match는 lowercase와 whitespace normalization만 허용한다. separator removal은 허용하지 않는다.
- false negative risk:
  - official label과 형태가 다른 더 자연스러운 canonical이 있을 수 있다. 그러나 WN3.0 count가 판단하지 못한 경우에는 source label을 우선한다.
- lost information:
  - 없음. `canonical_candidate_lemmas`와 `canonical_candidate_lemma_counts`는 보존한다.
- interaction with existing rules:
  - synset selection과 canonical surface selection은 분리한다.
  - lookup recovery의 separator-insensitive form match와 official label tie-break의 exact surface match는 다르다.

### Reversibility

- source column:
  - `canonical_surface`
  - `canonical_selection_tag`
  - `canonical_candidate_lemmas`
  - `canonical_candidate_lemma_counts`
- rule_id:
  - 없음. 후보 생성 단계다.
- metadata:
  - selection tag에 `selected_by_source_label_surface_after_wn30_all_zero` 또는 `selected_by_source_label_surface_after_wn30_tie`를 남긴다.
- rollback path:
  - source label tie-break 코드를 제거하고 후보 TSV를 재생성하면 된다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - COCO 80 rows 전체를 재생성한다.
  - 기존 ambiguous였던 `backpack`, `hot dog`가 official label surface로 canonical selected가 되는지 확인한다.
  - `potted plant`는 unresolved 상태로 유지되는지 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1~6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - 사용자가 WN3.0 count가 모두 0이거나 동률이면 source dataset official label surface와 동일한 lemma를 canonical로 선택하는 방향을 승인했다.
  - Google Ngram fallback은 현재 COCO ambiguous 2개가 official label tie-break로 해결되므로 구현하지 않고 future fallback으로만 둔다.

## 2026-07-05 - Objects365 Source-Label Synset Candidates

### Proposed change

- 변경 대상: Objects365 V2 category labels를 OEWN 2025+ source-label candidate TSV로 생성한다.
- target stage: source-label candidate generation.
- target rule id: active extraction/canonicalization rule 없음. future R4/R19/R23 input evidence 후보.
- rule generality classification:
  - source-specific evidence rule

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - 이후 active object MWE, object synonym, object parent 후보로 승격할 때 입력 evidence가 될 수 있다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - Superseded risk note: Objects365 label이 COCO label key와 같더라도 dataset별 의미가 다를 수 있다. 이 섹션 작성 당시에는 같은 normalized label key를 reuse 대상으로 보았으나, 이후 폐기됐다.
  - Objects365에는 COCO supercategory 같은 direct evidence를 적용하지 않는다.
- false negative risk:
  - 같은 의미지만 surface가 다른 label은 자동 reuse하지 않는다. 예: separator, slash, synonym 차이가 있으면 새 label로 처리한다.
  - semantic alias, head fallback, one-off rescue mapping을 쓰지 않으므로 unresolved가 남을 수 있다.
- lost information:
  - Superseded note: source dataset, source label, source category id, reuse source를 TSV에 보존한다는 old design이었다. 이후 duplicate-only metadata로 대체됐다.
- interaction with existing rules:
  - Deprecated: COCO 후보 TSV를 Objects365 builder 내부 lookup cache처럼 쓰는 방식은 dataset-specific shortcut이므로 제거 대상이다.
  - Corrected policy: dataset별 후보 생성기는 자기 source label만 처리하고, 누적 비교는 별도 통합 inventory TSV에서 수행한다.

### Reversibility

- source column:
  - `label_key`
  - `reused_from_existing_label`
  - `reused_from_datasets`
  - `reused_from_label`
  - `reused_from_selected_oewn_synset`
- rule_id:
  - 없음. 후보 생성 단계다.
- metadata:
  - Objects365 source url, source class, category id, lookup case, selection tag, ambiguity status.
- rollback path:
  - Objects365 source/candidate/ambiguous TSV와 script 변경을 제거하면 active pipeline에는 영향이 없다.

### Verification plan

- superseded:
  - 아래 항목은 COCO-specific reuse design의 old verification plan이며 현재 절차가 아니다.
- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - Objects365 V2 365 labels를 생성한다.
  - COCO overlap reuse count를 확인한다.
  - ambiguous, unresolved, rejected rows를 별도 TSV로 뽑는다.
  - reused row가 OEWN lookup을 다시 하지 않고 COCO evidence를 복사했는지 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1~6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - Deprecated note: exact normalized label key가 같으면 COCO 처리 결과를 reuse하는 방식으로 구현했으나, 이후 사용자 검토에서 dataset-specific 땜빵 rule로 판정되어 제거하기로 했다.
  - output:
    - `resources/source_labels/objects365_v2_categories.tsv`
    - `resources/source_labels/objects365_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/objects365_oewn2025plus_ambiguous.tsv`
    - `resources/source_labels/objects365_oewn2025plus_unresolved.tsv`
  - report: `docs/objects365_oewn2025plus_synset_probe.md`
  - result: rows=365, COCO exact label-key reuse=69, new lookup rows=296, selected total=233, ambiguous-like rows=71, unresolved-like rows=61.
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않았다.
  - execution note: source download and later TSV rewrite required `require_escalated`; this produced the generated files but does not mean sandbox permission behavior was fixed.

## 2026-07-05 - Replace COCO-Specific Objects365 Reuse With Integrated Source Inventory

### Proposed change

- 변경 대상:
  - `scripts/build_objects365_oewn_candidates.py`에서 COCO 후보 TSV 직접 참조 제거.
  - dataset별 후보 TSV를 누적하는 통합 inventory builder 추가.
- target stage:
  - source-label candidate generation only.
  - active Stage 1~6 extraction/canonicalization/count rule 변경 없음.
- target rule id:
  - active extraction rule 없음.
  - lexicon build history rule `1.7 Dataset 누적은 통합 inventory TSV에서만 한다`.
- rule generality classification:
  - general rule.

### Existing rules affected

- deprecated:
  - Objects365 builder가 `coco_oewn2025plus_synset_candidates.tsv`를 직접 읽고 같은 `label_key`를 reuse하던 구현.
- preserved:
  - dataset별 OEWN lookup recovery rule.
  - manual decision row 기록.
  - source label, selected synset, objectness gate evidence 기록.

### Count-table impact

- object count: 없음.
- attribute count: 없음.
- object-attribute pair count: 없음.
- action count: 없음.
- agent/patient pair count: 없음.
- relation triple count: 없음.
- object co-occurrence pair count: 없음.
- active lexicon에 아직 반영하지 않으므로 Stage 1~6 output count에는 영향 없음.

### Risk review

- false positive risk:
  - dataset별 builder가 이전 dataset 결과를 자동 reuse하지 않으므로 cross-dataset shortcut으로 잘못된 synset이 전파될 위험이 줄어든다.
- false negative risk:
  - 이전에는 overlap label이 reuse로 selected가 되었던 경우도 다시 lookup하면 ambiguous/unresolved가 될 수 있다.
  - 이 변화는 source-label candidate 단계의 보수적 변화이며, conflict/inventory에서 확인 가능하다.
- lost information:
  - 없음. COCO row와 Objects365 row는 통합 inventory에서 별도 row로 보존한다.
- interaction with existing rules:
  - canonical surface는 dataset 누적 후 다시 계산해야 한다는 기존 원칙과 맞는다.
  - parent evidence는 selected synset 기반 evidence로 보존 가능하지만 active parent lexicon 생성은 별도 단계다.

### Reversibility

- source column:
  - `dataset`
  - `source_label`
  - `source_label_key`
  - `source_file`
  - `selected_oewn_synset`
- metadata:
  - `selection_status`, `synset_selection_tag`, `manual_decision`, `decision_basis`.
- rollback path:
  - 새 inventory script와 generated inventory TSV를 제거하고 Objects365 builder의 COCO reuse를 되돌리면 이전 상태로 돌아간다.

### Verification plan

- code check:
  - `rg COCO_CANDIDATES scripts/build_objects365_oewn_candidates.py`가 match 없음.
- generation:
  - Objects365 candidates를 bounded runner로 다시 생성한다.
  - integrated inventory TSV를 생성한다.
- output checks:
  - Objects365 summary에서 `reused_rows=0`.
  - integrated inventory에 COCO와 Objects365 row가 모두 존재.
  - conflict TSV가 생성됨.

### Decision

- status: implemented
- rationale:
  - 사용자가 dataset-specific COCO reuse를 명시적으로 거부했고, 일반적인 누적 TSV 기반 workflow를 요구했다.

## 2026-07-05 - Prior Inventory Exact Label Duplicate Skip

### Proposed change

- 변경 대상:
  - source-label candidate generation에서 새 dataset label의 `lowercase + whitespace normalize` key가 prior integrated inventory에 이미 있으면 duplicate로 표시하고 semantic 처리를 생략한다.
- target stage:
  - source-label candidate generation only.
  - active Stage 1~6 extraction/canonicalization/count rule 변경 없음.
- target rule id:
  - active extraction rule 없음.
  - lexicon build history rule `1.7 Dataset 누적은 통합 inventory TSV에서만 한다`.
- rule generality classification:
  - general rule.

### Existing rules affected

- preserved:
  - COCO 파일을 직접 읽지 않는다.
  - prior evidence source는 통합 inventory TSV다.
- changed:
  - duplicate label key row는 OEWN lookup을 하지 않는다.
  - duplicate label key row는 selected synset, canonical surface, parent evidence를 비워 둔다.
  - duplicate label key row는 `selection_status=duplicate_existing_label_key`로 기록한다.
  - duplicate label key row는 semantic synset inventory에 넣지 않고 별도 duplicate TSV에만 기록한다.

### Count-table impact

- object count: 없음.
- attribute count: 없음.
- object-attribute pair count: 없음.
- action count: 없음.
- agent/patient pair count: 없음.
- relation triple count: 없음.
- object co-occurrence pair count: 없음.
- active lexicon에 아직 반영하지 않으므로 Stage 1~6 output count에는 영향 없음.

### Risk review

- false positive risk:
  - exact normalized label key가 같지만 dataset별 의도가 다른 경우에도 duplicate로 스킵될 수 있다.
  - 이 경우 semantic 정보는 prior inventory row만 남고 새 dataset row는 duplicate evidence가 된다.
- false negative risk:
  - duplicate row에서 dataset-specific synset 차이를 발견하지 못한다.
  - 사용자가 의도한 바가 "동일 label은 중복으로만 기록"이므로 이 tradeoff를 수용한다.
- lost information:
  - source dataset, source label, source category id, prior duplicate dataset/label/synset metadata는 보존한다.
  - 새 dataset row에서 OEWN candidate list는 생성하지 않는다.
- interaction with existing rules:
  - integrated inventory builder는 duplicate row를 `object_source_label_duplicates.tsv`로 분리한다.
  - conflict report는 semantic inventory row만 비교하므로 duplicate row는 conflict를 만들지 않는다.

### Reversibility

- source column:
  - `selection_status`
  - `duplicate_existing_label_key`
  - `duplicate_existing_datasets`
  - `duplicate_existing_labels`
  - `duplicate_existing_selected_oewn_synsets`
- metadata:
  - `decision_basis=duplicate_existing_label_key`.
- rollback path:
  - duplicate skip helper를 제거하고 source-label candidate TSV를 재생성하면 된다.

### Verification plan

- code check:
  - Objects365 builder가 COCO candidate TSV를 직접 참조하지 않는지 확인한다.
  - Objects365 builder가 integrated inventory를 prior source로 읽는지 확인한다.
- generation:
  - Objects365 candidates를 bounded runner로 다시 생성한다.
  - integrated inventory TSV를 다시 생성한다.
- output checks:
  - Objects365 summary에 `duplicate_existing_label_key_rows`가 존재한다.
  - duplicate row의 `selected_oewn_synset`, `canonical_surface`, `parent_oewn_synsets`가 비어 있는지 확인한다.

### Decision

- status: implemented
- rationale:
  - 사용자가 Objects365 처리 시 prior integrated inventory와 exact normalized duplicate이면 중복으로만 표시하고 synset/canonical/parent 처리를 하지 않는 규칙을 명시했다.
  - output:
    - `resources/source_labels/objects365_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/objects365_oewn2025plus_ambiguous.tsv`
    - `resources/source_labels/objects365_oewn2025plus_unresolved.tsv`
    - `resources/source_labels/object_source_label_synset_inventory.tsv`
    - `resources/source_labels/object_source_label_duplicates.tsv`
    - `resources/source_labels/object_source_label_synset_conflicts.tsv`
  - result:
    - Objects365 rows=365
    - duplicate_existing_label_key_rows=69
    - OEWN lookup rows=296
    - selected rows=230
    - rejected rows=6
    - unresolved rows=60
    - ambiguous rows=0
    - integrated semantic inventory rows=376
    - integrated duplicate rows=69
    - integrated source occurrence rows=445
    - integrated conflict label keys=0

## 2026-07-05 - Objects365 Object-Compatible First WN30 Synset Selection

### Proposed change

- 변경 대상: Objects365 OEWN 2025+ source-label candidate generation에서 여러 synset 후보가 있을 때의 WN3.0 lemma count 선택 순서.
- target stage: source-label candidate generation only.
- target rule id: active extraction/canonicalization rule 없음. future R4/R19/R23 input evidence 후보.
- rule generality classification:
  - source-specific evidence rule

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - Objects365 후보 TSV의 `selected_oewn_synset`, `synset_selection_tag`, `objectness_gate`가 바뀔 수 있다.
  - 이후 active object MWE, object synonym, object parent 후보로 승격할 때 입력 evidence가 달라질 수 있다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - 이 rule은 source object label의 standalone noun synset을 하나 고정하는 기준이다.
  - source label이 실제로는 compound noun의 일부, abbreviation, typo, dataset-specific shorthand인 경우에도 standalone noun sense가 선택될 수 있다.
  - 특히 `noun.person`도 object-compatible에 포함되어 있으므로 일부 label은 사람/집단 noun sense로 선택될 수 있다.
- false negative risk:
  - object-compatible 후보 안에서 count가 모두 0이거나 동률이면 non-object 후보를 보지 않고 ambiguous로 둔다.
  - 사용자가 승인한 범위 안에서 subtype audit이나 semantic rescue rule은 추가하지 않는다.
- lost information:
  - 없음. 모든 OEWN 후보와 WN3.0 count evidence는 TSV에 보존한다.
- interaction with existing rules:
  - COCO exact label-key reuse 방식은 이후 폐기됐다.
  - 현재 기준으로는 Objects365 모든 row가 Objects365 source label 자체로 lookup된다.
  - 이 rule은 Objects365 row 중 multiple synset 후보에만 적용한다.
  - object-compatible 후보가 하나 이상이면 그 후보군 안에서만 WN3.0 count를 비교한다.
  - object-compatible 후보가 없으면 전체 후보에서 WN3.0 count를 비교하고, 이후 objectness gate가 conditional 또는 hard conflict를 ambiguous로 돌린다.

### Reversibility

- source column:
  - `selected_oewn_synset`
  - `selected_oewn_lexfile`
  - `selected_oewn_objectness_class`
  - `objectness_gate`
  - `wn30_selection_tag`
  - `wn30_lemma_counts`
  - `synset_selection_tag`
- rule_id:
  - 없음. 후보 생성 단계다.
- metadata:
  - `synset_selection_tag`에 object-compatible-first 선택 또는 ambiguous reason을 남긴다.
- rollback path:
  - `scripts/build_objects365_oewn_candidates.py`의 object-compatible-first helper를 제거하고 후보 TSV를 재생성하면 된다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - Objects365 365 rows를 재생성한다.
  - ambiguous-like, unresolved-like, selected total 변화를 확인한다.
  - `Baseball`, `Basketball`, `Volleyball`, `Ring`, `Target`, `French` 같은 기존 conflict label의 선택 결과를 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1~6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - 사용자가 object-compatible 후보를 먼저 보고, 없을 때만 object-compatible 외 후보의 WN3.0 count를 기록하는 방향을 승인했다.
  - subtype audit이나 추가 one-off guard는 넣지 않는다.
  - output:
    - `resources/source_labels/objects365_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/objects365_oewn2025plus_ambiguous.tsv`
    - `resources/source_labels/objects365_oewn2025plus_unresolved.tsv`
  - superseded result: 이 시점의 `COCO exact label-key reuse=69` 결과는 이후 dataset-specific shortcut으로 폐기됐다.
  - current result after integrated-inventory correction: rows=365, OEWN lookup rows=365, selected total=288, ambiguous-like rows=10, unresolved-like rows=61.
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않았다.
  - observed note:
    - `Target`, `French`, `Speaker`, `Scale`, `Tissue` 같은 row는 object-compatible-first count로 selected됐다.
    - 이는 standalone object-label noun synset 선택 기준에서는 허용되는 결과다.
    - 다만 source label이 compound noun 일부나 dataset-specific shorthand인지 여부는 별도 source-label 품질 이슈로 남긴다.
  - execution note:
    - compile check는 sandbox 안에서 통과했다.
    - TSV regeneration은 sandbox `PermissionError` 후 같은 좁은 명령을 `require_escalated`로 실행해 생성했다.

## 2026-07-05 - Objects365 Remaining Ambiguous Label Decisions v2

### Proposed change

- 변경 대상: Objects365 OEWN 2025+ source-label candidate generation에서 남은 ambiguous label 20개를 사용자가 승인한 결정으로 처리한다.
- target stage: source-label candidate generation only.
- target rule id: active extraction/canonicalization rule 없음. future R4/R19/R23 input evidence 후보.
- rule generality classification:
  - explicit user-approved manual decision

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - Objects365 후보 TSV에서 남은 ambiguous row가 selected 또는 rejected로 바뀐다.
  - selected 중 후보가 여러 개라 첫 번째 허용 후보를 고른 row는 일반 manual select와 별도 tag로 남긴다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - 선택은 사용자가 승인한 Objects365 label에만 적용된다.
  - "후보 중 첫 번째 허용 후보"로 고른 row는 의미적으로 완전 확정한 row와 구분하기 위해 `selected_by_manual_objects365_first_allowed_candidate_review`로 tag한다.
- false negative risk:
  - 사용자가 reject한 label은 MWE 후보로도 쓰지 않는다.
  - typo correction이나 label rescue mapping은 추가하지 않는다. `Noddles`는 `Noodles`로 고치지 않는다.
- lost information:
  - 없음. all OEWN candidates와 lookup evidence는 TSV에 유지한다.
- interaction with existing rules:
  - lookup query를 바꾸지 않는다.
  - manual reject는 selected synset을 비우고 `selection_status=rejected`로 남긴다.
  - manual select는 selected synset만 지정하고, first-allowed 선택 여부를 `synset_selection_tag`에 남긴다.

### Reversibility

- source column:
  - `manual_decision`
  - `manual_decision_note`
  - `selected_oewn_synset`
  - `selection_status`
  - `synset_selection_tag`
- rule_id:
  - 없음. 후보 생성 단계다.
- metadata:
  - first-allowed select: `manual_decision=select:{synset_id}`, `synset_selection_tag=selected_by_manual_objects365_first_allowed_candidate_review`
  - reject: `manual_decision=reject:{synset_id}`, `synset_selection_tag=manual_rejected_objects365_label_review`
- rollback path:
  - manual decision table에서 해당 label을 제거하고 후보 TSV를 재생성하면 된다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - Objects365 365 rows를 재생성한다.
  - remaining ambiguous-like count, rejected row count, manual first-allowed selected row count를 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1~6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - 사용자가 남은 Objects365 ambiguous label의 keep/reject 판단을 제공했다.
  - keep 중 다수는 "여러 후보 중 첫 번째 허용 후보"라는 정책적 선택이므로 별도 tag가 필요하다.
  - output:
    - `resources/source_labels/objects365_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/objects365_oewn2025plus_ambiguous.tsv`
    - `resources/source_labels/objects365_oewn2025plus_unresolved.tsv`
  - result: rows=365, selected total=298, manual selected rows=43, manual first-allowed selected rows=14, manual rejected rows=6, ambiguous-like rows=0, unresolved-like rows=61.
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않았다.
  - execution note:
    - compile check는 sandbox 안에서 통과했다.
    - normal TSV regeneration은 sandbox `PermissionError`로 실패했다.
    - 같은 좁은 generation command를 `require_escalated`로 실행해 TSV를 생성했다.

## 2026-07-05 - Objects365 Manual Ambiguous Synset Decisions v1

### Proposed change

- 변경 대상: Objects365 OEWN 2025+ source-label candidate generation에서 사용자가 명시한 ambiguous label의 synset을 manual select로 고정한다.
- target stage: source-label candidate generation only.
- target rule id: active extraction/canonicalization rule 없음. future R4/R19/R23 input evidence 후보.
- rule generality classification:
  - explicit user-approved manual decision

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - Objects365 후보 TSV의 ambiguous row 일부가 selected row로 바뀐다.
  - 이후 active object MWE, object synonym, object parent 후보로 승격할 때 입력 evidence가 달라질 수 있다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - manual selection은 사용자가 승인한 Objects365 label에만 적용된다.
  - 같은 surface가 다른 dataset이나 caption token에 등장해도 이 결정이 자동 rule로 확장되지는 않는다.
- false negative risk:
  - 사용자가 list에 포함하지 않은 ambiguous row는 그대로 ambiguous로 남는다.
  - 이미 selected인 row는 이번 요청에 따라 건드리지 않는다.
- lost information:
  - 없음. all OEWN candidates와 lookup evidence는 TSV에 유지한다.
- interaction with existing rules:
  - manual select는 objectness gate보다 우선하고, `objectness_gate=manual_override`로 기록한다.
  - lookup query를 바꾸지 않는다. manual decision은 selected synset만 바꾼다.

### Reversibility

- source column:
  - `manual_decision`
  - `manual_decision_note`
  - `selected_oewn_synset`
  - `synset_selection_tag`
- rule_id:
  - 없음. 후보 생성 단계다.
- metadata:
  - `manual_decision=select:{synset_id}`
  - `synset_selection_tag=selected_by_manual_objects365_label_review`
- rollback path:
  - manual decision table에서 해당 label을 제거하고 후보 TSV를 재생성하면 된다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - Objects365 365 rows를 재생성한다.
  - manual-selected row count와 remaining ambiguous-like count를 확인한다.
  - 사용자가 제공했지만 이미 selected였던 labels는 unchanged인지 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1~6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - 사용자가 ChatGPT 검토 결과를 바탕으로 ambiguous row에 대한 manual synset 선택을 승인했다.
  - 이미 selected된 row는 이번 요청에 따라 건드리지 않는다.
  - 사용자 설명만으로 exact synset을 충분히 고르기 어려운 `Van`, `Projector`, `Printer`, `Pasta`, `Dumpling`은 ambiguous로 남겼다.
  - output:
    - `resources/source_labels/objects365_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/objects365_oewn2025plus_ambiguous.tsv`
    - `resources/source_labels/objects365_oewn2025plus_unresolved.tsv`
  - result: rows=365, selected total=284, manual selected rows=43, ambiguous-like rows=20, unresolved-like rows=61.
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않았다.
  - execution note:
    - compile check는 sandbox 안에서 통과했다.
    - TSV regeneration은 sandbox `PermissionError` 후 같은 좁은 명령을 `require_escalated`로 실행해 생성했다.

## 2026-07-05 - OpenImages Remaining Ambiguous Label Decisions v2

### Proposed change

- 변경 대상: OpenImages OEWN 2025+ source-label candidate generation에서 남은 ambiguous label 16개를 사용자가 명시한 synset으로 manual select한다.
- target stage: source-label candidate generation only.
- target rule id: active extraction/canonicalization rule 없음. future R4/R19/R23 input evidence 후보.
- rule generality classification:
  - explicit user-approved manual decision

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - OpenImages 후보 TSV의 remaining ambiguous rows가 selected rows로 바뀐다.
  - 이후 active object MWE, object synonym, object parent 후보로 승격할 때 입력 evidence가 달라질 수 있다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - 사용자가 label과 selected synset을 명시한 OpenImages row에만 적용된다.
  - lookup query를 바꾸지 않으므로 semantic rescue rule로 확장되지 않는다.
- false negative risk:
  - 없음. 이번 변경 대상은 사용자가 승인한 remaining ambiguous rows 전체다.
- lost information:
  - 없음. all OEWN candidates와 lookup evidence는 TSV에 유지한다.
- interaction with existing rules:
  - manual select는 objectness gate보다 우선하고, `objectness_gate=manual_override`로 기록한다.
  - `Squid`는 사용자가 first object-compatible fallback으로 승인한 row이므로 first-allowed selection tag를 남긴다.

### Reversibility

- source column:
  - `manual_decision`
  - `manual_decision_note`
  - `selected_oewn_synset`
  - `synset_selection_tag`
- rule_id:
  - 없음. 후보 생성 단계다.
- metadata:
  - `manual_decision=select:{synset_id}`
  - `synset_selection_tag=selected_by_manual_openimages_label_review`
  - `Squid` only: `synset_selection_tag=selected_by_manual_openimages_first_allowed_candidate_review`
- rollback path:
  - manual decision table에서 해당 label을 제거하고 후보 TSV를 재생성하면 된다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - OpenImages 601 rows를 재생성한다.
  - remaining ambiguous count가 0인지 확인한다.
  - manual-selected row count와 integrated inventory status를 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1~6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - 사용자가 남은 OpenImages ambiguous label 16개에 대해 selected synset을 명시했다.
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않는다.
  - output:
    - `resources/source_labels/openimages_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/openimages_oewn2025plus_ambiguous.tsv`
    - `resources/source_labels/openimages_oewn2025plus_unresolved.tsv`
    - `resources/source_labels/object_source_label_synset_inventory.tsv`
    - `resources/source_labels/object_source_label_duplicates.tsv`
    - `resources/source_labels/object_source_label_synset_conflicts.tsv`
  - result:
    - OpenImages rows=601, selected=359, duplicate_existing_label_key=180, rejected=2, unresolved=60, ambiguous=0.
    - OpenImages manual selected rows=62, manual rejected rows=2.
    - integrated inventory rows=797, selected=667, rejected=9, unresolved=121, ambiguous=0, conflict_label_keys=0.
  - execution note:
    - compile check는 sandbox 안에서 통과했다.
    - OpenImages TSV regeneration은 sandbox OEWN sqlite DB access error 후 같은 좁은 명령을 `require_escalated`로 실행해 생성했다.
    - integrated inventory regeneration은 sandbox same-directory temp file `PermissionError` 후 같은 좁은 명령을 `require_escalated`로 실행해 생성했다.

## 2026-07-05 - OpenImages Manual Selection Tag Correction v1

### Proposed change

- 변경 대상: OpenImages manual-selected rows 중 parent evidence로 선택된 labels가 `selected_by_manual_openimages_first_allowed_candidate_review`로 잘못 기록된 metadata를 정정한다.
- target stage: source-label candidate generation only.
- target rule id: active extraction/canonicalization rule 없음. source-label candidate metadata correction.
- rule generality classification:
  - explicit user-approved manual decision

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - selected synset은 바꾸지 않는다.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - OpenImages 후보 TSV의 `synset_selection_tag`와 `manual_decision_note`가 실제 선택 근거에 맞게 바뀐다.

### Count-table impact

- object count: 없음.
- attribute count: 없음.
- object-attribute pair count: 없음.
- action count: 없음.
- agent/patient pair count: 없음.
- relation triple count: 없음.
- object co-occurrence pair count: 없음.

### Risk review

- false positive risk:
  - selected synset을 바꾸지 않으므로 concept candidate 자체의 false positive risk는 변하지 않는다.
  - metadata tag만 근거에 맞게 바꾼다.
- false negative risk:
  - 없음.
- lost information:
  - 없음. 기존 tag가 fallback이었음을 문서 history에 남긴다.
- interaction with existing rules:
  - parent evidence로 선택 가능한 labels는 `selected_by_manual_openimages_label_review`로 기록한다.
  - 이번 정정 대상 labels 안에서는 `Squid`만 first-allowed tag로 유지한다.
  - 이번 정정 대상 밖의 기존 first-allowed rows는 건드리지 않는다.

### Reversibility

- source column:
  - `synset_selection_tag`
  - `manual_decision_note`
- rule_id:
  - 없음. 후보 생성 metadata correction이다.
- rollback path:
  - manual decision table의 `selection_tag`와 note를 이전 값으로 되돌리고 후보 TSV를 재생성하면 된다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - OpenImages 후보 TSV를 재생성한다.
  - `Shorts`, `Cocktail`, `Tin can`, `Stretcher`, `Whisk`, `Food`, `Panda`, `Saucer`, `Lavender`, `Honeycomb`, `Butterfly`, `Animal`, `Platter`가 label-review tag인지 확인한다.
  - `Pastry`는 이미 label-review tag인지 확인한다.
  - `Squid`는 first-allowed tag로 유지되는지 확인한다.
- regression comparison:
  - selected synset과 active lexicon을 바꾸지 않으므로 Stage 1~6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - 사용자가 해당 labels는 fallback이 아니라 OpenImages parent evidence로 선택 가능하다고 정정했다.
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않는다.
  - corrected labels:
    - `Shorts`, `Cocktail`, `Tin can`, `Stretcher`, `Whisk`, `Food`, `Panda`, `Saucer`, `Lavender`, `Honeycomb`, `Butterfly`, `Animal`, `Platter`
  - already correct:
    - `Pastry`
  - result:
    - 위 corrected labels는 `selected_by_manual_openimages_label_review`로 변경됐다.
    - `Pastry`는 기존처럼 `selected_by_manual_openimages_label_review`로 유지됐다.
    - `Squid`는 `selected_by_manual_openimages_first_allowed_candidate_review`로 유지됐다.
    - OpenImages manual tag counts: label-review selected=56, first-allowed selected=6, rejected=2.
    - OpenImages status counts unchanged: selected=359, duplicate_existing_label_key=180, rejected=2, unresolved=60, ambiguous=0.
    - integrated inventory status counts unchanged: selected=667, rejected=9, unresolved=121.
  - execution note:
    - compile check는 sandbox 안에서 통과했다.
    - OpenImages TSV regeneration은 sandbox same-directory temp file `PermissionError` 후 같은 좁은 명령을 `require_escalated`로 실행해 생성했다.
    - integrated inventory regeneration은 sandbox same-directory temp file `PermissionError` 후 같은 좁은 명령을 `require_escalated`로 실행해 생성했다.

## 2026-07-05 - Object Plus Conditional WN30 Ranking Pool v1

### Proposed change

- 변경 대상: Objects365/OpenImages OEWN 2025+ source-label candidate generation에서 여러 synset 후보가 있을 때 WN3.0 lemma count를 비교하는 우선 후보군을 바꾼다.
- 기존:
  - `object-compatible` 후보가 있으면 그 후보군 안에서만 WN3.0 lemma count를 비교한다.
  - `object-compatible` 후보가 없을 때만 전체 후보를 본다.
- 변경:
  - `object-compatible + conditional` 후보군을 먼저 만들고 그 안에서 WN3.0 lemma count를 비교한다.
  - 이 후보군에 단독 positive max가 있으면 provisional candidate로 삼고, 기존 objectness gate를 적용한다.
  - selected candidate가 `object-compatible`이면 pass, `conditional`이면 기존 rule대로 ambiguous/manual-check가 된다.
  - `object-compatible + conditional` 후보군이 없을 때만 나머지 후보군을 본다.
- target stage: source-label candidate generation only.
- target rule id: active extraction/canonicalization rule 없음. future R4/R19/R23 input evidence 후보.
- rule generality classification:
  - source-specific evidence rule

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - `resources/lexicons` 아래 active lexicon은 수정하지 않는다.
- 간접 영향:
  - Objects365/OpenImages 후보 TSV에서 selected/ambiguous status가 바뀔 수 있다.
  - `conditional` sense가 WN3.0 count상 더 일반적인 경우, 이전에는 무시되던 conditional candidate가 objectness gate로 올라와 ambiguous/manual-check가 될 수 있다.
  - 이후 active object MWE, object synonym, object parent 후보로 승격할 때 입력 evidence가 달라질 수 있다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - conditional candidate는 objectness gate에서 자동 selected가 아니라 ambiguous/manual-check로 빠지므로 자동 false positive risk는 제한된다.
  - object-compatible candidate는 기존과 같이 objectness gate pass다.
- false negative risk:
  - object-compatible 후보보다 conditional 후보가 count상 우세하면 기존 selected row가 ambiguous로 바뀔 수 있다.
  - 이는 더 일반적인 sense가 conditional일 수 있다는 문제를 반영하기 위한 의도된 보수화다.
- lost information:
  - 없음. all OEWN candidates와 WN3.0 count evidence는 TSV에 유지한다.
- interaction with existing rules:
  - objectness gate는 유지한다.
  - manual decisions는 기존처럼 objectness gate 이후에 적용한다.
  - lookup query와 lookup recovery rule은 바꾸지 않는다.

### Reversibility

- source column:
  - `selected_oewn_synset`
  - `selected_oewn_lexfile`
  - `selected_oewn_objectness_class`
  - `objectness_gate`
  - `wn30_selection_tag`
  - `wn30_lemma_counts`
  - `synset_selection_tag`
  - `decision_basis`
- rule_id:
  - 없음. 후보 생성 단계다.
- rollback path:
  - Objects365/OpenImages candidate helper의 first-pool definition을 `object-compatible` only로 되돌리고 후보 TSV와 통합 inventory를 재생성하면 된다.

### Verification plan

- unit test:
  - active pipeline rule이 아니므로 이번 변경에서는 추가하지 않는다.
- small sample inspection:
  - Objects365 후보 TSV를 재생성하고 selected/ambiguous/unresolved/rejected status를 확인한다.
  - OpenImages 후보 TSV를 재생성하고 selected/ambiguous/unresolved/rejected status를 확인한다.
  - 통합 inventory를 재생성하고 status와 conflict count를 확인한다.
- regression comparison:
  - active lexicon에 반영하지 않았으므로 Stage 1~6 regression은 수행하지 않는다.

### Decision

- status: implemented
- rationale:
  - 사용자가 object-compatible보다 conditional sense가 더 일반적일 수 있음을 지적했고, conditional은 뒤쪽 objectness gate에서 manual-check로 빠지므로 ranking pool에 포함해도 자동 확정 위험이 제한된다고 승인했다.
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않는다.
  - output:
    - `resources/source_labels/objects365_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/objects365_oewn2025plus_ambiguous.tsv`
    - `resources/source_labels/objects365_oewn2025plus_unresolved.tsv`
    - `resources/source_labels/openimages_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/openimages_oewn2025plus_ambiguous.tsv`
    - `resources/source_labels/openimages_oewn2025plus_unresolved.tsv`
    - `resources/source_labels/object_source_label_synset_inventory.tsv`
    - `resources/source_labels/object_source_label_duplicates.tsv`
    - `resources/source_labels/object_source_label_synset_conflicts.tsv`
  - result:
    - Objects365 rows=365, selected=226, duplicate_existing_label_key=69, rejected=6, unresolved=60, ambiguous=4.
    - Objects365 ambiguous labels: `Ring`, `Brush`, `Target`, `French`.
    - OpenImages rows=601, selected=357, duplicate_existing_label_key=180, rejected=2, unresolved=60, ambiguous=2.
    - OpenImages ambiguous labels: `Table`, `Television`.
    - integrated inventory rows=797, selected=661, rejected=9, unresolved=121, ambiguous=6, conflict_label_keys=0.
  - execution note:
    - compile check는 sandbox 안에서 통과했다.
    - Objects365/OpenImages TSV regeneration은 sandbox OEWN sqlite DB access error 후 같은 좁은 명령을 `require_escalated`로 실행해 생성했다.
    - integrated inventory regeneration은 sandbox same-directory temp file `PermissionError` 후 같은 좁은 명령을 `require_escalated`로 실행해 생성했다.

## 2026-07-05 - Remaining Ambiguous Source Label Manual Decisions v1

### Proposed change

- 변경 대상: Object Plus Conditional WN30 ranking 이후 남은 Objects365/OpenImages ambiguous source-label rows 6개.
- 사용자가 아래 exact decision을 승인했다.

|dataset|label|decision|synset|
|---|---|---|---|
|OpenImages|`Table`|select|`oewn-04386330-n`|
|OpenImages|`Television`|select|`oewn-04413042-n`|
|Objects365|`Ring`|select|`oewn-04099721-n`|
|Objects365|`Brush`|select|`oewn-02911542-n`|
|Objects365|`Target`|select|`oewn-04401354-n`|
|Objects365|`French`|reject||

- target stage: source-label candidate generation only.
- target rule id: active extraction/canonicalization rule 없음. future R4/R19/R23 input evidence 후보.
- rule generality classification:
  - explicit user-approved manual decision

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - active `resources/lexicons/*`는 수정하지 않는다.
- 간접 영향:
  - Objects365/OpenImages 후보 TSV에서 6개 ambiguous row의 status가 selected 또는 rejected로 바뀐다.
  - 통합 source-label inventory의 selected/rejected/ambiguous status count가 바뀐다.
  - 이후 active object MWE, object synonym, object parent 후보로 승격할 때 입력 evidence가 달라질 수 있다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - manual selected labels는 source-label inventory에서 selected evidence가 된다.
  - active lexicon으로 승격하지 않았으므로 현재 extraction/count output에는 영향이 없다.
- false negative risk:
  - `French`를 reject하면 Objects365 source label inventory에서 selected evidence로 쓰지 않는다.
  - 사용자가 명시적으로 reject했으므로 unresolved/ambiguous가 아니라 rejected로 보존한다.
- lost information:
  - 없음. all OEWN candidates, selected/manual decision, rejected decision note는 TSV에 남긴다.
- interaction with existing rules:
  - lookup query는 바꾸지 않는다.
  - semantic alias나 rescue mapping을 추가하지 않는다.
  - existing objectness gate는 manual override decision 뒤에 source metadata로 남는다.

### Reversibility

- source columns:
  - `manual_decision`
  - `manual_decision_note`
  - `synset_selection_tag`
  - `decision_basis`
  - `selection_status`
  - `selected_oewn_synset`
  - `selected_oewn_lexfile`
- rollback path:
  - Objects365/OpenImages manual decision dict에서 위 6개 entries를 제거하고 candidate TSV와 통합 inventory를 재생성하면 된다.

### Verification plan

- exact row verification:
  - Objects365 ambiguous rows가 0이 되는지 확인한다.
  - OpenImages ambiguous rows가 0이 되는지 확인한다.
  - 지정한 5개 selected label의 `selected_oewn_synset`이 user-provided synset과 일치하는지 확인한다.
  - `French`가 `selection_status=rejected`로 남는지 확인한다.
- integrated inventory verification:
  - 통합 inventory의 ambiguous rows가 0이 되는지 확인한다.
  - conflict label keys가 0인지 확인한다.
- active lexicon verification:
  - `resources/lexicons/*`가 이번 작업으로 수정되지 않았는지 확인한다.

### Decision

- status: implemented
- rationale:
  - 사용자가 exact synset/reject decision을 제공했다.
  - 자동 lookup, automatic ranking, semantic alias rule을 추가하지 않는다.
  - output:
    - `resources/source_labels/objects365_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/openimages_oewn2025plus_synset_candidates.tsv`
    - `resources/source_labels/object_source_label_synset_inventory.tsv`
    - `resources/source_labels/object_source_label_duplicates.tsv`
    - `resources/source_labels/object_source_label_synset_conflicts.tsv`
  - result:
    - Objects365 rows=365, selected=229, duplicate_existing_label_key=69, rejected=7, unresolved=60, ambiguous=0.
    - OpenImages rows=601, selected=359, duplicate_existing_label_key=180, rejected=2, unresolved=60, ambiguous=0.
    - integrated inventory rows=797, selected=666, rejected=10, unresolved=121, ambiguous=0, conflict_label_keys=0.
  - exact row check:
    - `Ring -> oewn-04099721-n`
    - `Brush -> oewn-02911542-n`
    - `Target -> oewn-04401354-n`
    - `French -> rejected`
    - `Table -> oewn-04386330-n`
    - `Television -> oewn-04413042-n`
  - execution note:
    - sandbox 안에서는 atomic temp file 생성이 `PermissionError`로 실패했다.
    - 같은 bounded command를 `require_escalated` 실행 모드로 돌려 TSV를 생성했다.

## 2026-07-05 - Manual Source Label Tag Simplification v1

### Proposed change

- 변경 대상: source-label candidate TSV의 manual decision metadata tag.
- 기존 tag는 dataset별/상황별로 길게 나뉘어 있었다.
  - `selected_by_manual_objects365_label_review`
  - `selected_by_manual_objects365_first_allowed_candidate_review`
  - `selected_by_manual_openimages_label_review`
  - `selected_by_manual_openimages_first_allowed_candidate_review`
- 변경 tag:
  - `manual_select`
  - `first_object_compatible_fallback`
  - `manual_reject`
- 세부 근거는 tag가 아니라 `manual_decision_note`에 남긴다.
- target stage: source-label candidate generation metadata only.
- target rule id: active extraction/canonicalization rule 없음.
- rule generality classification:
  - explicit user-approved manual decision metadata cleanup

### Existing rules affected

- active Stage 1~6 pipeline 없음.
- active `resources/lexicons/*` 수정 없음.
- source-label candidate TSV의 `synset_selection_tag`와 `decision_basis` 문자열만 바뀐다.
- selected/rejected/ambiguous/unresolved count 자체는 바뀌지 않는다.

### Count-table impact

- object count: 없음.
- attribute count: 없음.
- object-attribute pair count: 없음.
- action count: 없음.
- agent/patient pair count: 없음.
- relation triple count: 없음.
- object co-occurrence pair count: 없음.

### Risk review

- false positive risk: 없음. synset 선택 자체를 바꾸지 않는다.
- false negative risk: 없음. synset 선택 자체를 바꾸지 않는다.
- lost information:
  - 없음. source dataset, label, manual synset, note는 그대로 유지한다.
  - dataset별 구분은 `dataset` column으로 가능하다.
- interaction with existing rules:
  - manual decision 적용 순서는 유지한다.
  - tag granularity만 줄인다.

### Reversibility

- source columns:
  - `synset_selection_tag`
  - `decision_basis`
  - `manual_decision_note`
- rollback path:
  - tag constant 문자열을 이전 dataset-specific tag로 되돌리고 candidate TSV를 재생성한다.

### Verification plan

- Objects365/OpenImages candidate TSV 재생성 후 tag count를 확인한다.
- manual selected rows가 `manual_select` 또는 `first_object_compatible_fallback`로만 표현되는지 확인한다.
- manual rejected rows가 `manual_reject`로 표현되는지 확인한다.
- 통합 inventory status count와 conflict count를 확인한다.

### Decision

- status: implemented
- rationale:
  - 사용자가 ChatGPT에서 받은 decision을 그대로 반영하는 상황이므로, tag는 manual select와 first object-compatible fallback 정도만 구분하면 충분하다고 명시했다.
  - result:
    - manual selected rows는 `manual_select` 또는 `first_object_compatible_fallback`로만 표현된다.
    - manual rejected rows는 `manual_reject`로 표현된다.
    - integrated inventory `synset_selection_tag` count:
      - `manual_select`: 115
      - `first_object_compatible_fallback`: 20
      - `manual_reject`: 10
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않았다.

## 2026-07-05 - Full Integrated Canonical Regeneration After Remaining Decisions v1

### Proposed change

- 변경 대상: 남은 Objects365/OpenImages ambiguous source-label decision과 manual tag simplification을 반영한 뒤, 통합 inventory 기준 canonical decision을 재생성한다.
- target stage: source-label candidate analysis only.
- target rule id: active Stage 1~6 rule 없음. future R19/R23 input evidence 후보.
- rule generality classification:
  - general rule

### Existing rules affected

- 직접 영향:
  - active Stage 1~6 pipeline 없음.
  - active `resources/lexicons/*`는 수정하지 않는다.
- 간접 영향:
  - `object_synset_canonical_decisions.tsv`가 COCO + Objects365 + OpenImages 통합 inventory 기준으로 갱신된다.
  - canonical tie-break에 필요한 Google Ngram evidence가 갱신된다.

### Count-table impact

- object count: 현재 없음.
- attribute count: 현재 없음.
- object-attribute pair count: 현재 없음.
- action count: 없음.
- agent/patient pair count: 현재 없음.
- relation triple count: 현재 없음.
- object co-occurrence pair count: 현재 없음.

### Risk review

- false positive risk:
  - active lexicon으로 승격하지 않았으므로 현재 extraction/count output에는 영향이 없다.
  - Ngram fallback은 selected OEWN lemma 후보끼리만 비교한다.
- false negative risk:
  - Ngram evidence가 없거나 동률이면 canonical ambiguous로 남을 수 있다.
- lost information:
  - 없음. source labels, candidate lemmas, WN3.0 counts, Ngram mean frequency를 TSV에 보존한다.
- interaction with existing rules:
  - synset selection은 바꾸지 않는다.
  - canonical surface selection만 통합 inventory 기준으로 다시 계산한다.

### Reversibility

- output:
  - `resources/source_labels/google_ngram_canonical_frequency_evidence.tsv`
  - `resources/source_labels/object_synset_canonical_decisions.tsv`
  - `resources/source_labels/object_synset_canonical_ambiguous.tsv`
- rollback path:
  - 이전 TSV snapshot으로 되돌리거나 canonical decision script를 이전 inventory/evidence 상태에서 재실행하면 된다.

### Verification plan

- bounded generation:
  - `scripts/run_script_with_timeout.py --timeout-seconds 120 scripts/build_google_ngram_canonical_frequency_evidence.py`
  - `scripts/run_script_with_timeout.py --timeout-seconds 120 scripts/build_object_synset_canonical_decisions.py`
- inspection:
  - `object_source_label_synset_inventory.tsv` status count를 확인한다.
  - `object_synset_canonical_ambiguous.tsv` row count가 0인지 확인한다.
  - Ngram evidence status가 `ok`인지 확인한다.

### Decision

- status: implemented
- rationale:
  - 사용자가 남은 synset ambiguous 처리 후 canonical과 parent까지 재생성하라고 지시했다.
  - output:
    - `resources/source_labels/google_ngram_canonical_frequency_evidence.tsv`
    - `resources/source_labels/object_synset_canonical_decisions.tsv`
    - `resources/source_labels/object_synset_canonical_ambiguous.tsv`
  - result:
    - selected inventory rows=666
    - selected synset groups=648
    - canonical selected rows=648
    - canonical ambiguous rows=0
    - Google Ngram evidence rows=8, all status=`ok`
  - newly resolved by Google Ngram:
    - `remote|Remote control -> remote`
    - `donut|Doughnut -> doughnut`
  - active lexicon impact: 없음. `resources/lexicons`는 수정하지 않았다.

## 2026-07-05 - LVIS OEWN 2025+ Source-Label Candidate Generation v1

### Proposed change

- 변경 대상: LVIS v1 category labels를 OEWN 2025+ noun synset 후보 TSV로 생성한다.
- 이 작업은 source-label 후보 생성이며 active Stage 2/Stage 5 lexicon을 수정하지 않는다.
- rule generality classification:
  - source-specific evidence rule
- target stage and rule id:
  - active extraction/canonicalization rule 없음
  - 향후 R4/R7/R19/R23 후보 evidence 생성

### Existing rules affected

- active Stage 1~6 pipeline 없음.
- active `resources/lexicons/*` 수정 없음.
- 기존 source-label 누적 원칙과 같은 흐름을 따른다.

### Rule order

LVIS `synset` metadata가 있어도 source label surface lookup을 건너뛰지 않는다.

1. LVIS `name`에서 underscore를 space로 바꾼 source label surface를 만든다.
2. prior integrated inventory에 같은 `lowercase + whitespace normalize` label key가 있으면 duplicate로 기록하고 OEWN lookup을 하지 않는다.
3. prior inventory에 없는 label만 source label surface로 OEWN 2025+ lookup을 수행한다.
4. lookup recovery는 기존 허용 범위만 사용한다.
   - exact normalized label
   - hyphen, underscore, space separator variant
   - joined separator variant
   - OEWN Morphy noun result
5. OEWN 후보가 하나면 그 후보를 선택한다.
6. OEWN 후보가 여러 개면 LVIS `synset` metadata를 후보 선택 evidence로 쓴다.
   - LVIS `synset`은 lookup query를 대체하지 않는다.
   - LVIS `synset`이 source label lookup 결과 후보 중 하나와 WordNet 3.0 sense 기준으로 맞을 때만 선택 근거가 된다.
   - LVIS `synset` metadata가 lookup 후보와 맞지 않으면 query를 바꿔서 살리지 않는다.
7. LVIS `synset`으로도 하나를 고르지 못하면 기존 WN3.0 lemma count fallback을 쓴다.
8. selected synset은 objectness gate를 통과해야 한다.
9. unresolved 또는 ambiguous는 그대로 TSV에 남기고 manual decision 전에는 자동 rescue하지 않는다.

### Count-table impact

- object count: 없음.
- attribute count: 없음.
- object-attribute pair count: 없음.
- action count: 없음.
- agent/patient pair count: 없음.
- relation triple count: 없음.
- object co-occurrence pair count: 없음.

### Risk review

- false positive risk:
  - LVIS `synset`이 source label lookup 후보 안에 있을 때만 쓰므로 semantic alias rescue 위험을 줄인다.
- false negative risk:
  - LVIS `synset` metadata가 있어도 label surface lookup이 실패하면 unresolved가 늘 수 있다.
  - 이건 현재 승인된 rule 순서를 지키기 위한 의도적 보수성이다.
- reversibility:
  - LVIS raw `name`, `synonyms`, `synset`, definition, frequency, image/instance count를 TSV에 보존한다.
  - all OEWN candidates, selected synset, selection tag를 TSV에 보존한다.
- verification plan:
  - LVIS candidate TSV 생성
  - ambiguous/unresolved TSV 생성
  - integrated inventory 재생성
  - active `resources/lexicons/*`가 수정되지 않았는지 확인

### Decision

- status: approved by user request and implemented
- rationale:
  - 사용자가 "LVIS로 synset 구해보고 ambiguous 알려줘"라고 요청했다.
  - active lexicon 승격은 하지 않는다.

## 2026-07-05 - LVIS Ambiguous Manual Decisions v1

### Proposed change

- 변경 대상: LVIS OEWN 2025+ source-label candidate generation에서 사용자가 명시한 ambiguous label 28개를 `manual_select`로 처리한다.
- rule generality classification:
  - explicit user-approved manual decision
- target stage and rule id:
  - active extraction/canonicalization rule 없음
  - source-label candidate metadata only

### Manual decisions

|label|selected synset|
|---|---|
|`award`|`oewn-06709228-n`|
|`Bible`|`oewn-06443410-n`|
|`calendar`|`oewn-06499232-n`|
|`card`|`oewn-06639513-n`|
|`diary`|`oewn-06413674-n`|
|`dollar`|`oewn-13417070-n`|
|`milestone`|`oewn-07285872-n`|
|`money`|`oewn-13406050-n`|
|`newspaper`|`oewn-06277798-n`|
|`notebook`|`oewn-06427062-n`|
|`passport`|`oewn-06512928-n`|
|`pennant`|`oewn-06888338-n`|
|`receipt`|`oewn-06532213-n`|
|`tag`|`oewn-07288121-n`|
|`birthday card`|`oewn-06639767-n`|
|`booklet`|`oewn-06425532-n`|
|`buoy`|`oewn-07280883-n`|
|`business card`|`oewn-06437074-n`|
|`identity card`|`oewn-06489042-n`|
|`checkbook`|`oewn-13435483-n`|
|`comic book`|`oewn-06608568-n`|
|`keycard`|`oewn-06489489-n`|
|`phonebook`|`oewn-06435397-n`|
|`postcard`|`oewn-06640445-n`|
|`brake light`|`oewn-07280695-n`|
|`street sign`|`oewn-06806967-n`|
|`windsock`|`oewn-07272250-n`|
|`softball`|`oewn-86432478-n`|

### Existing rules affected

- active Stage 1~6 pipeline 없음.
- active `resources/lexicons/*` 수정 없음.
- LVIS source-label candidate TSV의 ambiguous rows가 selected rows로 바뀐다.
- manual decision은 lookup query를 바꾸지 않는다.
- manual synset은 현재 OEWN lookup 후보 안에 있을 때만 허용한다.

### Count-table impact

- object count: 없음.
- attribute count: 없음.
- object-attribute pair count: 없음.
- action count: 없음.
- agent/patient pair count: 없음.
- relation triple count: 없음.
- object co-occurrence pair count: 없음.

### Risk review

- false positive risk:
  - manual decisions가 conditional lexfile까지 selected로 승격한다.
  - 다만 user-approved exact synset만 허용하고, current lookup 후보 밖 synset은 허용하지 않는다.
- false negative risk:
  - 없음. 기존 ambiguous를 selected로 고정한다.
- reversibility:
  - `manual_decision=select:<synset>`과 `manual_decision_note`에 근거를 남긴다.
  - all OEWN candidates는 TSV에 유지한다.
- verification plan:
  - LVIS candidate TSV 재생성
  - LVIS ambiguous rows가 0인지 확인
  - integrated inventory ambiguous rows가 0인지 확인
  - conflict label keys가 0인지 확인
  - active lexicon이 수정되지 않았는지 확인

### Decision

- status: implemented
- rationale:
  - 사용자가 28개 ambiguous label의 selected synset을 명시했다.
  - `softball`은 LVIS definition이 "ball used in playing softball"라 artifact ball sense로 manual select한다.
- verification result:
  - LVIS candidate TSV: `selected=771`, `duplicate_existing_label_key=313`, `unresolved=119`, `ambiguous=0`.
  - `manual_select=28`.
  - integrated inventory: `selected=1437`, `rejected=10`, `unresolved=240`, `ambiguous=0`.
  - conflict label keys: `0`.

## 2026-07-05 - Integrated Parent Evidence Preservation

### Proposed change

- 변경 대상: source-label candidate, integrated inventory, canonical decision TSV에서 selected OEWN synset의 immediate hypernym parent evidence를 보존한다.
- rule generality classification:
  - general rule
- target stage and rule id:
  - active Stage 5 R23의 source-label 후보 evidence
  - active lexicon 생성 없음

### Existing rules affected

- 새 parent 선정 rule을 만들지 않는다.
- 이미 문서화한 "selected synset의 모든 immediate hypernym을 parent evidence로 보존" 정책을 LVIS와 통합 inventory/canonical decision output에도 적용한다.
- parent 하나를 고르지 않는다.

### Count-table impact

- object count: 없음.
- attribute count: 없음.
- object-attribute pair count: 없음.
- action count: 없음.
- agent/patient pair count: 없음.
- relation triple count: 없음.
- object co-occurrence pair count: 없음.

### Risk review

- false positive risk:
  - parent evidence는 selected synset의 immediate hypernym 전체이므로, parent가 여러 개인 경우 downstream에서 하나로 오해하면 안 된다.
- false negative risk:
  - 없음. selected synset이 있는 canonical decision row는 parent evidence를 모두 채운다.
- reversibility:
  - `parent_oewn_synsets`, `parent_oewn_lexfiles`, `parent_lemmas`, `parent_selection_tag` columns로 보존한다.
  - active lexicon은 생성하지 않았으므로 source TSV 재생성으로 되돌릴 수 있다.
- verification plan:
  - LVIS candidate TSV 재생성
  - integrated inventory 재생성
  - canonical decision TSV 재생성
  - canonical decision rows의 parent evidence filled/empty count 확인

### Decision

- status: implemented
- verification result:
  - canonical decision rows: `1368`.
  - parent evidence filled rows: `1368`.
  - parent evidence empty rows: `0`.
  - active lexicon 수정 없음.

## 2026-07-05 - Apply Existing Object Source-Label Synset Rule To Visual Genome

### Proposed change

- 변경 대상: 이미 승인된 Object MWE 후보 관리 + OEWN lookup recovery + synset 선정 규칙을 Visual Genome `objects.json.zip` source label에 적용한다.
- 이 변경은 Visual Genome 전용 새 synset 선정 rule을 추가하는 것이 아니다.
- Visual Genome용 코드는 dataset input schema가 달라서 필요한 source adapter다.
- rule generality classification:
  - general rule application with dataset-specific metadata input
- target stage and rule id:
  - active extraction/canonicalization rule 없음
  - future R4/R19/R23 input evidence 후보

### Existing rules affected

- active Stage 1~6 extraction/canonicalization/count rule 변경 없음.
- active `resources/lexicons/*` 수정 없음.
- 공통 규칙:
  - source label surface 자체로 OEWN 2025+ noun lookup을 수행한다.
  - lookup recovery는 exact, separator variant, joined variant, OEWN Morphy까지만 허용한다.
  - dataset synset metadata는 lookup query를 대체하지 않는다.
  - dataset evidence로 하나를 고르지 못하고 OEWN 후보가 여러 개이면 WN3.0 lemma count fallback을 사용한다.
  - selected candidate가 conditional/hard-conflict이면 기존 objectness gate로 ambiguous/manual-check 처리한다.
  - prior integrated inventory에 같은 `label_key`가 있으면 duplicate로 기록하고 OEWN lookup을 다시 하지 않는다.
- Visual Genome metadata input:
  - 같은 `label_key`가 여러 번 나오므로 label별 non-empty synset occurrence를 집계한다.
  - non-empty synset 중 최빈 synset이 유일하면 그 synset을 dataset metadata evidence로 사용한다.
  - non-empty synset 최빈값이 동률이면 ambiguous로 둔다.
  - non-empty synset이 하나도 없으면 dataset synset evidence가 없는 것으로 보고 WN3.0 lemma count fallback을 쓴다.

### Count-table impact

- object count: 없음.
- attribute count: 없음.
- object-attribute pair count: 없음.
- action count: 없음.
- agent/patient pair count: 없음.
- relation triple count: 없음.
- object co-occurrence pair count: 없음.

### Risk review

- false positive risk:
  - Visual Genome `objects.json`의 `names`에는 clause-like/noisy labels가 포함된다.
  - 이 script는 dirty label을 semantic alias로 고치지 않고 unresolved/ambiguous로 남긴다.
  - 최빈 Visual Genome synset 자체가 noisy할 수 있으나, OEWN lookup 후보와 매칭될 때만 선택 근거로 쓴다.
- false negative risk:
  - source label surface lookup이 실패하면 Visual Genome synset metadata가 있어도 query를 바꿔 살리지 않는다.
  - 이건 semantic rescue rule을 막기 위한 의도적 제한이다.
- reversibility:
  - Visual Genome source label, label occurrence count, synset occurrence count, selected lookup case, all OEWN candidates, selection tag를 TSV에 보존한다.
  - generated TSV와 script를 제거하면 active pipeline에는 영향이 없다.
- verification plan:
  - Visual Genome candidate TSV 생성
  - ambiguous/unresolved TSV 생성
  - summary count 확인
  - active `resources/lexicons/*`가 수정되지 않았는지 확인

### Decision

- status: corrected after user feedback
- rationale:
  - 사용자는 새 Visual Genome 전용 rule을 원한 것이 아니라, 이미 정한 공통 source-label synset 선정 rule을 Visual Genome source에 적용하라고 했다.
  - 이전 표현의 `Visual Genome source-specific evidence rule`은 별도 전용룰처럼 읽히므로 수정했다.

## 2026-07-06 - Apply Visual Genome v14 Manual Noun-Synset Decisions

### Proposed change

- 변경 대상: `visual_genome_oewn2025plus_synset_candidates.tsv`의 ambiguous rows에 사용자가 제공한 v14 manual noun-synset decision overlay를 적용한다.
- rule generality classification:
  - explicit user-approved manual decision
- target stage and rule id:
  - active Stage 1~6 extraction/canonicalization/count rule 없음
  - future R4/R19/R23 input evidence 후보

### Existing rules affected

- active Stage 1~6 pipeline 없음.
- active `resources/lexicons/*` 수정 없음.
- source-label inventory에서 Visual Genome ambiguous label의 selected synset만 사용자가 제공한 `select:oewn-...-n` 값으로 고정한다.
- lookup query를 바꾸지 않는다.
- selected synset은 현재 OEWN lookup 후보 안에 있을 때만 허용한다.
- OEWN lexfile/objectness class는 reject 기준이 아니라 diagnostic metadata로 보존한다.
- `synset_selection_tag`는 `manual_select`로 단순화하고, v14의 세부 decision tag/confidence/note는 `manual_decision_note`에 보존한다.

### Count-table impact

- object count: 없음.
- attribute count: 없음.
- object-attribute pair count: 없음.
- action count: 없음.
- agent/patient pair count: 없음.
- relation triple count: 없음.
- object co-occurrence pair count: 없음.

### Risk review

- false positive risk:
  - `noun.quantity`, `noun.act`, `noun.attribute` 같은 lexfile도 noun mapping으로 selected가 된다.
  - 다만 이 TSV는 active object lexicon이 아니며, source-label noun-synset inventory에만 반영된다.
- false negative risk:
  - 없음. 기존 ambiguous rows를 selected noun synset으로 고정한다.
- reversibility:
  - `resources/source_labels/visual_genome_ambiguous_manual_decisions_v14_complete_noun_mapping.tsv`를 제거하거나 overlay loader를 비활성화하고 candidate TSV를 재생성하면 원래 ambiguous 상태로 되돌릴 수 있다.
  - `manual_decision=select:<synset>`과 `manual_decision_note`에 v14 근거를 남긴다.
- verification plan:
  - v14 TSV의 decision format, label key uniqueness, current candidate match를 확인한다.
  - Visual Genome candidate TSV 재생성
  - Visual Genome ambiguous rows가 0인지 확인
  - integrated inventory 재생성
  - canonical decision TSV 재생성
  - active lexicon이 수정되지 않았는지 확인

### Decision

- status: implemented
- rationale:
  - 사용자가 제공한 v14 file은 reject 목적이 아니라 잘못된 synset 선택 위험을 해소하기 위한 noun mapping decision이다.
  - 검증 결과 v14 decision 4,499개는 모두 current Visual Genome candidate row와 label key로 매칭되고, 선택 synset도 current `all_oewn_synsets` 안에 있다.
- verification result:
  - Visual Genome candidate TSV: `selected=13,111`, `duplicate_existing_label_key=1,350`, `unresolved=68,300`, `ambiguous=0`.
  - Visual Genome manual selected rows: `4,499`.
  - Visual Genome ambiguous TSV rows: `0`.
  - Integrated inventory: `selected=14,548`, `rejected=10`, `unresolved=68,540`, `ambiguous=0`.
  - Integrated conflict label keys: `0`.
  - Canonical decision TSV: `selected synset groups=9,192`, `canonical selected=8,145`, `canonical ambiguous=1,047`.
  - Active `resources/lexicons/*` 수정 없음.
## 2026-07-06 - Chunked Google Ngram Evidence Collection

### Proposed change

- Change only the generated-evidence collection script for Google Books Ngram canonical fallback.
- Keep the canonical decision rule unchanged:
  - compare only remaining OEWN/WordNet lemma candidates inside the selected synset group
  - use English 2019, 2000-2019, case-insensitive, smoothing 0 mean frequency
  - choose a unique positive max; otherwise leave canonical ambiguous
- Replace per-synset sequential Ngram HTTP calls with chunked unique-surface HTTP calls.
- Add a small sample mode before full evidence generation.
- Add bounded progress output and explicit output path control.

### Classification

- rule generality classification: general implementation improvement
- target rule id: active extraction/canonicalization rule 없음
- affected evidence: future R19 input evidence candidate only

### Existing rules affected

- Active Stage 1-6 extraction/canonicalization/count rules: none.
- Canonical surface selection rule: unchanged.
- Generated artifact affected:
  - `resources/source_labels/google_ngram_canonical_frequency_evidence.tsv`

### Count-table impact

- Current active count tables: none.
- Future canonical object count may change only insofar as previously missing Ngram evidence becomes available for already documented canonical fallback.

### Risk review

- false positive risk:
  - Chunking surfaces across synset groups could accidentally reuse the same surface frequency for multiple synsets. This is acceptable because Google Ngram evidence is surface-frequency evidence, not synset-specific evidence, and each output row still keeps its own `selected_oewn_synset`.
- false negative risk:
  - A failed chunk request can mark many surfaces as error/missing at once. The script must record `status` and query URL so the failure is visible and rerunnable.
- reversibility:
  - Re-run the previous evidence script version or restore the previous TSV snapshot.
  - Output TSV keeps synset id, surface, surface key, frequency fields, query URL, and status.

### Verification plan

- Compile the changed script.
- Run a 50-group sample with timeout and a sample output path.
- Inspect sample status counts.
- Run the full evidence generation with timeout only after sample succeeds.
- Re-run canonical decision generation and report selected/ambiguous counts.

### Decision

- status: approved by user in conversation
- rationale:
  - The previous implementation did about one Google request per synset group and was too slow for the Visual Genome-expanded inventory.
  - The implementation should gather the same kind of evidence with fewer requests and bounded progress rather than waiting silently.

### Verification result

- Script syntax check:
  - `run_python.ps1 -c ast.parse(...)`
  - result: `ast_ok`
- Sample run:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 120 scripts/build_google_ngram_canonical_frequency_evidence.py --limit-groups 200 --output resources/source_labels/google_ngram_canonical_frequency_evidence_sample.tsv --chunk-size 240 --request-sleep-seconds 0.05`
  - result: `candidate_synset_groups=200`, `requested_unique_surfaces=456`, `chunks=2`, `evidence_rows=456`, `synset_groups=200`
  - status: `ok=451`, `missing=5`
- Full run:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 240 scripts/build_google_ngram_canonical_frequency_evidence.py --chunk-size 240 --request-sleep-seconds 0.05`
  - result: `candidate_synset_groups=1078`, `requested_unique_surfaces=2444`, `chunks=11`, `evidence_rows=2446`, `synset_groups=1078`
  - first full run status included `error:TimeoutError=480`, so a bounded reuse run was used.
- Reuse retry:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 240 scripts/build_google_ngram_canonical_frequency_evidence.py --reuse-existing --chunk-size 40 --request-sleep-seconds 0.05`
  - result: `candidate_synset_groups=215`, `requested_unique_surfaces=493`, `chunks=13`, `evidence_rows=2446`, `synset_groups=1078`
  - final Ngram evidence status: `ok=2418`, `missing=28`
- Canonical decision regeneration:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 180 scripts/build_object_synset_canonical_decisions.py`
  - result: `selected_synset_groups=9192`, `canonical_selected_rows=9167`, `canonical_ambiguous_rows=25`
## 2026-07-06 - Single Available Positive Google Ngram Canonical Fallback

### Proposed change

- Change the canonical fallback rule inside the generated source-label canonical decision script.
- When Google Ngram fallback is reached and multiple canonical candidates exist, select the candidate if exactly one candidate has available positive Google Ngram mean frequency and the remaining candidates have missing evidence.
- Keep rows ambiguous when:
  - no candidate has positive Ngram evidence
  - two or more candidates have positive evidence but tie
  - candidate evidence is missing for all candidates

### Classification

- rule generality classification: general rule
- target rule id: active extraction/canonicalization rule 없음
- affected evidence: future R19 input evidence candidate only

### Existing rules affected

- Active Stage 1-6 extraction/canonicalization/count rules: none.
- Source-label canonical decision rule affected:
  - previous rule required at least two candidates with Ngram evidence before selecting a unique positive max.
  - new rule allows a single positive Ngram candidate to win when all other candidates are missing.

### Count-table impact

- Current active count tables: none.
- Future canonical object count may change if these source-label decisions are later promoted into active R19 object canonicalization.
- Current source-label canonical ambiguous rows are expected to decrease.

### Risk review

- false positive risk:
  - If Google Ngram fails to return a valid but rare competing surface, the available candidate can win by absence of evidence.
  - This is accepted by user direction for current source-label canonical evidence.
- false negative risk:
  - Candidates with no positive evidence still remain ambiguous.
- reversibility:
  - Revert `_select_by_google_ngram()` logic and rerun `build_object_synset_canonical_decisions.py`.
  - Generated TSVs keep candidate lemmas, WN3.0 counts, Ngram candidate surfaces, and Ngram mean frequencies.

### Verification plan

- Syntax-check `scripts/build_object_synset_canonical_decisions.py`.
- Re-run canonical decision generation.
- Report canonical selected/ambiguous counts and remaining ambiguous tags.

### Decision

- status: approved by user in conversation
- rationale:
  - User explicitly instructed: "한쪽만 Ngram있으면 그걸로 선택해".

### Verification result

- Syntax check:
  - command: `run_python.ps1 -c ast.parse(...)`
  - result: `ast_ok`
- Canonical decision regeneration:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 180 scripts/build_object_synset_canonical_decisions.py`
  - result:
    - `selected_synset_groups=9192`
    - `canonical_selected_rows=9181`
    - `canonical_ambiguous_rows=11`
    - `selected_by_single_available_positive_google_ngram=14`
- Remaining ambiguous tag counts:
  - `ambiguous_wn30_mapping_missing_google_ngram_evidence_missing=7`
  - `ambiguous_no_source_variant_or_lookup_matched_oewn_lemma=2`
  - `ambiguous_wn30_all_zero_or_missing_google_ngram_evidence_missing=1`
  - `ambiguous_wn30_mapping_missing_google_ngram_tie=1`
## 2026-07-06 - Query Single Normalized Ngram Candidate After Surface Collapse

### Proposed change

- Fix Google Ngram evidence generation for canonical candidates that collapse to a single normalized surface.
- Example:
  - OEWN lemmas: `Moon|moon`
  - Ngram candidate surfaces after `_surface_key()`: `moon`
- Previous evidence generation skipped these groups because it only queried when there were at least two normalized Ngram candidates.
- New behavior queries the single normalized candidate when the canonical decision remains unresolved and an Ngram candidate surface exists.

### Classification

- rule generality classification: general implementation correction
- target rule id: active extraction/canonicalization rule 없음
- affected evidence: future R19 input evidence candidate only

### Existing rules affected

- Canonical selection rule: unchanged.
- Google Ngram evidence coverage: expanded to unresolved groups with one normalized Ngram candidate.

### Count-table impact

- Current active count tables: none.
- Source-label canonical ambiguous rows may decrease after regenerating Ngram evidence and canonical decisions.

### Risk review

- false positive risk:
  - Low. The script only queries a surface already produced by the canonical candidate rule.
- false negative risk:
  - Reduced for case/capitalization pairs such as `Moon|moon` and `sun|Sun`.
- reversibility:
  - Revert the candidate-count condition in `build_google_ngram_canonical_frequency_evidence.py` and regenerate evidence/decision TSVs.

### Verification plan

- Directly verify Google Ngram returns `moon` and `sun`.
- Syntax-check evidence script.
- Regenerate missing evidence with `--reuse-existing`.
- Regenerate canonical decisions.
- Report remaining ambiguous rows.

### Decision

- status: approved as bug fix after user question
- rationale:
  - User correctly challenged the impossible-looking result that `moon` and `sun` were missing from Ngram.

### Verification result

- Direct evidence check:
  - `moon` has Google Ngram evidence: `mean_frequency=1.5498757347e-10`.
  - `sun` has Google Ngram evidence: `mean_frequency=1.17327472182e-09`.
- Syntax checks:
  - `scripts/build_google_ngram_canonical_frequency_evidence.py`: `ast_ok`
  - `scripts/build_object_synset_canonical_decisions.py`: `ast_ok`
- Ngram evidence regeneration:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 180 scripts/build_google_ngram_canonical_frequency_evidence.py --reuse-existing --chunk-size 40 --request-sleep-seconds 0.05`
  - result:
    - `candidate_synset_groups=9`
    - `requested_unique_surfaces=11`
    - `evidence_rows=2451`
    - `synset_groups=1083`
- Canonical decision regeneration:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 180 scripts/build_object_synset_canonical_decisions.py`
  - result:
    - `selected_synset_groups=9192`
    - `canonical_selected_rows=9186`
    - `canonical_ambiguous_rows=6`
    - `selected_by_single_available_positive_google_ngram=19`
- Specific fixed rows:
  - `oewn-09381255-n`: `canonical_surface=moon`, `canonical_selection_tag=selected_by_single_available_positive_google_ngram`
  - `oewn-09473312-n`: `canonical_surface=sun`, `canonical_selection_tag=selected_by_single_available_positive_google_ngram`

## 2026-07-06 - Apply Morphy To Canonical Source Surface Support Keys

### Proposed change

- Fix source-label canonical candidate generation so that the documented
  canonical surface variant rule is actually implemented.
- Current documented rule says source surface variants include:
  - lowercase
  - Morphy noun results
  - space/underscore variants
  - hyphen to space/underscore variants
  - joined separator variant when needed
- Current code only adds Morphy output when the original OEWN lookup itself used
  `selected_lookup_case=morphy`.
- New behavior adds OEWN Morphy noun variants for every source label when
  building canonical `support_keys`.
- This is not a label-specific mapping. It does not add `lice -> louse` or
  `fila -> filum` by hand.

### Classification

- rule generality classification: general implementation correction
- target rule id: active extraction/canonicalization rule 없음
- affected evidence: future R19 input evidence candidate only

### Existing rules affected

- Source-label canonical decision support-key generation now matches the
  already documented rule text.
- Synset selection is unchanged.
- Google Ngram fallback rule is unchanged.
- Active Stage 1-6 extraction/canonicalization/count rules are unchanged.

### Count-table impact

- Current active count tables: none.
- Source-label canonical ambiguous rows may decrease after regenerating
  canonical decisions.
- Future object canonical lexicon may include more selected canonical surfaces
  if these source-label decisions are later promoted.

### Risk review

- false positive risk:
  - Low to medium. Morphy can admit an inflected base form that did not appear
    as an OEWN lemma surface in the source label.
  - The candidate is still restricted to lemmas inside the already selected OEWN
    synset.
- false negative risk:
  - Reduced for plural/singular source labels such as `lice -> louse`.
- reversibility:
  - Remove Morphy-derived support keys and rerun
    `build_object_synset_canonical_decisions.py`.
  - Generated TSVs keep source labels, selected queries, selected lookup cases,
    synset lemmas, candidate lemmas, and selection tags.

### Verification plan

- Syntax-check `scripts/build_object_synset_canonical_decisions.py`.
- Regenerate canonical decisions with bounded runner.
- Verify `lice` and `fila` rows.
- Report remaining ambiguous rows and tag counts.

### Decision

- status: approved as bug fix after user correction
- rationale:
  - User pointed out that the documented canonical variant rule already includes
    Morphy, and the implementation was not applying it to source label support
    keys.

### Verification result

- Syntax checks:
  - `scripts/build_object_synset_canonical_decisions.py`: `ast_ok`
  - `scripts/build_google_ngram_canonical_frequency_evidence.py`: `ast_ok`
- First canonical regeneration after Morphy support-key fix:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 180 scripts/build_object_synset_canonical_decisions.py`
  - result:
    - `canonical_selected_rows=9187`
    - `canonical_ambiguous_rows=5`
  - specific rows:
    - `lice`: `canonical_surface=louse`
    - `fila`: `canonical_surface=filum`
- Ngram evidence regeneration for newly opened candidates:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 180 scripts/build_google_ngram_canonical_frequency_evidence.py --reuse-existing --chunk-size 40 --request-sleep-seconds 0.05`
  - result:
    - `candidate_synset_groups=5`
    - `requested_unique_surfaces=8`
    - `evidence_rows=2453`
    - `synset_groups=1084`
    - evidence status: `ok=2425`, `missing=28`
- Final canonical regeneration:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 180 scripts/build_object_synset_canonical_decisions.py`
  - result:
    - `selected_synset_groups=9192`
    - `canonical_selected_rows=9188`
    - `canonical_ambiguous_rows=4`
  - specific rows:
    - `lice`: `canonical_surface=louse`, `canonical_selection_tag=selected_single_source_or_lookup_matched_synset_lemma`
    - `fila`: `canonical_surface=filum`, `canonical_selection_tag=selected_single_source_or_lookup_matched_synset_lemma`
    - `horse flies`: `canonical_surface=horse fly`, `canonical_selection_tag=selected_by_google_ngram_frequency_unique_max`

## 2026-07-06 - Normalize Google Ngram Possessive Apostrophe Spacing

### Proposed change

- Fix Google Books Ngram evidence matching for possessive phrases.
- Google returns possessive phrases as `men 's`, `men 's room`,
  `cat 's foot`, and `cat 's feet` even when the requested source surfaces
  are `men's`, `men's room`, `cat's foot`, and `cat's feet`.
- The existing `_surface_key()` did not collapse this API spelling, so valid
  Google Ngram records were incorrectly written as `status=missing`.
- Also prefer the `(All)` case-insensitive aggregate record when Google returns
  both `(All)` and case variants for the same surface key.

### Classification

- rule generality classification: general implementation correction
- target rule id: active extraction/canonicalization rule 없음
- affected evidence: future R19 input evidence candidate only

### Existing rules affected

- Canonical surface selection rule is unchanged.
- Google Ngram evidence parsing now matches the already intended surface-key
  behavior more faithfully.
- Active Stage 1-6 extraction/canonicalization/count rules are unchanged.

### Count-table impact

- Current active count tables: none.
- Source-label canonical ambiguous rows decrease after regenerated evidence.
- Future object canonical lexicon may use these corrected source-label
  decisions if promoted later.

### Risk review

- false positive risk:
  - Low. The normalization is limited to possessive apostrophe-s spacing:
    `word 's` -> `word's`.
  - It does not collapse arbitrary quoted or apostrophe-containing expressions.
- false negative risk:
  - Reduced for possessive canonical candidates.
- reversibility:
  - Revert `_surface_key()` possessive normalization and `_records_by_surface_key()`
    aggregate preference, then regenerate Ngram evidence and canonical decisions.

### Verification plan

- Directly query Google Ngram for `men's`, `men's room`, `cat's foot`,
  and `cat's feet`.
- Syntax-check the changed scripts.
- Regenerate the complete Ngram evidence TSV without `--reuse-existing`.
- Regenerate canonical decisions.
- Verify the affected rows and remaining ambiguous count.

### Decision

- status: implemented
- rationale:
  - User correctly challenged the impossible-looking result that `men's` and
    `cat's feet` had no Google Ngram evidence.

### Verification result

- Direct Google Ngram query returned positive evidence for all four surfaces,
  but with API surfaces spelled as `men 's`, `men 's room`, `cat 's foot`,
  and `cat 's feet`.
- Syntax check:
  - `scripts/build_object_synset_canonical_decisions.py`: `ast_ok`
  - `scripts/build_google_ngram_canonical_frequency_evidence.py`: `ast_ok`
- Full Ngram evidence regeneration:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 300 scripts/build_google_ngram_canonical_frequency_evidence.py --chunk-size 40 --request-sleep-seconds 0.05`
  - result:
    - `candidate_synset_groups=1084`
    - `requested_unique_surfaces=2454`
    - `evidence_rows=2457`
    - `synset_groups=1084`
    - evidence status: `ok=2437`, `missing=20`
- Canonical decision regeneration:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 180 scripts/build_object_synset_canonical_decisions.py`
  - result:
    - `selected_synset_groups=9192`
    - `canonical_selected_rows=9191`
    - `canonical_ambiguous_rows=1`
- Specific fixed rows:
  - `oewn-03751977-n`: `men's=1.03184071651e-05`, `men's room=3.38075991116e-07`, selected `canonical_surface=men's`
  - `oewn-11942843-n`: `cat's feet=4.95027585937e-09`, `cat's foot=2.49589245062e-09`, selected `canonical_surface=cat's feet`

## 2026-07-06 - Normalize Google Ngram Punctuation Spacing Between Word Characters

### Proposed change

- Generalize the Google Ngram response surface adapter.
- Google Ngram can return punctuation as separated tokens, for example:
  - `men 's`
  - `cat 's feet`
  - `ping - pong table`
  - `table - tennis table`
- Normalize response and candidate surfaces into the same comparison key by:
  - converting curly apostrophes and dash variants to ASCII forms
  - removing spacing around hyphen only when the hyphen is between word characters
  - removing spacing around apostrophe only when the apostrophe is between word characters
  - collapsing repeated whitespace
- This does not add response-only candidates. It only lets a Google response
  match an already generated canonical candidate surface.

### Classification

- rule generality classification: general implementation correction
- target rule id: active extraction/canonicalization rule 없음
- affected evidence: future R19 input evidence candidate only

### Existing rules affected

- Canonical surface selection rule is unchanged.
- Google Ngram evidence parsing now uses a more general response/candidate
  surface key.
- Active Stage 1-6 extraction/canonicalization/count rules are unchanged.

### Count-table impact

- Current active count tables: none.
- Source-label canonical ambiguous rows decrease after regenerated evidence.
- Future object canonical lexicon may use this corrected source-label decision
  if promoted later.

### Risk review

- false positive risk:
  - Low. The normalization only removes punctuation spacing when punctuation
    is between word characters.
  - Plain-space variants remain distinct: `ping pong table` is not collapsed
    into `ping-pong table`.
- false negative risk:
  - Reduced for Google Ngram responses that tokenize punctuation.
- reversibility:
  - Revert `_surface_key()` punctuation-spacing normalization, then regenerate
    Ngram evidence and canonical decisions.

### Verification plan

- Verify `_surface_key()` examples:
  - `ping - pong table` -> `ping-pong table`
  - `table - tennis table` -> `table-tennis table`
  - `ping pong table` remains `ping pong table`
  - `men 's room` -> `men's room`
- Syntax-check the changed scripts.
- Regenerate the complete Ngram evidence TSV.
- Regenerate canonical decisions.
- Verify the final ambiguous count and the `ping-pong table` row.

### Decision

- status: implemented
- rationale:
  - User correctly pointed out that Google returned a response and the count was
    lost due to key mismatch. The fix belongs in the Google Ngram adapter, not
    in a label-specific canonical rule.

### Verification result

- Syntax check:
  - `scripts/build_object_synset_canonical_decisions.py`: `ast_ok`
  - `scripts/build_google_ngram_canonical_frequency_evidence.py`: `ast_ok`
- Surface key checks:
  - `ping-pong table` -> `ping-pong table`
  - `ping - pong table` -> `ping-pong table`
  - `ping pong table` -> `ping pong table`
  - `table-tennis table` -> `table-tennis table`
  - `table - tennis table` -> `table-tennis table`
  - `men 's room` -> `men's room`
  - `cat 's feet` -> `cat's feet`
- Full Ngram evidence regeneration:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 300 scripts/build_google_ngram_canonical_frequency_evidence.py --chunk-size 40 --request-sleep-seconds 0.05`
  - result:
    - `candidate_synset_groups=1084`
    - `requested_unique_surfaces=2454`
    - `evidence_rows=2457`
    - `synset_groups=1084`
    - evidence status: `ok=2456`, `missing=1`
- Canonical decision regeneration:
  - command: `scripts/run_script_with_timeout.py --timeout-seconds 180 scripts/build_object_synset_canonical_decisions.py`
  - result:
    - `selected_synset_groups=9192`
    - `canonical_selected_rows=9192`
    - `canonical_ambiguous_rows=0`
- Specific fixed row:
  - `oewn-04388674-n`: `table-tennis table=2.6702378475e-09`, `ping-pong table=3.80042704768e-08`, selected `canonical_surface=ping-pong table`

## 2026-07-06 - Align Stage 2 Tokenizer Source With Stage 3 Model

### Proposed change

- Replace Stage 2's standalone `spacy.blank("en")` tokenizer with
  `en_core_web_trf` loaded in tokenizer-only mode.
- Exclude all pipeline components in Stage 2:
  `transformer`, `tagger`, `parser`, `attribute_ruler`, `lemmatizer`, `ner`.
- Continue to use `nlp.make_doc(caption)` for tokenization.
- Do not change quote merge, object MWE merge, hyphen merge, raw extraction,
  or canonicalization behavior.

### Classification

- rule generality classification: implementation alignment
- target rule id: R2 Tokenization
- affected evidence: Stage 2 token boundaries only

### Existing rules affected

- R2 implementation source changes from blank English tokenizer to the same
  spaCy model package used by Stage 3.
- R3-R5 and later rules are unchanged.

### Count-table impact

- No direct count table change expected.
- A tokenizer boundary difference would propagate downstream, but a pre-change
  probe over current 100-record sample plus stress strings found no blank/trf
  token boundary differences.

### Risk review

- false positive risk:
  - Low. The earlier comparison found no token text/offset differences on the
    checked sample.
- operational risk:
  - Stage 2 now requires `en_core_web_trf` to be installed even when Stage 3 is
    not run.
- reversibility:
  - Revert `make_stage2_nlp()` to `spacy.blank("en")`.

### Verification result

- Stage 2 unit test:
  - command: `.\scripts\run_tests.ps1 --timeout-seconds 60 discover -s tests -p test_stage2_preprocess.py`
  - result: 7 tests passed.
- Syntax check:
  - command: `.\scripts\run_python.ps1 -c "... ast.parse ..."`
  - result: `ast ok`.
- `compileall` note:
  - `compileall src tests scripts` failed because it attempted to write
    `__pycache__` files under the junction/OneDrive-backed repo path and hit
    `PermissionError`.
  - This was not a Python syntax failure; `ast.parse` passed on changed files.

## 2026-07-06 - Move Object MWE Handling Out Of Stage 2

### Proposed change

- Disable Stage 2 object MWE retokenization.
- Remove Stage 3 object-MWE POS correction from the active pipeline.
- Move object span selection to Stage 4:
  - for each noun chunk, generate left-expanding spans ending at the chunk root;
  - use the longest span that has an OEWN noun lookup;
  - if no span has an OEWN noun lookup, do not create an object mention for that noun chunk.
- Keep selected-span internal tokens mapped to the created object mention so
  agent, patient, and relation edges can still resolve from dependency children.
- Stage 5 object canonicalization now uses raw surface fallback and preserves
  selected synset metadata. It does not use external source-label canonical
  surfaces.

### Classification

- rule generality classification: pipeline rule simplification
- affected rule ids: R4, R7, R12, R19, R22, R23
- external source labels: not used in runtime canonicalization

### Risk review

- false positive risk:
  - Reduced versus pre-merging external object MWE lexicons because runtime object
    spans must be inside an actual noun chunk and must include the noun chunk root.
- false negative risk:
  - Increased for noun chunks not found in OEWN. This is intentional for the
    object-only test policy: no OEWN noun lookup means no object count.
- operational risk:
  - Runtime OEWN DB access can fail in the Codex sandbox because the repo path is
    junction/OneDrive-backed. Unit tests inject a fake lookup and production code
    treats unavailable OEWN as lookup unavailable rather than crashing.

### Verification result

- Syntax check:
  - changed Stage 2/3/4/5 modules and tests: `ast ok`
- Unit tests:
  - `test_stage2_preprocess.py`: 6 passed
  - `test_stage3_annotate.py`: 5 passed
  - `test_stage4_extract_raw.py`: 6 passed
  - `test_stage5_canonicalize.py`: 4 passed

### Notes

- Offline ambiguous synset resolution remains separate pipeline work.
- Superseded on 2026-07-07: once a GPIC observed inventory row has final
  `selected_oewn_synset`, Stage 5 should attach immediate hypernym parent
  evidence from that selected synset.

## 2026-07-06 - Remove Object MWE Dead Code And CLI Arguments

### Proposed change

- Remove inactive Stage 2 object MWE loader and retokenization helpers.
- Remove inactive Stage 3 object-MWE compatibility plumbing.
- Remove `--object-mwes` from Stage 2, Stage 3, and fast benchmark CLI entry
  points.
- Remove the stale `object_mwe` column from generated token evidence tables.

### Classification

- rule generality classification: cleanup after accepted pipeline simplification
- affected rule ids: R4, R7
- runtime behavior: no active object MWE merge or POS correction remains before
  Stage 4 noun chunk selected-span lookup.

### Verification result

- Search check:
  - active `src`, `tests`, and `scripts` contain no
    `ObjectMweEntry`, `load_object_mwes`, `--object-mwes`,
    `object_mwes_path`, `is_object_mwe`, or object-MWE merge helper references.
- Syntax check:
  - changed Python files: `ast ok 12`
- Full bounded unittest discovery:
  - command: `.\scripts\run_tests.ps1 --timeout-seconds 240 discover -s tests -p "test_*.py"`
  - result: 48 tests passed.

### Notes

- Historical entries above still mention the old object MWE lexicon review work.
  Those remain as history, not active pipeline behavior.

## 2026-07-06 - Stage 4 Ambiguous Object Synset Gate

### Proposed change

- Stop Stage 4 when a noun chunk selected span has OEWN noun synset candidates
  but no selected synset.
- Keep the existing behavior for unresolved spans: if no OEWN noun lookup exists,
  do not create an object mention for that noun chunk.

### Classification

- rule generality classification: required gate for the accepted object-synset
  policy
- affected rule ids: R12
- custom or external: custom gate over OEWN lookup result

### Why

- The runtime 20-caption sample showed that Stage 4 accepted ambiguous OEWN rows
  as raw objects because it checked only `lookup.synsets`.
- This allowed Stage 5/6 raw fallback to count objects whose selected synset was
  not resolved, which contradicts the current rule that ambiguous synsets must
  be handled by offline resolution before downstream counting.

### Risk review

- false positive risk:
  - Reduced, because ambiguous object senses no longer enter raw/canonical
    counts silently.
- false negative risk:
  - Stage 4 can now stop earlier on captions containing ambiguous object spans.
    This is intentional; those spans require offline synset resolution.
- operational risk:
  - Batch runs can fail fast on the first ambiguous span. The error includes
    caption id, surface, query, candidate synsets, lexfiles, and WN3.0 count
    evidence for inventory update.

### Verification result

- Syntax check:
  - `stage4_extract_raw.py` and `test_stage4_extract_raw.py`: `ast ok 2`
- Unit tests:
  - `test_stage4_extract_raw.py`: 7 passed
- Runtime check:
  - the 20-caption Stage 4 run stops at
    `caption_id=c90e89252ab6c4dde38fddfe360d0ce85dd31790e7ae838dc610bebb349f2b5f`,
    `surface=graphics`, `tag=ambiguous_wn30_all_zero`.

## 2026-07-06 - GPIC Observed Object Inventory As Active Stage 4 Input

### Proposed change

- Separate historical external source-label inventories from the active GPIC
  caption pipeline.
- Add a GPIC-observed object span inventory builder that reads Stage 3 GPIC
  records and writes synset selection status, objectness gate, and extraction
  status separately.
- Require Stage 4 and the fast benchmark path to receive a GPIC object inventory
  unless the user explicitly asks for a runtime OEWN probe/debug run.

### Classification

- rule generality classification: correction of pipeline input boundary
- affected rule ids: R12, R19, R23
- custom or external: custom GPIC observed inventory gate over OEWN lookup

### Why

- The active caption pipeline should not use COCO, LVIS, Objects365,
  OpenImages, Visual Genome, V3Det, or ImageNet source-label inventories as
  runtime object evidence.
- Those files are useful as historical/offline probes, but using them as active
  GPIC caption input would mix external visual-label inventory decisions with
  observed GPIC caption spans.
- Stage 4 should therefore consume a GPIC observed object inventory built from
  the same GPIC Stage 3 records it will extract from.
- `selected_oewn_synset` alone must not imply that the row can be counted as an
  object. The active extraction gate is `extraction_status=selected`.

### Risk review

- false positive risk:
  - Reduced for source-label leakage because Stage 4 no longer silently falls
    back to external source-label inventory decisions.
  - Still present for plain OEWN noun spans such as `scene`, `right`, or
    `center` during inventory construction. These rows are not counted unless
    their selected synset passes the objectness gate.
- false negative risk:
  - Increased for unresolved GPIC noun chunks because unresolved rows are not
    counted as objects.
  - Stage 4 stops on ambiguous rows, so unresolved ambiguous inventory must be
    handled before full extraction.
- reversibility:
  - Reversible through the explicit `--object-inventory` input and the
    `--allow-runtime-oewn-lookup` debug flag.

### Verification result

- Syntax check:
  - command: AST parse over five changed Python files
  - result: `ast ok 5`
- Unit tests:
  - command: `.\scripts\run_tests.ps1 --timeout-seconds 180 discover -s tests -p test_stage4_extract_raw.py`
  - result: 9 tests passed
- GPIC observed inventory sample:
  - command: bounded script runner over
    `outputs\case_reports_sentence20_current\stage3_records.jsonl`
  - result:
    - `caption_total`: 20
    - `noun_chunk_total`: 263
    - `inventory_rows`: 194
    - `synset_selection_status_counts`: selected 169, ambiguous 17, unresolved 8
    - `objectness_gate_counts`: object_compatible 102, conditional 58,
      hard_conflict 9, empty 25
    - `extraction_status_counts`: selected 102, manual_required 67,
      ambiguous 17, unresolved 8
- Stage 4 inventory gate check:
  - command: Stage 4 over the same 20 records with the generated GPIC observed
    inventory
  - result: stopped at the first row that is not extraction-ready,
    `surface=front`, `extraction_status=manual_required`,
    `objectness_gate=conditional`.

### Filesystem note

- The active runtime path
  `C:\Users\rlath\Documents\Codex\gpic-explainable-link` is a junction to the
  OneDrive-backed repository path.
- A sandboxed generated TSV write failed with `PermissionError` while creating
  the same-directory atomic temp file.
- The successful generated-artifact run used the same narrow bounded command
  with `require_escalated`. This is not a logic fix; it is an approved
  outside-sandbox execution for a generated output path outside the current
  sandbox write boundary.

## 2026-07-07 - GPIC Object Inventory Decision Status Naming

### Trigger

- The previous GPIC observed inventory exposed three different status-like columns:
  `synset_selection_status`, `objectness_gate`, and `extraction_status`.
- The manual/GPT review queue should be grouped only as:
  - already chosen
  - needs manual choice
  - excluded

### Decision

- Use one human-facing status column:
  - `decision_status=chosen`: already chosen and allowed into Stage 4 object extraction.
  - `decision_status=needs_manual`: must be decided offline/manual before Stage 4.
  - `decision_status=excluded`: not counted as an object under current rules.
- Keep causes in `decision_reason`:
  - `selected_object_compatible`
  - `manual_objectness_required`
  - `manual_synset_required`
  - `no_oewn_noun_synset`
- Keep `objectness_gate` only as evidence, not as the main status.

### Files Changed

- `src/gpic_concepts_v1/stage4_extract_raw.py`
- `scripts/build_gpic_observed_object_inventory.py`
- `tests/test_stage4_extract_raw.py`
- `docs/rules_v1.md`
- `docs/output_schema_v1.md`
- `docs/implementation_plan_v1.md`

### Verification

- AST check passed for Stage 4 extractor, GPIC inventory builder, and Stage 4 tests.
- `git diff --check` passed for changed code/docs.
- Stage 4 unit tests: 9 passed.
- Regenerated 20-caption GPIC observed object inventory:
  - `chosen`: 102
  - `needs_manual`: 84
  - `excluded`: 8
- Stage 4 with that inventory stops as expected on:
  - `surface=front`
  - `decision_status=needs_manual`
  - `objectness_gate=conditional`

## 2026-07-07 - Plural Common Noun Head Lemma Lookup Order

### Trigger

- GPIC observed object inventory used observed exact surface before the head lemma
  surface.
- This can let plural exact entries select the wrong OEWN sense before the normal
  object head is tried:
  - `windows` can hit a plural exact non-visual sense before `window`
  - `men` can hit a group sense before `man`

### Decision

- If a noun chunk selected span has a plural common noun head, probe the head
  lemma surface before the observed exact surface.
- If the head is not a plural common noun, keep observed exact lookup first.
- Keep raw mention text as the observed caption surface. Only lookup query order
  changes.

### Implementation Note

- Plural common noun means:
  - token POS is `NOUN`
  - and token TAG is `NNS` or MORPH contains `Number=Plur`
- This is not a label-specific exception for `men` or `windows`.

### Verification

- Added unit test:
  - observed raw surface `men`
  - lookup query `man`
  - raw mention text remains `men`
- Stage 4 unit tests: 10 passed.
- Regenerated 20-caption GPIC observed object inventory:
  - `chosen`: 105
  - `needs_manual`: 81
  - `excluded`: 8
- Confirmed examples:
  - `men` -> selected query `man`, `chosen`
  - `windows` -> selected query `window`, `chosen`
  - `leaves` -> selected query `leaf`, `chosen`

## 2026-07-07 - Joined Separator Variant Manual Guard

### Trigger

- Separator removal lookup can create unrelated joined words:
  - `A man -> aman`
  - `black shirt -> blackshirt`
  - `black top -> blacktop`
- This is not a reason to make every multiword object span manual. The risk is
  specific to lookup cases where space, hyphen, or underscore was removed.

### Decision

- Do not automatically choose spans whose OEWN hit came from `joined_variant` or
  `last_word_morphy_after_joined_variant`.
- Mark them as:
  - `decision_status=needs_manual`
  - `decision_reason=manual_joined_variant_required`
- Keep exact and space-preserving multiword lookup eligible for `chosen`.
- Skip multiword candidate spans that start with function words such as `DET`,
  `ADP`, or `PRON`, so `A man` falls through to the root span `man`.

### Files Changed

- `src/gpic_concepts_v1/stage4_extract_raw.py`
- `scripts/build_gpic_observed_object_inventory.py`
- `tests/test_stage4_extract_raw.py`
- `docs/rules_v1.md`
- `docs/implementation_plan_v1.md`
- `docs/output_schema_v1.md`

### Verification

- AST check passed for Stage 4 extractor, GPIC inventory builder, and Stage 4 tests.
- `git diff --check` passed for changed code/docs.
- Stage 4 unit tests: 13 passed.
- Regenerated 20-caption GPIC observed object inventory:
  - `chosen`: 103
  - `needs_manual`: 83
  - `excluded`: 8
  - `manual_joined_variant_required`: 3
- Confirmed rows:
  - `black shirt -> blackshirt`, `needs_manual`
  - `black top -> blacktop`, `needs_manual`
  - `seed pods -> seedpod`, `needs_manual`
  - `man -> man`, `chosen`

## 2026-07-07 - Excluded Means Counted With Status

### Trigger

- Some observed caption spans are not safe to force-resolve with a general rule:
  pronouns, visual properties, no-synset labels, and similar rows.
- Dropping them at extraction time would silently remove count evidence even
  though the inventory already records `decision_status=excluded`.

### Decision

- Treat `excluded` as a downstream filter/status tag, not as a runtime drop
  signal.
- Stage 4 creates object mentions for inventory rows with:
  - `decision_status=chosen`
  - `decision_status=excluded`
- Stage 4 stops only for:
  - `decision_status=needs_manual`
- Missing inventory rows remain uncounted.

### Verification

- Added a Stage 4 regression test for an excluded no-synset inventory row.
- Stage 4 unit tests: 14 passed.

## 2026-07-07 - Selected Synset Parent Evidence Propagated To Runtime Outputs

### Trigger

- The accepted offline rule already said: after manual/ambiguous synset
  resolution, every final selected synset should receive all immediate OEWN
  hypernym parents.
- The runtime Stage 4/5 path preserved `selected_oewn_synset`, but did not
  propagate immediate hypernym parent evidence into `parent_concepts`.

### Decision

- Do not introduce a new parent ontology.
- Do not choose one parent when OEWN gives multiple immediate hypernyms.
- Use the selected OEWN synset itself as the only parent source.
- Preserve every immediate hypernym synset ID:
  - `parent_oewn_synsets`
  - `parent_oewn_lexfiles`
  - `parent_lemmas`
  - `parent_selection_tag=selected_all_immediate_oewn_hypernyms`
- Stage 5 maps those parent synset IDs to object `parent_concepts` with
  `parent_source=selected_oewn_hypernym`.

### Files Changed

- `scripts/build_gpic_observed_object_inventory.py`
- `scripts/enrich_gpic_inventory_parents.py`
- `src/gpic_concepts_v1/schema.py`
- `src/gpic_concepts_v1/stage4_extract_raw.py`
- `src/gpic_concepts_v1/stage5_canonicalize.py`
- `src/gpic_concepts_v1/stage6_export_counts.py`
- `tests/test_stage4_extract_raw.py`
- `tests/test_stage5_canonicalize.py`
- `tests/test_stage6_export_counts.py`
- docs updated to remove stale "future frozen parent inventory" wording.

### Verification

- Syntax check passed for 9 changed Python files: `ast ok 9`.
- `git diff --check` passed for changed Python/test files.
- Unit tests:
  - `test_stage4_extract_raw.py`: 14 passed
  - `test_stage5_canonicalize.py`: 4 passed
  - `test_stage6_export_counts.py`: 2 passed
- Existing 20-caption redecided inventory was enriched in place:
  - rows: 194
  - selected_synset_missing_rows: 10
  - parent_filled_rows: 184
  - parent_empty_rows: 0
- Re-ran Stage 4/5/6 for the 20-caption sample:
  - Stage 4 object mentions: 263
  - Stage 5 object mentions with parent: 251
  - Stage 6 object count tables now expose `parent_concepts`
  - relation/object-pair tables now expose source/target parent columns.

## 2026-07-07 - GPIC Observed Inventory Canonical Surface Propagated To Runtime Outputs

### Trigger

- The offline rule already required canonical lemma selection after selected
  synset resolution.
- Runtime Stage 5 still reported `canonical_source_counts={"raw_fallback":510}`
  for the 20-caption sample, meaning canonical surface selection had not been
  applied.

### Decision

- Add offline canonical enrichment for GPIC observed object inventory rows.
- Use only GPIC observed caption surfaces and selected OEWN synset lemmas.
- Do not use COCO/LVIS/Objects365/OpenImages/Visual Genome source labels.
- Selection order:
  1. selected synset lemma set
  2. observed surface variants
  3. single candidate lemma
  4. WN3 lemma count unique positive max
  5. exact observed surface match
  6. stored Google Ngram evidence
  7. unresolved canonical ambiguous row
- Preserve the decision in:
  - `canonical_surface`
  - `canonical_label_key`
  - `canonical_selection_tag`
  - `canonical_candidate_lemmas`
  - `canonical_candidate_lemma_counts`

### Files Changed

- `scripts/enrich_gpic_inventory_canonical.py`
- `scripts/build_gpic_observed_object_inventory.py`
- `src/gpic_concepts_v1/schema.py`
- `src/gpic_concepts_v1/stage4_extract_raw.py`
- `src/gpic_concepts_v1/stage5_canonicalize.py`
- `src/gpic_concepts_v1/atomic_io.py`
- `tests/test_atomic_io.py`
- `tests/test_stage4_extract_raw.py`
- `tests/test_stage5_canonicalize.py`

### Verification

- Syntax check passed for changed canonical-related Python files.
- Unit tests:
  - `test_atomic_io.py`: 3 passed
  - `test_stage4_extract_raw.py`: 14 passed
  - `test_stage5_canonicalize.py`: 4 passed
- Current 20-caption redecided inventory canonical enrichment:
  - rows: 194
  - selected_synset_missing_rows: 10
  - canonical_selected_rows: 184
  - canonical_ambiguous_rows: 0
- Re-ran Stage 4/5/6 and the 20-caption markdown report.
- Stage 5 summary after re-run:
  - `gpic_observed_inventory`: 251
  - `raw_fallback`: 259

## 2026-07-07 - Canonical Ambiguity Stops Before Runtime Extraction

### Trigger

- Canonical enrichment could be run even when inventory rows still had
  `decision_status=needs_manual`.
- That allowed a partial canonical probe over already-selected rows, but it
  violated the intended pipeline order.
- Canonical enrichment could write unresolved canonical rows to the ambiguous
  TSV while still exiting successfully.
- Stage 4 also needed an explicit guard against selected-synset rows whose
  `canonical_surface` was still empty.

### Decision

- Canonical enrichment now requires all synset/objectness manual decisions to
  be resolved first.
- If any `needs_manual` row remains, the command exits before OEWN loading and
  does not write canonical output.
- If canonical enrichment leaves any unresolved canonical row, it writes the
  output, ambiguous TSV, and summary, then exits nonzero.
- Stage 4 raises `Stage4SynsetAmbiguityError` when an inventory row has a
  selected synset but no resolved `canonical_surface`.
- `decision_status=excluded` remains countable metadata and does not trigger
  this canonical gate.

## 2026-07-07 - Canonical Matching Key Diacritic Folding

### Trigger

- The sentence 101-200 run produced one canonical ambiguous row:
  - observed surface: `café`
  - selected synset: `oewn-02939042-n`
  - OEWN lemmas: `cafe|coffeehouse|coffee shop|coffee bar`
- The selected synset was already correct, but canonical matching failed because
  the observed surface kept the accent while OEWN lemma did not.

### Decision

- Add diacritic folding to canonical matching keys only.
- Do not rewrite raw observed surface text.
- Example: `café` compares as `cafe` for canonical lemma matching.

### Verification

- Added unit coverage for `_surface_key("café") == "cafe"`.
- `test_enrich_gpic_inventory_canonical.py`: 2 tests passed.
- Re-ran canonical enrichment for sentence 101-200 inventory:
  - `canonical_ambiguous_rows=0`
  - `canonical_selected_rows=448`

## 2026-07-07 - Manual Resolution Gate for Surface Corrections

### Trigger

- A manual-reviewed row can decide that the original observed span should be
  represented by a different surface/head form.
- Example:
  - observed span: `white feathers`
  - intermediate selected query: `white feather`
  - intended canonical surface: `feather`
- If the surface/head form changes, the selected synset must be re-looked-up
  and written back. Leaving `selected_oewn_synset` blank while marking the row
  `chosen` makes parent/canonical enrichment ambiguous.
- Also, any final inventory row whose status is not clearly `chosen` or
  `excluded` must remain pending manual work, not silently pass downstream.

### Decision

- Add a shared inventory manual-resolution gate.
- Final enrichment stages accept only:
  - `decision_status=chosen`
  - `decision_status=excluded`
- Unknown explicit `decision_status` values normalize to `needs_manual`.
- `decision_status=chosen` with a changed `selected_query` or
  `canonical_surface` and blank `selected_oewn_synset` is blocked with:
  - `surface_correction_requires_synset_lookup`
- Parent enrichment now runs this gate before OEWN loading.
- Canonical enrichment now runs this gate before OEWN loading.
- Stage 4 inventory loading now normalizes unknown explicit decision statuses
  to `needs_manual` instead of preserving arbitrary strings.

### Files Changed

- `src/gpic_concepts_v1/inventory_validation.py`
- `src/gpic_concepts_v1/stage4_extract_raw.py`
- `scripts/enrich_gpic_inventory_parents.py`
- `scripts/enrich_gpic_inventory_canonical.py`
- `tests/test_inventory_validation.py`
- `tests/test_enrich_gpic_inventory_parents.py`
- `tests/test_enrich_gpic_inventory_canonical.py`

### Verification

- AST parse passed for changed Python files: 7 files.
- Unit tests:
  - `test_inventory_validation.py`: 4 passed
  - `test_enrich_gpic_inventory_canonical.py`: 4 passed
  - `test_enrich_gpic_inventory_parents.py`: 1 passed
  - `test_stage4_extract_raw.py`: 15 passed

## 2026-07-08 - Attribute Manual Chosen Rows Require Selected Synset

### Trigger

- Manual attribute feedback can mark a row as `chosen` while leaving
  `selected_oewn_synset` blank.
- Example: `TYR` was reviewed as a false-positive brand-like modifier with no
  selected OEWN synset, but the feedback row still had `decision_status=chosen`.
- Treating this as chosen lets a no-synset manual row pass as if it had a
  resolved lexical identity.

### Decision

- Attribute canonical enrichment normalizes:
  - `decision_status=chosen`
  - `selected_oewn_synset=` blank
- to:
  - `decision_status=excluded`
- before manual blocking and canonical selection.
- All `excluded` rows are treated the same for canonical enrichment.
- `excluded` is a resolved manual status, but it is not a canonical decision.
- For every `excluded` row, canonical columns are cleared and
  `canonical_selection_tag=not_applicable_excluded`, regardless of whether a
  selected synset exists.
- For non-excluded selected-synset rows, feedback-provided canonical columns are
  ignored. The script recomputes canonical surface from selected synset evidence
  and reports ambiguous rows when it cannot decide.

### Risk Review

- This is a general consistency rule, not a TYR-specific rescue mapping.
- It does not convert `needs_manual` rows. Pending manual rows still block.
- It does not convert automatic `no_synset` rows. No-synset inventory rows keep
  their existing no-synset handling.
- It prevents excluded rows from looking canonicalized only because a feedback
  file carried a raw fallback or manual surface.
- It also prevents selected rows from silently inheriting a feedback-provided
  canonical surface that did not go through the canonical selection rule.

### Verification

- Added unit coverage for a manual `chosen` row with blank
  `selected_oewn_synset`.
- Unit test:
  - `test_enrich_gpic_attribute_inventory_canonical.py`: 5 passed.
- Updated the repo-local 20-caption manual-resolved attribute inventory so
  `TYR` is already `excluded`.
- Re-ran the 20-caption attribute canonical enrichment from that normalized
  input:
  - `normalized_no_synset_chosen_rows`: 0
  - `excluded_not_applicable_rows`: 4
  - `selected_synset_missing_rows`: 0
  - `canonical_selected_rows`: 97
  - `canonical_ambiguous_rows`: 0
  - `manual_surface_canonical`: 0
  - canonical tag counts:
    - `selected_single_observed_variant_matched_synset_lemma`: 95
    - `selected_by_wn30_lemma_count_unique_positive_max`: 2
    - `not_applicable_excluded`: 4
  - status counts: `chosen=97`, `excluded=4`
  - all excluded rows now have `canonical_selection_tag=not_applicable_excluded`.

## 2026-07-08 - Attribute Type Manual Taxonomy Decisions Imported

### Trigger

- The 20-caption observed attribute inventory now has manual taxonomy decisions
  for attribute type.
- User-provided files:
  - `gpic_observed_attribute_inventory_typed.tsv`
  - `gpic_observed_attribute_inventory_type_audit.tsv`

### Decision

- Preserve the typed inventory as a separate output artifact.
- Attribute type is a manual taxonomy decision, not an OEWN hypernym.
- Canonical columns remain script-owned; type columns are manual-owned.
- `excluded` rows may carry `attribute_type` for audit/filtering, but remain
  non-canonical.

### Files

- `outputs/case_reports_sentence20_current/gpic_observed_attribute_inventory_typed.tsv`
- `outputs/case_reports_sentence20_current/gpic_observed_attribute_inventory_type_audit.tsv`

### Verification

- Typed rows: 101
- Canonical inventory rows: 101
- `span_key` mismatch with canonical inventory: 0
- Blank `attribute_type` rows: 0
- Manual canonical tag rows in typed file: 0
- Decision status counts:
  - `chosen`: 97
  - `excluded`: 4
- Most frequent attribute types:
  - `color_attribute`: 12
  - `color_intensity_attribute`: 9
  - `state_condition_attribute`: 9
  - `size_scale_attribute`: 8
  - `domain_modifier_attribute`: 7
  - `texture_attribute`: 7

## 2026-07-08 - Attribute Typed Inventory to Stage 5 Lexicon Export

### Trigger

- The typed attribute inventory existed as an offline artifact, but Stage 5 only
  consumes lexicon TSVs.
- Without an explicit export step, the count tables would not show
  `attribute_type`.

### Decision

- Add `scripts/export_attribute_stage5_lexicons.py`.
- The script writes a complete Stage 5 lexicon bundle from a resolved typed
  attribute inventory.
- `decision_status=chosen` rows with `canonical_surface` are exported as
  `attribute_synonyms.tsv` rows.
- `decision_status=excluded` and `decision_status=no_synset` rows are not
  exported as canonical synonyms.
- If those rows have `attribute_type`, the type is exported against the
  raw-fallback canonical key only.
- Feedback-provided canonical values on `excluded` rows are ignored.

### Risk Review

- This is not a new extraction rule; it only converts resolved offline
  inventory decisions into the existing Stage 5 lexicon input format.
- It keeps excluded rows countable while preventing excluded manual canonical
  values from becoming canonical synonyms.

### Verification

- Unit test:
  - `test_export_attribute_stage5_lexicons.py`: 1 passed.
- 20-caption typed attribute lexicon export:
  - inventory rows: 101
  - chosen synonym rows added: 97
  - attribute type rows: 99
  - excluded type rows: 4
  - ignored excluded canonical rows: 0
- 20-caption Stage 5 rerun with generated lexicon bundle:
  - canonical mentions: 510
  - canonical edges: 289
  - canonical source counts:
    - `gpic_observed_inventory`: 251
    - `lexicon`: 128
    - `raw_fallback`: 131
- 20-caption Stage 6 rerun:
  - fact total: 4830
  - object-attribute pair rows: 129
  - object-attribute pair rows with `attribute_type`: 129
  - excluded attribute mentions remain `canonical_source=raw_fallback`.
## 2026-07-08: Add `nmod` To Attribute Modifier Dependencies

Proposed rule or lexicon change:

- Extend R11.1 and R13 attribute modifier dependency set from
  `{"amod", "compound"}` to `{"amod", "compound", "nmod"}`.
- Do not add `conj` in this change. Coordinated attributes such as `yellow` in
  `maroon and yellow jerseys` remain a known limitation unless a separate
  coordination rule is approved.

Rule generality classification:

- General rule.
- This is a syntactic recall expansion inside noun chunks, not a label-specific
  rescue mapping.

Target stage and rule id:

- Stage 3.5, R11.1: GPIC observed attribute inventory lookup.
- Stage 4, R13: Noun chunk modifier to attribute.

Existing rules affected:

- R11.1 attribute inventory candidate collection.
- R13 Stage 4 attribute mention and `has_attribute` edge extraction.

Expected count-table impact:

- Attribute counts and object-attribute pair counts may increase when spaCy
  attaches a noun-like modifier as `nmod` inside the same noun chunk.
- Example target case: `maroon and yellow jerseys` should at least produce
  `jersey -> maroon`.

False positive risk:

- Low to medium.
- Risk is bounded because the rule only sees tokens inside a spaCy noun chunk
  and outside the selected object core span.
- Some nominal modifiers that are better treated as relation-like noun
  complements may now count as attributes.

False negative risk:

- Reduced for noun-like color/material/category modifiers tagged as `nmod`.
- Coordinated modifiers whose `dep` is `conj` are still not recovered by this
  change.

Reversibility:

- Reversible by removing `nmod` from `ATTRIBUTE_MODIFIER_DEPS`.
- Output rows remain traceable through R13 and Stage 3 token dependency evidence.

Verification plan:

- Add Stage 4 test that an `nmod` token inside a noun chunk becomes an
  attribute edge.
- Add observed attribute inventory test that an `nmod` token becomes an
  inventory candidate.
- Re-run the 20-caption report and verify `maroon -> jersey` appears.

Decision status:

- Approved by user in chat on 2026-07-08.

## 2026-07-11: Active Preposition MWE Relation Handling

Proposed rule or lexicon change:

- Add an active `resources/lexicons/preposition_mwes.tsv` lexicon built from
  reviewed external preposition MWE inventory rows.
- During Stage 4, detect exact contiguous preposition MWE token spans before
  action/relation extraction.
- Mark selected preposition MWE span tokens as `relation_mwe_consumed`.
- Exclude consumed relation MWE tokens from phrasal action candidates and from
  remaining single-ADP relation extraction.
- Create an R18.1 relation edge only when the matched MWE has a source object
  through its initial relation token head and a target object through the final
  ADP direct `pobj`.
- Preserve raw span surface, matched token ids, canonical relation label, and
  relation components as edge metadata.
- Export relation component facts/counts from that metadata in Stage 6.

Rule generality classification:

- General rule plus custom lexicon.
- The rule is not a caption-specific rescue patch. It applies only to reviewed
  preposition MWE lexicon spans and still requires ordinary dependency/object
  evidence for the edge endpoints.

Target stage and rule id:

- Stage 3.5 preposition MWE lexicon bundle.
- Stage 4 R15 action candidate exclusion.
- Stage 4 R18 single-ADP relation exclusion.
- Stage 4 R18.1 preposition MWE relation edge.
- Stage 5 R24 relation canonicalization.
- Stage 6 R25 count export.

Existing rules affected:

- R15 phrasal action candidates no longer use tokens already consumed by a
  preposition MWE span.
- R18 single-ADP relations no longer count ADP tokens already consumed by a
  selected preposition MWE span.
- R24 is no longer purely raw-preserving for all relation labels; single ADP
  labels remain raw-preserving, while preposition MWE labels preserve the Stage
  4 lexicon canonical relation label.
- R25 gains relation component facts/counts from existing Stage 4 relation MWE
  metadata.

Expected count-table impact:

- Relation triple labels such as `of` may be replaced by preposition MWE labels
  such as `in front of` when the MWE source/target dependency evidence is
  present.
- Some single-ADP relation triples disappear when their ADP belongs to a
  selected preposition MWE relation span.
- New relation component facts/count rows are emitted for selected preposition
  MWE relation edges.
- Action counts may lose false phrasal-action candidates that used relation MWE
  preposition tokens.

False positive risk:

- Medium. A reviewed preposition MWE string can still appear in a caption where
  the local syntax does not function as a relation MWE. The edge rule limits
  this by requiring source object and final-ADP direct `pobj` target evidence.

False negative risk:

- Medium. The matcher uses exact contiguous token sequence matching and will
  miss discontinuous, inflected, misspelled, or unlisted preposition MWE
  variants.

Reversibility:

- Reversible by removing the lexicon file, disabling relation MWE span
  detection, and removing R18.1 relation-component export. Existing source
  metadata fields identify `relation_mwe` edges and consumed token ids.

Verification plan:

- Add a Stage 4 test for a relation MWE such as `in front of` that creates one
  R18.1 relation edge, suppresses the consumed single-ADP relation, and leaves
  the internal noun out of object extraction.
- Add a Stage 4 test that consumed relation MWE tokens are excluded from
  phrasal action candidate construction.
- Add Stage 5 and Stage 6 tests that relation MWE labels are preserved and
  relation component facts/counts are exported.

Decision status:

- Approved by user in chat on 2026-07-11 with "진행해" after reviewing the
  updated preposition MWE plan.

## 2026-07-10: R15 Reject Fronted Prepositions For Phrasal Action Candidates

Proposed rule or lexicon change:

- R15 should not build `VERB+preposition` or `VERB+particle+preposition`
  action candidates from a preposition token that appears before the VERB head.
- A preposition candidate is valid for phrasal action lookup only when
  `prep.i > verb.i`.

Rule generality classification:

- General rule.
- This is a syntactic ordering constraint, not a caption-specific rescue patch,
  semantic alias, or relation lexicon change.

Target stage and rule id:

- Stage 4 R15 action mention extraction.
- R17 and R18 are indirectly affected only because they consume ADPs selected
  by R15.

Existing rules affected:

- R15 still allows VERB+particle, VERB+preposition, and
  VERB+particle+preposition candidates, but only with following prepositions.
- R17 selected phrasal-action ADP patient extraction applies only to ADPs that
  survive the R15 ordering constraint.
- R18 relation suppression applies only to ADPs consumed by a valid selected
  phrasal action span.

Expected count-table impact:

- Removes false action counts such as `frame in` when the `In` token is a
  fronted locative PP before the verb.
- May restore those fronted ADPs to normal downstream handling instead of
  suppressing them as consumed action tokens.
- Does not affect ordinary following-preposition action candidates such as
  `look at`, `sit in`, or `stand out`.

False positive risk:

- Low. The rule removes a known structural false positive caused by using
  backward prepositions as phrasal action evidence.

False negative risk:

- Low to medium. Rare inverted or poetic verb/preposition constructions where a
  phrasal preposition appears before the verb will not be selected as phrasal
  actions in v1.

Reversibility:

- Reversible by removing the `prep.i > verb.i` filter from R15 candidate
  generation.
- Output rows retain R15 source details, including selected token indices and
  prep token indices, so affected spans are auditable.

Verification plan:

- Add a regression test where `In ... frame` has a fronted `prep` child before
  the verb and must remain a single `frame` action even if `frame in` exists in
  action lookup.
- Keep the existing `look at` test to verify following prepositions still form
  selected phrasal action spans, create selected-ADP patients, and suppress the
  relation edge.

Decision status:

- Approved by user in chat on 2026-07-10.

## 2026-07-10: Offline Action Canonical Inventory Build

Proposed rule or lexicon change:

- Add an offline canonical enrichment step for resolved GPIC observed action
  inventory rows.
- Use the selected OEWN verb synset, observed action surface, `selected_query`,
  verb-head Morphy variants, WN3 lemma counts, and optional Google Ngram
  evidence to fill action canonical fields.

Rule generality classification:

- general rule.
- This is an offline canonical preparation rule, not a one-off action rescue
  mapping.

Target stage and rule id:

- Stage 3.5, R11.4.
- Prepares R22 action canonicalization input.

Existing rules affected:

- R15 action inventory must be resolved before this step.
- R22 currently uses action synonym TSV; this step prepares canonical action
  evidence but does not by itself change active Stage 5 output.

Expected count-table impact:

- No direct impact until the canonical action inventory is exported/connected to
  R22.
- Once connected, action count keys may move from raw observed surfaces such as
  `deepening` to canonical surfaces such as `deepen`.

False positive risk:

- Low for this enrichment step because it only uses already selected action
  synsets.
- Ambiguous canonical decisions are blocked instead of silently selected.

False negative risk:

- Some rows may remain canonical ambiguous and require manual decision before
  active export.

Reversibility:

- Re-run from the unresolved/resolved action inventory without canonical fields.
- Generated canonical output and ambiguous TSV can be removed and regenerated.

Verification plan:

- Add unit tests for action canonical enrichment:
  - pending `needs_manual` blocks canonical enrichment
  - selected `shining -> shine` row fills canonical surface
  - `raw_fallback` row remains not applicable
- Run bounded tests and generate the sentence-20 action canonical inventory.

Decision status:

- Approved by user in chat on 2026-07-10 after clarifying that action canonical
  inventory build is the next step after action synset manual resolution.

## 2026-07-09: Apply Sentence-20 Action Manual Synset Decisions

Proposed rule or lexicon change:

- Apply the user-provided manual decisions for the 8 pending action inventory
  rows in `outputs/case_reports_sentence20_current`.
- Add an action manual-resolution overlay script.
- Add an optional Stage 4 runner action inventory gate.

Rule generality classification:

- explicit user-approved manual decision.
- The runner gate is a general readiness gate for R15 action inventory input.
- This does not add a new semantic fallback or one-off automatic rescue rule.

Target stage and rule id:

- Stage 3.5 offline action inventory resolution.
- Stage 4, R15 action mention extraction.

Existing rules affected:

- R15 action lookup remains unchanged.
- R15 pending manual action decisions are now resolvable through a durable TSV
  artifact before Stage 4 uses an action inventory.

Expected count-table impact:

- The 8 previously blocked action spans can be emitted with user-selected
  `selected_query` and `selected_oewn_synset`.
- `shining` resolves through `shine`; `slopes` resolves through `slope`.
- `sits in` is preserved as a user-approved known false positive decision.

False positive risk:

- Limited to the supplied manual rows.
- `sits in` is explicitly marked as a known false positive in the manual
  decision note.

False negative risk:

- Low. The overlay only replaces rows that were already `needs_manual`.

Reversibility:

- Remove or edit
  `outputs/case_reports_sentence20_current/gpic_observed_action_inventory_manual_decisions.tsv`
  and regenerate the resolved action inventory.
- The full unresolved inventory remains available as
  `gpic_observed_action_inventory.tsv`.

Verification plan:

- Unit test the action manual overlay script.
- Unit test that Stage 4 can consume a resolved action inventory row.
- Unit test that the Stage 4 runner blocks pending action inventory rows while
  allowing `raw_fallback` rows.
- Run the bounded action-related tests.
- Apply the manual decisions and verify merged status counts.

Decision status:

- Approved by user in chat on 2026-07-09 by providing the 8 manual synset
  decisions.

## 2026-07-09: Action Verb Lookup Exact Surface Filter Before Morphy

Proposed rule or lexicon change:

- Revise R15 action OEWN verb lookup so raw phrase lookup is accepted as exact
  only when the returned synset has a verb lemma whose surface matches the raw
  phrase query.
- If no exact surface lemma hit exists, use verb-head Morphy queries and accept
  only synsets whose lemmas match the Morphy query.
- If two or more Morphy queries produce OEWN verb hits, do not choose
  alphabetically or by first result; mark the action inventory row as
  `needs_manual`.

Rule generality classification:

- General rule.
- This is not a label-specific rescue. It prevents OEWN's internal morphology
  from being recorded as an exact query and prevents Morphy candidate ordering
  from selecting a wrong lemma.

Target stage and rule id:

- Stage 4, R15 action lookup.
- Offline action inventory builder that uses the same Stage 4 action lookup
  helper.

Existing rules affected:

- R15 `VERB or selected phrasal action span to action`.
- R15 manual gate: unresolved or ambiguous action synset decisions must be
  resolved before Stage 4 proceeds.

Expected count-table impact:

- Some inflected forms such as `sitting`, `lying`, `made`, `lit`, `worn`, and
  `splitting` may move from raw-surface `needs_manual` to base-lemma lookup.
- Some forms with multiple Morphy verb hits such as `shining` may move from
  incorrectly `chosen` to `needs_manual`.
- Action count formulas do not change, but selected action lemmas and manual
  queue size may change.

False positive risk:

- Low to medium. Morphy can still propose multiple plausible verb lemmas, but
  multiple OEWN-hit queries are now blocked instead of auto-selected.

False negative risk:

- Low. A true inflected verb form that is also an exact OEWN verb lemma remains
  eligible because exact matching is based on synset lemma surface.

Reversibility:

- Reversible by removing the exact-surface lemma filter and multiple-Morphy-hit
  manual gate from R15 lookup.
- Generated inventories preserve `selected_lookup_case`, `selected_query`,
  `synset_selection_tag`, and `decision_reason` for audit.

Verification plan:

- Add unit tests for `sitting -> sit` after rejecting OEWN internal morphology
  as exact.
- Add unit tests for `shining -> shin|shine` being marked `needs_manual`.
- Regenerate the 20-caption action inventory and check representative rows.

Decision status:

- Approved by user in chat on 2026-07-09.

## 2026-07-09: Action `needs_manual` Must Block Stage 4

Proposed rule or lexicon change:

- Correct the R15 action lookup implementation so a selected action candidate
  with `decision_status=needs_manual` stops Stage 4 instead of being emitted as
  a raw action mention.

Rule generality classification:

- Implementation correction for an already approved gate.
- This is not a new rescue mapping or a semantic fallback.

Target stage and rule id:

- Stage 4, R15.

Existing rules affected:

- R15 selected phrasal action span to action.
- The Stage 4 readiness rule that unresolved manual decisions must not be
  promoted into formal raw extraction.

Expected count-table impact:

- Incomplete action synset decisions no longer produce downstream action,
  agent/patient, Stage 5, Stage 6, or Markdown output.
- Runs that contain unresolved action candidates stop and require offline/manual
  resolution first.

False positive risk:

- Reduced, because ambiguous action senses are no longer counted as if they were
  resolved.

False negative risk:

- Temporary output may be blocked until action inventory/manual resolution is
  completed.

Reversibility:

- Reversible by removing the Stage 4 ambiguity guard, but that would violate the
  current pipeline gate.

Verification plan:

- Change the unit test that previously allowed ambiguous action lookup to assert
  `Stage4SynsetAmbiguityError`.
- Run the bounded Stage 4 unit test file.

Decision status:

- Approved by user in chat on 2026-07-09 after identifying that the previous
  behavior incorrectly passed `needs_manual` action lookup rows.

Verification result:

- `test_inventory_validation.py`: 5 passed.
- `test_formal_inventory_gates.py`: 6 passed.
- AST parse passed for changed runner, validation, and test files.

## 2026-07-08: Apply Attribute Manual Decisions For Sentence 101-200 Inventory

Proposed rule or lexicon change:

- Apply the user-provided manual resolution TSV for the 96 pending attribute
  inventory rows in the sentence 101-200 sample.
- Merge the resolved rows back into the full 257-row attribute inventory.
- Recompute canonical surfaces for the full resolved inventory using the
  existing offline attribute canonical rule.
- Export a Stage 5 attribute synonym lexicon bundle from the canonical
  inventory if no pending or canonical-ambiguous rows remain.

Rule generality classification:

- explicit user-approved manual decision.
- This does not add a new extraction, lookup, canonicalization, or repair rule.

Target stage and rule id:

- Stage 3.5 offline attribute inventory resolution.
- Stage 5 R20 attribute synonym canonicalization input bundle.

Existing rules affected:

- R11.1 and R11.2 are used as documented.
- R20 receives an updated attribute synonym TSV bundle generated from the
  resolved inventory.

Expected count-table impact:

- Attribute canonical labels may change for raw attribute surfaces that now have
  canonical surfaces.
- Attribute mention counts do not change at Stage 4; Stage 5 labels and Stage 6
  aggregate keys may change.

False positive risk:

- Limited to the supplied manual decisions and existing canonical enrichment
  policy.

False negative risk:

- Low. The merge only replaces the pending 96 rows with user-resolved rows and
  does not remove observed attribute rows.

Reversibility:

- Revert to the prior `gpic_observed_attribute_inventory_current.tsv` snapshot
  and rerun canonical enrichment/export.
- Generated output files can be removed or regenerated from the original full
  inventory plus the manual decision TSV.

Verification plan:

- Check row counts and status counts after merge.
- Run canonical enrichment and require `canonical_ambiguous_rows=0`.
- Export Stage 5 attribute lexicons.
- Run the Stage 5 formal gate with the canonical inventory.
- Run narrow tests for attribute canonical enrichment, attribute lexicon export,
  and formal inventory gates.

Decision status:

- Approved by user in chat on 2026-07-08 by providing the resolved TSV files.

Verification result:

- Manual resolution overlay:
  - full rows: 257
  - overlaid rows: 96
  - merged decision statuses: `chosen=257`
- Attribute canonical enrichment:
  - rows: 257
  - canonical selected rows: 231
  - no-synset fallback rows: 26
  - canonical ambiguous rows: 0
- Stage 5 attribute lexicon export:
  - attribute synonym rows: 231
  - attribute type rows: 0
- Formal Stage 5 run passed the attribute inventory gate:
  - `formal_attribute_inventory_gate=True`
  - canonical mentions: 2252
  - canonical edges: 1234
- Stage 6 regenerated:
  - fact total: 21843
  - object-attribute pair rows: 547
- Markdown report regenerated for 100 captions.
- Tests:
  - `test_apply_attribute_manual_resolution.py`: 2 passed.
  - `test_enrich_gpic_attribute_inventory_canonical.py`: 6 passed.
  - `test_export_attribute_stage5_lexicons.py`: 1 passed.
  - `test_formal_inventory_gates.py`: 6 passed.
  - AST parse passed for changed/generated helper scripts and tests.

Verification result:

- `test_inventory_validation.py`: 5 passed.
- `test_formal_inventory_gates.py`: 5 passed.
- AST parse passed for changed runner, validation, and test files.

Verification result:

- `test_stage4_extract_raw.py`: 17 passed.
- `test_build_gpic_observed_attribute_inventory.py`: 6 passed.
- 20-caption rerun produced `object_attribute_pair:jersey:maroon`.
- `yellow` in `maroon and yellow jerseys` remains excluded by this change
  because its dependency is `conj`, which was intentionally not added.

## 2026-07-08: OEWN-Based Phrasal Action Span Selection

Proposed rule or lexicon change:

- Let R15 select an OEWN-backed phrasal action span from a VERB head plus
  particle/preposition dependency evidence.
- Map selected action span tokens to the action mention.
- Treat only the direct `pobj` of an ADP consumed by the selected phrasal action
  as an R17 patient candidate.
- Exclude ADP tokens consumed by selected phrasal action spans from R18 relation
  extraction.

Rule generality classification:

- General syntax plus lexical evidence rule.
- It is not a caption-specific rescue patch. If OEWN does not validate the
  phrasal action candidate, extraction falls back to the single VERB action.

Target stage and rule id:

- Stage 4 R15 action mention extraction.
- Stage 4 R17 patient edge extraction.
- Stage 4 R18 relation edge extraction.

Existing rules affected:

- R15 no longer means only one-token VERB action.
- R17 keeps direct `obj`/`dobj` behavior and adds only selected phrasal action
  ADP `pobj` as a narrow exception.
- R18 no longer counts ADP tokens already consumed by selected phrasal action
  spans.

Expected count-table impact:

- Some action labels may become phrasal surfaces such as `look at` instead of
  only `look`.
- Some relation triples using consumed phrasal-action ADPs may disappear.
- Some agent/patient pair rows may gain patients from selected phrasal-action
  prepositional objects.
- Corrected on 2026-07-09: action synset ambiguity is not pass-through
  metadata. If the selected R15 action candidate has
  `decision_status=needs_manual`, Stage 4 must stop before raw extraction
  output.

False positive risk:

- Medium. `pos == ADP` is allowed as preposition evidence by policy, so noisy
  ADP candidates can be generated. OEWN verb lookup limits automatic selection.

False negative risk:

- Medium. Phrasal actions absent from OEWN or not captured by dependency child
  structure fall back to single VERB.

Reversibility:

- Reversible by disabling R15 phrasal action lookup and removing the consumed
  ADP checks from R17/R18.

Verification plan:

- Add a unit test where selected `look at` consumes `at`, creates an action
  patient from `pobj`, and suppresses the relation edge.
- Keep the existing `sit on` test as the fallback case where prepositional
  object is not a patient.
- Add a unit test where an ambiguous single-verb action synset stops raw
  extraction before downstream output.

Decision status:

- Approved by user in chat on 2026-07-08.

## 2026-07-08: Formal Inventory Readiness Gates For Stage 4 And Stage 5

Proposed rule or lexicon change:

- Add runner-level gates so formal Stage 4 cannot run with unresolved object
  inventory rows.
- Add runner-level gates so formal Stage 5 cannot run with unresolved attribute
  inventory rows.
- Keep preview behavior possible only through an explicit preview flag for
  Stage 5.

Rule generality classification:

- General process gate.
- This is not an extraction rule, rescue mapping, or semantic alias. It prevents
  incomplete offline inventory decisions from being presented as formal output.

Target stage and rule id:

- Stage 4 runner gate before R12-R18.
- Stage 5 runner gate before R19-R24.
- Appendix gates in `docs/rules_v1.md`.

Existing rules affected:

- Stage 4 object extraction now requires the object inventory to be globally
  ready, not merely ready for matched spans.
- Stage 5 canonicalization now requires an attribute inventory unless the run is
  explicitly marked as unresolved preview.

Expected count-table impact:

- No direct count formula changes.
- Some commands that previously produced incomplete count tables will now stop
  before generating formal outputs.

False positive risk:

- Low. The gate may stop a run that the user intended only as a preview; this is
  handled by an explicit preview flag on Stage 5.

False negative risk:

- Low. The gate does not remove valid mentions; it only prevents formal output
  before inventory decisions are complete.

Reversibility:

- Reversible by removing the runner guards and validation tests.
- Existing generated preview files remain as historical snapshots.

Verification plan:

- Add inventory validation tests for selected-synset rows missing canonical
  surface.
- Add Stage 4 runner test that an object inventory with pending rows is blocked
  before extraction.
- Add Stage 5 runner tests that unresolved attribute inventory is blocked and
  explicit preview mode still runs.

Decision status:

- Approved by user in chat on 2026-07-08.

## 2026-07-11: Action-Attached Preposition MWE Relation Candidate Preservation

Proposed rule or lexicon change:

- Extend R18.1 preposition MWE relation extraction when the matched MWE is
  attached to a VERB head rather than directly to an object head.
- If the VERB has exactly one direct object-mapped source candidate among
  `nsubj`, `obj`, and `dobj`, create a normal `relation` edge using that
  object as source.
- If the VERB has multiple such source candidates, do not pick one source.
  Create one `ambiguous_relation_candidate` edge per candidate source and export
  those as separate candidate facts/counts in Stage 6.

Rule generality classification:

- General syntax-preserving candidate rule.
- This is not semantic PP source disambiguation and not a caption-specific
  rescue patch. It uses only direct dependency children and existing object
  mappings.

Target stage and rule id:

- Stage 4 R18.1 preposition MWE relation extraction.
- Stage 5 R24 relation metadata preservation.
- Stage 6 R25 count export.

Existing rules affected:

- R18.1 no longer requires the initial relation token head itself to be an
  object when that head is a VERB with direct object argument candidates.
- R24 still does not change source/target; it only preserves the new candidate
  metadata.
- R25 gains `ambiguous_relation_candidate` facts/counts from Stage 4 candidate
  edges.

Expected count-table impact:

- Relation triple count may gain rows such as `women --in front of--> screen`
  when the preposition MWE is attached to an action with exactly one direct
  object argument source candidate.
- Ambiguous candidate count may gain rows when action-attached source candidates
  are multiple.
- Relation component count remains tied to confirmed preposition MWE relation
  edges; ambiguous candidate edges are counted in their own candidate table so
  relation components are not duplicated by multiple source candidates.

False positive risk:

- Medium. A direct `nsubj`, `obj`, or `dobj` child of the action may not be the
  semantic source of the PP relation.
- Multi-candidate cases are not forced into normal relation triples; they are
  isolated as candidate facts.

False negative risk:

- Medium. Indirect objects, conjunct source objects, passive subjects, or
  semantic PP attachment are still not recovered.

Reversibility:

- Reversible by removing the action-head source candidate branch from R18.1 and
  dropping `ambiguous_relation_candidate` from schema and Stage 6 export.
- Candidate rows preserve `source_resolution`, token indices, and R18.1
  metadata so downstream filters can remove them.

Verification plan:

- Add a Stage 4 test where `stand in front of screen` creates a normal R18.1
  relation from the single `nsubj` object source.
- Add a Stage 4 test where an action has both `nsubj` and `dobj` object
  candidates and creates `ambiguous_relation_candidate` edges rather than a
  normal relation triple.
- Add Stage 5/Stage 6 tests that candidate edges survive canonicalization and
  export to `ambiguous_relation_candidate_counts.tsv`.

Decision status:

- Approved by user in chat on 2026-07-11.

## 2026-07-11: R18.1 Object-Mapped Child Source Candidates

Proposed rule or lexicon change:

- Replace the R18.1 action-attached source candidate dependency allow-list
  with a simpler evidence rule: when the initial preposition MWE token is
  attached to a VERB head and that head has direct children already mapped to
  object mentions, preserve all of those object-mapped children as source
  candidates regardless of dependency label.

Rule generality classification:

- General syntax-preserving candidate rule.
- This is not semantic PP source disambiguation. It preserves object-mapped
  dependency children as candidate evidence and lets the existing
  normal-vs-ambiguous R18.1 path decide whether to emit a relation edge or
  candidate occurrence.

Target stage and rule id:

- Stage 4 R18.1 preposition MWE relation extraction.
- Stage 6 R25 ambiguous relation occurrence count remains unchanged.

Existing rules affected:

- R18.1 no longer hardcodes source candidate deps to `nsubj`, `obj`, and
  `dobj`.
- R18.1 still requires the initial relation token head to be an object or a
  `VERB`/`AUX` head.
- R18.1 target detection remains based on final ADP direct `pobj` object
  mapping.

Expected count-table impact:

- Relation triple count may gain rows when a VERB-attached preposition MWE has
  exactly one object-mapped child with a dependency outside the previous
  allow-list, such as `nsubjpass` or `attr`.
- Ambiguous relation occurrence count may gain rows when multiple
  object-mapped children are present under the same VERB head.

False positive risk:

- Medium. Any direct object-mapped child of the VERB may not be the semantic
  source of the preposition MWE.
- The risk is bounded because multi-source cases stay in
  `ambiguous_relation_candidate` and are counted once per occurrence.

False negative risk:

- Medium. This still does not recover source objects from ADJ heads, inherited
  agents, conjunct siblings, fronted PP inversion, or non-child ancestors.

Reversibility:

- Reversible by restoring the dep allow-list filter in
  `_relation_mwe_*_source_candidates`.
- Edge metadata preserves `source_dep`, `source_resolution`, and candidate
  lists, so downstream audit can filter newly included dependency labels.

Verification plan:

- Add a Stage 4 test where an action-attached preposition MWE uses a single
  `nsubjpass` object child as relation source.
- Add a Stage 4 test where an action-attached preposition MWE uses a single
  `attr` object child as relation source.
- Run bounded Stage 4 and Stage 5 canonicalization tests that inspect R18.1
  metadata.

Decision status:

- Approved by user in chat on 2026-07-11.

## 2026-07-11: R13 Attribute Conjunct Expansion

Proposed rule or lexicon change:

- Extend R11.1/R13 attribute modifier detection so a token with `dep == "conj"`
  becomes an attribute candidate only when it is reachable through a same-noun-
  chunk `conj` chain rooted at an already accepted attribute modifier.
- Keep the existing base attribute modifier deps as `amod`, `compound`, and
  `nmod`.

Rule generality classification:

- General dependency-evidence recall rule.
- This is not a semantic repair rule and does not globally treat every `conj`
  token as an attribute. The `conj` token inherits candidacy only through a
  chain rooted at an already accepted noun-chunk attribute modifier.

Target stage and rule id:

- Stage 3.5 R11.1 GPIC observed attribute inventory lookup.
- Stage 4 R13 noun chunk modifier to attribute.

Existing rules affected:

- R11.1/R13 attribute candidate detection gains direct conjunct expansion.
- Object core token consumption remains unchanged.
- Quantity detection remains unchanged and still has precedence over attribute
  detection.
- Stage 5/Stage 6 canonicalization and count aggregation are not changed by
  this rule; they simply receive additional R13 attribute mentions when the
  dependency evidence exists.

Expected count-table impact:

- Attribute count and object-attribute pair count may increase for coordinated
  modifiers such as `maroon and yellow jerseys`.
- Existing base modifier counts should not decrease.

False positive risk:

- Low to medium. A parser may attach a non-attribute conjunct under an accepted
  modifier.
- The risk is bounded because expansion is limited to `conj` chains in the same
  noun chunk and does not cross chunk boundaries.

False negative risk:

- Still misses floating predicate attributes, cross-chunk coordination, and
  conjuncts not reachable from an accepted attribute modifier through a
  same-noun-chunk `conj` chain.

Reversibility:

- Reversible by removing the direct conjunct expansion helper and keeping only
  the base `amod`/`compound`/`nmod` checks.
- Conj-expanded mentions preserve `modifier_source=conj_of_attribute_modifier`
  metadata and the immediate `conj_head_i` for audit.

Verification plan:

- Add a Stage 4 regression test for `maroon and yellow jerseys` where `maroon`
  is `nmod` and `yellow` is `conj`.
- Add a Stage 4 regression test for a chained coordination such as `blue, white,
  and yellow planes`.
- Verify both record-based and doc-direct Stage 4 extraction paths keep the same
  behavior where applicable.
- Run bounded Stage 4 tests and a compile check.

Decision status:

- Approved by user in chat on 2026-07-11.

## 2026-07-12: R18/R18.1 Relation Target Conj Expansion

Proposed rule or lexicon change:

- Extend relation target detection so a confirmed target object reached through
  `dep == "pobj"` can distribute the same relation to object-mapped tokens
  reachable through a target-side `conj` chain.
- Apply this to both single-ADP R18 relations and preposition-MWE R18.1
  relations.
- Do not expand source-side conjuncts in this rule.

Rule generality classification:

- General dependency-evidence recall rule.
- This is not semantic relation source disambiguation and not a label-specific
  rescue. It only follows explicit dependency coordination from an already
  accepted target object.

Target stage and rule id:

- Stage 4 R18 single ADP relation extraction.
- Stage 4 R18.1 preposition MWE relation extraction.

Existing rules affected:

- R18 gains target-side object `conj` expansion after a direct `pobj` target is
  found.
- R18.1 gains target-side object `conj` expansion after a final-ADP direct
  `pobj` target is found.
- R18.1 still treats multiple independent target bases as ambiguous.
- R18.1 with one source candidate and one target base creates normal relation
  edges for the base target and each coordinated target.
- Stage 5 R24 and Stage 6 counting rules remain unchanged; they consume the
  extra Stage 4 relation edges.

Expected count-table impact:

- Relation triple count may increase for coordinated targets such as
  `next to a wall and a door` or `in front of a wall and a banner`.
- Ambiguous relation candidate count should not increase for target conjunction
  alone when there is one source candidate and one target base.
- Existing ambiguous cases with multiple independent target bases remain
  ambiguous relation candidates.

False positive risk:

- Low to medium. A parser may attach a non-target object as a conjunct of the
  target object.
- The risk is bounded because expansion starts only from an object-mapped
  direct `pobj` target and follows only object-mapped `conj` chains.

False negative risk:

- Still misses target objects not connected by dependency `conj`, targets in
  appositive/list structures, target objects hidden behind pronouns, and source
  conjunct expansion.

Reversibility:

- Reversible by removing the target-conj expansion helper and restoring direct
  `pobj` target candidates only.
- Edge metadata records `target_resolution`, `target_base_i`, and immediate
  `conj_head_i` for expanded targets.

Verification plan:

- Add a Stage 4 test where R18 `on` creates relation edges to both a base
  target and a coordinated target.
- Add a Stage 4 test where R18.1 `in front of` creates normal relation edges to
  both a base target and a coordinated target when the source is resolved.
- Keep the existing R18.1 multiple-independent-target test ambiguous.
- Run bounded Stage 4 and Stage 6 tests plus syntax check.

Decision status:

- Approved by user in chat on 2026-07-12.

## 2026-07-12: R16.2/R17.1 Passive Voice Event Role Normalization

Proposed rule:

- Add Stage 4 R17.1: if an action head has a direct object-mapped
  `nsubjpass` or `csubjpass` child, create an `event_role` edge with label
  `patient`.
- Add Stage 4 R16.2: if that same action has already produced an R17.1
  passive subject edge, and the action has a direct `by` child with
  `dep in {"agent", "prep"}` whose direct `pobj` child is object-mapped,
  create an `event_role` edge with label `agent`.
- Preserve passive evidence in metadata:
  - R17.1: `raw_role=theme`, `voice_normalization=passive_to_active`,
    `role_source=passive_subject`.
  - R16.2: `raw_role=by_agent_or_causer`,
    `voice_normalization=passive_to_active`,
    `role_source=passive_by_phrase`.
- Stage 6 keeps count keys unchanged but adds `raw_role` and
  `voice_normalization` as explanatory aggregate fields for
  `agent_patient_pair_counts.tsv`.

Rule generality classification:

- General dependency-evidence semantic-role normalization.
- This is not a caption-specific patch. It uses standard passive dependency
  evidence and object mapping.
- R16.2 is gated on R17.1, so active `walk by a river`-style uses of `by` do
  not become passive agents.

Target stage and rule id:

- Stage 4 R17.1 passive subject to patient/theme.
- Stage 4 R16.2 passive by-phrase to agent/by-agent-or-causer.

Existing rules affected:

- R16 remains direct active `nsubj -> agent`.
- R16.1 still blocks passive-like conjunct targets; it does not infer passive
  roles.
- R17 remains direct `obj/dobj` and selected phrasal-action ADP `pobj`
  patient extraction.
- R18/R18.1 relation extraction is unchanged.
- Stage 5 remains graph-shape preserving; passive role edges are created
  before canonicalization.

Expected count-table impact:

- Passive subjects can increase `event_role:*:patient:*` counts.
- Passive `by` phrases can increase `event_role:*:agent:*` counts.
- Existing active event role count keys keep their old meaning.
- Additional `raw_role` and `voice_normalization` columns explain whether a row
  came from passive normalization.

False positive risk:

- Low to medium. Parser errors around `by` attachment could still create a
  passive agent, but the rule requires a passive subject on the same action.

False negative risk:

- No non-`by` passive causer recovery.
- No passive role recovery when the passive subject or by-object is not
  object-mapped.
- No coreference-based passive agent/theme recovery.
- No action collapse such as treating `surrounded by` as a phrasal action.

Rejected scope:

- Do not perform passive repair in Stage 5.
- Do not infer passive agents from arbitrary relations or prepositions.
- Do not create new object mentions to satisfy passive roles.

Verification plan:

- Add failing Stage 4 tests for `nsubjpass -> patient`, passive `by` agent,
  and active `by` non-passive negative case.
- Add Stage 6 test that passive metadata survives into fact/table output.
- Run bounded Stage 4 and Stage 6 tests, then re-run the current 100-caption
  sample and inspect added R16.2/R17.1 edges.

Decision status:

- Approved by user in chat on 2026-07-12.

## 2026-07-12: R16.1 Action Conjunct Agent Inheritance

Proposed rule or lexicon change:

- Add agent-only event-role inheritance for conjunct actions.
- After direct R16/R17 event role extraction, if an action head has
  `dep == "conj"`, has no existing agent, and its dependency head is another
  action with exactly one agent, copy that agent to the conjunct action.
- Do not inherit into passive-like target actions whose direct children include
  `nsubjpass`, `auxpass`, or `agent`.
- Do not inherit patients.

Rule generality classification:

- General dependency-evidence recall rule.
- This is not semantic role inference. It only transfers an already observed
  agent across explicit action coordination.

Target stage and rule id:

- Stage 4 R16.1 action conjunct agent inheritance.

Existing rules affected:

- R16 direct `nsubj` agent extraction remains unchanged.
- R17 patient extraction remains unchanged.
- R16.1 runs after direct R16/R17 role creation and before R18/R18.1 relation
  extraction.
- Stage 5 and Stage 6 consume the additional raw `event_role` agent edges
  without adding new interpretation.
- Passive-like target actions are excluded because their surface subject is
  already patient-like or their agent may be expressed by a `by` phrase.

Expected count-table impact:

- Agent/patient pair count may increase for coordinated actions such as
  `dogs standing and moving`.
- Action count should not change.
- Patient count should not change from this rule.

False positive risk:

- Low to medium. A parser may attach two actions as conjuncts even when the
  agent should not be shared.
- The risk is bounded by requiring the source action to have exactly one agent
  and the target action to have no existing agent.
- A 100-caption self-review found false positives such as active action
  `stand` sharing its agent into passive-like `parked/framed`; the
  `nsubjpass`/`auxpass`/`agent` target gate was added as a general syntax
  safety rule.

False negative risk:

- Still misses shared agents when actions are not linked by `conj`, when the
  source action has no extracted agent, or when the source action has multiple
  candidate agents.
- Does not recover passive/semantic agents.

Rejected scope:

- Patient inheritance is intentionally excluded. Structures such as
  `standing and holding a bat` show why copying patient roles across conjunct
  actions would be unsafe.

Reversibility:

- Reversible by removing the R16.1 inheritance pass after direct event role
  extraction.
- Inherited edges preserve `role_source=conj_agent_inheritance`,
  `source_action_i`, `target_action_i`, and `conj_head_i` metadata.

Verification plan:

- Add a Stage 4 regression test where a conjunct action inherits one agent from
  a source action.
- Add a chained-conj regression test to confirm fixed-point inheritance.
- Add a negative regression test proving patient roles are not inherited.
- Add a negative regression test proving passive-like conjunct targets do not
  inherit active agents.
- Run bounded Stage 4 tests and Stage 6 count tests.

Decision status:

- Approved by user in chat on 2026-07-12.

## 2026-07-12: GPIC Observed Object Inventory Prior Reuse

Proposed rule or lexicon change:

- Add an optional prior GPIC observed object inventory input to the object inventory builder.
- When a current observed object span has the same exact `span_key` as a final prior GPIC inventory row, reuse the prior row's selected synset, canonical surface, and parent evidence.
- Refresh only current-run evidence fields: `count`, `caption_count`, `example_caption_ids`, and `example_surfaces`.
- Do not reuse unresolved prior rows. A prior row is reusable only when the shared final-inventory gate has no blockers, including selected-synset rows missing canonical surface.

Rule generality classification:

- General offline inventory reuse rule over exact GPIC observed span keys.
- This is not a semantic synonym rule, not an external dataset fallback, and not a tag-list-specific patch.

Target stage and rule id:

- Stage 3.5 GPIC observed object inventory builder.
- Supports R12 by preventing already resolved GPIC spans from re-entering the manual queue.

Existing rules affected:

- Stage 3.5 object lookup still builds rows from observed GPIC noun chunks.
- OEWN lookup remains the fallback for spans not found in the prior GPIC inventory.
- Stage 4 inventory readiness gates remain unchanged.
- COCO/LVIS/Objects365/OpenImages/Visual Genome source-label inventories remain inactive for this pipeline.

Expected count-table impact:

- No direct count-table change by itself.
- Current run counts/examples are recalculated, but reused rows preserve prior semantic decisions so sentence and tag-list batches can be accumulated without repeating manual work.

False positive risk:

- Low for exact `span_key` reuse. Risk is stale or wrong prior decisions being carried forward.
- This is bounded by using only final prior rows and recording `prior_gpic_observed_object_inventory` in `decision_basis`.

False negative risk:

- Spans with different surface keys still require normal lookup/manual resolution.
- No alias, synonym, or external source-label matching is attempted.

Reversibility:

- Remove `--prior-object-inventory` usage or ignore rows whose `decision_basis` contains `prior_gpic_observed_object_inventory`.

Verification plan:

- Add unit tests for exact prior reuse, unreusable pending prior rows, and prior MWE span precedence before OEWN probing.
- Rebuild the tag-list object inventory with the sentence100 resolved object inventory as prior and compare `needs_manual` count.

Decision status:

- Approved by user in chat on 2026-07-12 after tag-list object inventory repeated already resolved GPIC spans as `needs_manual`.

## 2026-07-12: Attribute Canonical Exact Surface Matching Correction

Proposed rule or lexicon change:

- Correct the existing offline canonical surface implementation so exact
  observed-surface tie breaking uses actual observed caption surfaces, not the
  lookup-only `selected_query`.
- Preserve the existing canonical matching-key diacritic folding when a raw
  observed surface uniquely matches one selected-synset lemma after folding.

Rule generality classification:

- General implementation correction for the documented R11.2 canonical rule.
- This is not an attribute-specific rescue mapping and not a semantic alias.

Target stage and rule id:

- Stage 3.5 offline attribute canonical inventory build.
- Shared canonical helper used by R11.2 and object canonical enrichment.

Existing rules affected:

- Existing rule already says canonical tie-breaking uses the observed caption
  span surface.
- Existing canonical matching key already folds diacritics, introduced for
  cases such as `café -> cafe`.
- This change prevents `selected_query` from acting like an observed exact
  surface in canonical tie-breaking.

Expected count-table impact:

- No new raw extraction rows.
- Canonical labels may change only for rows previously blocked as canonical
  ambiguous because of display-case or diacritic matching:
  - letter-like attributes such as `E`, `N`, `S`
  - accented surfaces whose OEWN lemma omits the accent, such as `sautéed`

False positive risk:

- Low. Candidate lemmas still must belong to the selected OEWN synset.
- Diacritic-folded exact matching selects only when exactly one lemma matches
  the raw observed surface key.

False negative risk:

- Low. If more than one lemma matches the observed key, the row remains in the
  existing count/ngram/manual path.

Reversibility:

- Revert the canonical helper changes and remove the regression tests.
- Affected rows preserve `canonical_selection_tag` evidence.

Verification plan:

- Add regression tests for:
  - `observed_surface=E`, `selected_query=e`, selected synset lemmas `E|e`
    choosing `E`.
  - `observed_surface=sautéed`, selected synset lemmas `saute|sauteed`
    choosing `sauteed`.
- Re-run the 1k attribute canonical enrichment and confirm
  `canonical_ambiguous_rows=0`.

Decision status:

- Approved by user in chat on 2026-07-12 as part of reprocessing the attribute
  inventory after the object correction pass.

## 2026-07-12: Manual Attribute Synset Decision For `star`

Proposed rule or lexicon change:

- Apply the explicit manual attribute decision for observed attribute surface
  `star` in the corrected front-1000 GPIC attribute inventory.
- Change the selected synset from the celestial-body noun sense
  `oewn-09467004-n` (`noun.object`) to the shape sense
  `oewn-13904301-n` (`noun.shape`).
- Let the canonical enrichment step recompute the canonical surface from the
  selected synset instead of trusting a hand-entered canonical field.

Rule generality classification:

- Explicit user-approved manual decision.
- This is not a new automatic fallback rule.

Target stage and rule id:

- Stage 3.5 R11.1 GPIC observed attribute inventory lookup.
- Stage 3.5 R11.2 offline attribute canonical inventory build.

Existing rules affected:

- R11.1 remains unchanged; this only resolves one pending manual attribute row.
- R11.2 remains the owner of canonical surface selection.

Expected count-table impact:

- The `star` attribute row can proceed through canonicalization and Stage 5.
- Attribute extraction row count is unchanged.
- Canonical attribute count may include `star` instead of blocking the run.

False positive risk:

- Low for this run because the user explicitly selected the shape sense for the
  observed `star` modifier in `yellow star sign`.

False negative risk:

- None introduced for automatic extraction; this manual decision only resolves
  the current observed row.

Reversibility:

- Reversible by changing the `star` row in the manual resolved attribute TSV
  back to `needs_manual` or selecting another OEWN synset.
- The selected synset and selection tag remain visible in the inventory row.

Verification plan:

- Overlay the one-row manual decision on
  `gpic_observed_attribute_inventory_rebuilt_after_object.tsv`.
- Re-run attribute canonical enrichment and require
  `canonical_ambiguous_rows=0`.
- Run the Stage 5 attribute inventory readiness gate.
- Export the Stage 5 attribute lexicon bundle from the corrected inventory.

Decision status:

- Approved by user in chat on 2026-07-12.

## 2026-07-12: Formal Stage 4 Action Inventory Gate

Proposed rule or lexicon change:

- Require a resolved GPIC observed action inventory before formal Stage 4 raw
  extraction.
- Keep runtime OEWN action lookup available only through an explicit
  preview/debug flag, so formal runs cannot accidentally skip Stage 3.6 action
  inventory preparation.

Rule generality classification:

- General formal pipeline gate.
- This is not an extraction rule or semantic rescue mapping.

Target stage and rule id:

- Stage 3.6 action inventory preparation.
- Stage 4 R15 action extraction gate.

Existing rules affected:

- R15 action extraction remains unchanged once Stage 4 begins.
- `run_stage4_extract_raw.py` and `run_mixed_caption_pipeline.py` must refuse
  formal execution without `--action-inventory`.
- Runtime OEWN action lookup is still allowed for explicitly marked preview or
  probe runs only.
- The offline action inventory builder must use the same preposition MWE span
  detection as Stage 4 before building action candidates, so relation MWE tokens
  cannot become phrasal action tokens during inventory preparation.

Expected count-table impact:

- No direct count change from the gate itself.
- Prevents formal Stage 4/5/6 outputs from being created before action
  `needs_manual` rows are resolved.

False positive risk:

- None for extraction behavior.

False negative risk:

- None for extraction behavior.
- Operationally, users must now provide action inventory before formal Stage 4.

Reversibility:

- Reversible by restoring optional action inventory in the runners.
- Preview flag keeps a narrow escape hatch for diagnostics.

Verification plan:

- Add runner tests that Stage 4 refuses missing action inventory unless the
  explicit preview flag is present.
- Add mixed runner test that formal `run_mixed_caption_pipeline()` refuses
  missing action inventory.
- Add action inventory builder test proving relation MWE consumed tokens are
  excluded before phrasal action candidate selection.
- Run bounded tests for formal gates and mixed pipeline helpers.

Decision status:

- Approved by user in chat on 2026-07-12 after the front-1k run attempted to
  move past Stage 3.5 without enforcing Stage 3.6.

## 2026-07-13: Object/Attribute Surface-Changing Lookup Conflict Gate

Proposed rule or lexicon change:

- Remove automatic prior `selected_query` reuse from GPIC observed object and
  attribute inventories.
- Keep exact `span_key` prior reuse, but reject old automatic surface-changing
  prior rows when the row has `span_key != selected_query` and no explicit
  manual-decision evidence.
- During object runtime lookup, compare observed exact-surface hits with
  surface-changing lookup hits from lemma/Morphy/normalization.
- During attribute runtime lookup, run the same exact-vs-base conflict check
  only when the observed attribute token is a plural common noun.
- If both routes find OEWN candidates and select different synsets, mark the row
  `needs_manual` instead of accepting the first lookup hit.
- Keep prior `selected_query` reuse for action inventory only, where the target
  behavior is verb inflection reuse such as `rides -> ride` and `sitting in ->
  sit in`.

Rule generality classification:

- General rule.
- This is not a one-off mapping for `glasses`; `glasses -> glass`,
  `arms -> arm`, `works -> work`, and similar lexicalized plural/lemma conflicts
  are all handled by the same gate.

Target stage and rule id:

- Stage 3.5 GPIC observed object inventory.
- Stage 3.5 R11.1 GPIC observed attribute inventory lookup.
- Stage 4 R12 object extraction uses the same object lookup helper.

Existing rules affected:

- Replaces the older object rule that plural common noun heads looked up the
  head lemma before observed exact surface.
- Replaces object/attribute unique selected-query prior reuse with exact
  `span_key` prior reuse only.
- Does not change R15 action selected-query reuse.

Expected count-table impact:

- Previously accepted object rows, and plural common noun attribute rows, may
  move back to `needs_manual`
  when observed exact and changed-query lookup disagree.
- Front-1000 outputs generated before this rule are invalid for object/attribute
  canonical/count analysis until the inventories are rebuilt and pending rows
  are resolved.
- Stage 4/5/6 should block if the rebuilt object or attribute inventory has
  pending `needs_manual` rows.

False positive risk:

- Lower for object/attribute because lexicalized plural and surface-changing
  conflicts no longer silently select the lemma/base-form sense.

False negative risk:

- Medium operationally: some spans that could have been automatically resolved
  by lemma/Morphy will now require manual resolution when the observed exact
  surface also has a competing OEWN sense.

Reversibility:

- Reversible by restoring selected-query reuse and lemma-first lookup order.
- The generated inventories preserve `span_key`, `observed_surface`,
  `selected_query`, `all_oewn_synsets`, `all_oewn_lexfiles`,
  `synset_selection_tag`, and `decision_reason`, so reopened rows can be audited.

Verification plan:

- Add object unit tests for lexicalized plural conflict and for no object
  selected-query prior reuse.
- Add attribute unit tests for observed exact vs Morphy conflict and for no
  attribute selected-query prior reuse.
- Run bounded object and attribute inventory tests.
- Rebuild the front-1000 object and attribute inventories before running formal
  Stage 4/5/6 again.

Decision status:

- Approved by user in chat on 2026-07-13 after the `glasses -> glass` issue was
  traced to broad selected-query/lemma reuse.

## 2026-07-13: Plural Object Exact-Vs-Base Candidate Conflict Gate

Proposed rule or lexicon change:

- Strengthen the object surface-changing lookup conflict gate only for plural
  common noun head spans.
- If a plural common noun exact observed surface has an OEWN noun hit, still
  probe the lemma/Morphy/base-form query for conflict evidence.
- If exact plural and base-form queries both have OEWN noun candidates, keep
  automatic selection only when the base-form hit resolves to the same selected
  synset as the exact plural hit.
- If the base-form hit has candidates but no selected synset, or resolves to a
  different selected synset, mark the row `needs_manual` with
  `decision_reason=manual_surface_query_conflict_required`.
- Do not make every plural row manual: plural rows with no competing base-form
  hit, or with exact/base agreement on one selected synset, can still be
  automatic.

Rule generality classification:

- General rule.
- This covers lexicalized plural and plural/base sense conflicts such as
  `glasses/glass`, `colors/color`, `pants/pant`, `trunks/trunk`, and
  `rings/ring` without adding label-specific mappings.

Target stage and rule id:

- Stage 3.5 GPIC observed object inventory.
- Stage 4 R12 object extraction uses the same object lookup helper.

Existing rules affected:

- Narrows and strengthens the earlier surface-changing lookup conflict gate for
  plural common nouns.
- Keeps exact `span_key` prior reuse authoritative. Already resolved/manual
  rows are not reopened unless the user filters or removes them from the prior.
- Does not affect attribute or action selected-query reuse rules.

Expected count-table impact:

- New plural object rows that were previously auto-selected from exact plural
  surface evidence may become `needs_manual` when their base-form query also
  has OEWN noun candidates.
- Formal Stage 4/5/6 should block until those newly pending plural object rows
  are manually resolved.
- Existing non-plural object rows should be unchanged.

False positive risk:

- Lower for plural lexicalization because exact plural and base-form candidate
  conflicts no longer silently choose one side.

False negative risk:

- Medium operationally: some plural rows with harmless base-form ambiguity will
  require manual review.

Reversibility:

- Reversible by removing the plural-specific strict conflict flag from object
  lookup.
- Generated rows preserve `span_key`, `selected_query`, `all_oewn_synsets`,
  `all_oewn_lexfiles`, `synset_selection_tag`, and `wn30_lemma_counts`.

Verification plan:

- Add unit tests where plural exact is selected but base-form has unresolved
  OEWN candidates and the row becomes `needs_manual`.
- Run bounded object inventory and Stage 4 tests.
- Build a filtered-prior 10K object inventory with plural-head prior rows
  removed, then inspect the new plural `needs_manual` output.

Decision status:

- Approved by user in chat on 2026-07-13: "이 rule 기준으로 다시 해야겠다.
  object lexicon에서 plural이었던거 싹 다 지워버리고 need manual 다시 뽑아보자."

## 2026-07-13: User Manual Decision Authority Over Semantic Audit

Proposed rule or lexicon change:

- Clarify that explicit user-provided manual TSV rows and exact row decisions
  are authoritative pipeline decisions.
- A later semantic audit may flag a selected synset as questionable, but that
  audit is advisory only. It must not automatically reopen, override, or block
  a row that the user manually marked as `chosen` or `excluded`.

Rule generality classification:

- General decision-management rule.
- This is not a label-specific rescue mapping or automatic lookup rule.

Target stage and rule id:

- Stage 3.5 GPIC observed object/attribute/action inventories.
- Stage 4/5/6 formal gates that consume resolved inventories.

Existing rules affected:

- Clarifies the manual resolution gate after object/attribute/action
  `needs_manual` rows are resolved.
- Does not change OEWN lookup, canonicalization, parent enrichment, or count
  export logic.

Expected count-table impact:

- None for already resolved inventories.
- Future semantic audits will report advisory findings without invalidating
  user-approved manual decisions unless the user explicitly asks to revise them.

False positive risk:

- The pipeline may preserve a user-approved synset that a later audit considers
  semantically suboptimal.

False negative risk:

- Lower operational churn: manually approved rows are not repeatedly reopened by
  advisory audits.

Reversibility:

- Reversible by reopening specific rows with an explicit user request and a
  new manual decision TSV.
- Existing row metadata preserves `decision_status`, `decision_reason`,
  `selected_query`, `selected_oewn_synset`, and manual-resolution fields.

Verification plan:

- Treat manual overlay outputs as resolved when `needs_manual=0`.
- Report semantic audit concerns separately from formal blocker status.

Decision status:

- Approved by user in chat on 2026-07-13: even if a manual row appears wrong,
  follow the user's manual decision.

## 2026-07-14: Stage 3.5 Google Ngram Evidence Missing Must Refresh Before Canonical Manual Block

Proposed rule or lexicon change:

- When attribute/action canonical enrichment produces a
  `canonical_selection_tag` containing `google_ngram_evidence_missing`, the
  workflow must treat it as missing evidence, not as a manual canonical
  decision.
- The workflow now checks whether the required
  `(selected_oewn_synset, google_ngram_candidate surface_key)` rows exist in
  `resources/source_labels/google_ngram_canonical_frequency_evidence.tsv`.
- If any required evidence row is absent, the workflow runs a Google Ngram
  evidence refresh script, appends/updates the evidence TSV, and reruns the
  canonical enrichment step.
- Only after the evidence rows exist and canonical still cannot be selected
  may the row remain as a canonical blocker/manual review item.

Rule generality classification:

- General workflow guard.
- This is not a term-specific rescue rule; the `de-icing -> de-ice/deice`
  case exposed the missing guard.

Target stage and rule id:

- Stage 3.5 offline attribute/action canonical inventory build.
- Affects the workflow orchestration rule that advances inventory build steps.

Existing rules affected:

- Enforces the existing canonical rule that missing Google Ngram evidence must
  be queried before manual canonical resolution.
- Does not change canonical selection order after evidence is available.

Expected count-table impact:

- No direct Stage 6 count semantics change.
- Prevents rows from being incorrectly sent to manual canonical review before
  Google Ngram evidence has been collected.

False positive risk:

- Google Books Ngram may prefer a surface that is more frequent in books than
  in visual captions, but this is already the accepted fallback criterion.

False negative risk:

- If Google Ngram API returns no record, a status row is still written; the
  workflow will not repeatedly query the same missing pair and will then leave
  the row as a real canonical blocker.

Reversibility:

- Reversible by removing the refresh action from
  `scripts/run_stage35_inventory_workflow.py` and deleting the appended
  evidence rows.

Verification plan:

- Unit tests assert that missing evidence triggers
  `refresh_attribute_google_ngram_evidence` instead of
  `blocked_attribute_canonical`.
- Unit tests assert that an already queried missing evidence row blocks without
  an infinite refresh loop.
- Unit tests assert that the refresh action reruns attribute canonical
  enrichment.

Decision status:

- Approved by user in chat on 2026-07-14 after repeated missing-evidence
  failures: local evidence TSV misses must trigger a fresh Google Ngram query.

## 2026-07-24: R11.1 Attribute Inventory Conjunct Consistency Repair

Proposed rule or lexicon change:

- Repair the Stage 3.5 observed attribute inventory builder so it applies the
  same same-noun-chunk conjunct expansion already implemented by Stage 4 R13.
- A `conj` token becomes an inventory candidate only when it is reachable from
  an accepted `amod`/`compound`/`nmod` modifier through the existing R13
  conjunct traversal.
- Reuse the Stage 4 record-path conjunct helper rather than maintaining a
  second independent traversal.
- Keep R14 quantity precedence in the inventory builder as well as Stage 4.
- Add an attribute-candidate rule version to checkpoint metadata so a
  pre-repair checkpoint cannot resume into a post-repair inventory.

Rule generality classification:

- Existing-rule implementation consistency repair.
- This does not add global `conj` interpretation or a term-specific fallback.

Target stage and rule id:

- Stage 3.5 R11.1 observed attribute inventory lookup.
- Aligns its candidate set with Stage 4 R13.

Existing rules affected:

- Implements the R11.1 conjunct behavior approved on 2026-07-11 but omitted
  from `build_gpic_observed_attribute_inventory.py`.
- R13 extraction semantics, OEWN lookup order, canonicalization, and Stage 6
  aggregation are unchanged.

Expected count-table impact:

- After rebuilding and publishing the attribute inventory, conjunct attributes
  that previously reached Stage 5 as raw fallback can receive their resolved
  OEWN canonical mapping.
- Attribute mention and edge occurrence counts do not increase solely from
  this repair because R13 already emitted the conjunct mentions.

False positive risk:

- No new risk beyond the already approved R13 same-chunk conjunct expansion.
- The traversal remains rooted at an accepted base modifier and excludes
  consumed object-core and quantity tokens.

False negative risk:

- Cross-chunk coordination and conjuncts not reachable from an accepted base
  modifier remain excluded.

Reversibility:

- Reversible by removing the shared conjunct expansion call from the inventory
  builder and restoring the prior checkpoint rule version.

Verification plan:

- Add direct and chained conjunct inventory tests.
- Verify quantity-like conjuncts remain excluded.
- Audit current Stage 4 conjunct attribute mentions whose Stage 5
  `canonical_source` is `raw_fallback`, then probe their surfaces against
  OEWN 2025+.

Decision status:

- Approved by user in chat on 2026-07-24.

Verification result:

- `tests/test_build_gpic_observed_attribute_inventory.py` now covers direct
  conjuncts, chained conjuncts, and quantity precedence.
- `tests/test_audit_attribute_conj_inventory_coverage.py` covers the exact
  Stage 5 synonym-key audit semantics.
- Combined targeted test result: `20 passed`.
- The existing 1M Stage 4 artifact contained 170,852 conjunct attribute
  mentions across 4,591 normalized surfaces.
- Current attribute synonyms covered 3,245 surfaces. The remaining 1,346
  surfaces accounted for 2,400 raw-fallback mentions.
- OEWN re-probing found synsets for 267 of those fallback surfaces
  (417 mentions): 68 were auto-selectable and 199 remained `needs_manual`.
- Audit artifacts:
  `outputs/front1000000_gpic_nano_20260715/attribute_conj_inventory_coverage_20260724/`.
## 2026-07-25: R11.1/R13/R20 OEWN Attribute MWE Support

- Existing single-token attribute inventory rows remain authoritative and are
  never replaced by a prefix snapshot.
- Adds OEWN-valid contiguous multi-token attribute units to the same cumulative
  attribute inventory under the composite key
  `(attribute_unit_type, span_key)`.
- Stage 3.5 and Stage 4 share one selector for object-core boundaries,
  same-chunk conjunct branches, relation/quantity barriers, longest-span
  selection, and local suppression of internal single attributes.
- MWE lookup permits space/hyphen/underscore equivalence and anchor-only
  Morphy. It forbids phrase-wide Morphy and joined separator removal.
- Stage 4 formal runners require a resolved inventory with the current MWE
  schema/rule version before extraction.
- Stage 5 exports both single and MWE raw-to-canonical mappings and blocks
  conflicting raw keys.
- Prefix rollout updates only validated MWE rows. A larger prefix replaces
  MWE count/evidence with its full-prefix rescan values instead of adding them.
- Stage 5 treats the current MWE inventory as authoritative for MWE raw keys:
  prior `gpic_observed_attribute_inventory` synonym rows for those keys are
  removed and regenerated from the current inventory. Conflicts with manual or
  external lexicon sources still block export.
- Stage 4 registers exact observed MWE forms before generated lookup aliases.
  `span_key`, `observed_surface`, and `example_surfaces` own their
  separator-equivalent keys; Morphy/separator aliases from `lookup_forms`
  cannot override an exact owner. Conflicting aliases without an exact owner
  still block extraction.
- Checkpoint schema/rule versions prevent reuse of pre-MWE checkpoints.
- Expected Stage 6 changes are limited to attribute mentions,
  `has_attribute`, attribute counts, and attribute-object pairs.

## 2026-07-26: Attribute MWE Rollout Verification Must Stream Shards

- Formal 1M Stage 4/5 output may be stored either directly under a stage
  directory or under `shards/shard_*/stage4|stage5`.
- The rollout verifier resolves both layouts and streams every JSONL file.
  It must not materialize the complete 1M mention or edge corpus in RAM.
- Only selected MWE mentions and their keys are retained for cross-stage
  checks. Per-caption attribute rows are released after overlap validation.
- Publication is blocked unless all non-attribute Stage 4 type counts and
  non-attribute Stage 6 tables are unchanged, quantity rows are unchanged,
  MWE mention counts match the prefix inventory, each attached MWE has exactly
  one edge, and every MWE canonical mention is a lexicon hit.

## 2026-07-26: Resolved Attribute MWE Evidence Recount

- OEWN discovery and final lexicon application are separate passes.
  A manually resolved MWE or separator-equivalent form can match additional
  occurrences that the discovery pass could not yet associate with that row.
- After formal Stage 4, chosen MWE `count`, `caption_count`,
  `example_caption_ids`, `observed_surface`, and `example_surfaces` are
  replaced from emitted MWE mentions keyed by
  `source_detail.inventory_span_key`.
- Recount is a full-prefix replacement, not an additive update. Existing
  single-token and excluded MWE rows are not modified.
- Unknown Stage 4 inventory keys, duplicate mention keys, unresolved rule
  versions, and chosen MWE rows with zero Stage 4 mentions block the recount.

## 2026-07-26: Attribute MWE Stage 4/5 Separator Contract

- Stage 5 exports separator-equivalent raw keys for MWE rows using the same
  normalization as the Stage 4 matcher.
- This covers cases such as inventory `stained-glass window` matching the raw
  Stage 4 surface `stained glass window`.
- Existing/manual canonical conflicts continue to block lexicon export.

## 2026-07-26: Legacy Quantity Baseline Normalization

- Rollout verification compares current unified quantity rows against legacy
  `quantity_counts.tsv` and `object_quantity_pair_counts.tsv` when the
  baseline predates `attribute_kind`.
- `count_key` and storage-table names are normalized away; semantic labels,
  parents, counts, caption counts, examples, raw variants, and rule IDs remain
  part of the equality check.

## 2026-08-25: R28 Fixed-Lexicon Scale-Out Orchestration

Proposed rule or lexicon change:

- Add a formal outer runner for fixed-inventory GPIC Lite/Full extraction.
- Group immutable caption shards into deterministic work units, run the
  existing mixed Stage 1-6 implementation for each unit, and atomically write
  a completion receipt only after output validation.
- Permit resume with a different visible GPU count by keeping runtime worker
  assignments outside the immutable run identity.
- Merge completed unit Stage 6 directories through the existing count-table
  merger and preserve Stage 5 unit roots for downstream adapters.

Target stage and rule id:

- Operational execution across Stage 1-6 under R28.

Existing rules affected:

- R1-R27 semantics are unchanged.
- R28 gains a production-scale resume and artifact-integrity contract.

Expected count-table impact:

- None relative to running the same immutable inputs and inventory bundle in
  one formal mixed run. Unit-local count tables are intermediate partitions;
  the existing Stage 6 merger produces the global tables.

False positive risk:

- None from extraction semantics. The main operational risk is accepting a
  stale or partial unit, addressed by receipt/input/inventory hashes and atomic
  promotion.

False negative risk:

- None from extraction semantics. A missing unit remains visibly incomplete
  and blocks final merge.

Reversibility:

- Fully reversible by running the existing monolithic/sharded mixed runner on
  the same inputs. The scale-out wrapper does not mutate inventories.

Verification plan:

- Unit-test deterministic grouping, identity stability across GPU counts,
  receipt validation, partial-output rejection, and completed-unit resume.
- Compare a bounded multi-unit run against a single formal run for Stage 5 row
  totals and every Stage 6 table.
- Verify remote progress, cgroup memory, GPU assignment, and output growth
  before launching 10M.

Decision status:

- Approved by user in chat on 2026-08-25 for resumable 10M/100M fixed-lexicon
  extraction with 1-8 GPUs and GPU-count changes across restarts.

## 2026-08-25: Stage 1 Lite/Full Caption Identifier Alias

Proposed rule or lexicon change:

- Accept `id` as the Lite/Full immutable-shard spelling of the same caption
  SHA stored as `key` in Nano-era GPIC rows.
- If both fields occur, require exact equality and fail on conflict.

Target stage and rule id:

- Stage 1 input adaptation under R1; no extraction or lexicon rule changes.

Expected count-table impact:

- None for equivalent inputs. This only lets the formal Lite/Full shard schema
  reach the existing Stage 1-6 implementation without rewriting source data.

Risk and reversibility:

- Conflicting identifiers are blocked instead of guessed. The change is
  reversible by normalizing immutable input rows back to `key` before Stage 1.

Verification:

- Unit tests cover `key`, `id`, matching dual fields, conflicting dual fields,
  and missing identifiers.
- A 100-caption two-unit MLXP smoke must match a single formal run before 10M.
