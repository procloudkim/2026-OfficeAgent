# OfficeAgent V2 Phase 2 PRD

> **문서 역할:** V2를 시작할 때 합의한 설계 목표와 중단 기준을 보존하는 역사적
> 계약입니다. 현재 구현 상태나 실행 방법을 판단할 때는
> [V2 Traceability](./V2_REQUIREMENTS_TRACEABILITY.md)와
> [Evaluator Manual](./EVALUATOR_MANUAL.md)을 사용하십시오.

| 항목 | 값 |
|---|---|
| 설계 수명주기 | `BASELINE` — 구현·검증 상태는 V2 Traceability가 소유 |
| 기준선 | V1 `main` rollback baseline; exact locator는 V2 Traceability가 소유 |
| 작업 브랜치 | `feat/v2-postgres-pgvector-compose` |
| 목표 | PostgreSQL+pgvector 단일 영속 계층과 재현 가능한 DB/테스트 실행 경계 |
| 증거 경계 | 구현·완료 주장은 [V2 Traceability](V2_REQUIREMENTS_TRACEABILITY.md)의 evidence에만 연결한다 |

## 1. 문제와 목표

V1은 PDF/TXT/Markdown 수집, Chroma 검색, SQLite 답변 캐시, 실제 Codex App
Server SSE를 구현했다. 그러나 SQLite metadata/cache와 Chroma vector가 서로 다른
영속 계층이라 document metadata commit, cache invalidation, vector delete/upsert를
한 transaction으로 묶을 수 없다. 실패 또는 동시 요청 중 이전·부분·빈 vector
상태와 새 metadata가 함께 보일 수 있고, 그 창에서 stale cache가 다시 생성될 수
있다.

V2의 목표는 제품 API와 Codex host 경계를 유지하면서 다음 상태를 한
PostgreSQL transaction으로 관리하는 것이다.

- document hash/version/ready 상태
- chunk text/provenance와 384차원 embedding
- answer cache와 document/version dependency
- cache invalidation 이력
- corpus revision
- migration version/checksum

완료 판단은 파일 존재가 아니라 실제 PostgreSQL+pgvector 통합 테스트, clean
실행, 현재 V2 HEAD의 실제 Codex/SSE 영수증으로 한다.

## 2. 사용자·평가자 시나리오

### 2.1 문서 수집

1. 사용자가 PDF, TXT 또는 Markdown 파일을 업로드한다.
2. 서버는 파일을 파싱·청킹하고 transaction 밖에서 embedding을 계산한다.
3. 쓰기 직전 stable document key에 transaction-scoped advisory lock을 잡고 현재
   hash/version을 재확인한다.
4. document, chunk/vector, cache invalidation, corpus revision, ready 상태를 한
   transaction으로 반영한다.
5. 일부 단계가 실패하면 전체를 rollback하고 직전 ready corpus를 유지한다.

### 2.2 검색·답변

1. exact cache를 먼저, 없으면 cosine `0.95` 이상 similar cache를 찾는다.
2. cache miss면 pgvector exact cosine 검색으로 top-k candidate를 구한다.
3. application score는 `1 - cosine_distance`로 통일하고 기본 threshold `0.30`을
   적용한다.
4. 근거가 없으면 LLM을 호출하지 않고 명시적 `no_evidence` 응답을 반환한다.
5. 근거가 있으면 실제 Codex App Server delta를 HTTP SSE로 전달한다.
6. citation은 모델 문자열이 아니라 검색 chunk의 filename, document version,
   page 또는 offset으로 서버가 조립한다.

### 2.3 안전한 cache 저장

Retrieval 시작 시 corpus revision과 candidate document/version snapshot을 잡되,
LLM 호출·SSE 중에는 DB transaction이나 lock을 유지하지 않는다. 완료 시 현재
revision이 시작 revision과 같을 때만 cache를 저장한다. 달라졌다면 답변은
완료하되 `cache_written=false`, `cache_skip_reason=corpus_changed`를 노출한다.

## 3. 기능 요구사항

### 3.1 호환성 유지

- 기존 endpoint를 유지한다.
  - `GET /health/live`
  - `POST /v1/documents`
  - `POST /v1/questions`
  - `POST /v1/questions/stream`
- `GET /health/ready`를 추가해 DB 연결, migration head, vector extension을
  확인한다. Codex 로그인이나 실제 LLM은 readiness 조건이 아니다.
- 문서 수집 응답의 document ID/hash/version/change/chunk count/cache invalidation
  metadata를 유지한다.
- 답변과 SSE의 `request_id`, `cache_hit`, `cache_match`, citation과 error event
  계약을 유지한다.

### 3.2 PostgreSQL schema

| 대상 | 최소 책임 |
|---|---|
| `documents` | stable ID/key, filename, MIME, hash, version, ready, timestamps |
| `document_chunks` | document/version FK, chunk index/text, page/offset, `vector(384)` |
| `corpus_state` | singleton revision |
| `answer_cache` | normalized question/vector, answer/citations, config versions, revision, active/invalidation |
| `cache_dependencies` | cache와 document/version candidate dependency |
| `schema_migrations` | immutable migration version/checksum/applied timestamp |

### 3.3 Ingestion 상태

- `CREATED`: 새 ready corpus를 저장하고 corpus 전체 active cache를 soft
  invalidation한다.
- `UPDATED`: version을 증가시키고 이전 dependency 또는 새 chunk와 cache 질문
  vector의 관련성 기준에 해당하는 cache를 무효화한다.
- `UNCHANGED + ready`: revision, chunk와 cache를 바꾸지 않는 no-op이다.
- embedding/retrieval configuration version이 달라 안전한 관련성 판정을 할 수
  없으면 update 시 전체 active cache를 무효화한다.
- 무효화 row는 삭제하지 않고 `active=false`, timestamp, reason을 남긴다.

### 3.4 Migration

- forward-only SQL 파일을 순서대로 적용한다.
- migration advisory lock으로 동시 실행을 직렬화한다.
- 적용된 파일 checksum이 바뀌면 실패한다.
- 빈 DB 적용, 재실행 idempotency, 현재 migration 실패 rollback을 검증한다.
- V1 SQLite/Chroma runtime data는 이관하지 않고 빈 V2 DB로 시작한다.

### 3.5 Docker와 CI

- PostgreSQL/pgvector, migration과 offline integration test를 Docker 책임으로
  둔다. Hosted CI는 이 재현 경계를 그대로 실행한다.
- API와 실제 Codex App Server는 host에서 실행한다.
- Compose DB는 loopback-only port, named volume, `pg_isready` healthcheck를
  사용한다.
- `docker compose run --build --rm test` 한 줄이 DB readiness, migration, subscription-
  free pytest를 모두 수행한다.
- `quality-gates`는 lock check, Ruff, offline test, Compose config/build,
  DB/migration, integration test, readiness와 evaluator 문서 검사를 수행한다.
  source 구현과 실제 candidate SHA의 hosted check-run 증거를 구분하며 image
  publish, 실제 Codex와 외부 deployment는 비목표다.

### 3.6 관찰 가능성과 개인정보 경계

Structured timing receipt는 구현했다. API가 만든 UUID `request_id`,
operation/outcome, 실행된 parse/embed/persist/cache lookup/retrieve/LLM/cache write와
total duration, 제한된 cache/answer path 및 오류 type만 JSON schema v1로 구성한다.
LLM stage는 upstream delta 대기만, total은 pipeline wall time을 뜻한다. 업로드
filename·문서 원문, 질문, prompt, answer, citation, DSN, credential과 exception
message는 payload에 넣지 않는다. 기본 logger 전달은 best-effort라 receipt 유실
가능성이 있으며 benchmark, SLO, 분산 trace 또는 영구 보관 구현이 아니다.

## 4. 자동화 수용 조건

| ID | 수용 조건 | 현재 상태 |
|---|---|---|
| `TEST-01` | in-process FastAPI ASGI TXT/PDF upload → parser → ready document와 pgvector chunk 저장, revision 증가, same-byte no-op | `VERIFIED` |
| `TEST-02` | 한국어 3문서 fixture에서 실제 `<=>` query top-k/citation/score, no-evidence LLM skip | `VERIFIED` |
| `TEST-03` | exact/similar hit, CREATED/UPDATED/UNCHANGED, dependency·similarity invalidation과 config fallback | `VERIFIED` |
| `TX-ROLLBACK` | chunk/vector 실패 시 document/cache/revision까지 전체 rollback, 이전 ready corpus 보존 | `VERIFIED` |
| `TX-CONCURRENCY` | 동일 문서 직렬화, 다른 문서 revision 일관성 | `VERIFIED` |
| `CACHE-RACE` | LLM 중 corpus 변경 시 stale answer cache 저장 방지 | `VERIFIED` |
| `MIGRATION` | empty DB, 재실행, checksum mismatch, 실패 rollback | `VERIFIED` |
| `SSE` | 첫 delta가 completed보다 먼저 전달되고 현재 V2 HEAD에서 실제 Codex live smoke | `VERIFIED` |
| `CLEAN-RUN` | clean checkout에서 README DB/migration/API/test/readiness 절차 성공 | `VERIFIED` |
| `TIMING` | V1/V2 ingestion과 QA의 실행 stage·terminal path receipt를 process 안에서 한 번 구성 | `PARTIAL`; best-effort sink와 current-SHA hosted 재검증은 별도 |

## 5. 비목표

- Redis, Qdrant/OpenSearch 또는 별도 vector DB
- HNSW/IVFFlat ANN index와 성능 튜닝
- Kubernetes, 다중 노드 HA와 외부 배포
- OCR, 사용자 인증, 문서 삭제 API
- V1 runtime data 자동 migration
- production fake LLM 또는 동일 cache miss single-flight
- 측정하지 않은 속도·비용·가용성 우위 주장

## 6. 릴리스·중단 경계

- V1 `main`을 rollback 기준선으로 보존한다.
- 공식 세 경로와 transaction rollback이 실패하면 V2 merge 준비를 중단한다.
- clean run, 실제 Codex/SSE, 문서–코드 정합성 중 하나라도 실패하면 V2를
  `main`에 merge하지 않는다.
- 최종 감사 뒤에도 사용자 승인 없이 `main`에 merge하지 않는다.

## 7. 결정 상태

PostgreSQL 17.10 + pgvector 0.8.6, exact cosine, 빈 V2 DB, host Codex와
DB/test-only Docker 범위는 구현·검증됐습니다. DEC-010·011·013은 P0-08 계약에 따라
`main` supersession에 대해 `PROPOSED`로 유지하며, V2 branch 구현 evidence의
`VERIFIED` 상태와 구분합니다. DEC-012의 structured-output 보류 조건은 DEC-013과
ADR-0005의 protocol/API/live 검증으로 V2 branch에서 supersede했습니다.
각 claim의 최신 상태는 `docs/V2_REQUIREMENTS_TRACEABILITY.md`만 관리합니다.
