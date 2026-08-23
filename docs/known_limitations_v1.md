# V1 Known Limitations

이 문서는 v1 baseline이 일부러 하지 않는 일을 정리한다.

목적은 변명 목록이 아니라, 결과를 해석할 때 어디까지 믿고 어디부터 믿으면 안 되는지를 명확히 하는 것이다.

v1의 기준은 다음 한 문장이다.

> 문장에서 직접 보이는 spaCy parse evidence와 명시 lexicon만 사용해서 countable concept을 만든다.

따라서 v1은 high-recall parser가 아니다. 설명 가능한 최소 baseline이다.

## 1. 전체 한계

| 구분 | v1 동작 | 한계 | 결과 해석 |
|---|---|---|---|
| 입력 | caption text만 사용 | image를 보지 않음 | caption에 없는 정보는 절대 복구하지 않는다. |
| parsing | spaCy parse evidence 사용 | spaCy parser 오류가 그대로 전파될 수 있음 | 잘못 붙은 dependency는 잘못된 tuple로 이어질 수 있다. |
| rule | 문서화된 rule만 사용 | 많은 edge case를 일부러 복구하지 않음 | 누락이 있을 수 있지만, 왜 누락됐는지는 설명 가능해야 한다. |
| canonicalization | resolved inventory와 명시 lexicon lookup 중심 | no-synset concept은 raw fallback으로 남음 | parent가 비거나 canonical이 raw와 같을 수 있다. |
| count export | 새 해석 없이 집계만 수행 | export 단계에서 누락 복구 없음 | count table은 extraction 결과의 집계이지 추가 parser가 아니다. |

## 2. 입력 형태 한계

### 2.1 Tag-list caption은 제한적으로 처리

v1은 일반 문장형 caption을 주 extraction 대상으로 삼고, tag-list caption은 제한된 object/attribute/quantity extraction만 수행한다.

GPIC row의 `caption_type`이 `tag`인 caption은 다음처럼 처리한다.

- `caption_shape = tag_list`
- sentence path로 보내지 않음
- comma segment별로 spaCy annotation
- segment 내부 noun chunk 기반 object/attribute/quantity만 count 가능

tag-list 제한 이유:

- comma segment가 object인지 attribute인지 context인지 문법만으로 안정적으로 판단하기 어렵다.
- tag-list를 일반 문장처럼 spaCy parsing하면 dependency와 noun chunk가 의미 없는 경우가 많다.
- tag-list action/relation source와 target은 안정적으로 설명하기 어렵다.

### 2.2 Noise removal은 기본 적용하지 않음

v1에서는 aggressive noise removal을 하지 않는다.

하지 않는 것:

- watermark phrase 제거
- URL 제거
- stock-photo phrase 제거
- emoji 제거
- 파일명 prefix 제거
- resolution text 제거

이유:

- 제거 rule이 잘못 작동하면 원문 caption 정보가 사라진다.
- 먼저 raw baseline을 만들고, noise 문제가 count에 실제로 얼마나 영향을 주는지 본 뒤 별도 rule로 추가한다.

## 3. Stage 2 한계: preprocessing

### 3.1 Quote span merge

quote span은 placeholder로 바꾸지 않고 원문 quote span을 하나의 token처럼 보호한다.

한계:

- quote의 의미를 해석하지 않는다.
- `"1709-1"`이 number인지 text인지 구분하지 않는다.
- quote가 어떤 object에 붙는지는 Stage 4 evidence에만 의존한다.

### 3.2 Object MWE는 Stage 2에서 merge하지 않음

Stage 2는 object MWE를 retokenize하지 않는다. Object span은 Stage 4에서
resolved object inventory의 selected span을 noun chunk에 적용해 결정한다.

한계:

- inventory에 관측·해결되지 않은 새 span은 기존 single-token/core fallback으로 남을 수 있다.
- selected span은 token metadata로 관리되므로 spaCy 자체 tokenization이나 POS를 고치지 않는다.
- quote-protected 단일 token 안의 공백은 object MWE token 수로 해석하지 않는다.

### 3.3 Hyphen word merge

plain hyphen word만 보호한다.

한계:

- numeric range, symbol expression, equation-like expression은 보호 대상에서 제외될 수 있다.
- hyphen span을 merge한다고 해서 그 의미가 object인지 attribute인지 자동 결정되는 것은 아니다.

## 4. Stage 3 한계: linguistic annotation

Stage 3은 annotation만 한다.

즉, 이 단계에서는 object, attribute, action, relation을 만들지 않는다.

### 4.1 spaCy model error

spaCy tagger와 parser는 learned model이다.

따라서 다음 오류가 생길 수 있다.

- noun을 AUX/VERB로 오인
- adjective를 PROPN/NOUN으로 오인
- coordination attachment 오류
- participle attachment 오류
- sentence root 오류
- quote나 title phrase 분석 오류

v1은 이런 오류를 대부분 복구하지 않는다.

### 4.2 Object MWE POS correction 없음

Stage 2 object MWE retokenization이 비활성화되어 있으므로 object MWE 전용
POS/TAG correction도 적용하지 않는다. Stage 4 inventory span selection은
Stage 3의 원래 token/POS/dependency evidence를 보존한다.

## 5. Stage 4 한계: raw concept extraction

Stage 4는 raw mention과 raw edge를 만든다.

v1의 원칙은 단순하다.

- noun chunk 내부 resolved inventory selected span -> object
- selected object core 밖 modifier 또는 resolved attribute MWE -> attribute
- `nummod`/`NUM` -> quantity
- resolved single VERB 또는 phrasal action span -> action
- documented direct/passive/conj/ACL evidence -> event role
- single ADP 또는 reviewed preposition MWE + object evidence -> relation

### 5.1 Object 누락 가능

다음 object는 누락될 수 있다.

- spaCy noun chunk가 잡지 못한 object
- parser가 verb/adjective로 잘못 본 object
- 생략 구조 안의 object
- tag-list segment에서 noun chunk로 분석되지 않는 object
- pronoun이 가리키는 실제 antecedent object

v1은 with-absolute recovery나 scene fallback object recovery를 하지 않는다.

### 5.2 Attribute 누락 가능

다음 attribute는 누락될 수 있다.

- noun chunk 밖에 떠 있는 attribute
- parser가 잘못 붙인 modifier
- 문장 전체에 걸친 style/medium 표현
- predicative `acomp` attribute
- resolved inventory에 없는 새로운 attribute MWE
- quote 안에서 concept이 아닌 text로 보호된 attribute-like 문자열

### 5.3 Action role 누락 가능

다음 action role은 누락될 수 있다.

- subject가 pronoun인 경우
- `relcl` relative pronoun을 antecedent noun으로 치환해야 하는 경우
- conjoined verb가 patient를 생략한 경우
- R16.3 범위 밖 non-finite verb의 implied subject가 필요한 경우
- gapping construction

v1은 direct role, passive subject/by-agent, action-conj agent inheritance,
VBG `acl` head-object agent만 처리한다. Broad patient/subject inheritance는 하지 않는다.

### 5.4 Relation 한계

v1 relation은 single ADP/preposition + direct `pobj` 또는 reviewed
preposition MWE + final direct `pobj`에 기반한다.

하지 않는 것:

- reviewed preposition MWE lexicon 밖의 relation MWE repair
- relation source disambiguation
- self-edge repair
- prepositional object를 action patient로 바꾸는 해석
- relation label semantic normalization

따라서 relation table에는 single ADP raw relation과 documented preposition
MWE relation만 나온다.

## 6. Reference와 coreference 한계

v1은 coreference system이 아니다.

하지 않는 것:

- pronoun resolution
- generic anaphora resolution
- `one`, `another`, `others`, `both` instance splitting
- relative pronoun repair
- possessive pronoun target recovery

예상되는 결과:

- `he`, `she`, `it`, `they`가 가리키는 object로 role이 복원되지 않을 수 있다.
- `the object`, `the device`, `the structure` 같은 표현이 앞 object와 merge되지 않을 수 있다.
- `a red one` 같은 표현이 원래 object의 colored instance로 복원되지 않을 수 있다.

이 제한은 의도적이다.

coreference는 scoring rule과 예외 처리가 빠르게 복잡해지기 때문에 v1 baseline에서 제외한다.

## 7. Passive voice 한계

v1은 direct passive subject와 passive `by` phrase만 semantic event_role로 보존한다.

예:

`The building is surrounded by trees.`

현재 처리:

- `building`이 object로 잡히고 `nsubjpass`이면 `surround`의 `patient/theme`으로 만든다.
- `by trees`가 같은 passive action의 direct `by` phrase이면 `trees`를 `agent/by_agent_or_causer`로 만든다.
- 생성 edge에는 `voice_normalization=passive_to_active` metadata를 남긴다.

남는 한계:

- `surrounded by`를 하나의 relation/action으로 collapse하지 않는다.
- `by`가 아닌 causer phrase는 passive agent로 복원하지 않는다.
- passive subject나 by-object가 object mapping에 없으면 새 object를 만들지 않는다.
- coreference가 필요한 passive agent/theme 복원은 하지 않는다.

이유:

- passive event role은 Stage 4 raw extraction에서 명시 edge로 만든다.
- Stage 5는 여전히 edge 구조를 새로 고치지 않고, Stage 4가 만든 edge를 canonicalize만 한다.

## 7.1 ACL/relative clause 한계

v1은 `acl` action의 head noun/object를 agent로 쓰는 좁은 R16.3만 적용한다.

예:

`A man holding a bat.`

현재 처리:

- `holding.dep == acl`, `holding.tag == VBG`, `holding.head == man`이면 `holding --agent--> man`을 만든다.
- 이미 direct `nsubj` agent가 있는 action이면 새 agent를 만들지 않는다.
- `tag == VBN`인 acl action이나 passive-like acl action이면 새 agent를 만들지 않는다.

남는 한계:

- `relcl` relative pronoun subject는 아직 head noun으로 치환하지 않는다.
- `A man who is holding a bat.`에서 `who`를 `man`으로 resolve하는 rule은 별도로 필요하다.
- `advcl`, `xcomp`, `ccomp`, `acomp`, VBG/VBN fallback 같은 broad inheritance는 하지 않는다.

## 8. Canonicalization 한계

Stage 5는 raw extraction 결과를 단순화한다.

하지만 새 concept을 만들거나 edge를 고치지 않는다.

### 8.1 Synonym 한계

Object는 resolved object inventory의 canonical surface를 사용한다. Attribute와
action은 resolved inventory에서 export된 Stage 5 synonym lexicon을 사용한다.
선택 synset이 없는 countable row는 raw fallback으로 남는다.

### 8.2 Parent concept 한계

Object parent는 selected OEWN synset의 immediate hypernym evidence를 Stage 4
source detail에서 가져온다. Attribute parent는 현재 별도 taxonomy를 자동으로
만들지 않고 비워 둔다.

한계:

- parent가 비는 concept이 있을 수 있다.
- object parent는 1차 hypernym까지만 보존하며 전체 ancestor chain이 아니다.
- no-synset object와 attribute/action에는 OEWN parent가 없다.

### 8.3 Relation은 raw-preserving

relation은 v1에서 대표어로 적극 단순화하지 않는다.

이유:

- relation은 문맥 의존성이 크다.
- `over`, `by`, `with`, `on` 같은 relation은 단순 synonym mapping이 위험하다.
- 먼저 raw relation count를 보는 것이 더 안전하다.

## 9. Count export 한계

Stage 6은 count를 만든다.

하지 않는 것:

- 누락 object 복구
- relation source/target 변경
- agent/patient 변경
- canonicalization 추가 수행
- skipped caption 복구

따라서 count table의 의미는 다음과 같다.

> v1 rule이 실제로 추출한 concept의 count

즉, GPIC caption 안에 존재하는 모든 concept의 true count가 아니다.

## 10. V1 결과를 보여줄 때 써야 하는 표현

권장 표현:

- explainable caption-to-concept baseline
- documented rule-based extraction
- countable raw/canonical concept table
- conservative baseline with known omissions

피해야 할 표현:

- complete scene graph parser
- high-recall parser
- full semantic parser
- full coreference-aware parser
- image-grounded concept extractor

## 11. V2로 넘길 후보

아래 항목은 v1 결과를 본 뒤 별도 rule proposal로 검토한다.

| 후보 | 왜 v1에서 제외했는가 |
|---|---|
| tag-list action/relation extraction | 현재 tag-list는 object/attribute/quantity만 처리하며 event endpoint를 안정적으로 설명하기 어려움 |
| predicative attribute | `acomp`를 object에 연결하려면 copular/subject 해석 rule이 필요 |
| relation source disambiguation | action-attached PP의 복수 source/target 후보를 의미적으로 고르는 rule이 필요 |
| action patient inheritance | conjunct action patient 상속은 false positive 위험과 검증이 필요 |
| broader passive normalization | non-`by` causer, coreference, passive action collapse 등은 별도 semantic rewrite가 필요 |
| pronoun/coreference | scoring rule과 error analysis 필요 |
| generic anaphora | `the object`, `the device` 같은 generic noun list와 antecedent scoring 필요 |
| scene context | object/context 분리 기준이 복잡해지기 쉬움 |
| noise removal | 실제 noise 분포를 본 뒤 적용해야 함 |

## 12. 변경 원칙

이 문서의 제한사항을 없애려면 다음 순서를 지킨다.

1. `docs/rules_v1.md`에 새 rule을 먼저 추가한다.
2. 입력, 출력, 도구, rule type, count 영향, known limitation을 명시한다.
3. 기존 output schema와 충돌하는지 확인한다.
4. 그 다음에만 코드를 구현한다.

문서에 없는 보정은 구현하지 않는다.
