# Prompt Design

> **독자와 목적:** prompt·LLM·SSE 계약을 검토하는 독자가 신뢰할 수 없는 입력,
> structured output, cache identity와 citation의 한계를 구분하기 위한 문서입니다.
> 재현 절차는 [Evaluator Manual](./docs/EVALUATOR_MANUAL.md), 현재 상태는
> [V2 Traceability](./docs/V2_REQUIREMENTS_TRACEABILITY.md)가 소유합니다.

## 상태와 목표

프롬프트의 역할은 검색된 문서 근거 안에서 간결히 답하게 하는 것입니다. 검색
threshold, no-evidence와 citation 조립은 LLM이 아니라 서버 코드가 담당합니다.

canonical source는
[`src/officeagent/application/prompts.py`](./src/officeagent/application/prompts.py)이며
`PROMPT_TEMPLATE_VERSION = "grounded-structured-answer-v4"`도 그 파일에만 선언합니다.
제품 runtime의 effective prompt version은 이 값에 tokenizer proxy identity의 SHA-256
앞 16자리와 input budget을 결합합니다.

JSON envelope, version SSOT와 delimiter breakout 경계는 current-SHA code/test로
`VERIFIED`입니다. timeout/retry는 자동화로, 정상 Codex/SSE 경로는 live smoke로
확인했습니다. 한 필드 structured output, incremental plaintext projection과
실패 경계도 protocol/API/cache 회귀와 실제 Codex live smoke로 확인했습니다.
상세 영수증은 [V2 Traceability](./docs/V2_REQUIREMENTS_TRACEABILITY.md)가 소유합니다.

## Why–What–How

| Why | What | How |
|---|---|---|
| 문서 밖 추론 억제 | evidence-only 규칙 | JSON evidence가 지지하는 사실만 답하도록 명시 |
| 구조적 breakout 완화 | 하나의 untrusted-data JSON envelope | question/evidence/metadata를 `json.dumps`하고 `<`, `>`, `&` escape |
| 근거 부족 시 환각 방지 | 이중 abstention | 서버 score gate에서 LLM skip + prompt 규칙 |
| 다국어 질문 품질 | 질문 언어 유지 | 질문과 같은 언어로 간결히 답하도록 명시 |
| 허위 출처 방지 | citation 생성 분리 | LLM에 출처 생성을 요구하지 않고 서버가 Chunk에서 조립 |
| 출력 계약 위반 차단 | 한 필드 schema + strict decoder | JSON grammar를 숨기고 검증된 answer 문자열만 SSE로 projection |
| 과대 prompt 회피 | 완성 prompt 단위 proxy budget | score 순서를 유지하며 실제로 들어가는 evidence만 선택 |
| stale cache 방지 | effective prompt version | template·tokenizer proxy·budget과 embedding/retrieval version 결합 |

## Prompt 구조

지시문 뒤에 marker를 한 번만 쓰고, 이후 모든 사용자·문서 데이터를 JSON value로
직렬화합니다.

```text
role and grounding rules
Prompt template version: grounded-structured-answer-v4:counter=<16-hex>:budget=4096
UNTRUSTED_INPUT_JSON:
{"evidence":[...],"prompt_version":"grounded-structured-answer-v4:counter=<16-hex>:budget=4096","question":"..."}
```

각 evidence object는 다음 세 부분을 가집니다.

- `id`: 1부터 시작하는 prompt-local evidence 순번
- `text`: 검색된 청크 원문
- `metadata`: document id/version, filename, chunk id/index, page,
  start/end offset, retrieval score

`ensure_ascii=False`, `allow_nan=False`, compact separators와 `sort_keys=True`를
사용하고 serialized JSON의 `&`, `<`, `>`를 각각 `\u0026`, `\u003c`,
`\u003e`로 바꿉니다. JSON을 parse하면 원래 question/evidence/metadata 값이
round-trip 됩니다.

이 설계는 `</question>`, `</evidence>`, 가짜 marker나 악성 filename이 prompt의
구조적 경계를 닫는 것을 막습니다. 그러나 모델이 데이터 안 명령을 의미적으로
따르지 않는다고 완전히 증명하지는 않습니다.

## Grounding과 citation

1. 기본 top-5 검색 후 cosine score `0.30` 미만을 제거합니다.
2. 남는 청크가 없으면 LLM을 호출하지 않고 고정 no-evidence 응답을 반환합니다.
3. 남은 청크 전체로 완성한 application prompt를 tiktoken `o200k_base`의
   `encode_ordinary()`로 셉니다. 기본 proxy input budget은 `4096`입니다.
4. 전체 prompt가 넘치면 retrieval 순서대로 각 청크를 추가해 완성 prompt를 다시
   세고, 들어가는 청크는 유지하며 넘치는 청크는 통째로 건너뜁니다. 청크 원문을
   중간에서 자르거나 낮은 순위 청크를 앞질러 재정렬하지 않습니다. 넘친 hit 뒤의
   작은 hit도 계속 검사합니다. PostgreSQL retrieval 동점은 document/version/chunk
   key 순서이며 packer는 받은 순서와 중복 여부를 다시 해석하지 않습니다.
5. 하나도 들어가지 않으면 별도 prompt-budget 기권 문구와
   `cache_skip_reason="prompt_budget_exceeded"`를 반환하고 LLM·citation·cache write를
   모두 건너뜁니다. tokenizer load/count 실패도 무제한 prompt로 저하하지 않고 요청
   실패로 전파합니다.
6. 하나 이상 들어가면 그 선택된 청크만 prompt와 citation에 사용합니다. 하나라도
   제외됐다면 보이지 않는 후보를 cache dependency에서 잃지 않도록 answer cache를
   쓰지 않고 `cache_skip_reason="prompt_evidence_reduced"`를 반환합니다. 모두 들어간
   경우만 기존 revision/dependency 검사를 거쳐 cache합니다. prompt 안에는
   insufficient-evidence일 때 답변 불가를 말하도록 다시 명시합니다.

규범 알고리즘은 다음과 같습니다. `render`는 version·instruction·question·metadata·
evidence text를 동일한 canonical JSON serializer로 완성하며, 경계값은 허용합니다.

```text
if count(render(all_hits)) <= budget: select all_hits
else:
  selected = []
  for hit in all_hits:
    if count(render(selected + [hit])) <= budget: append hit
    else: continue
if selected is empty: prompt_budget abstention
if selected count < all_hits count: generate but do not cache
```

`answer.citation`은 retrieval provenance입니다. score gate를 통과했어도 proxy
budget에서 제외된 hit는 citation이 아닙니다. 남은 각 citation과 model answer의
entailment 또는 실제 사용 여부를 post-generation으로 검증하지 않으므로
`grounded=true`도 이 의미 경계 안에서 해석해야 합니다.

### Token budget의 정확한 의미

tiktoken `o200k_base`의 `encode_ordinary()`가 special-token 모양의 untrusted text도
일반 text로 취급해 **완성 application prompt 전체**를 자르지 않고 셉니다. 처음
encoding data를 준비할 때 network 또는 local cache가 필요할 수 있으며 load/count가
실패하면 무제한 prompt로 저하하지 않습니다. OpenAI 문서처럼 model마다 encoding이
다르므로 이 값은 Codex App Server가 선택한 model tokenizer의 정확한 계수가 아닙니다.
`4096`은 이 서비스가 만든 application prompt의 로컬 proxy 상한입니다. App Server가
더하는 system/runtime context, Codex model context window, output token reserve나 비용
상한을 보장하지 않습니다.

effective prompt version은 raw model/operator 문자열을 prompt에 넣지 않고 그
`token_counter_version` digest와 budget을 포함합니다. tokenizer identity나 budget이
바뀌면 기존 answer cache는 hit하지 않습니다. digest는 tokenizer bytes를 숨기기 위한
credential hash가 아니라 prompt 구조와 cache 의미를 고정하는 식별자입니다.
renderer, JSON field 또는 selection algorithm을 바꾸면 `PROMPT_TEMPLATE_VERSION`을
올리고, package·encoding·special-token 처리 정책은 `token_counter_version`을
바꿉니다.

encoding load/count 예외와 음수·비정수 count는 fail-closed입니다. JSON endpoint는
내부 문구를 숨긴 `502`, SSE는 이미 보낸 `answer.started` 뒤 `answer.error`로 끝나며
LLM·citation·`answer.completed`·cache write는 없습니다. timing receipt는 원문 없이
`outcome=error`와 제한된 exception type만 기록합니다.

threshold `0.30`, cache threshold `0.95`는 안전한 초기값이지 한국어 gold
dataset에서 최적화됐다는 주장이 아닙니다.

## Streaming과 cache

Codex App Server의 같은 turn·agent-message item에서 온 실제
`item/agentMessage/delta`만 live model output으로 인정합니다. 첫 visible answer
delta 전 transport/timeout만 한 번 재시도하고 이후 실패는 `answer.error`로
종료합니다.

여기서 retry cutoff는 gateway가 application에 공백을 포함한 첫 non-empty decoded
fragment를 yield하는 시점입니다. client socket의 실제 수신이나 exactly-once를
뜻하지 않습니다. `answer.delta`는 completion 전 provisional이며 client는
`answer.completed`를 받아야 성공 답변으로 확정할 수 있습니다.

cache hit의 80자 replay는 `cache_hit=true`로 live generation과 구분합니다. hit는
active row의 다음 값이 모두 현재 설정과 같을 때만 허용합니다.

- `prompt_version`
- `embedding_version`
- `retrieval_version`

## 자동화 검증

- `tests/test_prompts.py`
  - question/evidence delimiter 문자열의 JSON round-trip
  - filename/chunk/page metadata breakout
  - marker가 정확히 한 번만 존재
  - `PROMPT_TEMPLATE_VERSION`과 payload/cache SSOT
- `tests/test_prompt_packing.py`
  - 완성 prompt proxy count와 ranked keep/oversized skip
  - 선택된 evidence만 prompt·citation에 사용하고 일부 제외 시 cache 금지
  - 아무 evidence도 못 넣을 때 별도 기권과 LLM/cache skip
  - tokenizer identity·budget cache version과 raw identity 비노출
- `tests/test_postgres_corpus_store.py`
  - 세 version current-match hit와 각각의 mismatch miss
  - prompt version drift의 전체 fallback invalidation
- `tests/test_v2_offline_paths.py`
  - in-process FastAPI ASGI/pgvector/cache 흐름에서 version 저장과 cache 재사용
- `tests/test_codex_protocol.py`
  - exact output schema와 fragmented JSON string/escape/Unicode projection
  - malformed, duplicate/additional key, NUL, trailing/incomplete/oversized output 거부
  - 같은 item binding과 visible-delta 기준 timeout/transport retry
- `tests/test_streaming.py`
  - protocol failure 뒤 citation/completed/cache write 금지, JSON 502와 SSE error
  - 최초 answer와 cache replay의 정확한 문자열 일치

자동화는 구조·호출 경계를 검증합니다. answer 의미 품질, citation entailment,
adversarial prompt injection 방어율과 실제 Codex token headroom은 검증하지 않습니다.

## Structured output 계약

[OpenAI Codex App Server 문서](https://developers.openai.com/codex/app-server/)가
설명하는 current-turn `outputSchema`에 다음 최소 schema를 보냅니다.

```json
{
  "type": "object",
  "properties": {"answer": {"type": "string"}},
  "required": ["answer"],
  "additionalProperties": false
}
```

App Server schema에 의존해서 끝내지 않고 local decoder가 다음을 강제합니다.

- JSON object/key/string 문법과 fragment 경계를 incremental하게 해석
- escape, BMP Unicode와 surrogate pair를 plaintext로 복원
- completion에서 전체 JSON 재parse, 정확히 한 `answer` string과 incremental 결과 일치
- blank, NUL, 중복·추가 key, 잘못된 escape/surrogate, trailing/incomplete data 거부
- answer 65,536자, raw JSON 1,048,576자, stdio line 8 MiB의 로컬 상한

blank는 decoded Python `str.strip()` 결과가 비어 있는 경우입니다. raw control NUL은
JSON 문법에서, escaped `\u0000`은 decode 직후 거부합니다. answer/raw 단위는
Python `str` code point이고 valid surrogate pair는 한 code point가 됩니다. stdio
상한만 byte 단위입니다. 빈 delta는 item을 bind하지 않고 무시합니다. 첫 non-empty
matching delta는 non-empty `itemId`가 없으면 즉시 실패하고, 유효하면 그 ID를
bind합니다. 이후 누락·변경은 무시하지 않고 응답 전체를 protocol error로 종료합니다.

[RFC 8259](https://www.rfc-editor.org/rfc/rfc8259)은 object member name의 유일성을
권고하고 parser가 구현별 크기 제한을 둘 수 있음을 설명합니다. 이 구현은 parser별
차이를 허용하지 않고 duplicate key를 거부하며, 위 상한은 성능 benchmark나
Codex upstream 제한이 아니라 로컬 자원 정책으로 취급합니다.

이미 client에 전송된 SSE delta는 이후 protocol 오류가 나도 회수할 수 없습니다.
따라서 fail-closed의 정확한 의미는 `answer.error`로 끝내고 citation,
`answer.completed`와 cache write를 금지하는 것입니다. answer-level entailment
검증은 아직 범위 밖입니다.

최초 answer와 cache replay의 정확한 문자열 일치는 deterministic cross-layer
회귀가 검증합니다. 실제 live 영수증은 본문을 보관하지 않으므로 cache 전이와 delta
개수만 증명하며 content equality 증거로 사용하지 않습니다.

## Trade-off와 배운 점

- JSON envelope는 XML-like delimiter 조합보다 구조가 명확하지만 token과
  serialization test surface가 늘어납니다.
- version을 cache identity에 넣으면 correctness는 높아지고 reuse는 줄어듭니다.
- proxy budget은 비정상적으로 큰 retrieved context를 제한하지만 실제 Codex context
  limit을 증명하지 않으며, 반복 count는 top-k에 비례한 CPU 비용을 추가합니다.
- structured schema는 출력 형태를 제한하지만 답변의 사실성 자체를 보장하지 않습니다.
- incremental projection은 기존 SSE UX를 보존하지만 전용 JSON state machine과
  Unicode/size/item 경계의 유지보수 책임을 추가합니다.
- “구조적으로 escape했다”와 “의미적으로 안전하다”를 분리해야 허위 보안 주장을
  피할 수 있습니다.
- prompt version field의 존재가 아니라 실제 lookup/write와 mismatch regression이
  정책 enforcement 증거입니다.

## 외부 사실 근거

- [OpenAI token counting guide](https://developers.openai.com/cookbook/examples/how_to_count_tokens_with_tiktoken): model마다 encoding이 다르며 token 수는 선택한 encoding에 종속됩니다.
- [OpenAI tiktoken](https://github.com/openai/tiktoken): `o200k_base`를 이름으로 불러 text를 token sequence로 변환하는 공개 tokenizer입니다.
- [tiktoken `encode_ordinary`](https://github.com/openai/tiktoken/blob/main/tiktoken/core.py#L72-L84): special-token 문자열을 일반 text로 취급하는 전체-string encoding API입니다.
