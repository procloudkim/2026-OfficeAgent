# V2 Requirements Traceability

| 항목 | 값 |
|---|---|
| 문서 상태 | `PARTIAL` — G1·G2 infrastructure는 검증, G3 제품 통합은 DRAFT |
| 기준선 | V1 `29a429f16616ff63dd6ef175b870ec042be10e71` |
| 대상 브랜치 | `feat/v2-postgres-pgvector-compose` |
| claim 상태 | 제품: `DRAFT`/`IMPLEMENTED`/`VERIFIED`/`UNKNOWN`; gate·운영: `PASS`/`PARTIAL`/`DRAFT` |

이 표는 V2 요구사항을 code/test/evidence에 연결한다. G1 remote branch와 G2
database infrastructure만 검증됐다. 제품 composition은 계속 V1 SQLite+Chroma며
G3 전에는 V2 ingestion/retrieval/cache/SSE를 완료로 주장하지 않는다. 파일명이나
계획만으로 구현·검증 완료를 주장하지 않는다. V1의 27-test와 `e838cc7` live
smoke는 역사적 회귀 기준선이며 V2 제품 증거가 아니다.

## 1. 제품 요구사항 매트릭스

| 규칙·요구 | 수용 조건 | 대상 code/document | 대상 test/evidence | 상태 |
|---|---|---|---|---|
| `REQ-01`~`REQ-03` 문서 수집 | TXT/PDF HTTP upload, 실제 추출, ready document와 chunk/vector 저장, provenance, revision, same-byte no-op | parser/chunker 유지, `CorpusStore`, migration, FastAPI route | PostgreSQL-backed `test_ingestion.py`, `TEST-01` receipt | `DRAFT` |
| `REQ-04` RAG 검색 | 실제 pgvector `<=>` exact cosine와 top-k | PostgreSQL store query, `QuestionAnsweringService` | 한국어 3문서 fixture, SQL/`EXPLAIN` assertion | `DRAFT` |
| `REQ-05` 답변 생성 | 근거가 있을 때 production Codex App Server 호출 | 기존 Codex gateway 유지, lazy composition | fake gateway regression + final current-HEAD live smoke | `DRAFT` |
| `REQ-06` 출처 | 검색 chunk의 filename/version/page 또는 offset을 서버가 조립 | chunk schema, citation mapper | retrieval/API/SSE assertions | `DRAFT` |
| `REQ-07` 무근거 처리 | threshold 미만이면 `no_evidence`, citation/cache/LLM 없음 | score gate 유지 | no-evidence fake 호출 수 0 | `DRAFT` |
| `REQ-08` streaming | 실제 delta를 모으지 않고 completed 전에 SSE 전달 | Codex gateway와 SSE route 유지 | first-delta order test + final live delta receipt | `DRAFT` |
| `REQ-09` LLM 경계 | timeout/retry/error mapping, production fake 없음 | Codex adapter/config | protocol tests + source audit + live smoke | `DRAFT` |
| `REQ-10` exact cache | normalized exact active/config-matched row 우선 | `answer_cache`, CorpusStore lookup | fake LLM call count 불증가 | `DRAFT` |
| `REQ-11` similar cache | exact miss 후 pgvector cosine threshold `0.95` | question `vector(384)` lookup | similar fixture/call count | `DRAFT` |
| `REQ-12` cache metadata | response/SSE에 hit/match, write/skip metadata | API schema/events | JSON/SSE contract test | `DRAFT` |
| `REQ-13` 문서 변경 무효화 | CREATED 전체, UPDATED dependency+similarity, mismatch 전체 fallback, UNCHANGED no-op | transactional ingestion, soft invalidation | `TEST-03`, invalidation row/reason/version assertions | `DRAFT` |
| `REQ-14` prompt 설계 | 기존 untrusted-evidence/abstention prompt와 문서 정합 | `prompts.py`, `PROMPT_DESIGN.md` | prompt boundary regression | `DRAFT` |
| `TECH-01`/`TECH-02` | Python 3.11+ / FastAPI 유지 | `pyproject.toml`, app | runtime version, API tests | `DRAFT` |
| `TECH-12`/`TECH-13` | storage/search 선택과 이유 | Phase2 PRD, Trade-off, ADR-0004 | document review와 code parity | `DRAFT` |

## 2. Transaction·migration·운영 매트릭스

| ID | 불변식 | 대상 구현 | 필수 증거 | 상태 |
|---|---|---|---|---|
| `TX-01` | document/chunk/vector/cache invalidation/revision/ready 한 transaction | unified `CorpusStore` | fault injection 전체 rollback | `DRAFT` |
| `TX-02` | parse/chunk/embed는 transaction 밖, write 직전 hash/version 재확인 | ingestion service + advisory lock | stale precompute/concurrent ingest test | `DRAFT` |
| `TX-03` | 이전 ready corpus 보존과 성공 retry | transactional write path | failure→read old corpus→retry | `DRAFT` |
| `CC-01` | 동일 document ingestion 직렬화 | transaction-scoped advisory lock | concurrent tasks test | `DRAFT` |
| `CC-02` | 서로 다른 document ingestion의 revision 일관성 | locked singleton revision update | concurrent tasks/revision assertion | `DRAFT` |
| `CC-03` | LLM 중 revision 변경 시 cache write skip | retrieval snapshot + conditional save | `cache_skip_reason=corpus_changed` | `DRAFT` |
| `MIG-01` | forward-only SQL, version/checksum/applied timestamp | migration runner + numbered SQL | empty DB/re-run/checksum mismatch | `PASS` |
| `MIG-02` | migration advisory lock과 current-file rollback | migration runner | concurrent/non-prefix/failure rollback | `PASS` |
| `OPS-01` | PostgreSQL 17.10/pgvector 0.8.6 실제 runtime | digest-pinned Compose | head/version JSON + runtime inspection | `PASS` |
| `OPS-02` | DB/migration/vector readiness, LLM 미호출 | `/health/ready` | ready/unready integration smoke | `DRAFT` |
| `OPS-03` | one-line subscription-free integration test | Compose `test` service | `33 passed`; clean-down proof is G4 | `PARTIAL` |
| `OPS-04` | question/document raw text 없는 timing log | structured logging | capture/source review | `DRAFT` |

## 3. 공식 필수 테스트

| ID | Mock 금지 경계 | Mock 허용 경계 | 완료 영수증 | 상태 |
|---|---|---|---|---|
| `TEST-01` Ingestion | PostgreSQL, migration, pgvector, parser, HTTP upload | deterministic embedder, unused LLM | 명령/exit/current SHA | `DRAFT` |
| `TEST-02` Retrieval | PostgreSQL `<=>`, rank, provenance/citation | deterministic embedder, no-evidence LLM fake | 명령/exit/`EXPLAIN`/current SHA | `DRAFT` |
| `TEST-03` Cache invalidation | answer cache/dependency/invalidation/transaction | scripted fake LLM, deterministic embedder | 명령/exit/row assertions/current SHA | `DRAFT` |

`docker compose down` 직후 사전 DB 명령 없이 `docker compose run --rm test`가
성공해야 한다. Test session은 기존 개발 DB state에 의존하지 않는 고유 database
또는 schema를 사용하고 DB integration test를 직렬 실행한다. 동시성은 한 test
안의 explicit parallel tasks로만 만든다.

## 4. V1 회귀 계약

| V1 동작 | V2에서 보존할 것 | V2 증거 | 상태 |
|---|---|---|---|
| PDF/TXT/MD parsing | format validation, page/offset provenance, upload limit | parser + ingestion tests | `DRAFT` |
| API schema | document/question response fields와 status mapping | API regression | `DRAFT` |
| No-evidence | LLM skip, fixed semantic response, no citation/cache | retrieval regression | `DRAFT` |
| Codex protocol | read-only/no-tool JSON-RPC, timeout/retry | existing protocol suite | `DRAFT` |
| SSE | started→delta→citation→completed/error, same request ID | stream regression + live smoke | `DRAFT` |
| Prompt boundary | untrusted evidence와 metadata attribute escape | prompt test | `DRAFT` |
| Retrobot | generated paths만 stage/auto-commit | hook regression | `DRAFT` |

SQLite/Chroma adapter tests는 PostgreSQL 대체 integration test가 존재하고 V2
composition에서 더 이상 사용하지 않을 때만 제거한다. 제거 이유와 대체 test를
현재 SHA에 연결한다.

## 5. 불합격 기준 gate

| ID | PASS 조건 | 현재 상태 |
|---|---|---|
| `DQ-01` 실행 불가 | clean checkout README DB/migration/API/readiness와 one-line test | `DRAFT` |
| `DQ-02` 하드코딩 답변 | production Codex code path + current V2 HEAD actual SSE | `DRAFT` |
| `DQ-03` vector/cache DB 없음 | 실제 pgvector retrieval + DB cache/invalidation | `DRAFT` |
| `DQ-04` 자동화 테스트 전무 | 독립 실행되는 `TEST-01`~`TEST-03` | `DRAFT` |
| `DQ-05` OSS 통째 복사 | V1 증분 Git history + dependency/license provenance | `DRAFT` |
| `DQ-06` 허위 문서 | claim별 current code/test/receipt 또는 DRAFT 명시 | `DRAFT` |
| `DQ-07` 평가 창 밖 구현 | deadline 전 의미 있는 commit/push와 remote SHA | `DRAFT` |

## 6. Gate evidence ledger

| Gate | Done-when | 상태 |
|---|---|---|
| `G1` | branch + Phase2 PRD/Trade-off/ADR/matrix, remote parity `20fa220df55ef8b1d4a4da4b9695c9824bf507ae` | `PASS` |
| `G2` | PG 17.10/pgvector 0.8.6, migration `0001`, isolated Docker test `33 passed` | `PASS` |
| `G3` | unified CorpusStore와 transaction tests | `DRAFT` |
| `G4` | 실제 PostgreSQL 기반 공식 3경로와 one-line test | `DRAFT` |
| `G5` | RAG/cache/SSE와 concurrency/race 회귀 | `DRAFT` |
| `G6` | CI와 README/Architecture/Prompt/Decision/Map 정합 | `DRAFT` |
| `G7` | clean checkout/readiness + actual Codex/SSE | `DRAFT` |
| `G8` | DQ 7개와 V1/V2 비교, merge recommendation | `DRAFT` |

## 7. Evidence 승격 규칙

- `DRAFT` → `IMPLEMENTED`: 대상 code가 current SHA에 존재하고 정적 대조가 됨
- `IMPLEMENTED` → `VERIFIED`: current SHA에서 요구 범위의 실제 command/test/
  runtime receipt가 PASS
- 확인할 수 없으면 `UNKNOWN`; 과거 SHA receipt로 현재 claim을 승격하지 않음
- fake LLM test는 actual Codex/SSE 증거가 아니며, file presence는 runtime proof가
  아님
