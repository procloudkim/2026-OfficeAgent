# V2 Requirements Traceability

| 항목 | 값 |
|---|---|
| 문서 상태 | `PARTIAL` |
| 기준선 | V1 `29a429f16616ff63dd6ef175b870ec042be10e71` |
| 대상 브랜치 | `feat/v2-postgres-pgvector-compose` |
| 역사적 구현 기준선 | 제품 `67a592be6e10dc5434025073c7a7af1a0e26c027`, Retrobot `5ae7ccacf8b383ecf51360423697cc931b3853a3` |
| 역사적 Git 관측 | 2026-08-02 local HEAD = tracking ref = live remote `5ae7cca` |
| 역사적 runtime 영수증 | 2026-07-31 V2 `5ae7cca` exact archive에서 `docker compose run --build --rm test` → `48 passed in 7.39s`; 직접 관측했지만 receipt registry에는 미등록 |
| 현재 worktree 관측 | 2026-08-02 Docker `127 passed in 10.07s`; host `109 passed, 18 expected skips in 35.01s`; readiness-only HTTP 200; final-SHA receipt는 아님 |
| 미충족 | 최종 변경 SHA의 공식 Docker suite·real-DB readiness, clean checkout, actual Codex/SSE, 최종 `SUB-01` push parity |

V2의 committed baseline과 exact-archive Docker 영수증은 존재합니다. 최신
readiness·격리·disposable-DB/G7 계약은 dirty worktree suite에서 관측됐지만
커밋된 final SHA로 승격하지 않습니다. 새 변경 SHA의 Docker 실행과 실제
Codex/SSE 영수증이 생기기 전 전체 상태는 `PARTIAL`이며, V1의 과거 test/live
smoke나 이전 V2 영수증을 재사용하지 않습니다.

상태:

- `VERIFIED`: 파일·Git·test·공식 원문으로 직접 확인
- `PARTIAL`: 일부만 구현 또는 검증
- `MISSING`: 요구되지만 산출물이 없음
- `BLOCKED`: 외부 권한·정책·환경 때문에 실행 불가
- `UNKNOWN`: 확인 증거가 없음
- `PLANNED`: 선택했지만 구현 증거가 없음

## 1. 제품 요구사항

| 규칙 | 구현·테스트 근거 | 상태 |
|---|---|---|
| `REQ-01`~`REQ-03` 수집 | FastAPI multipart, 실제 TXT/PDF parser, transactional ingestion, ready/version/same-byte assertions | `PARTIAL` |
| `REQ-04` RAG | PostgreSQL pgvector `<=>`, 한국어 3문서 fixture, top citation | `PARTIAL` |
| `REQ-05` 답변 생성 | production bootstrap은 Codex gateway, offline은 scripted LLM double만 사용; V2 live runtime은 `UNKNOWN` | `PARTIAL` |
| `REQ-06` 출처 | server-side chunk→citation mapper; G7 offline contract는 create/update version의 filename/chunk/page/offset citation을 강제, actual live pending | `PARTIAL` |
| `REQ-07` 무근거 | zero/threshold miss에서 LLM·cache skip | `PARTIAL` |
| `REQ-08` streaming | in-process ASGI 계약; G7 harness는 두 miss의 복수 delta와 exact replay를 구분, actual live는 `UNKNOWN` | `PARTIAL` |
| `REQ-09` LLM 경계 | Codex protocol timeout/retry/no-tool test source 보존; V2 live는 `UNKNOWN` | `PARTIAL` |
| `REQ-10` exact cache | active + 세 current version exact predicate; G7 offline contract의 miss→exact-hit 전이 | `PARTIAL` |
| `REQ-11` similar cache | exact miss 후 pgvector cosine `>=0.95` | `PARTIAL` |
| `REQ-12` cache metadata | JSON/SSE hit/match/write/skip fields | `PARTIAL` |
| `REQ-13` 무효화 | CREATED 전체, UPDATED dependency+new similarity, version mismatch fallback, UNCHANGED no-op; G7은 update invalidation·v2 citation·miss 강제 | `PARTIAL` |
| `REQ-14` prompt | version SSOT, JSON envelope, delimiter round-trip, plain output | `PARTIAL` |

## 2. Transaction·concurrency·migration

| ID | 불변식과 증거 | 상태 |
|---|---|---|
| `TX-01` | document/chunk/vector/cache/revision/ready 한 transaction; post-invalidation trigger rollback | `PARTIAL` |
| `TX-02` | parse/chunk/embed transaction 밖; filename advisory lock 뒤 hash/version 재확인 | `PARTIAL` |
| `TX-03` | chunk 또는 revision 실패 뒤 이전 ready document/chunk/cache/revision 보존 | `PARTIAL` |
| `CC-01` | 동일 document 동시 ingest → CREATED+UNCHANGED, revision 1 | `PARTIAL` |
| `CC-02` | 서로 다른 document 동시 ingest → ready 2, revision 2 | `PARTIAL` |
| `CC-03` | LLM 중 revision 변경 → cache write `corpus_changed` skip | `PARTIAL` |
| `MIG-01` | forward-only version/checksum/applied time, empty DB/re-run/drift/history | `PARTIAL` |
| `MIG-02` | advisory lock, failed migration rollback | `PARTIAL` |
| `OPS-01` | digest-pinned PostgreSQL 17.10/pgvector 0.8.6 | `PARTIAL` |
| `OPS-02` | `/health/ready` DB/migration/vector와 success/failure/timeout test source; 최종 real DB 실행 pending | `PARTIAL` |
| `OPS-03` | isolated one-line Compose test, suite별 temporary database; 최종 변경 SHA 실행 pending | `PARTIAL` |
| `OPS-04` | raw question/evidence 없는 timing log | `PLANNED` — 제출 비목표 |

## 3. 공식 테스트와 double 경계

| ID | 실제 경계 | 허용 double | 로컬 증거 | 상태 |
|---|---|---|---|---|
| `TEST-01` Ingestion | PostgreSQL, migration, pgvector, parser, in-process FastAPI ASGI TXT/PDF | embedding, unused LLM | `test_v2_offline_paths.py` | `PARTIAL` |
| `TEST-02` Retrieval | PostgreSQL `<=>`, rank, provenance, in-process FastAPI ASGI/SSE contract | embedding, scripted LLM | 한국어 fixture/no-evidence | `PARTIAL` |
| `TEST-03` Cache | row/dependency/invalidation/revision/transaction | embedding, scripted LLM | exact/similar/update/rollback | `PARTIAL` |
| `P0-SEC-01` | image/runtime env/path/mount/effective libpq dbname | 없음 | dirty worktree container에서 credential-name·disposable-db guard PASS; final SHA pending | `PARTIAL` |
| `P0-PROMPT-01` | JSON serialization | 없음 | question/evidence/metadata breakout | `PARTIAL` |
| `P0-STRUCT-01` | structured output source 부재 | 해당 없음 | plain text prompt/gateway source audit | `PARTIAL` |

공식 명령:

```bash
docker compose run --build --rm test
```

Test runner는 고유 이름의 임시 database를 생성하고 migration을 적용하며 종료 시
그 database만 삭제합니다. 동시성은 개별 test 안의 explicit parallel task로
검증합니다.

## 4. P0 1~9 매핑

| P0 | 코드 | 회귀 |
|---|---|---|
| 1. 세 version 저장 | migration, `CacheVersions`, PostgreSQL/SQLite adapters | DB row assertions |
| 2. 세 version 모두 current-match | exact/similar SQL predicate와 soft invalidation | 세 개별 mismatch miss |
| 3. prompt SSOT | `prompts.py::PROMPT_TEMPLATE_VERSION` | declaration/payload assertion |
| 4. 안전한 구조 직렬화 | deterministic JSON + `<>&` escape | question/evidence/metadata breakout |
| 5. citation 의미 | API/prompt/docs의 retrieval provenance | 금지 표현 source scan |
| 6. offline double 제한 | V2 service container에 embedding/LLM double만 주입 | actual DB/parser + in-process FastAPI ASGI assertions |
| 7. test container 격리 | Dockerfile, `.dockerignore`, Compose sentinel | executable/env/mount runtime test |
| 8. DEC 상태 | DEC-010~012 `PROPOSED`, V1 보존 | decision/document audit |
| 9. structured output | 구현하지 않고 plain text delta 유지 | source/prompt regression |

## 5. 불합격 기준 gate

| ID | 현재 판단 | 남은 증거 |
|---|---|---|
| `DQ-01` 실행 불가 | `PARTIAL` | clean checkout 제품 실행과 README 절차 |
| `DQ-02` hardcode/LLM 없음 | `PARTIAL` | current-SHA actual Codex/SSE |
| `DQ-03` vector/cache DB 없음 | `PARTIAL` | committed 구현·과거 real-DB test, 최종 SHA 재실행 필요 |
| `DQ-04` 자동화 테스트 전무 | `PARTIAL` | dirty worktree Docker 127 PASS와 역사적 exact-archive suite는 존재; 최종 SHA DB 재실행을 분리 |
| `DQ-05` OSS 통째 복사 | `PARTIAL` | V1 증분 history와 dependency provenance 최종 감사 |
| `DQ-06` 허위 문서 | `PARTIAL` | current-SHA 문서/code/test parity 재검증 |
| `DQ-07` 평가 창 밖 구현 | `VERIFIED` | 기존 V2 meaningful artifact와 push가 평가 창 안에 존재; 최종 push는 `SUB-01`로 분리 |

## 6. 제출 process

| 규칙 | 상태 | 근거 또는 남은 작업 |
|---|---|---|
| `SUB-01` 최종 private push | `PARTIAL` | 기존 V2 baseline은 remote에 존재; 최종 변경 commit·Retrobot·push·live parity pending |

## 7. Gate ledger

| Gate | 상태 | 근거 또는 blocker |
|---|---|---|
| `G1` | `VERIFIED` | branch + 설계 문서 baseline의 remote parity |
| `G2` | `VERIFIED` | migration/Compose baseline source와 local/live remote SHA parity |
| `G3` | `PARTIAL` | dirty worktree real-DB transaction tests PASS; 최종 SHA DB tests pending |
| `G4` | `PARTIAL` | 공식 3경로 dirty-worktree Docker PASS; 최종 SHA 재실행 pending |
| `G5` | `PARTIAL` | RAG/cache/SSE/concurrency source committed; runtime freshness pending |
| `G6` | `PARTIAL` | dirty-worktree Docker의 readiness·isolation·live-harness 계약 PASS; final SHA Docker rerun pending |
| `G7` | `PARTIAL` | disposable DB·citation·miss→hit→update→miss harness와 offline contract PASS; clean checkout actual Codex/SSE pending |
| `G8` | `PARTIAL` | [V1/V2 비교](V1_V2_COMPARISON.md) 작성; final DQ·SHA·merge audit pending |

## 8. 승격 금지선

- commit 전 worktree test는 최종 변경 SHA receipt가 아닙니다.
- fake LLM SSE는 actual Codex incremental SSE가 아닙니다.
- schema field 존재만으로 lookup/write enforcement를 증명하지 않습니다.
- DEC-010~012는 `PROPOSED`를 유지합니다.
- 최종 변경 SHA integration과 live Codex/SSE 전에는 DEC-002/007/008 또는 ADR-0002를
  `SUPERSEDED`로 바꾸거나 V2를 `VERIFIED`로 표현하지 않습니다.
