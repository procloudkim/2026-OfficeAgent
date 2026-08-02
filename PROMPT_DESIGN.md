# Prompt Design

## 상태와 목표

프롬프트의 역할은 검색된 문서 근거 안에서 간결히 답하게 하는 것입니다. 검색
threshold, no-evidence와 citation 조립은 LLM이 아니라 서버 코드가 담당합니다.

canonical source는
[`src/officeagent/application/prompts.py`](./src/officeagent/application/prompts.py)이며
`PROMPT_TEMPLATE_VERSION = "grounded-json-v2"`도 그 파일에만 선언합니다.

JSON envelope, version SSOT와 delimiter breakout 경계는 current-SHA code/test로
`VERIFIED`입니다. timeout/retry는 자동화로, 정상 Codex/SSE 경로는 live smoke로
확인했습니다. Structured LLM output은 의도적으로 구현하지 않아 `REQ-09` 전체
평가는 `PARTIAL`입니다.
상세 영수증은 [V2 Traceability](./docs/V2_REQUIREMENTS_TRACEABILITY.md)가 소유합니다.

## Why–What–How

| Why | What | How |
|---|---|---|
| 문서 밖 추론 억제 | evidence-only 규칙 | JSON evidence가 지지하는 사실만 답하도록 명시 |
| 구조적 breakout 완화 | 하나의 untrusted-data JSON envelope | question/evidence/metadata를 `json.dumps`하고 `<`, `>`, `&` escape |
| 근거 부족 시 환각 방지 | 이중 abstention | 서버 score gate에서 LLM skip + prompt 규칙 |
| 다국어 질문 품질 | 질문 언어 유지 | 질문과 같은 언어로 간결히 답하도록 명시 |
| 허위 출처 방지 | citation 생성 분리 | LLM에 출처 생성을 요구하지 않고 서버가 Chunk에서 조립 |
| stale cache 방지 | prompt version SSOT | cache identity에 prompt/embedding/retrieval version 결합 |

## Prompt 구조

지시문 뒤에 marker를 한 번만 쓰고, 이후 모든 사용자·문서 데이터를 JSON value로
직렬화합니다.

```text
role and grounding rules
Prompt template version: grounded-json-v2
UNTRUSTED_INPUT_JSON:
{"evidence":[...],"prompt_version":"grounded-json-v2","question":"..."}
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

Codex App Server의 실제 `item/agentMessage/delta`만 live model output으로
인정합니다. 첫 delta 전 transport/timeout만 한 번 재시도하고 delta 이후 실패는
`answer.error`로 종료합니다.

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

자동화는 구조·호출 경계를 검증합니다. answer 의미 품질과 adversarial prompt
injection 방어율을 정량화하는 evaluator는 아직 없습니다.

## Structured output 비범위

LLM structured output은 별도 spike 전에는 구현하지 않습니다. spike에는 최소한
Codex App Server 지원 형태, SSE delta 영향, parse 실패·retry, latency와 citation
계약의 수용 조건이 필요합니다. 그 전에는 plain text delta와 서버 생성 retrieval
provenance를 유지합니다.

## Trade-off와 배운 점

- JSON envelope는 XML-like delimiter 조합보다 구조가 명확하지만 token과
  serialization test surface가 늘어납니다.
- version을 cache identity에 넣으면 correctness는 높아지고 reuse는 줄어듭니다.
- “구조적으로 escape했다”와 “의미적으로 안전하다”를 분리해야 허위 보안 주장을
  피할 수 있습니다.
- prompt version field의 존재가 아니라 실제 lookup/write와 mismatch regression이
  정책 enforcement 증거입니다.
