# V1/V2 Evidence-Bound Comparison

| 항목 | 현재 판정 |
|---|---|
| 문서 상태 | `PARTIAL` |
| V1 rollback | final HEAD `29a429f16616ff63dd6ef175b870ec042be10e71` |
| V2 committed baseline | product `67a592be6e10dc5434025073c7a7af1a0e26c027`, Retrobot `5ae7ccacf8b383ecf51360423697cc931b3853a3` |
| V2 현재 관측 | dirty-worktree Docker `127 passed in 10.07s`; host `109 passed, 18 expected skips in 35.01s`; readiness-only HTTP 200 |
| G8 merge recommendation | `NO` — final-SHA·clean checkout·actual Codex/SSE·remote parity pending |

이 문서는 성능 benchmark가 아니라 사실과 실패 경계를 비교합니다. 측정하지
않은 속도, 비용, 가용성이나 백업 복구 우위는 주장하지 않습니다. 요구사항별
변동 상태는 [V2 Traceability](V2_REQUIREMENTS_TRACEABILITY.md)가 소유합니다.

## 1. 증거 경계

| lane | 관측된 증거 | 이 문서의 해석 |
|---|---|---|
| V1 | implementation `bd08426`에서 27 tests PASS; final `29a429f`는 후속 Retrobot Markdown commit | V1 offline rollback 기준선 |
| V1 live | ancestor `e838cc7`에서 actual Codex App Server·Uvicorn SSE 17 deltas | 역사적 live 증거이며 V2 freshness 증거가 아님 |
| V2 committed | `5ae7cca` exact archive에서 Docker suite `48 passed in 7.39s` | committed PostgreSQL/pgvector baseline의 역사적 영수증 |
| V2 worktree | Docker `127 passed in 10.07s`; host `109 passed, 18 expected skips in 35.01s`; readiness-only HTTP 200 | disposable-DB/G7 계약을 포함하지만 final-SHA 및 actual-Codex receipt가 아님 |
| V2 final | 커밋된 최종 SHA의 clean run, actual Codex/SSE, 원격 parity가 없음 | 전체 상태는 `PARTIAL` |

상세 V1 영수증은 [V1 Traceability](REQUIREMENTS_TRACEABILITY.md), V2 gate는
[V2 Traceability](V2_REQUIREMENTS_TRACEABILITY.md)를 따릅니다.

## 2. 구조와 운영 비교

| 비교 축 | V1 SQLite + Chroma | V2 PostgreSQL + pgvector | 증거 및 한계 |
|---|---|---|---|
| Topology | API·Codex는 host, metadata/cache는 SQLite, vector는 Chroma PersistentClient | API·Codex는 host, document/vector/cache/revision은 Docker PostgreSQL | V2는 API/Codex containerization이 아님 |
| Transaction·failure | SQLite commit과 Chroma delete/upsert를 application이 순차 조정 | parse/chunk/embed 후 document/chunk/vector/cache/revision을 한 DB transaction으로 commit | V1 cross-store atomicity는 보장하지 않음; V2 rollback·concurrency는 DB test로 검사 |
| Cache policy | normalized exact 우선, cosine `0.95` similar, corpus/dependency invalidation | exact/similar에 prompt·embedding·retrieval version current-match, soft invalidation, corpus revision guard 추가 | cache hit rate 개선은 측정하지 않음 |
| Docker 역할 | 기본 실행 경로에서 제외 | DB, migration, subscription-free integration test만 소유 | CI workflow는 `PLANNED`; test image에 Codex credential을 전달하지 않음 |
| 운영 대상 | host Python·Node·Codex와 로컬 SQLite/Chroma data | host Python·Node·Codex + PostgreSQL service·extension·volume·migration | V2는 DB 운영 대상이 늘어남 |
| Migration·backup | 두 영속 경계를 따로 다룸 | forward-only SQL, checksum ledger, advisory lock; 하나의 DB 논리 경계 | V1 data 자동 이관·down migration·backup/restore drill은 비목표 또는 미검증 |
| 구현·유지보수 | infra가 작지만 두 adapter의 일관성 책임이 application에 있음 | migration, Compose, readiness, test runner가 추가되지만 persistence port는 하나 | 유지보수 비용 우위는 정량 측정하지 않음 |

## 3. 검증된 변화와 잔여 한계

V2 committed baseline에서 실제 PostgreSQL/pgvector, migration, parser,
transaction, retrieval, cache invalidation은 48-test exact-archive suite로 관측됐습니다.
현재 worktree의 readiness·격리·disposable-DB/G7 계약을 포함한 127-test Docker
PASS를 관측했습니다. 하지만 결과는 커밋에 고정되지 않았고 actual Codex를
호출하지 않아 V2 final 수용 증거로 승격하지 않습니다.

남은 한계는 다음과 같습니다.

- final committed SHA의 공식 Docker suite와 clean-checkout 제품 실행
- final SHA의 actual Codex App Server incremental SSE, citation, miss→hit,
  document update→miss
- final local·tracking·live remote SHA parity와 `DQ-01`~`DQ-07` 감사
- CI, structured timing log, backup/restore drill, 한국어 retrieval gold set
- answer-level entailment verifier, 동일 miss single-flight, HA/PITR

## 4. G8 판정

현재 merge recommendation은 `NO`입니다. 이는 공식 불합격 조항이 발동했다는
판정이 아니라, final-SHA·clean/live·remote 증거가 아직 없다는 정지선입니다.
`DEC-010`~`DEC-012`는 `PROPOSED`를 유지하고 V1 결정과 ADR-0002를
supersede하지 않습니다. 위 잔여 gate가 모두 닫혀도 사용자 승인 전에는
merge하지 않습니다.

관련 결정 근거는 [Storage/Docker Trade-off](TRADEOFF_STORAGE_AND_DOCKER.md),
[Decision Log](DECISION_LOG.md), [ADR-0002](adr/ADR-0002-storage-retrieval-and-cache.md),
[ADR-0004](adr/ADR-0004-postgresql-pgvector-storage.md)에 남아 있습니다.
