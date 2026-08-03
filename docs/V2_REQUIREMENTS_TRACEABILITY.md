# V2 Requirements Traceability

| 항목 | 값 |
|---|---|
| 문서 상태 | `CANDIDATE` — 기준선 `3f1bc7d`의 8-profile bundle은 `PASS`; 현재 candidate 재검증 중 |
| 대상 브랜치 | `feat/v2-quality-eval-ci` |
| V1 rollback | `29a429f16616ff63dd6ef175b870ec042be10e71` |
| 최초 evidence 기준선 | `4a596b008cfc0470c1f2d30ad59ce5f122d9995d` |
| 제출 결론 | V2 branch 제출 `YES`; `main` merge는 수행하지 않음 |
| 정직한 잔여 | `PROC-04`는 `PARTIAL`; current-SHA hosted 재검증 필요; prompt proxy packing은 local candidate evidence; gold v1 채택·실제 Codex 평가는 `PARTIAL`; 성능 gate는 `PLANNED` |

## 이 문서를 읽는 방법

이 파일은 실행 매뉴얼이 아니라 **요구사항→상태→증거 anchor**의 정본이다. 처음
실행하는 독자는 [Evaluator Manual](./EVALUATOR_MANUAL.md), 설계 이유를 찾는 독자는
[Architecture](../ARCHITECTURE.md)와 ADR을 먼저 사용한다.

제출 HEAD가 바뀌면 코드·문서 변경 여부와 무관하게 아래 여덟 profile을 모두 그
정확한 SHA에서 다시 실행한다. 과거 영수증은 역사 증거로만 남고, docs-only commit도
현재 제출 증거로 재사용하지 않는다. 특히 `REQ-09`는 core·actual live·document
profile이 같은 최종 SHA에서 모두 `PASS`해야 `VERIFIED`다.

이 문서가 현재 제출 상태의 정본입니다. `retros/`는 생성 시점의 append-only
이력이며, 그 안의 후속 검증 제안은 이후 current-SHA 영수증과 충돌하지 않습니다.

V1의 과거 test/live smoke를 V2 증거로 재사용하지 않는다. 아래 표는 candidate의
부모 `3f1bc7d` 기준선이며 현재 candidate의 완료 증거가 아니다. 최종 판정 전에는
모든 profile을 같은 candidate SHA의 새 영수증으로 교체한다.

| 외부 제출-control profile | 확인 경계 | `3f1bc7d` 기준선 결과 |
|---|---|---|
| `v2-core-suite` | PostgreSQL/pgvector, migration, 공식 3경로, transaction | `188 passed, 6 skipped`; 필수 node `3/3` |
| `v2-product-readiness` | 실제 DB readiness | HTTP `200`, migration `0001`, pgvector `0.8.6` |
| `v2-fresh-readme-launch` | detached clean README launch | launch·cleanup `PASS`; 명령·링크 static check |
| `v2-live-codex-sse` | actual Codex/Uvicorn SSE | structured projection, `miss → hit → update → miss`, 복수 delta·citation `PASS` |
| `v2-document-parity` | 문서–코드–테스트 | link, Ruff, lock, Compose, hook `PASS` |
| `v2-provenance` | template 증분·의존성 출처 | provenance와 지원자 확인 `PASS` |
| `v2-retrobot-runtime` | hook 활성화·제한 입력·자동 회고 | runtime activation pair `PASS` |
| `v2-remote-sync` | local/tracking/live remote | clean, ahead/behind `0/0`, exact SHA parity `PASS` |

Profile ID는 제출 repository 밖의 evidence-control Wiki에서 사용하는 audit
이름입니다. 평가자가 repository 안에서 재현할 core/readiness/live 명령은 README와
tests에 두며, 원문 답변·credential·전체 실행 log는 source에 복사하지 않습니다.

상태 언어는 `VERIFIED`, `PARTIAL`, `MISSING`, `BLOCKED`, `UNKNOWN`, `PLANNED`를
사용합니다. 불합격 gate는 해당 사유가 발동하지 않았을 때 `PASS`로 기록합니다.

## 1. 제품 요구사항

| 규칙 | 구현·테스트 근거 | 상태 |
|---|---|---|
| `REQ-01` 업로드 | FastAPI multipart와 입력 검증 | `VERIFIED` |
| `REQ-02` 파싱 | 실제 TXT/PDF/Markdown parser와 page/offset | `VERIFIED` |
| `REQ-03` 저장 | transactional ingestion, ready/version/same-byte no-op | `VERIFIED` |
| `REQ-04` RAG | PostgreSQL pgvector `<=>`, 한국어 fixture, top citation | `VERIFIED` |
| `REQ-05` 답변 | production Codex gateway와 actual live runtime | `VERIFIED` |
| `REQ-06` 출처 | server-side chunk→versioned citation mapper와 live receipt | `VERIFIED` |
| `REQ-07` 무근거 | threshold miss에서 LLM·cache skip | `VERIFIED` |
| `REQ-08` streaming | 실제 Codex delta를 Uvicorn HTTP/SSE로 순서대로 전달 | `VERIFIED` |
| `REQ-09` LLM 활용 | 한 필드 output schema, strict incremental decoder, bounded timeout/retry와 invalid-output no-cache; actual live Codex | `VERIFIED` |
| `REQ-10` exact cache | active + 세 current version exact predicate | `VERIFIED` |
| `REQ-11` similar cache | exact miss 후 pgvector cosine `>=0.95` | `VERIFIED` |
| `REQ-12` cache metadata | JSON/SSE hit/match/write/skip field | `VERIFIED` |
| `REQ-13` 무효화 | CREATED/UPDATED/UNCHANGED, dependency·similarity·version fallback | `VERIFIED` |
| `REQ-14` prompt | version SSOT, JSON envelope, delimiter round-trip, proxy-budget evidence selection과 설계 문서 | `VERIFIED` |

## 2. Transaction·concurrency·migration

| ID | 불변식과 증거 | 상태 |
|---|---|---|
| `TX-01` | document/chunk/vector/cache/revision/ready 한 transaction | `VERIFIED` |
| `TX-02` | transaction 밖 prepare, advisory lock 뒤 hash/version 재확인 | `VERIFIED` |
| `TX-03` | failure injection 뒤 이전 ready corpus와 cache/revision 보존 | `VERIFIED` |
| `CC-01` | 동일 document 직렬화 → CREATED+UNCHANGED, revision 1 | `VERIFIED` |
| `CC-02` | 서로 다른 document 동시 ingest → ready 2, revision 2 | `VERIFIED` |
| `CC-03` | LLM 중 revision 변경 → `corpus_changed` cache-write skip | `VERIFIED` |
| `MIG-01` | forward-only version/checksum, empty DB, re-run, drift/history | `VERIFIED` |
| `MIG-02` | advisory lock과 실패 migration rollback | `VERIFIED` |
| `OPS-01` | digest-pinned PostgreSQL 17.10/pgvector 0.8.6 | `VERIFIED` |
| `OPS-02` | `/health/ready` DB/migration/vector success·failure·timeout | `VERIFIED` |
| `OPS-03` | isolated Compose test와 suite별 temporary database | `VERIFIED` |
| `OPS-04` | raw question/evidence 없는 terminal timing receipt와 best-effort JSON sink | `IMPLEMENTED` — `tests/test_timing_observability.py`와 관련 회귀 PASS; current-SHA hosted check 전 candidate evidence |

## 3. 공식 테스트와 double 경계

| ID | 실제 경계 | 허용 double | 상태 |
|---|---|---|---|
| `TEST-01` | PostgreSQL, migration, pgvector, parser, ASGI TXT/PDF ingestion | embedding, unused LLM | `VERIFIED` |
| `TEST-02` | PostgreSQL `<=>`, rank, provenance, ASGI/SSE | embedding, scripted LLM | `VERIFIED` |
| `TEST-03` | cache row/dependency/invalidation/revision/rollback | embedding, scripted LLM | `VERIFIED` |
| `P0-SEC-01` | container executable/env/mount/effective DB target | 없음 | `VERIFIED` |
| `P0-PROMPT-01` | deterministic JSON serialization과 breakout | 없음 | `VERIFIED` |
| `P0-STRUCT-01` | 한 필드 schema→plaintext projection, protocol/API/cache failure 경계 | actual live만 offline double | `VERIFIED` |
| `P0-PROMPT-02` | 완성 prompt tiktoken `o200k_base` proxy count, ranked packing, 선택 citation, partial no-cache와 별도 budget 기권 | tokenizer/LLM deterministic double | `IMPLEMENTED` — focused local 회귀 PASS; candidate exact-SHA hosted check 전 |

### 3.1 제안 품질 평가

| ID | 경계와 증거 | 상태 |
|---|---|---|
| `EVAL-01` | 30-case dev/process-holdout, case·문서 hash와 portable dataset SHA, exact-byte approval contract | `PARTIAL` — loader 검증 PASS, 사용자 채택 `pending` |
| `EVAL-02` | 제품 ingestion/Q&A runner, split별 loopback disposable DB와 empty-corpus guard | `PARTIAL` — focused 22 PASS, 실제 FastEmbed/PostgreSQL/Codex run 미실행 |
| `EVAL-03` | retrieval hit/MRR, lexical answer·citation claim coverage, exact abstention | `PARTIAL` — deterministic proxy이며 semantic entailment·NLI·factuality 아님 |

dataset SHA는
`323edcd4591726d8ea1c8e36c4137fa2558d7b020260514eb7ea5ab6a0ac52a9`이고
`evals/gold/v1/approval.json`이 `pending`이다. 공개된 holdout은 blind benchmark가
아니며 이 데이터로 제품 기본값을 튜닝했다고 주장하지 않는다. 실제 run은 합성 질문과
검색 문맥을 사용자 구독의 로컬 Codex App Server에 전송하므로 CI에서 실행하지 않는다.

공식 명령은 다음 한 줄입니다.

```bash
docker compose run --build --rm test
```

여섯 skip은 격리 image에 의도적으로 없는 Git·flock이 필요한 host-only Retrobot
runtime test입니다. 필수 `TEST-01`~`TEST-03` node는 collect-only로 `3/3` 존재를
별도 확인합니다.

host suite의 `175 passed, 18 skipped`는 Compose DB URL·test-container sentinel이
없는 빠른 선택 집합이고, 위 Docker suite의 `188 passed, 6 skipped`는 DB 경로를
포함하되 host Git·flock runtime 여섯 건을 제외합니다. 따라서 합계 차이는 실패나
누락이 아니라 실행 환경별 명시적 selection입니다.

## 4. P0 1~9 매핑

| P0 | 코드 | 회귀 |
|---|---|---|
| 1. 세 version 저장 | migration, `CacheVersions`, PostgreSQL/SQLite adapters | DB row assertions |
| 2. 세 version current-match | exact/similar SQL predicate와 soft invalidation | 세 개별 mismatch miss |
| 3. prompt SSOT | `prompts.py::PROMPT_TEMPLATE_VERSION` | declaration/payload assertion |
| 4. 안전한 구조 직렬화 | deterministic JSON + `<>&` escape | question/evidence/metadata breakout |
| 5. citation 의미 | retrieval provenance | source scan과 live versioned citation |
| 6. offline double 제한 | embedding/LLM만 주입 | actual DB/parser + ASGI assertions |
| 7. test container 격리 | Dockerfile, `.dockerignore`, Compose sentinel | executable/env/mount runtime test |
| 8. DEC 상태 | DEC-010·011·013 `PROPOSED`; DEC-012 superseded | V1 rollback과 V2 구현 evidence를 분리 |
| 9. structured output | `outputSchema` + strict decoder로 plaintext SSE 유지 | protocol/API/cache/live regression |

## 5. 불합격 기준 gate

| ID | 판정 | 증거 anchor와 근거 |
|---|---|---|
| `DQ-01` 실행 불가 | `PASS` | detached clean README launch |
| `DQ-02` hardcode/LLM 없음 | `PASS` | actual Codex/Uvicorn SSE |
| `DQ-03` vector/cache DB 없음 | `PASS` | real PostgreSQL/pgvector core suite |
| `DQ-04` 자동화 테스트 전무 | `PASS` | 188-test Docker suite와 필수 3경로 |
| `DQ-05` OSS 통째 복사 | `PASS` | template 증분 history, dependency provenance, 지원자 확인 |
| `DQ-06` 허위 문서 | `PASS` | targeted functional/status registry, independent cold-read와 runtime bundle |
| `DQ-07` 평가 창 밖 구현 | `PASS` | applicant window와 meaningful artifact/push 시각 |

## 6. 제출 process

| 규칙 | 상태 | 근거 또는 한계 |
|---|---|---|
| `DOC-01` README | `VERIFIED` | 한 줄 run/test, detached clean launch와 core/document bundle |
| `DOC-02` Architecture | `VERIFIED` | core/readiness/clean/live가 뒷받침하는 구조·SDK 설명 |
| `DOC-03` Prompt Design | `VERIFIED` | JSON/version/schema/decoder code·test와 실패 의미의 정합성 |
| `DOC-04` tests | `VERIFIED` | 공식 3경로와 자동화 suite |
| `DOC-05` 회고 산출물 | `VERIFIED` | 현재 자동 회고 pair와 timeline |
| `PROC-03` Retrobot 자동 회고 | `VERIFIED` | 활성 hook의 setup→retro→product chronology와 skip-free runtime test |
| `PROC-04` worked-day commit·push | `PARTIAL` | 8월 2일 commit의 당일 push를 증명하지 못함; 불합격이 아닌 Track C 과정 위험 |
| `PROC-05` 회고 내용 | `VERIFIED` | bounded latest WORKLOG H2와 자동 KPT artifact |
| `SUB-01` private final push | `VERIFIED` | local/tracking/live remote exact parity, clean, ahead/behind `0/0` |

## 7. 상태 namespace

| Namespace | 현재 판정 | 의미·정본 |
|---|---|---|
| `SOURCE-EVIDENCE` | 8-profile bundle `PASS` | 최종 HEAD의 same-SHA 제출 증거; 계약 gate가 아님 |
| `OFFICIAL-RULES` | 제출 준비 `PASS` | 과제 원문 rule scorecard; CI workflow를 필수로 요구하지 않음 |
| `INTERNAL-V2` | CI source `IMPLEMENTED`, check `PENDING` | 실제 PR check-run 전에는 G6를 승격하지 않음 |

따라서 공식 제출 준비 `PASS`와 자체 내부 계약 `FAIL`은 모순이 아닙니다.
`REQ-09`는 SCORING 요구로 세 evidence profile에 연결해 `VERIFIED`했지만 필수
합격 gate로 격상하지 않습니다. `PROC-04`는 두 판정과 별개로 `PARTIAL`입니다.

## 8. 승격 금지선

- HEAD가 바뀌면 여덟 profile을 모두 같은 최종 SHA에서 다시 실행한 뒤 final audit을
  수행합니다. 과거 영수증은 현재 제출 증거로 승계하지 않습니다.
- fake LLM SSE를 actual Codex incremental SSE로 표현하지 않습니다.
- schema field 존재만으로 lookup/write enforcement를 주장하지 않습니다.
- tiktoken `o200k_base` proxy budget을 정확한 Codex token/context/output 상한으로 표현하지
  않습니다. selection policy나 renderer가 바뀌면 prompt template version도 올립니다.
- `REQ-09`는 core protocol/API/cache, actual live Codex, document parity의 same-SHA
  영수증 중 하나라도 없으면 `PARTIAL`로 내립니다.
- `PROC-04`는 완전한 worked-day remote chronology 없이는 `PARTIAL`입니다.
- DEC-010·011·013은 P0-08에 따라 `main` supersession에는 `PROPOSED`, V2 branch
  evidence에는 `VERIFIED`를 유지합니다. DEC-012는 branch에서 DEC-013이 supersede했습니다.
- C0 workflow는 `d86f4c4`의 hosted run #2에서 PASS했지만 이후 HEAD의 증거로
  승계하지 않습니다. timing source는 구현했어도 benchmark·SLO로 표현하지 않으며,
  backup/restore drill과 HA/PITR는 아직 구현했다고 주장하지 않습니다.
