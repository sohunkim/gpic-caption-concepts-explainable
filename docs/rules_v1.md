# V1 Explainable Rule Set

이 문서는 GPIC caption을 countable concept으로 바꾸기 위한 v1 baseline rule을 고정한다.

목표는 누락을 최소화하는 것이 아니라, 각 산출물이 어떤 rule에서 나왔는지 설명 가능한 baseline을 만드는 것이다.

현재 구현 상태:

- Stage 1 caption shape 판단 구현됨
- Stage 2 spaCy preprocessing 구현됨
- Stage 3 spaCy linguistic annotation은 `en_core_web_trf` 기준으로 구현됨
- Stage 4 raw concept extraction 구현됨
- Stage 5 canonicalization 구현됨
- Stage 6 count export 구현됨
- Stage 2 object MWE retokenization은 비활성화됨
- object span/MWE 판단은 GPIC caption에서 관측한 object span inventory를 기준으로 수행함
- attribute synset/canonical 판단은 GPIC caption에서 관측한 attribute inventory를 기준으로 offline 준비함
- attribute type taxonomy는 아직 active Stage 5/6 output에 붙이지 않고 offline audit artifact로만 둠
- preposition MWE는 Stage 4에서 lexicon span으로 감지하고 relation edge/count 및 ambiguous relation occurrence count에 사용함
- tag-list caption은 comma segment별 spaCy annotation 후 object/attribute/quantity만 같은 raw schema로 추출함
- formal mixed runner는 sentence와 tag-list를 shape별 annotation path로 처리한 뒤 원래 caption 순서의 단일 Stage 3 records 파일로 합치고, Stage 4/5/6은 그 단일 파일에서 하나의 count set을 생성함
- Stage 3.5 inventory workflow runner는 object/attribute/action inventory 준비 상태를 판정하고, clear된 단계 다음 offline step으로 자동 진행함
- Stage 3.5 inventory workflow가 완료되면 object/attribute/action/lexicon final paths를 묶은 inventory bundle manifest를 남기고, 다음 inventory build나 formal mixed run은 이 bundle을 단일 입력으로 사용할 수 있음
- 현재 active inventory는 output snapshot 경로가 아니라 `resources/gpic_inventory/current/inventory_bundle.json`으로 publish된 central bundle을 기준으로 함
- 공식 inventory promotion run은 Stage 3.5 workflow 완료 시 `--publish-current`로 central bundle publish까지 같은 workflow 안에서 수행함
- central bundle publish는 object/attribute/action TSV, action canonical TSV, Stage 5 lexicon dir, action inventory pipeline-state sidecar를 함께 복사해야 하며, formal run은 이 central bundle을 단일 입력으로 사용함
- COCO/LVIS/Objects365/OpenImages/Visual Genome source-label inventory는 active pipeline 입력이 아님
- Stage 2 output은 concept output이 아니라 token/span inspection output임

## 0. Output 목표

최종 산출물은 문장 재생성이 아니다.

최종 산출물은 아래 count table을 만들 수 있는 구조화 records다.

- object count
- attribute count
- object-attribute pair count
- action count
- agent/patient pair count
- relation triple count
- relation component count
- ambiguous relation occurrence count
- object co-occurrence pair count

## 1. Six-Stage Pipeline

| Stage | 이름 | 입력 | 출력 | 핵심 원칙 |
|---|---|---|---|---|
| 1 | Caption shape 판단 | raw caption row | sentence 또는 tag-list shape label | caption 종류를 판단한다. tag-list는 sentence path와 분리된 tag-list path로 보낸다. |
| 2 | spaCy preprocessing | caption text | protected spaCy Doc | tokenization 후 깨지면 안 되는 span만 merge한다. |
| 3 | spaCy linguistic annotation | protected spaCy Doc 또는 tag-list segment Docs | token, POS, TAG, MORPH, lemma, dependency, noun chunk | spaCy annotation만 한다. tag-list는 comma segment별로 따로 annotate한다. concept extraction은 하지 않는다. |
| 3.5 | GPIC observed inventories and relation MWE preparation | Stage 3 records, reviewed preposition MWE lexicon | observed object span inventory, observed attribute inventory, observed action inventory, relation MWE lexicon bundle | GPIC caption에서 관측된 object/attribute/action span을 OEWN lookup하고 ambiguous는 offline으로 해결한다. Action inventory 후보 생성 전에는 active preposition MWE span을 먼저 감지해 phrasal action 후보에서 제외한다. |
| 4 | Raw concept extraction | annotated sentence Doc 또는 annotated tag-list segments + GPIC object inventory | raw mentions, raw edges | sentence는 GPIC inventory에서 확정된 noun chunk selected span과 dependency에서 직접 보이는 것만 추출한다. tag-list는 segment 내부 object/attribute/quantity만 추출한다. |
| 5 | Canonicalization | raw mentions, raw edges | canonical labels, selected synset metadata, parent evidence | object는 GPIC observed inventory의 canonical surface, selected synset metadata, immediate hypernym parent evidence를 보존한다. inventory canonical이 없으면 raw surface로 남긴다. single ADP relation은 raw-preserving이고 preposition MWE relation은 Stage 4 lexicon canonical label을 보존한다. |
| 6 | Count export | canonical mentions, canonical edges | count tables, fact rows | 새 해석을 하지 않고 집계만 한다. |

## 2. Rule Table

| Rule ID | Stage | Rule 이름 | 입력 | 출력 | 도구 | 도구 유형 | Rule 유형 | Count 영향 | Known limitation |
|---|---:|---|---|---|---|---|---|---|---|
| R1 | 1 | Caption shape 판단 | raw GPIC caption row의 `caption_type` | `sentence` 또는 `tag_list` | custom router | custom fixed rule | baseline | 처리 path 결정 | GPIC `caption_type` 값이 알려진 집합 밖이면 처리하지 않음 |
| R1.1 | 1 | Tag-list route | `tag_list` caption | tag-list row for segment annotation | custom router | custom fixed rule | baseline routing | tag-list object/attribute/quantity count 가능 | tag-list를 sentence parser path로 보내지 않음 |
| R2 | 2 | Tokenization | caption text | spaCy tokens | `en_core_web_trf` tokenizer-only `nlp.make_doc()` | spaCy rule-based tokenizer | baseline | 직접 count 없음 | tokenizer error가 뒤로 전파됨 |
| R3 | 2 | Quote span merge | tokenized quote span | merged quote token | custom quote detector + spaCy Retokenizer | custom fixed rule + spaCy rule-based | baseline | quote가 object로 오염되는 것을 줄임 | unmatched quote는 복구하지 않음 |
| R4 | 2 | Reserved: no object MWE retokenization | 없음 | 없음 | 없음 | inactive | removed baseline rule | 직접 count 없음 | object MWE는 Stage 4 selected span에서 처리 |
| R5 | 2 | Hyphen word merge | hyphen-connected word tokens | merged hyphen token | custom hyphen detector + spaCy Retokenizer | custom fixed rule + spaCy rule-based | baseline | hyphen lexical unit 보존 | numeric range와 symbol expression은 제외 |
| R6 | 3 | TAG annotation | protected Doc | token TAG | spaCy tagger | spaCy learned | baseline evidence | 직접 count 없음 | model error가 뒤로 전파됨 |
| R7 | 3 | Reserved: no object MWE POS correction | 없음 | 없음 | 없음 | inactive | removed baseline rule | 직접 count 없음 | Stage 2 object MWE merge가 없으므로 보정도 없음 |
| R8 | 3 | Dependency parsing | protected Doc | dependency tree | spaCy parser | spaCy learned | baseline evidence | 직접 count 없음 | attachment error가 뒤로 전파됨 |
| R9 | 3 | POS, MORPH annotation | tagged/parsed Doc | POS, MORPH | spaCy attribute_ruler | spaCy rule-based | baseline evidence | 직접 count 없음 | spaCy model 설정에 의존 |
| R10 | 3 | Lemmatization | annotated Doc | token lemma | spaCy lemmatizer | spaCy rule-based | baseline evidence | canonicalization 입력 | lemma는 canonical이 아님 |
| R11 | 3 | Noun chunking | annotated Doc | noun chunks | spaCy noun_chunks | spaCy rule-based over parse | baseline evidence | object/attribute 추출 입력 | spaCy가 놓친 chunk는 v1에서 복구하지 않음 |
| R11.1 | 3.5 | GPIC observed attribute inventory lookup | Stage 3 noun chunks, consumed object core token ids | observed attribute inventory rows | custom rule over Stage 3 records + OEWN lookup | custom fixed rule + external lexical evidence | offline inventory preparation | 직접 count 없음. Stage 4 attribute count를 위한 offline evidence | noun chunk 내부 selected object core span 밖 `amod`/`compound`/`nmod` token과, 그 token에서 같은 noun chunk 내부 `conj` chain으로 이어지는 token만 후보로 삼음 |
| R11.2 | 3.5 | Offline attribute canonical inventory build | selected attribute synset rows | canonical attribute surface evidence | OEWN lemma evidence, WN3 count evidence, optional Google Ngram evidence | fixed policy over offline evidence | offline canonical preparation | 직접 count 없음. Stage 5 attribute canonicalization 준비 | selected synset이 없으면 canonical inventory에서는 적용 불가로 둠 |
| R11.3 | 3.5 | Offline attribute parent taxonomy build | selected/canonical attribute inventory | offline attribute parent/type taxonomy artifact | manual taxonomy decision | explicit manual decision | offline taxonomy preparation | 직접 count 없음. active Stage 5/6에는 아직 반영하지 않음 | attribute parent/type은 OEWN hypernym이 아니라 taxonomy 방식이며 active output에서는 보류 |
| R11.4 | 3.5 | Offline action canonical inventory build | resolved observed action inventory | canonical action surface evidence | OEWN verb lemma evidence, WN3 count evidence, optional Google Ngram evidence | fixed policy over offline evidence | offline canonical preparation | 직접 count 없음. R22 action canonicalization 준비 | `raw_fallback` action row는 selected synset이 없으므로 canonical inventory 적용 불가로 둠 |
| R11.5 | 3.5 | Offline action canonical export | completed action canonical inventory | Stage 5 `action_synonyms.tsv` rows | generated TSV export from completed inventory | deterministic export | lexicon bundle preparation | R22 action count key에 영향 | canonical ambiguous 또는 non-chosen row는 export하지 않음 |
| R12 | 4 | Noun chunk selected span to object | noun chunk, GPIC observed object inventory | object mention with selected span metadata | custom rule over spaCy noun chunk + GPIC inventory lookup | custom fixed rule + inventory lookup | baseline extraction | object count | inventory row가 있으면 `chosen`/`excluded` 모두 object mention으로 세며, `needs_manual` 또는 selected synset의 unresolved canonical row는 Stage 4를 중단함 |
| R13 | 4 | Noun chunk modifier to attribute | noun chunk modifier | attribute mention, has_attribute edge | custom rule over chunk tokens | custom fixed rule | baseline extraction | attribute count, object-attribute pair count | sentence path에서는 chunk 밖 floating attribute는 붙이지 않고, `conj`는 accepted attribute modifier에서 시작하는 same-chunk conj chain 안에서만 확장함. tag-list path에서는 object가 없는 단일 attribute-like segment만 unattached attribute mention으로 보존함 |
| R14 | 4 | Noun chunk modifier to quantity | numeric or quantity-like chunk modifier | quantity mention, has_quantity edge | custom rule + small quantity lexicon | custom fixed rule + custom lexicon lookup | baseline extraction | quantity count | ambiguous quantity는 raw로 남김 |
| R15 | 4 | VERB or selected phrasal action span to action | VERB token, optional particle/preposition child evidence, OEWN verb lookup, relation MWE consumed token ids | action mention with selected action span metadata | custom rule over POS/dependency + OEWN verb lookup | custom fixed rule + external lexical evidence | baseline extraction | action count | no OEWN verb span이면 single VERB raw fallback. relation MWE token은 phrasal action 후보에서 제외 |
| R16 | 4 | `nsubj` to agent | VERB token, `nsubj` child | action-agent edge | custom rule over dependency | custom fixed rule | baseline extraction | agent/patient pair count | active/direct subject만 agent로 연결함 |
| R16.1 | 4 | Action conjunct agent inheritance | action head token with `dep == "conj"`, source conjunct action with exactly one agent | inherited action-agent edge | custom rule over dependency + existing R16/R16.1 agent edges | custom fixed rule | baseline extraction | agent/patient pair count | agent만 상속하며 patient는 상속하지 않음. source action의 agent가 0개 또는 2개 이상이면 상속하지 않음. passive-like target action에는 상속하지 않음 |
| R16.2 | 4 | Passive by-phrase to agent | passive action with object-mapped passive subject, direct `by` child and object-mapped `pobj` | passive by-agent event_role edge | custom rule over dependency | custom fixed rule | baseline extraction | agent/patient pair count | passive subject가 먼저 R17.1로 잡힌 action에서만 적용 |
| R17 | 4 | Object dependency to patient | action head token or selected phrasal action preposition, object child | action-patient edge | custom rule over dependency + selected action span map | custom fixed rule | baseline extraction | agent/patient pair count | default `pobj`는 patient가 아니며, selected phrasal action span이 소비한 ADP의 direct `pobj`만 patient 후보로 사용 |
| R17.1 | 4 | Passive subject to patient/theme | action head token, `nsubjpass`/`csubjpass` object child | passive subject event_role patient edge | custom rule over dependency | custom fixed rule | baseline extraction | agent/patient pair count | raw_role=`theme`, voice_normalization=`passive_to_active` metadata를 남김 |
| R18 | 4 | Single ADP plus direct `pobj` to relation | ADP/preposition token, direct `pobj` child, target-side object `conj` chain | source-relation-target edge | custom rule over dependency | custom fixed rule | baseline extraction | relation triple count | selected phrasal action span 또는 relation MWE span에 소비된 ADP는 single-ADP relation에서 제외. target-side conj만 확장하고 source conj는 확장하지 않음 |
| R18.1 | 4 | Preposition MWE plus final `pobj` to relation | preposition MWE lexicon span, initial relation token head, final ADP direct `pobj` child, target-side object `conj` chain | source-relation-target edge or ambiguous relation candidate edge with canonical relation MWE label and component metadata | custom span matcher + TSV lexicon + dependency evidence | custom fixed rule + custom lexicon lookup | baseline extraction | relation triple count, relation component count, ambiguous relation occurrence count | action-attached MWE with multiple object-mapped child source or multiple independent target bases is not disambiguated; target conj chain from one base target is expanded into normal relation edges |
| R19 | 5 | Object canonicalization from GPIC inventory | raw object surface, selected synset metadata, canonical surface evidence | canonical object label | GPIC observed object inventory source detail | fixed policy over offline canonical decision | baseline canonicalization | canonical object count | canonical surface가 없으면 raw surface 유지 |
| R20 | 5 | Attribute synonym canonicalization | raw attribute surface | canonical attribute | explicit TSV lexicon | custom lexicon lookup | baseline canonicalization | canonical attribute count | attribute type은 active output에서 보류. unknown attribute는 raw surface 유지 |
| R21 | 5 | Quantity raw-preserving canonicalization | raw quantity lemma | same quantity label | no extra tool | fixed policy | baseline canonicalization | quantity count | quantity normalization은 아직 하지 않음 |
| R22 | 5 | Action synonym canonicalization | raw action surface | canonical action | explicit TSV lexicon | custom lexicon lookup | baseline canonicalization | canonical action count | action parent_concepts lexicon은 아직 없음 |
| R23 | 5 | Object parent concept mapping | selected OEWN synset parent evidence | object parent display labels plus synset-id evidence | GPIC object inventory source detail | fixed policy over OEWN hypernym evidence | baseline canonicalization | object parent count | selected synset이 없으면 parent는 empty. 내부 근거는 synset ID이고, 사람이 보는 parent label은 parent lemma display를 먼저 쓴다. |
| R24 | 5 | Relation canonicalization | raw single-ADP relation label or preposition MWE label | single ADP는 raw-preserving, preposition MWE는 Stage 4 lexicon canonical relation label 유지 | no extra tool | fixed policy over Stage 4 evidence | baseline canonicalization | relation count | Stage 5에서 relation source/target 또는 label을 새로 추론하지 않음 |
| R25 | 6 | Count export | canonical mentions and edges | count tables, fact rows | exporter | custom fixed rule | baseline export | final output | 새 linguistic interpretation 금지. single-ADP relation은 canonical relation label 자체를 component 하나로 세고, preposition MWE는 Stage 4의 `relation_components` metadata를 component별로 센다. ambiguous relation candidate는 Stage 4 relation MWE metadata에서만 생성 |
| R26 | 3.5-6 | Formal pipeline state manifest gate | generated formal pipeline artifacts and sidecar manifest | `pipeline_state.json` or artifact sidecar state plus gate pass/fail | custom manifest writer/reader | custom fixed rule | formal execution gate | 직접 count 없음. stale/preview/out-of-order artifacts가 formal Stage 4/5/6으로 들어가는 것을 차단 | manifest가 없는 legacy artifact는 formal input으로 거부되며, 필요하면 해당 artifact를 현재 runner로 재생성해야 함 |
| R27 | 3.5-5 | Stage 3.5 inventory workflow orchestration | Stage 3 records, resolved object inventory, optional prior/manual inventory artifacts | workflow state JSON, next required step, generated attribute/action/canonical/export artifacts | custom workflow runner over existing inventory scripts | custom fixed rule | offline execution orchestration | 직접 count 없음. clear된 Stage 3.5 step 뒤의 다음 offline step을 자동 실행하고 blocker에서 중단 | extraction/canonical/count semantics를 새로 만들지 않음. legacy artifact는 명시 path로 전달해야 함 |
| R27.1 | 3.5-6 | Inventory bundle manifest gate | completed Stage 3.5 workflow state or inventory bundle manifest | object/attribute/action inventory paths and Stage 5 lexicon bundle path used together | custom manifest reader/validator | custom fixed rule | formal execution/input selection gate | 직접 count 없음. 다음 inventory build와 formal run이 같은 resolved inventory set을 보게 함 | bundle manifest가 없으면 legacy per-path arguments는 여전히 가능하지만 mismatch를 자동으로 막지는 못함 |
| R27.2 | 3.5-6 | Current inventory publish from complete workflow | completed Stage 3.5 workflow bundle and explicit publish request | `resources/gpic_inventory/current/inventory_bundle.json` plus copied current TSV/lexicon files | custom workflow runner + publish helper | custom fixed rule | formal input promotion gate | 직접 count 없음. complete된 snapshot을 active current inventory로 승격해 다음 run이 같은 통합 inventory를 보게 함 | guard/probe run이 current를 덮어쓰지 않도록 publish는 명시 요청이 있을 때만 수행함 |
| R28 | 1-6/remote | Official execution incident gate | official pipeline, inventory publish, long-running wrapper, or remote wrapper invocation | `.pipeline_state/running.json`, open incident block, and resolved incident history | `scripts/incident_gate.py` plus guarded entrypoints | custom execution guard | operational safety gate | 직접 count 없음. 실패 후 원인·guard·검증 없이 공식 실행을 반복하는 것을 차단 | wrapper를 우회한 내부 함수 직접 호출과 validator에 정의되지 않은 의미 오류는 자동 감지할 수 없음 |

## 3. Stage별 상세 기준

### Stage 1. Caption shape 판단

하는 일:

- raw caption을 `sentence` path 또는 `tag_list` path로 보낸다.

하지 않는 일:

- tokenization
- POS tagging
- object extraction
- tag-list 내부 의미 추론

v1 기준:

- caption identifier는 Nano-era raw row의 `key`, Lite/Full immutable shard의
  `id`, downstream pipeline row의 `caption_id`에서 공통 resolver로 읽는다.
  둘 이상이 함께 있으면 값이 모두 같아야 하며, 다르면 error로 처리한다.
- 확인된 GPIC row field인 `caption_type`을 사용한다.
- `caption_type == "tag"`이면 내부 shape를 `tag_list`로 둔다.
- `caption_type in {"short", "medium", "long"}`이면 내부 shape를 `sentence`로 둔다.
- 그 외 `caption_type` 값은 추측하지 않고 error로 처리한다.
- comma 개수, 문장부호, 문장 길이로 tag-list를 추정하지 않는다.
- `tag_list`로 판정되면 sentence path로 보내지 않고 tag-list row로 따로 남긴다.
- tag-list row는 comma segment별 annotation path로 보낸다.
- tag-list를 문장 하나처럼 dependency parsing해서 sentence extraction을 실행하지 않는다.
- tag-list 전용 action/relation 추론은 v1에서 하지 않는다.

### Stage 2. spaCy preprocessing

순서:

1. `en_core_web_trf` tokenizer-only `nlp.make_doc()`
2. quote span merge
3. hyphen word merge

object MWE 기준:

- Stage 2에서는 object MWE를 merge하지 않는다.
- object MWE 또는 multi-word object 여부는 Stage 4에서 noun chunk 내부 selected span으로 판단한다.

Stage 2 산출물:

- 최종 concept이 아니다.
- token list와 protected span metadata를 inspection용 JSONL로 저장한다.

중요한 제외:

- relation MWE retokenization 없음. preposition MWE는 Stage 4에서 span metadata로만 처리
- phrasal action merge 없음
- quote placeholder 치환 없음
- `spacy.blank("en")` tokenizer 사용 없음

### Stage 3. spaCy linguistic annotation

포함:

- 기본 spaCy model은 `en_core_web_trf`
- tagger
- parser
- attribute_ruler
- lemmatizer
- noun_chunks
- tag-list caption은 comma로 segment split한 뒤 각 segment를 별도 Doc으로 annotate한다.
- tag-list Stage 3 record는 segment별 token/noun_chunk evidence와 원 caption char offset을 보존한다.

하지 않는 일:

- object 생성
- attribute 생성
- action 생성
- relation 생성
- coreference 처리

### Stage 3.5. GPIC observed object inventory

입력:

- Stage 3 records
- GPIC sentence captions에서 관측된 noun chunk와 token evidence
- optional prior resolved GPIC observed object inventory TSV

하는 일:

- noun chunk 내부에서 root를 오른쪽 끝으로 갖는 left-expanding span을 만든다.
- left-expanding span이 DET/ADP/PRON 같은 function-word token으로 시작하면 multiword object 후보로 보지 않는다. 예: `A man`은 `aman` lookup으로 보내지 않고 `man`을 본다.
- 각 span을 GPIC observed surface로 보고 OEWN noun lookup을 수행한다.
- prior resolved GPIC observed object inventory에 같은 `span_key` row가 있으면 OEWN lookup/manual status를 다시 만들지 않고 prior row의 selected synset, canonical surface, parent evidence를 재사용한다. 이때 `count`, `caption_count`, example evidence는 현재 run 기준으로 갱신한다.
- 사용자가 manual TSV 또는 exact row decision으로 `chosen`/`excluded`를 확정한 row는 이후 pipeline에서 authoritative decision으로 취급한다. semantic audit에서 더 나은 synset 후보가 보여도 그것은 advisory finding일 뿐이며, 사용자가 그 row를 다시 열라고 명시하지 않는 한 자동으로 `needs_manual`로 되돌리거나 다른 synset으로 덮어쓰지 않는다.
- 단, prior row가 observed `span_key`와 다른 `selected_query`를 가진 자동 surface-changing row이고 explicit manual decision evidence가 없으면 exact prior reuse 대상으로 보지 않는다. 이 row는 현재 rule로 다시 lookup해야 한다.
- object inventory에서는 prior `selected_query` 기준 재사용을 하지 않는다. `book -> books` 같은 활용형 전파보다 `glasses -> glass`, `arms -> arm`, `works -> work` 같은 lexicalized plural 충돌 위험이 더 크기 때문이다.
- `excluded` row는 exact `span_key`에서만 재사용하고, `selected_query` 기준으로 넓게 전파하지 않는다.
- observed exact surface lookup이 OEWN noun synset을 찾으면 그 exact surface 결과를 우선한다.
- 단, prior/manual inventory row가 없는 새 runtime lookup에서 observed exact surface와 lemma/Morphy/base-form query가 서로 다른 selected synset을 찾으면 자동 선택하지 않고 `needs_manual`, `decision_reason=manual_surface_query_conflict_required`로 둔다.
- plural common noun head span은 observed exact surface가 OEWN noun synset을 찾더라도 lemma/Morphy/base-form query를 conflict check 용도로 함께 조회한다. observed exact query와 base-form query가 둘 다 OEWN noun 후보를 찾고, base-form 쪽이 exact와 같은 selected synset으로 단일 확정되지 않으면 `needs_manual`, `decision_reason=manual_surface_query_conflict_required`로 둔다.
- observed exact surface가 실패한 경우에는 lemma/Morphy/normalization으로 surface가 바뀐 lookup query를 선택 후보로 사용한다.
- `joined_variant`처럼 space, hyphen, underscore를 제거해서 붙인 query로만 잡힌 span은 자동 `chosen`으로 올리지 않고 `needs_manual`로 둔다. 예: `A man -> aman`, `black shirt -> blackshirt`, `black top -> blacktop`.
- OEWN noun synset이 있는 가장 긴 span을 inventory row로 남긴다.
- selected synset이 있는 row는 selected synset의 immediate hypernym 전체를 parent evidence로 남긴다.
- parent evidence의 식별 기준은 parent synset ID다.
- 사람이 보는 parent label은 parent lemma display를 먼저 쓰고, parent synset ID는 별도 evidence로 함께 보존한다.
- `needs_manual` row가 하나라도 남아 있으면 canonical enrichment로 넘어가지 않는다. synset 선택 또는 objectness 판단이 끝난 뒤에만 canonical surface를 선정한다.
- manual resolution이 끝난 selected synset row는 offline canonical rule로 canonical surface를 선정한다.
- canonical surface 선정은 selected synset lemma set, observed caption surface variants, WN3 lemma count, observed exact surface, 저장된 Google Ngram evidence 순서로 진행한다.
- canonical 후보가 Google Ngram 단계까지 갔는데 저장된 evidence row가 없으면 manual guess로 확정하지 않는다. 동일한 Google Ngram 설정으로 evidence를 조회해서 `google_ngram_canonical_frequency_evidence.tsv`에 기록한 뒤 canonical enrichment를 다시 실행한다.
- Stage 3.5 workflow runner는 `canonical_selection_tag`에 `google_ngram_evidence_missing`이 있고 해당 `(selected_oewn_synset, candidate surface_key)` evidence row가 없으면 `resolve_*_canonical`로 넘기지 않고 Google Ngram evidence refresh를 먼저 실행해야 한다.
- Google Ngram evidence refresh 뒤 canonical enrichment를 다시 실행한다. 이미 evidence row가 있는데도 positive evidence가 없거나 tie가 남은 경우에만 canonical blocker/manual review로 둔다.
- canonical row의 `canonical_selection_tag`가 `google_ngram_evidence_missing`이면 manual canonical resolver로 `canonical_surface`를 직접 채우는 것도 금지한다. 이 상태는 evidence 수집 미완료이지 manual decision 대상이 아니다.
- canonical matching key는 `strip + lowercase`, apostrophe/hyphen normalization, underscore-to-space, whitespace normalization, diacritic folding을 적용한다. 예: `café`는 matching key에서 `cafe`로 비교한다.
- canonical ambiguous가 남으면 `canonical_surface`를 비우고 별도 ambiguous TSV에 기록한 뒤 Stage 4로 진행하지 않는다.
- `decision_status`는 사람이 보는 최종 queue 상태만 기록한다.
  - `chosen`: 이미 고른 것. Stage 4에서 object mention으로 세도 된다.
  - `needs_manual`: 골라야 할 것. synset 선택 또는 objectness 판단을 offline/manual로 끝내야 한다.
  - `excluded`: count에는 넣지만, downstream에서 필터링할 수 있도록 quality/status tag로 남긴다.
- `decision_reason`은 왜 그 queue로 갔는지만 기록한다.
  - `selected_object_compatible`: synset이 선택됐고 object-compatible lexfile이다.
  - `manual_joined_variant_required`: separator 제거로 붙인 query가 synset을 찾았으나 false positive 위험이 있어 manual 확인이 필요하다.
  - `manual_surface_query_conflict_required`: prior/manual inventory row 없이 observed exact surface와 lemma/Morphy/base-form query가 서로 다른 selected synset을 찾아 manual 확인이 필요하다.
    - plural common noun head span에서는 base-form query가 selected synset까지 단일 확정되지 않았더라도 OEWN noun 후보를 찾으면 surface/base 의미 충돌 가능성으로 같은 reason을 사용한다.
  - `manual_objectness_required`: synset은 선택됐지만 conditional/hard-conflict lexfile이라 objectness 판단이 필요하다.
  - `manual_synset_required`: OEWN noun 후보가 있지만 selected synset이 없다.
  - `no_oewn_noun_synset`: OEWN noun 후보가 없다.
- `objectness_gate`는 `decision_reason`을 설명하는 evidence이며 main status로 쓰지 않는다.

하지 않는 일:

- COCO/LVIS/Objects365/OpenImages/Visual Genome source-label inventory를 읽지 않는다.
- prior 재사용은 GPIC observed object inventory끼리의 exact `span_key` reuse에만 적용한다. 외부 source-label inventory, semantic alias, selected-query reuse는 prior로 쓰지 않는다.
- 외부 object dataset label을 GPIC caption span의 synonym 또는 canonical으로 쓰지 않는다.
- `needs_manual` row와 selected synset은 있지만 canonical surface가 비어 있는 row를 extraction 중에 자동으로 fallback하지 않는다.

산출물:

- `resources/gpic_inventory/observed_object_span_inventory.tsv`
- 이 파일은 GPIC caption에서 관측된 span만 담는다.

#### Stage 3.5. GPIC observed attribute inventory

입력:

- Stage 3 records
- Stage 3.5 object inventory에서 결정된 noun chunk selected object span

하는 일:

- noun chunk 내부 selected object lookup span 전체를 무조건 consumed로 보지 않는다.
- selected object의 canonical/core surface가 lookup span의 suffix token span과 매칭되면 그 core suffix token만 consumed로 본다.
- true MWE처럼 object core가 full phrase와 매칭되면 full phrase token이 consumed된다.
- consumed core token을 제외한 noun chunk 내부 token 중 `dep in {"amod", "compound", "nmod"}`이면 attribute 후보로 만든다.
- 위 attribute 후보 token에서 `conj` chain으로 이어지는 token도 같은 noun chunk 안에 있고 consumed core token이 아니면 attribute 후보로 만든다.
- `conj` token은 독립적으로 attribute 후보가 되지 않고, 이미 accepted attribute modifier가 head일 때만 확장된다.
- Stage 3.5 attribute inventory builder는 Stage 4 R13과 동일한 same-chunk
  `conj` traversal을 적용해야 한다. Base dependency token만 inventory에
  넣고 R13에서만 conjunct를 확장하는 구현은 허용하지 않는다.
- attribute inventory의 내부 식별키는
  `(attribute_unit_type, span_key)`이다. 기존 row는
  `attribute_unit_type=single_token`으로 취급하고, 하나의 Stage 3 token
  text 안에 공백이 있어도 MWE로 재분류하지 않는다.
- object core 밖 base/conj attribute anchor마다 anchor를 오른쪽 끝으로 갖는
  contiguous left-expanding span을 생성한다. 실제 Stage 3 token이 2개
  이상인 span만 `attribute_unit_type=mwe` 후보가 된다.
- attribute MWE span은 selected object core, relation-MWE consumed token,
  quantity, `cc`, punctuation, noun-chunk 경계와 tag-list segment 경계를
  넘지 않는다.
- attribute MWE OEWN lookup은 raw `lowercase + strip`, space/hyphen/underscore
  separator variant, anchor token만 Morphy한 재조립 phrase, 그 phrase의
  separator variant 순서로 수행한다. Phrase 전체 Morphy와 separator 완전
  제거형은 사용하지 않는다.
- 반환 synset lemma가 lookup query와 separator-equivalent하게 맞는 경우만
  attribute MWE OEWN hit로 인정한다.
- OEWN hit가 하나 이상인 span 중 token 수가 가장 긴 span을 선택한다.
  겹침 tie는 오른쪽 anchor, 시작 위치 순으로 결정하고 선택된 span끼리는
  겹치지 않는다. Longest span이 `needs_manual`이어도 짧은 span으로
  후퇴하지 않는다.
- object가 없는 tag-list segment 전체가 known OEWN-valid attribute MWE이면
  그 segment 전체를 MWE inventory 관측으로 남긴다.
- 모든 attribute inventory row는 `attribute_unit_type`,
  `span_token_count`, `anchor_token_offset`, `lookup_forms`,
  `attribute_mwe_rule_version`을 기록한다.
- quantity 판정은 Stage 3.5 inventory에서도 R13/R14 runtime과 같은 우선순위를
  사용한다. `NUM` 또는 quantity modifier로 판정된 token은 attribute
  inventory의 base/conj 후보로 중복 추가하지 않는다.
- raw surface는 원문 그대로 보존한다.
- lookup query는 raw surface를 `lowercase + strip`한 값으로 만든다.
- OEWN 2025+에서 lookup한다.
- 없으면 Morphy 후 다시 검색한다.
- 그래도 없으면 selected synset은 비워 두되 `decision_status=chosen`, `decision_reason=no_oewn_attribute_synset`으로 남긴다. synset search에서는 제외하지만 count 후보에는 남긴다.
- prior resolved GPIC observed attribute inventory에 같은 `span_key` row가 있으면 selected synset/canonical evidence를 재사용한다.
- 단, prior row가 observed `span_key`와 다른 `selected_query`를 가진 자동 surface-changing row이고 explicit manual decision evidence가 없으면 exact prior reuse 대상으로 보지 않는다. 이 row는 현재 rule로 다시 lookup해야 한다.
- attribute inventory에서는 prior `selected_query` 기준 재사용을 하지 않는다.
- `excluded` row와 no-synset row는 exact `span_key`에서만 재사용하고, `selected_query` 기준으로 넓게 전파하지 않는다.
- observed exact surface lookup이 OEWN attribute synset을 찾으면 그 exact surface 결과를 우선한다.
- 단, attribute 후보 token이 plural common noun이면 exact hit가 있어도 Morphy/base-form query를 conflict check 용도로 함께 조회한다.
- plural common noun attribute 후보에서 prior/manual inventory row가 없는 새 runtime lookup의 observed exact surface와 Morphy/base-form query가 서로 다른 selected synset을 찾으면 자동 선택하지 않고 `needs_manual`, `decision_reason=manual_surface_query_conflict_required`로 둔다.
- plural common noun이 아닌 attribute 후보는 observed exact surface가 실패한 경우에만 Morphy로 surface가 바뀐 lookup query를 사용한다.
- OEWN 2025+ 검색 결과 synset이 하나면 그 synset을 선택한다.
- OEWN 2025+ 검색 결과 synset이 여러 개면 sense key 기준 WordNet 3.0 lemma count를 사용한다.
  - lemma count > 0인 attribute-compatible 항목 중 최대값이 유일하면 선택한다.
  - 최대값이 동률이면 ambiguous로 둔다.
  - lemma count > 0인 conditional 항목 중 최대값이 유일하면 선택한다.
  - 최대값이 동률이면 ambiguous로 둔다.
  - attribute-compatible, conditional 후보가 없으면 그 외 항목 중 최대값이 유일할 때 선택한다.
  - 다시 최대값이 동률이면 ambiguous로 둔다.
  - lemma count가 모두 0이면 ambiguous로 둔다.
  - sense key 기준 WordNet 3.0 synset이 하나도 검색되지 않으면 ambiguous로 둔다.
- lookup query는 생성됐는데 OEWN API 결과가 비정상적이면 report한다.
- OEWN lexfile gate:
  - attribute-compatible은 auto selected 가능하다.
  - conditional과 hard_conflict는 `needs_manual`로 보내 manual 확인 대상으로 둔다.

attribute-compatible lexfiles:

- `adj.all`
- `adj.pert`
- `adj.ppl`
- `noun.attribute`
- `noun.shape`
- `noun.state`
- `noun.substance`

conditional lexfiles:

- `noun.Tops`
- `noun.act`
- `noun.animal`
- `noun.artifact`
- `noun.body`
- `noun.cognition`
- `noun.communication`
- `noun.event`
- `noun.food`
- `noun.group`
- `noun.location`
- `noun.object`
- `noun.person`
- `noun.phenomenon`
- `noun.plant`
- `noun.possession`
- `noun.process`
- `noun.quantity`
- `noun.relation`
- `noun.time`
- `verb.body`
- `verb.change`
- `verb.cognition`
- `verb.communication`
- `verb.competition`
- `verb.consumption`
- `verb.contact`
- `verb.creation`
- `verb.emotion`
- `verb.motion`
- `verb.perception`
- `verb.possession`
- `verb.social`
- `verb.stative`
- `verb.weather`

hard_conflict lexfiles:

- `adv.all`
- `noun.feeling`
- `noun.motive`

attribute status:

- `chosen`: selected synset이 있고 attribute-compatible gate를 통과함
- `needs_manual`: selected synset은 있으나 conditional/hard_conflict gate 때문에 사람이 봐야 하거나, OEWN 후보가 여러 개이고 rule로 selected synset을 못 고름. 구체적 원인은 `decision_reason`과 `synset_selection_tag`에 남김
- `excluded`: manual decision으로 downstream에서 구분해야 하는 attribute-like row. count에는 남기되 status로 보존함
- OEWN 후보가 없는 row도 `chosen`으로 남기되 `decision_reason=no_oewn_attribute_synset`과 빈 selected synset으로 구분한다.

offline attribute canonical inventory build:

- canonical enrichment로 넘어갈 수 있는 status는 `chosen`, `excluded`이다.
- `needs_manual` row가 남아 있으면 canonical enrichment를 중단한다.
- attribute synset/manual gate에서는 canonical surface 누락을 blocker로
  세지 않는다. canonical blocker는 `needs_manual` row가 모두 해결된 뒤
  offline canonical inventory build 단계에서만 따진다.
- manual feedback에서 `decision_status=chosen`이어도 `selected_oewn_synset`이 비어 있으면 canonical surface를 정하지 않는다. count에는 raw surface로 남길 수 있도록 빈 selected synset evidence를 보존한다.
- `excluded` row는 selected synset 유무와 무관하게 canonical 대상이 아니다. `canonical_surface`와 `canonical_label_key`는 비우고 `canonical_selection_tag=not_applicable_excluded`로 표시한다.
- selected synset이 없으면 canonical surface를 정하지 않는다. `canonical_surface`와 `canonical_label_key`는 비우고 `canonical_selection_tag=not_applicable_no_selected_synset`으로 표시한다.
- input manual feedback TSV에 `canonical_surface` 또는 `manual_*` canonical tag가 있어도 canonical decision으로 쓰지 않는다.
- canonical surface는 이 단계가 selected synset evidence를 기준으로 처음부터 다시 계산한다.
- selected synset이 있으면 selected synset 안 WordNet/OEWN lemma를 가져온다.
- observed caption에서 생성한 surface variants와 형태 매칭되는 lemma만 남긴다.
  - lowercase + strip
  - canonical matching key에서 diacritic folding
  - morphy
  - space/underscore variants
  - hyphen space/underscore variants
  - 필요하면 separator 제거 variant
- 남은 OEWN lemma 후보가 하나면 canonical로 선택한다.
- 남은 OEWN lemma 후보가 여러 개라면 lemma.count()를 비교하여 단독 최대를 canonical로 선택한다.
- count가 전부 0이거나 동률이면 observed caption span surface와 동일한 lemma를 canonical로 선정한다.
  - 이 exact observed surface 비교에는 lookup용 `selected_query`를 넣지 않는다.
  - display surface exact match가 하나로 정해지지 않으면, diacritic-folded canonical matching key 기준 raw observed surface와 유일하게 맞는 lemma를 선택한다.
  - 동일한 lemma가 없으면 selected synset의 전체 lemma set으로 되돌린 뒤 Google Ngram 기준 frequency를 비교한다.
  - 2개 이상이 남으면 남은 항목 기준 Google Ngram(2000-2019) frequency를 비교한다.
- 그래도 동률이면 ambiguous로 두고 manual로 결정한다.

offline attribute parent inventory build:

- attribute parent는 OEWN hypernym이 아니라 taxonomy 방식으로 manual 결정한다.
- attribute type도 canonical script가 정하지 않고 offline/manual taxonomy decision으로 채울 수 있다.
- type decision은 offline artifact의 `attribute_type`, `attribute_type_selection_tag`, `attribute_type_reason`에 기록할 수 있다.
- active Stage 5/6/report는 아직 attribute type을 사용하지 않는다.
- Stage 5는 typed inventory TSV를 직접 읽지 않는다. Stage 5용 lexicon bundle export는 attribute synonym만 활성화한다.
- active lexicon export 기준:
  - `decision_status=chosen`이고 `canonical_surface`가 있으면 `attribute_synonyms.tsv`에 `raw -> canonical`을 쓴다.
  - synonym raw key는 Morphy 후 lookup query가 아니라 caption에서 관측된 원본 surface를 기준으로 한다. `span_key`, `observed_surface`, `example_surfaces`에 있는 원본 surface variants를 모두 `raw -> canonical` row로 export한다.
  - `decision_status=excluded` row는 `attribute_synonyms.tsv`에 쓰지 않는다.
  - selected synset이 없어 canonical surface가 비어 있는 row는 `attribute_synonyms.tsv`에 쓰지 않는다.
  - `attribute_types.tsv`는 active Stage 5용으로 export하지 않는다.
  - manual feedback에 canonical 값이 있더라도 `excluded` row의 canonical synonym으로 쓰지 않는다.

산출물:

- `resources/gpic_inventory/observed_attribute_inventory.tsv`
- 이 파일은 GPIC caption에서 관측된 attribute modifier만 담는다.

#### Stage 3.5. Preposition MWE lexicon bundle

입력:

- 외부 preposition MWE 후보 inventory
- manual keep/drop review가 끝난 preposition MWE row
- Google Ngram에서 관측된 user-approved generated spatial relation pattern row

하는 일:

- active runtime lexicon은 `resources/lexicons/preposition_mwes.tsv`에 둔다.
- lexicon row는 caption에서 실제 matching할 token sequence와 canonical relation label을 분리해서 보존한다.
- canonical relation label은 preposition MWE entry를 `lowercase + strip`한 값이다.
- matching token sequence는 실제 caption surface와 맞아야 하므로 source lemma만 있고 관측 surface가 다른 row는 관측 surface variant를 matcher에 넣는다.
- user-approved Google Ngram relation pattern row는 `term`을 matching token sequence와 canonical relation label로 같이 사용한다.
- Google Ngram relation pattern row는 `ngram_found == yes`이고 `ngram_status == ok`인 row만 active lexicon에 넣는다.
- relation component는 canonical relation label을 token 단위로 나눈 metadata다.

하지 않는 일:

- Stage 2 retokenization을 하지 않는다.
- preposition MWE lexicon으로 object/action/relation을 새로 의미 추론하지 않는다.
- target object가 dependency/object mapping에서 확인되지 않으면 relation edge를 만들지 않는다.
- source object가 직접 확인되지 않더라도 R18.1의 action-attached direct object-mapped child 후보 규칙 밖에서는 source를 복구하지 않는다.

#### Stage 3.5. GPIC observed action inventory lookup

Input:

- Stage 3 records
- active preposition MWE lexicon
- optional prior resolved GPIC observed action inventory TSV

Rules:

- Before generating action candidates for a Stage 3 record, detect contiguous
  preposition MWE spans with the same matcher used by Stage 4 R18.1.
- Tokens inside selected preposition MWE spans are marked as consumed for action
  candidate generation.
- Action inventory candidates use VERB, VERB+particle, VERB+preposition, and
  VERB+particle+preposition spans only after consumed preposition MWE tokens are
  excluded.
- If a prior resolved action inventory has the same exact `span_key` with a
  final `chosen` or `raw_fallback` row, reuse that decision.
- If exact `span_key` reuse does not apply, run the normal OEWN/Morphy action
  lookup. When that lookup produces one or more normalized query candidates,
  reuse a prior resolved action decision by `selected_query` only if the prior
  inventory has a unique final `chosen` synset for that query. If multiple
  prior `selected_query` matches disagree, keep the row `needs_manual`.
- Any remaining action row with ambiguous synset or ambiguous Morphy evidence is
  written as `needs_manual`.
- If any `needs_manual` row remains, action canonical enrichment and formal
  Stage 4 extraction must not proceed.

#### Stage 3.5. GPIC observed action canonical inventory

Input:

- resolved GPIC observed action inventory

Rules:

- If any action inventory row still has `decision_status=needs_manual`, action
  canonical enrichment stops.
- `raw_fallback` rows have no selected synset, so canonical inventory is marked
  not applicable for those rows.
- `chosen` rows must have `selected_oewn_synset`.
- For selected synset rows, collect OEWN verb lemmas from the selected synset.
- Keep lemmas that match observed action surface variants or `selected_query`.
  - `lowercase + strip`
  - verb-head Morphy
- If one OEWN lemma candidate remains, use it as canonical.
- If multiple candidates remain, use the unique maximum WN3 lemma count.
- If counts are all zero or tied, prefer a lemma matching observed action
  surface or `selected_query`.
- If still unresolved, optional Google Ngram evidence may choose a unique max.
- If still unresolved, leave `canonical_surface` empty and write the row to the
  canonical ambiguous TSV.
- If any canonical ambiguous row remains, do not export the row as an R22 active
  action synonym.
- When no canonical ambiguous row remains, selected `chosen` rows may be
  exported to Stage 5 `action_synonyms.tsv`.
- Action synonym raw keys are observed pre-Morphy caption surfaces, not only
  lookup queries. Export includes `span_key`, `observed_surface`, and
  `example_surfaces` variants when they map to the same resolved
  `canonical_surface`.
- `raw_fallback` rows are not exported because they have no selected synset and
  no canonical action surface.

### Stage 4. raw concept extraction

입력:

- `stage3_records.jsonl`에 저장된 token table, dependency table, noun chunk table
- `resources/gpic_inventory/observed_object_span_inventory.tsv`
- Stage 4는 spaCy model을 다시 실행하지 않는다.

정식 실행 gate:

- Stage 4 runner는 object inventory 전체를 먼저 검사한다.
- object inventory에 pending `decision_status` row가 하나라도 있으면 Stage 4를 실행하지 않는다.
- `chosen` row가 selected synset을 가졌지만 `canonical_surface`가 비어 있으면 canonical ambiguous 또는 canonical 미완료 상태로 보고 Stage 4를 실행하지 않는다.
- `chosen` row가 surface/head correction을 했지만 selected synset이 비어 있으면 Stage 4를 실행하지 않는다.
- 이 gate는 matched span에만 적용하지 않고 inventory 전체에 적용한다.

허용 추출:

- noun chunk 내부 selected span -> object
- noun chunk modifier -> attribute 또는 quantity
- VERB -> action
- `nsubj` -> agent
- `obj`, `dobj` -> patient
- preposition MWE + final direct `pobj` -> relation
- remaining single ADP/preposition + direct `pobj` -> relation

세부 기준:

- R12 object는 noun chunk 내부에서 root를 오른쪽 끝으로 갖는 left-expanding span을 만들고, GPIC observed object inventory에 row가 있는 가장 긴 span으로 만든다.
- tag-list path의 R12 object도 segment 내부 noun chunk에서 같은 selected span rule을 사용한다.
- lookup 후보와 surface-changing conflict gate는 Stage 3.5와 동일하다. raw object mention text는 observed surface를 유지한다.
- inventory에 없는 span은 object count에서 제외한다.
- inventory row가 `decision_status=excluded`이면 object mention은 만들되 source_detail에 status/reason을 보존한다.
- inventory row가 `decision_status=needs_manual`이면 raw fallback으로 넘기지 않고 Stage 4를 중단한다. 이 항목은 offline resolution에서 먼저 해결해야 한다.
- inventory row에 selected synset이 있는데 `canonical_surface`가 비어 있으면 raw fallback으로 넘기지 않고 Stage 4를 중단한다. canonical ambiguous는 offline canonical inventory build에서 먼저 해결해야 한다.
- 선택된 lookup span 전체가 아니라 selected object core span token만 consumed 처리되고, action/relation edge 연결을 위해 같은 object mention으로 매핑한다.
- selected object core span은 inventory canonical/core surface가 lookup span의 suffix와 매칭되면 그 suffix token span이다.
- canonical/core suffix가 lookup span 안에서 매칭되지 않으면 fallback으로 lookup span 전체를 core로 본다.
- core span 밖 modifier token은 attribute/quantity 후보로 남긴다.
- R13 attribute modifier는 같은 noun chunk 안에서 `dep in {"amod", "compound", "nmod"}`인 token을 기본 후보로 사용한다.
- R13은 기본 후보에서 `conj` chain으로 이어지는 token이 같은 noun chunk 안에 있고 consumed core token이 아니면 attribute로 확장한다.
- R13은 `conj` token을 독립 attribute 후보로 보지 않는다.
- R13은 resolved attribute inventory의 MWE matcher와 Stage 3.5가 공유하는
  span selector를 사용한다. 선택된 MWE는 attribute mention 하나와
  `has_attribute` edge 하나를 만들고, 내부 token의 single-token attribute
  mention은 해당 noun chunk에서만 억제한다.
- attribute MWE의 local consumption은 action extraction으로 전달하지 않는다.
  따라서 `painted face`처럼 attribute/action 중복이 허용된 기존 정책은
  유지한다.
- `dark brown and bright blue jersey`처럼 conjunction branch가 나뉘면
  `dark brown`, `bright blue`를 각각 선택하고 branch 사이를 가로지르는
  MWE는 만들지 않는다.
- object 없는 tag-list segment 전체가 resolved attribute MWE이면 unattached
  attribute mention 하나를 보존하고 `has_attribute` edge는 만들지 않는다.
- R14 quantity modifier는 같은 noun chunk 안에서 `dep == "nummod"` 또는 `pos == "NUM"`인 token만 사용한다.
- tag-list path에서 segment 내부 object가 없고 segment가 단일 attribute-like token으로만 구성되면 unattached attribute mention으로 남긴다.
- tag-list path에서 unattached attribute mention은 `has_attribute` edge를 만들지 않는다.
- R15 action은 `pos == "VERB"`인 token을 head로 보고, 가능한 경우 OEWN verb lookup으로 selected action span을 만든다.
- Stage 4는 action 후보를 만들기 전에 preposition MWE lexicon span을 먼저 감지하고, 선택된 span 내부 token을 `relation_mwe_consumed`로 둔다.
- preposition MWE span이 겹치면 longest span을 선택하고, 길이가 같으면 더 앞선 span을 선택한다.
- preposition MWE span은 retokenize하지 않고 metadata로만 남긴다.
- R15 selected action span 후보는 single VERB, VERB+particle, VERB+preposition, VERB+particle+preposition이다.
- particle 후보는 VERB의 child 중 `dep == "prt"` 또는 `tag == "RP"`인 token이다.
- preposition 후보는 VERB의 child 또는 particle의 child 중 `dep == "prep"` 또는 `pos == "ADP"`인 token이다.
- R15 preposition 후보는 token index가 VERB head보다 뒤에 있어야 한다. 즉 `prep.i <= verb.i`이면 VERB+preposition 또는 VERB+particle+preposition action 후보로 쓰지 않는다.
- R15 particle/preposition 후보 중 `relation_mwe_consumed` token은 phrasal action 후보에서 제외한다.
- R15 action lookup은 raw phrase를 `lowercase + strip`한 surface와, verb head만 Morphy한 surface만 사용한다. action에서는 separator 제거/underscore rescue를 하지 않는다.
- R15 raw phrase OEWN verb lookup은 반환 synset lemma 중 raw phrase surface와 형태가 정확히 맞는 lemma가 있는 synset만 exact hit로 인정한다. OEWN 내부 morphology로 반환된 base lemma synset은 exact hit로 보지 않는다.
- R15 exact hit가 없으면 verb head token만 Morphy하고 나머지 particle/preposition token과 다시 합친 query로 OEWN verb lookup을 한다. 이때도 반환 synset lemma 중 Morphy query와 형태가 맞는 synset만 hit로 인정한다.
- R15 Morphy query 중 OEWN verb hit query가 2개 이상이면 자동 선택하지 않고 `needs_manual`로 보낸다.
- R15 valid OEWN verb 후보가 있으면 longest span을 선택한다. 길이가 같고 VERB+particle과 VERB+preposition이 모두 가능하면 VERB+particle을 선택한다.
- R15 valid OEWN verb 후보의 synset 결정이 `needs_manual`이면 raw fallback으로 넘기지 않고 Stage 4를 중단한다. 이 항목은 offline action inventory/manual resolution에서 먼저 해결해야 한다.
- R15 valid OEWN verb 후보가 없으면 single VERB action mention을 raw fallback으로 만든다. 이 경우 selected synset이 없으므로 action inventory status는 `chosen`이 아니라 `raw_fallback`이다.
- R15 selected action span 내부 token은 action mention으로 매핑한다.
- R16 agent edge는 action head token의 direct child 중 `dep == "nsubj"`이고 그 child token이 selected object mapping에 있을 때만 만든다.
- R16.1 action conjunct agent inheritance는 R16/R17 direct event role 생성 후, R18/R18.1 relation 생성 전에 수행한다.
- R16.1 target action은 action head token의 `dep == "conj"`이고 그 token이 action head mapping에 있어야 한다.
- R16.1 source action은 target action의 dependency head를 따라 찾은 action head이다. source action이 먼저 R16.1로 agent를 상속받은 경우도 fixed-point 반복으로 다음 conjunct action에 전달할 수 있다.
- R16.1 target action에 이미 agent edge가 있으면 상속하지 않는다.
- R16.1 target action에 direct child dep 중 `nsubjpass`, `auxpass`, `agent`가 있으면 passive-like target으로 보고 agent를 상속하지 않는다.
- R16.1 source action의 agent target이 정확히 1개일 때만 target action으로 agent edge를 복사한다. source agent가 0개 또는 2개 이상이면 상속하지 않는다.
- R16.1 inherited agent edge는 `rule_id == "R16.1"`을 쓰고 `role_source`, `source_action_i`, `target_action_i`, `conj_head_i` metadata를 남긴다.
- R16.1은 patient를 상속하지 않는다.
- R16.3 acl action head-object agent inheritance는 R16/R17/R17.1/R16.2 direct event role 생성 후, R16.1 action conjunct agent inheritance 전에 수행한다.
- R16.3 target action은 action head token의 `dep == "acl"`이고 `tag == "VBG"`이며 그 token이 action head mapping에 있어야 한다.
- R16.3 source object는 acl action의 dependency head token이다. 이 head token이 selected object mapping에 있을 때만 agent edge를 만든다.
- R16.3 target action에 이미 agent edge가 있으면 새 agent edge를 만들지 않는다.
- R16.3은 `tag == "VBN"`인 acl action에는 적용하지 않는다. VBN reduced relative/participial modifiers는 passive/adjectival reading이 흔하므로 agent로 복구하지 않는다.
- R16.3 target action에 direct child dep 중 `nsubjpass`, `auxpass`, `agent`가 있으면 passive-like target으로 보고 agent를 상속하지 않는다.
- R16.3 inherited agent edge는 `rule_id == "R16.3"`을 쓰고 `role_source == "acl_head_object_agent"`, `acl_head_i`, `action_i`, `target_i` metadata를 남긴다.
- R16.3은 patient를 상속하지 않는다.
- R16.3은 `relcl`, `advcl`, `xcomp`, `ccomp`, `acomp`, VBG/VBN fallback을 처리하지 않는다. relative pronoun subject resolution은 별도 rule로 남긴다.
- R16.2 passive by-agent edge는 같은 action에서 R17.1 passive subject patient edge가 만들어진 경우에만 시도한다.
- R16.2는 action head의 direct child 중 `lemma/text == "by"`이고 `dep in {"agent", "prep"}`인 token을 by cue로 본다.
- R16.2 by cue의 direct child 중 `dep == "pobj"`이고 selected object mapping에 있는 token을 agent target으로 만든다.
- R16.2 source detail에는 `raw_role == "by_agent_or_causer"`, `voice_normalization == "passive_to_active"`, `role_source == "passive_by_phrase"`를 남긴다.
- R17 patient edge는 action head token의 direct child 중 `dep in {"obj", "dobj"}`이고 그 child token이 selected object mapping에 있을 때 만든다.
- R17 selected phrasal action span이 ADP를 소비한 경우, 그 ADP의 direct `pobj` child가 selected object mapping에 있으면 patient edge로 만든다.
- R17.1 passive patient edge는 action head token의 direct child 중 `dep in {"nsubjpass", "csubjpass"}`이고 그 child token이 selected object mapping에 있을 때 만든다.
- R17.1 source detail에는 `raw_role == "theme"`, `voice_normalization == "passive_to_active"`, `role_source == "passive_subject"`를 남긴다.
- R18.1 preposition MWE relation edge는 matched span의 initial relation token head가 source object mapping에 있고, final ADP의 direct `pobj` child가 target object mapping에 있을 때 만든다.
- R18.1 matched span의 initial relation token head가 object가 아니고 `pos in {"VERB", "AUX"}`이면, 그 head의 direct child 중 object mapping이 있는 child를 dep label과 무관하게 relation source 후보로 본다.
- R18.1 final ADP의 direct `pobj` child가 object mapping에 있으면 base target 후보로 본다.
- R18.1 base target 후보에서 `conj` chain으로 이어지는 token이 object mapping에 있으면 같은 relation의 target으로 확장한다.
- R18.1 target conj 확장은 target 쪽에만 적용하며 source object의 conjunct sibling은 확장하지 않는다.
- R18.1 source 후보가 정확히 1개이고 target base 후보가 정확히 1개이면, base target과 그 conj-chain target 각각에 normal `relation` edge를 만든다.
- R18.1 source 후보가 0개 또는 2개 이상이거나, 독립 target base 후보가 0개 또는 2개 이상이면 source/target을 확정하지 않고 audit용 `ambiguous_relation_candidate` edge를 만든다. 이 edge는 normal relation triple count에는 넣지 않는다.
- R18.1 source 또는 target 후보가 0개인 경우에는 object mention을 새로 만들지 않고, edge endpoint에 audit-only sentinel `__missing_source__` 또는 `__missing_target__`을 둔다.
- R18.1 ambiguous relation candidate count는 후보 pair 개수가 아니라 matched MWE occurrence 단위로 센다. 같은 caption 안 같은 matched token indices와 relation label에서 나온 후보 pair 또는 missing endpoint candidate는 Stage 6에서 하나의 ambiguous relation occurrence fact로 묶는다.
- R18.1 relation label은 lexicon row의 canonical relation label을 쓴다.
- R18.1 relation edge와 ambiguous candidate edge source detail에는 raw span surface, matched token indices, relation components, initial relation token index, final ADP token index, source/target candidate metadata를 보존한다.
- R18 relation edge는 `pos == "ADP"` token의 direct child 중 `dep == "pobj"`가 target object mapping에 있고, ADP head token이 source object mapping에 있을 때 만든다.
- R18 target base `pobj`에서 `conj` chain으로 이어지는 token이 object mapping에 있으면 같은 source/relation에서 target relation edge를 추가로 만든다.
- R18 target conj 확장은 target 쪽에만 적용하며 source object의 conjunct sibling은 확장하지 않는다.
- R18 selected phrasal action span에 소비된 ADP token은 relation 후보에서 제외한다.
- R18 preposition MWE span에 소비된 ADP token은 single-ADP relation 후보에서 제외한다.
- Stage 4는 agent/patient/relation edge를 만들기 위해 새 object mention을 추가하지 않는다.

명시적 제외:

- 일반 `pobj`를 action patient로 쓰지 않는다. 단, selected phrasal action span에 소비된 ADP의 direct `pobj`는 patient 후보로 쓴다.
- action conjunct에서 patient를 상속하지 않는다.
- `relcl` relative pronoun subject를 head noun으로 치환하지 않는다. 이 문제는 R16.3 `acl` head-object agent와 별도이다.
- passive voice는 direct passive subject와 passive `by` phrase만 semantic event_role로 보존한다. 그 밖의 passive 의미 추론, non-`by` causer, coreference 기반 passive agent 복원은 하지 않는다.
- semantic relation source disambiguation을 하지 않는다. 단, R18.1 action-attached preposition MWE에서 direct object-mapped child 후보가 보이면 단일 후보는 relation으로 만들고, 다중 후보는 별도 ambiguous relation candidate로 보존한다.
- self-edge repair를 하지 않는다.
- pronoun/reference resolution을 하지 않는다.
- scene context fallback을 하지 않는다.

### Stage 5. canonicalization

정식 실행 gate:

- Formal pipeline artifacts must carry a pipeline state manifest when the
  producing runner supports it.
- Action inventory used by formal Stage 4 must have an action-inventory sidecar
  state proving it was built after active preposition MWE span detection.
- Action inventory sidecar state must include
  `action_inventory_preposition_mwe_aware == true` and
  `preposition_mwe_detection_before_action == true`.
- A legacy action inventory without this sidecar state is not a formal Stage 4
  input; regenerate it with the current action inventory builder.
- Formal Stage 4 runner는 resolved action inventory를 입력으로 받아야 한다.
- action inventory를 입력하지 않는 runtime OEWN action lookup은 probe/debug
  preview에서만 허용한다.
- action inventory에 `needs_manual` row가 하나라도 있으면 Stage 4를 실행하지 않는다.
- Stage 5 runner는 active attribute canonicalization이 필요한 run에서 attribute inventory를 입력으로 받아야 한다.
- attribute inventory에 `needs_manual` row가 하나라도 있으면 Stage 5를 실행하지 않는다.
- `chosen` row가 selected synset을 가졌지만 `canonical_surface`가 비어 있으면 canonical ambiguous 또는 canonical 미완료 상태로 보고 Stage 5를 실행하지 않는다.
- selected synset이 없는 `chosen` row는 `decision_reason=no_oewn_attribute_synset`이면 canonical 적용 불가 row로 보고 Stage 5를 막지 않는다.
- 이 gate를 통과하지 않은 Stage 5/6/Markdown output은 정식 caption-to-concept 결과로 부르지 않는다.

허용:

- object selected synset metadata preservation
- object canonical surface from GPIC observed inventory
- object raw surface fallback when canonical surface is absent
- attribute synonym lookup
- attribute type lookup은 active output에서 보류
- quantity raw-preserving
- action synonym lookup
- action parent concept lookup은 아직 비활성
- object parent concept mapping은 Stage 4 source detail의 selected OEWN immediate hypernym evidence를 사용함
- object `parent_concepts`는 parent lemma display를 먼저 쓰고, parent synset ID는 `parent_oewn_synsets` evidence로 보존함
- relation canonicalization은 Stage 4 evidence를 보존한다.
  - single ADP relation은 raw-preserving이다.
  - preposition MWE relation은 Stage 4 lexicon canonical relation label을 그대로 유지한다.

금지:

- canonicalization 단계에서 새 object를 만들지 않는다.
- canonicalization 단계에서 agent/patient를 고치지 않는다.
- canonicalization 단계에서 relation source/target을 바꾸지 않는다.

### Formal pipeline state manifest

목적:

- 단계 진행 상태를 대화 기억이나 파일명 추정에 맡기지 않는다.
- formal runner가 이전 단계 산출물이 현재 rule 순서를 통과했는지 직접 확인한다.

기준:

- artifact-specific sidecar state는 `<artifact filename>.pipeline_state.json`
  경로에 둔다.
- mixed formal run의 전체 상태는 output directory의 `pipeline_state.json`에 둔다.
- sidecar state는 최소한 아래 정보를 보존한다.
  - `schema_version`
  - `artifact_type`
  - `stage`
  - `status`
  - `preview_mode`
  - input/output path evidence
- action inventory sidecar state는 추가로 아래 정보를 보존한다.
  - `action_inventory_preposition_mwe_aware`
  - `preposition_mwe_detection_before_action`
  - `relation_mwe_match_total`
  - `relation_mwe_consumed_token_total`
  - `decision_status_counts`
  - `needs_manual_rows`
- formal Stage 4는 action inventory sidecar state가 없거나, 위 두 boolean
  flag가 true가 아니면 실행하지 않는다.
- mixed runner는 formal output directory에 `pipeline_state.json`을 쓰고,
  `preview_mode`, inventory path, Stage 1/3/4/5/6 completion state를 남긴다.
- preview/debug run은 `preview_mode == true`로 표시하고, formal pipeline
  state로 취급하지 않는다.

### Stage 3.5 inventory workflow state

목적:

- Stage 3.5 offline 준비 순서를 대화 기억이나 파일명 추정에 맡기지 않는다.
- 이전 inventory phase가 clear되면 다음 phase로 자동 진행한다.
- 다음 batch 또는 formal run에서 사용할 object/attribute/action/lexicon set을 한 manifest로 묶어 경로 복붙 실수를 줄인다.
- blocker가 있으면 다음 formal step으로 넘어가지 않고, 어떤 manual 또는
  canonical 작업이 필요한지 state file에 남긴다.

기준:

- workflow state는 output directory의 `stage35_workflow_state.json`에 둔다.
- workflow가 `status == "complete"`가 되면 같은 output directory의
  `inventory_bundle.json`에도 formal input bundle을 쓴다.
- 공식 inventory promotion run은 workflow command에 `--publish-current`를
  주고, `status == "complete"`일 때 같은 프로세스 안에서
  `resources/gpic_inventory/current/inventory_bundle.json`까지 갱신한다.
- guard/probe/simulation run은 `--publish-current`를 주지 않으면 historical
  output snapshot으로만 남고 active current inventory를 덮어쓰지 않는다.
- workflow state는 최소한 아래 정보를 보존한다.
  - `schema_version`
  - `artifact_type == "stage35_inventory_workflow"`
  - `status`
  - `next_required_step`
  - object/attribute/action/canonical/export artifact path evidence
  - blocker count와 representative examples
  - 이번 실행에서 수행한 step 목록
  - publish 요청 여부와 publish 결과
- workflow runner는 새 extraction rule을 적용하지 않는다. 아래 기존 script만
  순서대로 호출한다.
  - observed attribute inventory build
  - attribute manual overlay
  - attribute canonical enrichment
  - observed action inventory build
  - action manual overlay
  - action canonical enrichment
  - Stage 5 lexicon export
- `needs_manual` 또는 canonical ambiguous row가 하나라도 있으면 runner는
  다음 phase를 실행하지 않고 `blocked_*` status로 중단한다.
- action inventory build는 active preposition MWE lexicon을 입력으로 받아
  R15 phrasal action 후보 생성 전 R18.1 relation MWE token을 제외한 상태여야 한다.
- workflow runner가 생성한 Stage 5 lexicon bundle은 R26 state manifest gate를
  통과할 수 있도록 action canonical export를 포함해야 한다.
- inventory bundle manifest는 최소한 `artifact_type`, `status`,
  `object_inventory`, `attribute_inventory`, `action_inventory`, `lexicon_dir`
  또는 `lexicon_output_dir`를 보존한다.
- runner가 inventory bundle과 개별 inventory path를 함께 받는 경우, 같은
  artifact family의 경로가 서로 다르면 실행을 중단한다. 명시 override가
  bundle을 조용히 덮어쓰지 않는다.

### Stage 6. count export

허용 count:

- object count
- object parent count
- attribute count
- quantity count
- object-attribute pair count
- object-quantity pair count
- action count
- agent/patient pair count
- relation triple count
- relation component count
- ambiguous relation occurrence count
- object co-occurrence pair count

aggregation 기준:

- `count`는 같은 `count_key`를 가진 fact row 수
- `caption_count`는 unique caption 수
- `example_caption_ids`는 최대 5개
- rows는 `count` 내림차순, 그 다음 `count_key` 오름차순
- raw variants는 raw surface를 `strip + lower`한 값
- parent 관련 count table은 사람이 읽는 `parent_concepts`를 먼저 보여주고, 같은 parent의 synset ID는 `parent_synset_ids` 계열 evidence column에 별도로 남긴다.
- relation component count는 preposition MWE relation edge에서는 `relation_components` metadata를 사용하고, single-ADP relation edge에서는 canonical relation label 자체를 component 하나로 사용한다.
- relation component count는 `relation`, `component`, `component_index`를 집계한다.
- ambiguous relation occurrence count는 preposition MWE edge 중 `edge_type == "ambiguous_relation_candidate"`인 Stage 4 metadata에서만 만든다.
- ambiguous relation occurrence count는 후보 pair가 아니라 `caption_id + matched_token_indices + relation` 단위로 묶어 센다.
- ambiguous relation occurrence count는 `source_status`, `relation`, `target_status`를 집계하고, candidate source/target 목록은 evidence column으로 보존한다. normal relation triple count에는 포함하지 않는다.

object co-occurrence 기준:

- 같은 caption 안의 unique canonical object label set으로 만든다.
- directed pair를 만든다. 즉 `A -> B`, `B -> A`를 모두 만든다.
- `source_object == target_object`인 self pair는 만들지 않는다.
- 같은 caption 안에 같은 canonical object mention이 여러 개 있어도 같은 object끼리 pair는 만들지 않는다.

금지:

- count export에서 새 rule 적용 금지
- count export에서 누락 복구 금지
- count export에서 semantic repair 금지
- sentence와 tag-list가 같은 formal run에 포함된 경우, count table을 따로 붙이지 않고 combined canonical mentions/edges에서 한 번 재집계한다.

## 4. Explicitly Excluded From V1

아래 기능은 v1에서 구현하지 않는다.

| 제외 항목 | 이유 |
|---|---|
| pronoun resolution | antecedent scoring이 필요해 설명 가능성이 떨어짐 |
| generic anaphora resolution | `the object`, `the device` 같은 표현의 antecedent 선택이 필요함 |
| `one`, `another`, `others`, `both` splitting | subgroup과 instance modeling이 필요함 |
| broader passive voice normalization | non-`by` causer, coreference, passive action collapse 등은 raw dependency 기준을 넘어 semantic role rewrite가 필요함 |
| inherited agent repair | 후처리 patch가 되기 쉬움 |
| skipped reference role recovery | 앞선 repair 실패를 다시 고치는 구조가 됨 |
| self-edge repair | coreference/relation repair에 의존함 |
| PP source disambiguation | relation source 선택에 semantic scoring이 필요함. R18.1의 action-attached direct object-mapped child 후보 보존은 scoring이 아니라 candidate fact 보존이므로 별도 허용 |
| with-absolute recovery | spaCy가 놓친 object를 patch rule로 복구하는 구조가 됨 |
| scene context fallback | object/context 분리 rule이 복잡해짐 |
| tag-list action/relation extraction | comma segment만으로 event/relation source와 target을 안정적으로 설명하기 어려움 |
| tag-list same-pipeline extraction | tag-list를 문장처럼 parsing하면 입력 형식 mismatch로 dependency/noun chunk가 흔들림 |
| undocumented phrasal action repair | OEWN verb lookup으로 선택되지 않은 prep/particle 구조를 semantic patch로 복구하지 않음 |
| LLM extraction | v1은 rule-based baseline |
| GPIC error-specific patch | rule 설명성이 무너짐 |

## 5. Rule Change Protocol

새 rule을 추가하려면 먼저 이 문서에 아래 형식으로 추가한다.

구현은 그 다음이다.

필수 항목:

- Rule ID
- Stage
- 입력
- 출력
- 도구
- 도구 유형
- Rule 유형
- Count 영향
- Known limitation

승인 전에는 코드에 넣지 않는다.

## Appendix. GPIC Object Inventory Manual-Resolution Gate

This rule applies before parent enrichment, canonical enrichment, and Stage 4
runtime extraction with a manual-resolved GPIC object inventory.

Allowed final statuses:

- `decision_status=chosen`
- `decision_status=excluded`

Pending statuses:

- `needs_manual`
- `manual_required`
- `ambiguous`
- blank or unknown explicit `decision_status`

Gate rules:

- Parent enrichment must stop before OEWN loading if any pending row remains.
- Canonical enrichment must stop before OEWN loading if any pending row remains.
- Stage 4 runner must stop before raw extraction if any pending row remains in
  the object inventory.
- Stage 4 runner must stop before raw extraction if a selected object synset
  has no `canonical_surface`.
- Stage 4 treats unknown explicit decision statuses as `needs_manual`.
- If a row is `chosen` and its surface/head form was changed, the selected
  synset must be re-looked-up and written into `selected_oewn_synset`.
- A `chosen` row with changed `selected_query` or `canonical_surface` but blank
  `selected_oewn_synset` is invalid and must be blocked as:
  - `surface_correction_requires_synset_lookup`
- `excluded` rows are not pending manual work. They remain countable metadata
  under the current policy.

## Appendix. GPIC Attribute Inventory Manual-Resolution Gate

This rule applies before Stage 5 canonicalization when attribute output is part
of the formal caption-to-concept result.

Allowed final statuses:

- `decision_status=chosen`
- `decision_status=excluded`

Pending statuses:

- `needs_manual`
- blank or unknown explicit `decision_status`

Gate rules:

- Stage 5 runner must stop before canonicalization if any pending row remains
  in the attribute inventory.
- Stage 5 runner must stop before canonicalization if a chosen attribute row
  has a selected synset but no `canonical_surface`.
- A chosen attribute row with no selected synset is allowed only as a no-synset
  fallback row. It remains countable through raw surface fallback and should be
  identified by `decision_reason=no_oewn_attribute_synset`.
- Stage 5/6/Markdown output created without passing this gate is preview output,
  not a formal caption-to-concept result.

## Appendix. GPIC Action Inventory Manual-Resolution Gate

This rule applies before formal Stage 4 extraction when an action inventory is
passed to the Stage 4 runner.

Allowed final statuses:

- `decision_status=chosen` with a non-empty `selected_oewn_synset`
- `decision_status=raw_fallback` with an empty `selected_oewn_synset`

Pending statuses:

- `needs_manual`
- blank or unknown explicit `decision_status`
- `chosen` with an empty `selected_oewn_synset`

Gate rules:

- Stage 4 runner must stop before raw extraction if any pending row remains in
  the action inventory.
- `raw_fallback` rows are allowed because they mean OEWN found no verb synset
  for that observed action span; count/export uses raw surface fallback.
- A manual action decision must select a synset from the current OEWN candidate
  list for that row.
- If manual resolution chooses one Morphy query from an ambiguous query set,
  write the selected singular query into `selected_query` and preserve the
  decision note in the manual decision artifact.

## Appendix. Fixed-Lexicon Scale-Out Execution Contract

This contract extends R28 for GPIC Lite/Full extraction after the inventory is
frozen. It changes execution and artifact layout, not Stage 1-6 semantics.

- The immutable caption-shard manifest and the completed current inventory
  bundle are the only semantic inputs.
- Contiguous input shards are grouped into deterministic work units. A work
  unit identity includes every input shard row count/SHA and the inventory
  bundle SHA; runtime GPU IDs and worker count are excluded from identity.
- Each work unit runs the existing formal mixed Stage 1-6 implementation. No
  separate extraction, fallback, canonicalization, or count rule is allowed in
  the scale-out wrapper.
- A work unit is complete only after its formal summary, Stage 5 artifacts,
  Stage 6 count tables, and receipt pass row/hash checks. The completion
  receipt is written atomically; a unit directory without that receipt is
  always incomplete and is never consumed by resume or final merge.
- Resume skips only receipt-verified completed units. An interrupted temporary
  unit is not accepted as progress and may be replaced only after the wrapper
  verifies that no worker still owns it.
- Available GPU count may differ between attempts. Remaining work units are
  assigned dynamically to the GPUs visible in the new attempt.
- The outer scale-out entrypoint is incident-gated. Its child unit executions
  are covered by the parent gate and must not create independent repository
  incident markers.
- Global Stage 6 tables are produced with the existing Stage 6 shard-count
  merger. Stage 5 unit roots remain independently streamable for downstream
  materialization and caption-order exposure accounting.
- Large captions, Stage outputs, and count artifacts stay on MLXP storage.
  Desktop transfer is limited to versioned code and small manifests.
