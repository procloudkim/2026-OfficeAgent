# ADR-0004: PostgreSQL+pgvector transactional CorpusStore

| 항목 | 내용 |
|---|---|
| Status | `Proposed — DRAFT` |
| Date | 2026-07-30 |
| Baseline | V1 `29a429f16616ff63dd6ef175b870ec042be10e71` |
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

이 결정은 아직 구현되지 않았다. 아래 수용 테스트와 current-SHA evidence가
모두 생길 때만 Status를 `Accepted — VERIFIED`로 전환하고 ADR-0002의 V1 storage
결정을 `SUPERSEDED by ADR-0004`로 표시한다. 그전까지 ADR-0002는 V1 정본이다.

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

Docker는 PostgreSQL/pgvector, migration, offline integration test와 CI만
소유한다. API와 실제 Codex App Server는 host에서 실행한다. Compose는 target
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

### Expected benefits to verify

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

## Acceptance evidence required

Status 승격에는 다음 현재-SHA 증거가 모두 필요하다.

- 실제 PostgreSQL+pgvector 기반 `TEST-01`~`TEST-03`
- write failure 전체 rollback과 이전 ready corpus 보존/retry
- 동일/서로 다른 document concurrency와 corpus revision 일관성
- LLM 중 corpus 변경 시 stale cache save skip
- migration empty DB/re-run/checksum mismatch/failure rollback
- `/health/ready`의 DB/migration/vector 확인
- clean `docker compose run --rm test`
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
