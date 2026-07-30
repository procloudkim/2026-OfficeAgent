# Storage and Docker Trade-off

| 항목 | 값 |
|---|---|
| 문서 상태 | `DRAFT` |
| 결정 | PostgreSQL 17.10 + pgvector 0.8.6 단일 영속 계층 |
| Docker 범위 | PostgreSQL, migration, offline integration test와 CI |
| 제외 | API/Codex containerization, Redis, ANN, 별도 vector DB |
| 증거 경계 | 운영비·성능·가용성의 정량 우위는 측정하지 않았으며 주장하지 않는다 |

## 1. 의사결정 질문

OfficeAgent는 document version, chunk/vector, answer cache, dependency와 corpus
revision을 함께 바꾼다. 선택 기준은 “vector 검색이 되는가”보다 다음 실패 비용을
얼마나 줄이는가다.

1. 부분 commit과 stale cache를 막을 수 있는가
2. 동시 요청 중 일관된 ready corpus를 보이는가
3. backup/restore와 migration 경계가 몇 개인가
4. health, log, CI에서 진단해야 할 서비스가 몇 개인가
5. 지원 수명과 patch 경로를 설명할 수 있는가
6. V1에서 API·embedding·prompt를 보존하며 전환할 수 있는가

## 2. 현재 V1에서 확인된 비용

V1은 SQLite가 document/cache/dependency를, Chroma PersistentClient가
chunk/vector를 소유한다. `IngestionService`는 SQLite metadata와 cache
invalidation을 먼저 commit한 뒤 embedding과 Chroma delete→upsert를 수행한다.
따라서 두 저장소를 묶는 transaction이 없고, 실패 창에 새 metadata와 이전·부분·
빈 vector 상태가 함께 보일 수 있다. 동시 질의가 그 창에서 다시 cache를 만들 수
있다는 한계도 V1 Architecture와 ADR-0002에 이미 기록돼 있다.

이 문제는 SQLite나 Chroma 각각의 단독 결함이 아니라 **한 제품 불변식을 두
commit 경계에 나눈 topology 비용**이다.

## 3. 공식 자료에서 확인한 운영 사실

- Chroma 공식 Python client 문서는 `PersistentClient`를 local development와
  testing 용도로 설명하며 production에는 server-backed instance를 권한다.[1]
- PostgreSQL transaction은 여러 statement를 all-or-nothing 단위로 묶고,
  commit 전 중간 상태를 다른 transaction에 숨긴다.[2]
- PostgreSQL 공식 versioning policy는 major version을 5년 지원한다. 2026-07-30
  확인 시 17의 current minor는 17.10이며 final release 예정일은
  2029-11-08이다.[3]
- `pg_dump`는 DB가 사용 중이어도 일관된 backup을 만들고 일반 reader/writer를
  막지 않는다.[4]
- pgvector는 vector를 PostgreSQL의 다른 데이터와 함께 저장하며 exact/approximate
  nearest-neighbor와 cosine distance를 제공한다.[5]
- Docker Compose는 단순 시작 순서와 readiness를 구분하며 healthcheck와
  `service_healthy`로 dependency 준비 완료를 기다릴 수 있다.[6]
- 2026-07-30 actual-host `docker manifest inspect`에서
  `pgvector/pgvector:0.8.6-pg17-bookworm`의 `linux/amd64` manifest
  `sha256:f193b9c848deb27b0e892f85ed07b53fd83ccb1feb634ba415b1cdc44411bedc`
  를 직접 확인했다.[G0 receipt](verification/2026-07-30-v2-g0.md) 이 영수증은
  tag 존재와 대상 architecture만 증명하며 “최신” 또는 성능 개선을 증명하지
  않는다.

## 4. 후보 비교

`초기 구현비`와 `V1→V2 전환비`는 이 repository 범위의 상대 판단이다.
`유지보수비`는 서비스·schema·backup·health·failure test 경계 수를 뜻하며 실제
금액이 아니다.

| 후보 | 초기 구현비 | 유지보수비 | Transaction 정합성 | 동시성 | Backup/restore·migration | Health·지원·Docker/CI | V1→V2 비용 | 판정 |
|---|---|---|---|---|---|---|---|---|
| SQLite + Chroma PersistentClient | 낮음 | 중간 | 각 store 내부만 가능; cross-store 불가 | single-host write 경쟁과 ingest/query gap을 별도 보상 | SQLite와 Chroma directory를 함께 일관되게 보존·복구해야 함 | local test에는 단순하나 Chroma 공식 production 권고와 불일치 | 없음 | V1 기준선 유지용; V2 거절 |
| PostgreSQL + Chroma Server | 중간~높음 | 높음 | PostgreSQL metadata/cache와 Chroma vector를 한 commit으로 묶지 못함 | network/service failure와 version skew 추가 | DB와 Chroma volume의 이중 migration·backup·restore | Chroma production posture는 개선되나 두 health plane과 image lifecycle 필요 | 두 adapter·복구 protocol 교체 | production 가능 후보지만 이번 불변식에는 비경제적 |
| **PostgreSQL + pgvector** | 중간 | **낮음~중간** | document/chunk/vector/cache/revision을 한 DB transaction으로 처리 | MVCC, row/advisory lock과 revision guard를 한 DB에서 검증 | 하나의 schema migration과 PostgreSQL backup/restore 경계 | 지원 중인 PostgreSQL release, 단일 DB health, pinned Compose/CI; pgvector/vendor SLA는 별도 | CorpusStore와 migration 구현 필요; API/embedding/prompt 보존 | **선택** |
| PostgreSQL + pgvector + Redis | 높음 | 높음 | source of truth는 DB에 둘 수 있으나 Redis hit/invalidation은 별도 commit | hot key와 single-flight에 유리할 수 있으나 stampede/TTL/DEL 정책 필요 | PostgreSQL 외 Redis persistence·eviction·rebuild 정책 추가 | 별도 service, port, health, patch와 장애 test 추가 | 선택안 이후 다시 cache topology 변경 | 측정된 병목 전에는 거절 |
| PostgreSQL + 전문 vector DB | 높음 | self-hosted 높음 / managed 계약 의존 | metadata/cache와 vector service 간 atomic commit 불가 | 대규모 ANN·분산 운영 기능은 강점일 수 있음 | DB와 vector cluster 각각 snapshot/restore/DR 필요 | managed support/SLA 선택 가능하나 credential·network·비용·vendor lifecycle 추가 | dual-write/outbox/reconciliation 필요 | 규모·SLA·규제 요구가 증명될 때 재검토 |

## 5. PostgreSQL+pgvector 선택 이유

### 5.1 정확성의 경제성

단일 transaction은 보상 작업, outbox/saga, cross-store reconciliation과 그 실패
테스트를 줄인다. V2는 parse/chunk/embed를 transaction 밖에서 끝낸 뒤 document
key advisory lock을 잡고 hash/version을 재확인한다. 이후 metadata, chunk/vector,
cache invalidation, revision, ready를 한 commit으로 반영한다. 실패하면 모두
rollback한다.

이는 “더 빠르다”는 주장이 아니다. **부분 상태를 수리하고 설명하는 코드와 운영
절차를 줄이는 선택**이다.

### 5.2 운영·지원의 경제성

한 DB에서 schema migration, health/readiness, SQL inspection, backup/restore와
patch policy를 관리한다. PostgreSQL 17.10은 현재 지원되는 minor이고 명시된
지원 종료 일정이 있다. pgvector는 extension package와 compatibility를 별도로
검증해야 하며 PostgreSQL 자체 지원이 extension SLA를 뜻하지는 않는다.
상용 기술지원이 필요하면 PostgreSQL/extension을 지원하는 vendor 계약을 별도
검토해야 한다. 이번 과제는 vendor SLA를 구현 또는 검증했다고 주장하지 않는다.

### 5.3 전환의 경제성

다음 V1 자산을 유지한다.

- FastAPI endpoint와 response/SSE schema
- parser, chunking과 384차원 embedding 계약
- retrieval score `1 - cosine_distance`
- retrieval/cache threshold 초기값 `0.30`/`0.95`
- Codex App Server gateway, prompt와 no-evidence 정책

교체 범위는 storage port/composition, migration, DB-backed integration test와
readiness로 제한한다. V1 runtime data 이관을 버리고 빈 V2 DB로 시작해 one-off
migration 및 dual-read 비용을 제거한다.

## 6. Docker 판단

PRD의 실행 조건은 `docker compose up` 또는 README에 명시한 한 줄 방법이고,
테스트도 README의 별도 한 줄 명령을 요구한다. V2에서 Docker를 선택한 이유는
과시가 아니라 PostgreSQL/pgvector version, migration, health와 CI integration
test를 같은 topology로 반복하기 위해서다.

### Docker가 소유하는 것

- digest-pinned PostgreSQL+pgvector DB
- `pg_isready` healthcheck와 named volume
- migration runner와 subscription-free test image
- CI의 Compose config/build/DB/migration/readiness 검증

### Docker가 소유하지 않는 것

- host의 Codex login/session
- 실제 LLM 호출과 SSE live smoke
- production deployment 또는 image publish
- `.codex`, credential, 업로드 문서/질문 데이터

따라서 API는 host에서 `uv run officeagent`로 실행한다. 이는 전체 container
수보다 인증 경계와 evaluator 재현성을 함께 최적화한 절충이다.

## 7. 선택안의 비용과 중단 기준

- PostgreSQL server와 Docker Desktop이 필요해 V1 embedded-only 경로보다 초기
  준비가 늘어난다.
- pgvector extension install/version과 384차원 schema를 migration에서 검증해야
  한다.
- exact cosine는 bounded corpus에는 단순하지만 대규모 성능을 보장하지 않는다.
- 단일 노드이며 HA, PITR 운영, 복구 훈련은 이번 범위에서 구현하지 않는다.
- 공식 3경로, rollback, concurrency, stale-cache race, clean test와 actual
  Codex/SSE 중 하나라도 실패하면 V2를 main에 merge하지 않는다.

## 8. 재검토 trigger

- corpus/query 규모 측정에서 exact search가 목표 latency를 지속적으로 초과
- PostgreSQL cache lookup이 측정된 병목이고 Redis의 별도 일관성 비용을 상쇄
- 다중 tenant, 지역 분산, HA/SLA 또는 규제상 관리형 vector service가 필요
- 운영 조직이 특정 vendor의 enterprise support 계약을 요구

그전에는 서비스 수와 진실원천을 늘리지 않는다.

## 9. 출처

1. Chroma Python Client: https://docs.trychroma.com/reference/python/client
2. PostgreSQL 17 Transactions: https://www.postgresql.org/docs/17/tutorial-transactions.html
3. PostgreSQL Versioning Policy (2026-07-30 확인): https://www.postgresql.org/support/versioning/
4. PostgreSQL 17 `pg_dump`: https://www.postgresql.org/docs/17/app-pgdump.html
5. pgvector upstream README: https://github.com/pgvector/pgvector
6. Docker Compose startup order: https://docs.docker.com/compose/how-tos/startup-order/
7. Chroma Docker deployment: https://docs.trychroma.com/deployment/docker
8. Redis cache-aside: https://redis.io/docs/latest/develop/use-cases/cache-aside/
9. Qdrant production installation: https://qdrant.tech/documentation/installation/

Local evidence는 `ARCHITECTURE.md`, ADR-0002, V1 storage code/tests와 G0 actual-host
manifest receipt다. 외부 자료는 제품 선택의 일반 특성을 뒷받침하며 이 repository의
구현 완료 증거를 대신하지 않는다.
