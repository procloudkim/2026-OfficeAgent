# Prompt Design

## 상태와 목표

프롬프트의 역할은 검색된 문서 근거 안에서 간결히 답하게 하는 것입니다. 검색
threshold, no-evidence와 citation 조립은 LLM이 아니라 서버 코드가 담당합니다.

canonical source는
[`src/officeagent/application/prompts.py`](./src/officeagent/application/prompts.py)이며
`PROMPT_TEMPLATE_VERSION = "grounded-structured-answer-v3"`도 그 파일에만 선언합니다.

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
| stale cache 방지 | prompt version SSOT | cache identity에 prompt/embedding/retrieval version 결합 |

## Prompt 구조

지시문 뒤에 marker를 한 번만 쓰고, 이후 모든 사용자·문서 데이터를 JSON value로
직렬화합니다.

```text
role and grounding rules
Prompt template version: grounded-structured-answer-v3
UNTRUSTED_INPUT_JSON:
{"evidence":[...],"prompt_version":"grounded-structured-answer-v3","question":"..."}
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
3. 근거가 있으면 prompt에도 insufficient-evidence일 때 답변 불가를 말하도록
   반복합니다.
4. LLM 답변과 별개로 서버가 relevant `Chunk`에서 citation을 만듭니다.

`answer.citation`은 retrieval provenance입니다. 현재는 모든 relevant hit를
반환하며 model answer와 각 citation의 entailment 또는 실제 사용 subset을
post-generation으로 검증하지 않습니다. `grounded=true`도 이 의미 경계 안에서
해석해야 합니다.

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

자동화는 구조·호출 경계를 검증합니다. answer 의미 품질과 adversarial prompt
injection 방어율을 정량화하는 evaluator는 아직 없습니다.

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
`answer.completed`와 cache write를 금지하는 것입니다. tokenizer 기반 최적화와
answer-level entailment 검증은 이 변경의 범위가 아닙니다.

최초 answer와 cache replay의 정확한 문자열 일치는 deterministic cross-layer
회귀가 검증합니다. 실제 live 영수증은 본문을 보관하지 않으므로 cache 전이와 delta
개수만 증명하며 content equality 증거로 사용하지 않습니다.

## Trade-off와 배운 점

- JSON envelope는 XML-like delimiter 조합보다 구조가 명확하지만 token과
  serialization test surface가 늘어납니다.
- version을 cache identity에 넣으면 correctness는 높아지고 reuse는 줄어듭니다.
- structured schema는 출력 형태를 제한하지만 답변의 사실성 자체를 보장하지 않습니다.
- incremental projection은 기존 SSE UX를 보존하지만 전용 JSON state machine과
  Unicode/size/item 경계의 유지보수 책임을 추가합니다.
- “구조적으로 escape했다”와 “의미적으로 안전하다”를 분리해야 허위 보안 주장을
  피할 수 있습니다.
- prompt version field의 존재가 아니라 실제 lookup/write와 mismatch regression이
  정책 enforcement 증거입니다.
