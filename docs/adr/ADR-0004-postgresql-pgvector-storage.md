# ADR-0004: PostgreSQL+pgvector transactional CorpusStore

| 항목 | 내용 |
|---|---|
| Status | `main` supersession은 `PROPOSED` (P0-08); V2 branch 구현 evidence는 `VERIFIED` |
| Date | 2026-07-30 |
| Baseline | V1 `main` rollback baseline; exact locator는 [V2 Traceability](../V2_REQUIREMENTS_TRACEABILITY.md)가 소유 |
| Target | PostgreSQL 17.10 + pgvector 0.8.6 |

## Context

V1은 SQLite가 document/cache/dependency를, Chroma PersistentClient가 chunk와
embedding을 소유한다. Metadata/cache commit과 Chroma delete→upsert 사이에
분산 transaction이 없으므로 실패나 동시 요청에서 부분 상태와 stale recache가
가능하다. V1의 보수적 invalidation/retry는 순차 failure window를 줄이지만
cross-store atomicity와 concurrency safety를 증명하지 않는다.

Chroma 공식 문서는 PersistentClient를 local development/testing 용도로 두고
production에는 server-backed Chroma를 권한다. Chroma Server로 전환해도
PostgreSQL metadata/cache와 vector service의 commit, backup, migration과 health
경계는 둘로 남는다.

## Decision

V2는 PostgreSQL 17.10과 pgvector 0.8.6을 유일한 영속 계층으로 사용하고 기존
`MetadataStore`/`VectorStore` 분할 mutation을 하나의 `CorpusStore` 또는
unit-of-work port로 교체한다.

G2의 Compose/schema/migration 위에 G3 `CorpusStore`, API composition과 P0
offline path를 구현했습니다. 아래 수용 테스트와 current-SHA evidence는 모두
충족됐지만 P0-08 계약에 따라 `main` supersession은 `PROPOSED`로 유지합니다.
따라서 ADR-0002도 V1 rollback 정본으로 보존하며, V2 branch evidence
`VERIFIED`와 decision lifecycle을 혼합하지 않습니다.

## G2 pin-point: Why → What → How → Trade-off → 배운 점

- **Why**: 두 저장소의 partial state와 무효화 race를 G3의 한 transaction으로
  해결하기 전에, schema와 migration failure boundary를 재현 가능하게 만든다.
- **What**: PostgreSQL+pgvector, Psycopg 3, numbered SQL과 digest-pinned Compose를
  G2 infrastructure로 채택했다.
- **How**: checksum ledger, forward-prefix 검사, advisory lock, migration별 rollback,
  migration test별 disposable database로 `0001` apply/re-run/drift/history/failure/concurrency를
  검증했다. 전체 suite runner는 suite당 temporary database 하나를 사용한다.
- **Trade-off**: Docker와 migration 운영비가 추가됩니다. Clean-clone과 actual
  Codex/SSE는 검증했지만 HA/PITR와 backup/restore drill은 제공하지 않습니다.
- **배운 점**: 측정하지 않은 성능 우위보다 진실원천과 failure surface를 줄이는
  선택이 이번 유지보수 경제성의 더 강한 증거다.

## Transaction invariant

Parsing, chunking과 embedding은 DB transaction 밖에서 수행한다. 쓰기 직전에
normalized document key에 transaction-scoped advisory lock을 획득하고 current
hash/version을 재확인한다. 다음 일곱 변경은 한 transaction이다.

1. document hash/version 재확인
2. document metadata 반영
3. 이전 chunk/vector 교체
4. 새 chunk/vector 저장
5. 관련 cache soft invalidation
6. corpus revision 증가
7. ready 상태 반영

하나라도 실패하면 모두 rollback하며 직전 ready corpus가 계속 조회돼야 한다.
LLM 호출과 SSE 중에는 transaction, row lock 또는 advisory lock을 보유하지 않는다.

## Data ownership

| Table | 책임 |
|---|---|
| `documents` | stable key/ID, filename/MIME, content hash, version, ready와 timestamps |
| `document_chunks` | document/version, chunk provenance/text와 `vector(384)` embedding |
| `corpus_state` | cache-write race guard인 singleton revision |
| `answer_cache` | normalized question/vector, answer/citations, config versions, active/invalidation |
| `cache_dependencies` | top-k candidate cache와 document/version 관계 |
| `schema_migrations` | immutable forward-only migration version/checksum |

`answer_cache` hit는 active row와 prompt/embedding/retrieval version 일치로 판단한다.
Global revision equality를 hit의 두 번째 진실원천으로 사용하지 않는다. Revision은
LLM 실행 중 corpus가 바뀐 경우 completed answer의 cache write를 건너뛰는
race guard다.


## P0 addendum — cache/input/structured-output boundary `VERIFIED`

- `answer_cache`의 `prompt_version`, `embedding_version`, `retrieval_version`은
  cache identity의 일부다. active exact/similar hit는 세 값이 현재 설정과 모두
  같을 때만 허용한다. `PROMPT_TEMPLATE_VERSION`의 단일 정본은 `prompts.py`다.
- question, evidence text와 metadata는 JSON untrusted-data envelope로
  직렬화했다. delimiter breakout 회귀는 구조적 parse/round-trip을 증명하지만
  injection 의미 안전성이나 answer entailment를 증명하지 않는다.
- citation은 server-generated retrieval provenance다. model answer의 문장별 근거
  검증이나 model-use subset의 verified claim이 아니다.
- V2 offline integration에서 double은 LLM과 embedding만 허용한다. PostgreSQL,
  migration, pgvector, parser와 cache transaction은 실제 구현을 쓰고 HTTP/SSE는
  in-process FastAPI ASGI 계약을 검증한다.
- Compose test container에는 Codex/Node/API key/.codex mount를 넣지 않으며 이
  경계의 runtime assertion도 G3/G4 evidence에 포함한다.
- 후속 [ADR-0005](ADR-0005-codex-structured-output-streaming.md)가 structured-output
  spike의 protocol/API/cache/live 조건을 충족했습니다. 한 필드 schema를
  incremental plaintext로 projection하며 server-generated provenance는 유지합니다.

### P0 pin-point

- **Why**: configuration drift와 delimiter ambiguity가 cache correctness와 prompt
  boundary를 약화시킬 수 있다.
- **What**: three-version cache identity와 JSON envelope를 구현했다.
- **How**: real PostgreSQL integration과 breakout/version-mismatch regression을
  자동화했다. 실행 evidence는 V2 Traceability가 소유한다.
- **Trade-off**: cache reuse가 감소하고 implementation/test surface가 늘어난다.
- **배운 점**: schema field 존재는 policy enforcement 증거가 아니며 lookup/write와
  current-SHA integration을 함께 검증해야 한다.

## Retrieval and cache decision

- `vector(384)` exact cosine distance를 사용한다. V1 embedding dimension을
  유지한다.
- application score는 `1 - cosine_distance`로 고정한다.
- 초기 retrieval/cache threshold `0.30`/`0.95`를 유지하되 최적값이라고
  주장하지 않는다.
- exact cache 다음 similar cache 순서다.
- `CREATED`는 전체 active cache, `UPDATED`는 이전 dependency와 새 chunk
  similarity 관련 cache의 합집합을 soft invalidation한다.
- configuration mismatch로 관련성 판정이 불안전하면 전체 active cache를
  invalidation한다.
- `UNCHANGED + ready`는 revision/chunk/cache no-op이다.

## Migration decision

Migration은 numbered forward-only SQL과 application runner로 관리한다.

- advisory lock으로 동시 실행 직렬화
- version/checksum/applied timestamp 기록
- 이미 적용된 파일 checksum 변경 시 fail closed
- 빈 DB 전체 적용과 재실행 idempotency
- 현재 migration 실패 시 해당 transaction rollback
- 적용된 migration 수정 금지; 새 번호로 forward-fix

V1 SQLite/Chroma runtime data는 이관하지 않는다. 빈 V2 DB를 사용하고 rollback은
V1 branch 또는 forward-fix로 수행한다.

## Docker boundary

Docker는 PostgreSQL/pgvector, migration과 offline integration test를 소유한다.
CI workflow는 `PLANNED`이며 API와 실제 Codex App Server는 host에서 실행한다.
Compose는 target
architecture manifest digest, loopback DB port, named volume, `pg_isready`
healthcheck와 `service_healthy` dependency를 사용한다.

이 경계는 host Codex credential을 container에 mount하지 않으면서 DB topology와
test command를 재현한다. Image publish, 외부 deployment와 production CD는 이
결정의 범위가 아니다.

## Alternatives considered

### Keep SQLite + Chroma PersistentClient

초기 구현은 가장 작지만 V1의 두 commit/backup/health 경계를 유지하고 Chroma의
공식 production 권고와 맞지 않는다. V1 rollback 기준선으로만 보존한다.

### PostgreSQL + Chroma Server

Chroma의 production posture는 개선하지만 vector와 metadata/cache의 atomic
mutation은 해결하지 못한다. 두 service의 migration, backup, readiness와
reconciliation 비용이 이번 corpus 규모에서 정당화되지 않는다.

### PostgreSQL + pgvector + Redis

Measured hot-cache 병목이나 single-flight 요구가 없고 Redis invalidation/TTL/
failure surface가 추가된다. PostgreSQL cache로 먼저 정확성 invariant를 증명한다.

### Professional vector DB

분산 ANN, managed SLA 또는 규제 요구가 증명될 때 유효할 수 있다. 현재는 별도
service, credential, network, snapshot/DR와 dual-write protocol이 과도하다.

## Consequences

### 검증된 효과와 미검증 운영 범위

- document/vector/cache/revision의 한 transaction과 전체 rollback
- DB, migration, backup/restore, health와 CI 운영 대상 감소
- SQL로 invalidation 이력과 current state를 함께 관찰
- V1 API, parser, embedding, prompt와 Codex/SSE 계약 보존

### Costs and limits

- Docker/PostgreSQL 준비와 migration runner가 새로 필요하다.
- pgvector extension package/version compatibility를 별도로 고정·검증한다.
- exact search는 단순성을 선택하며 대규모 latency를 보장하지 않는다.
- 단일 노드이고 HA/PITR 운영·복구 훈련을 구현하지 않는다.
- PostgreSQL project support가 pgvector 또는 vendor SLA를 자동 제공하지 않는다.

## Acceptance evidence

다음 현재-SHA 증거를 모두 확인했습니다. 이는 implementation acceptance를
증명하지만 P0-08이 고정한 `PROPOSED` decision lifecycle을 자동 변경하지 않습니다.

- 실제 PostgreSQL+pgvector 기반 `TEST-01`~`TEST-03`
- write failure 전체 rollback과 이전 ready corpus 보존/retry
- 동일/서로 다른 document concurrency와 corpus revision 일관성
- LLM 중 corpus 변경 시 stale cache save skip
- migration empty DB/re-run/checksum mismatch/failure rollback
- `/health/ready`의 DB/migration/vector 확인
- clean `docker compose run --build --rm test`
- current V2 HEAD의 실제 Codex App Server incremental SSE smoke
- README/Architecture/Traceability와 code claim parity

## References

- [Storage/Docker Trade-off](../TRADEOFF_STORAGE_AND_DOCKER.md)
- [V2 Phase 2 PRD](../PRD_PHASE2.md)
- [V1 ADR-0002](ADR-0002-storage-retrieval-and-cache.md)
- PostgreSQL transactions: https://www.postgresql.org/docs/17/tutorial-transactions.html
- PostgreSQL versioning: https://www.postgresql.org/support/versioning/
- Chroma client guidance: https://docs.trychroma.com/reference/python/client
- pgvector upstream: https://github.com/pgvector/pgvector
