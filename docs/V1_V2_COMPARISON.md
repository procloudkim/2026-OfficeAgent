# V1/V2 Evidence-Bound Comparison

> **독자와 목적:** 평가자가 V1과 V2의 검증된 차이만 비교하도록 돕는 문서입니다.
> 실행 절차는 [Evaluator Manual](./EVALUATOR_MANUAL.md), 현재 제출 판정은
> [V2 Traceability](./V2_REQUIREMENTS_TRACEABILITY.md)를 우선합니다.

| 항목 | 현재 판정 |
|---|---|
| 문서 상태 | `VERIFIED` |
| V1 rollback | `29a429f16616ff63dd6ef175b870ec042be10e71` |
| 최초 V2 evidence 기준선 | `4a596b008cfc0470c1f2d30ad59ce5f122d9995d` |
| 제출 권고 | `YES` — V2 branch 제출 가능; `main` merge는 별도 승인 전 수행하지 않음 |

이 문서는 성능 benchmark가 아니라 사실과 실패 경계를 비교합니다. 측정하지 않은
속도, 비용, 가용성이나 복구 우위는 주장하지 않습니다. 요구사항별 최신 상태는
[V2 Traceability](V2_REQUIREMENTS_TRACEABILITY.md)가 소유합니다.

## 1. 증거 경계

| lane | 관측된 증거 | 해석 |
|---|---|---|
| V1 | implementation `bd08426`에서 27 tests PASS; final `29a429f`는 후속 Retrobot Markdown commit | `HISTORIC` rollback 기준선 |
| V1 live | ancestor `e838cc7`에서 actual Codex/Uvicorn SSE 17 deltas | `HISTORIC`; V2 freshness로 재사용하지 않음 |
| V2 초기 | `5ae7cca` exact archive에서 Docker 48 tests PASS | `HISTORIC` external observation; current evidence로 재사용하지 않음 |
| V2 source evidence | current-SHA 8-profile bundle: Docker core, clean launch, readiness, actual Codex/SSE, 문서·출처·Retrobot, remote parity | 제출 evidence `VERIFIED`; 실행 수치는 V2 Traceability 참조 |

## 2. 구조와 운영 비교

| 비교 축 | V1 SQLite + Chroma | V2 PostgreSQL + pgvector | 증거 및 한계 |
|---|---|---|---|
| Topology | API·Codex host, metadata/cache SQLite, vector Chroma | API·Codex host, document/vector/cache/revision PostgreSQL | V2는 API/Codex containerization이 아님 |
| Transaction | 두 store mutation을 application이 순차 조정 | document/chunk/vector/cache/revision을 한 DB transaction으로 commit | V2 rollback·concurrency를 real DB test로 검사 |
| Cache | exact/similar와 corpus/dependency invalidation | 세 config version current-match, soft invalidation, revision guard 추가 | hit-rate 개선은 측정하지 않음 |
| Docker | 기본 실행 경로에서 제외 | DB, migration, subscription-free integration test | CI는 `PLANNED`; credential은 container에 전달하지 않음 |
| Migration | SQLite/Chroma 영속 경계를 따로 관리 | forward-only SQL, checksum ledger, advisory lock | V1 data 자동 이관·down migration은 비목표 |
| 유지보수 | infra는 작지만 cross-store 일관성 책임이 application에 존재 | 운영 DB는 늘지만 persistence 진실원천은 하나 | 비용 우위는 정량 측정하지 않음 |

## 3. 검증된 변화와 잔여 한계

V2 final bundle은 실제 PostgreSQL/pgvector transaction, migration, parser,
retrieval, cache invalidation, readiness와 detached clean launch를 확인했습니다.
별도 live profile은 Codex CLI 0.146.0과 Uvicorn HTTP/SSE에서 두 miss의 복수
plaintext delta, structured answer validation, exact replay, document update 뒤 miss와
versioned citation을 확인했습니다.
문서 claim registry, template 증분 provenance, Retrobot runtime과 remote exact parity도
같은 제출 audit에 포함됩니다.

남은 한계는 다음과 같습니다.

- `PROC-04`: 8월 2일 작업 commit의 당일 push 증거가 없어 `PARTIAL`; 불합격 항목은
  아니지만 Track C 과정 증거 위험
- CI, structured timing log, backup/restore drill과 한국어 retrieval gold set
- answer-level entailment verifier, 동일 miss single-flight, HA/PITR
- 별도 WSL Docker Engine과 `linux/amd64` 이외 architecture는 `UNKNOWN`

## 4. 제출 판정

V2 branch 제출 recommendation은 `YES`입니다. 이는 일곱 불합격 사유가 발동하지
않고 실행·테스트·문서·출처·remote evidence가 제출 가능한 상태라는 뜻입니다.
`REQ-09`의 structured output은 검증됐지만, 이 제출 권고가 모든 process 항목
만점을 뜻하지는 않습니다. `PROC-04`의 과거 증거 한계는 그대로 공개합니다.

DEC-010·011·013은 P0-08에 따라 `main` supersession에는 `PROPOSED`, V2 branch 구현
evidence에는 `VERIFIED`입니다. DEC-012는 V2 branch에서 DEC-013이 supersede했습니다.
실제 `main` merge는 수행하지 않았으며, 최종 audit
뒤에도 사용자 승인 없이는 merge하지 않습니다.

관련 근거는 [Storage/Docker Trade-off](TRADEOFF_STORAGE_AND_DOCKER.md),
[Decision Log](DECISION_LOG.md), [ADR-0002](adr/ADR-0002-storage-retrieval-and-cache.md),
[ADR-0004](adr/ADR-0004-postgresql-pgvector-storage.md),
[ADR-0005](adr/ADR-0005-codex-structured-output-streaming.md)에 남아 있습니다.
