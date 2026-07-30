# V2 Requirements Traceability

| 항목 | 값 |
|---|---|
| 문서 상태 | `PARTIAL` |
| 기준선 | V1 `29a429f16616ff63dd6ef175b870ec042be10e71` |
| 대상 브랜치 | `feat/v2-postgres-pgvector-compose` |
| 로컬 영수증 | dirty worktree `docker compose run --build --rm test` → `48 passed` |
| 미충족 | current-SHA commit, G2 remote parity, clean clone, actual Codex/SSE |

현재 V2 PostgreSQL composition과 P0 코드는 로컬 워크트리에 존재하고 구독 없는
실제 DB integration이 통과했습니다. 그러나 영수증이 불변 commit에 연결되지
않았고 실제 Codex/SSE도 실행하지 않았으므로 상태는 `IMPLEMENTED/PARTIAL`입니다.
V1의 과거 test/live smoke는 V2 증거로 재사용하지 않습니다.

상태:

- `IMPLEMENTED`: 현재 로컬 코드와 정적 대조가 됨
- `PARTIAL`: 로컬 runtime은 통과했지만 current-SHA/remote 또는 live 경계가 남음
- `VERIFIED`: current-SHA에서 요구 runtime 영수증까지 통과
- `UNKNOWN`: 확인 증거 없음

## 1. 제품 요구사항

| 규칙 | 구현·테스트 근거 | 상태 |
|---|---|---|
| `REQ-01`~`REQ-03` 수집 | FastAPI multipart, 실제 TXT/PDF parser, transactional ingestion, ready/version/same-byte assertions | `PARTIAL` |
| `REQ-04` RAG | PostgreSQL pgvector `<=>`, 한국어 3문서 fixture, top citation | `PARTIAL` |
| `REQ-05` 답변 생성 | production bootstrap은 Codex gateway, offline은 scripted LLM double만 사용 | `IMPLEMENTED`; live `UNKNOWN` |
| `REQ-06` 출처 | server-side chunk→citation mapper, filename/version/page/offset HTTP/SSE | `PARTIAL` |
| `REQ-07` 무근거 | zero/threshold miss에서 LLM·cache skip | `PARTIAL` |
| `REQ-08` streaming | SSE started→delta→citation→completed, cache replay 구분 | `IMPLEMENTED`; live incremental `UNKNOWN` |
| `REQ-09` LLM 경계 | 기존 Codex protocol timeout/retry/no-tool tests 보존 | `IMPLEMENTED`; V2 live `UNKNOWN` |
| `REQ-10` exact cache | active + 세 current version exact predicate | `PARTIAL` |
| `REQ-11` similar cache | exact miss 후 pgvector cosine `>=0.95` | `PARTIAL` |
| `REQ-12` cache metadata | JSON/SSE hit/match/write/skip fields | `PARTIAL` |
| `REQ-13` 무효화 | CREATED 전체, UPDATED dependency+new similarity, version mismatch fallback, UNCHANGED no-op | `PARTIAL` |
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
| `MIG-01` | forward-only version/checksum/applied time, empty DB/re-run/drift/history | `PASS` local |
| `MIG-02` | advisory lock, failed migration rollback | `PASS` local |
| `OPS-01` | digest-pinned PostgreSQL 17.10/pgvector 0.8.6 | `PASS` local |
| `OPS-02` | `/health/ready` DB/migration/vector 확인 | `DRAFT` |
| `OPS-03` | isolated one-line Compose test, temporary database | `PARTIAL` — 48 local |
| `OPS-04` | raw question/evidence 없는 timing log | `DRAFT` |

## 3. 공식 테스트와 double 경계

| ID | 실제 경계 | 허용 double | 로컬 증거 | 상태 |
|---|---|---|---|---|
| `TEST-01` Ingestion | PostgreSQL, migration, pgvector, parser, HTTP TXT/PDF | embedding, unused LLM | `test_v2_offline_paths.py` | `PARTIAL` |
| `TEST-02` Retrieval | PostgreSQL `<=>`, rank, provenance, HTTP/SSE | embedding, scripted LLM | 한국어 fixture/no-evidence | `PARTIAL` |
| `TEST-03` Cache | row/dependency/invalidation/revision/transaction | embedding, scripted LLM | exact/similar/update/rollback | `PARTIAL` |
| `P0-SEC-01` | image/runtime env/path/mount | 없음 | Node/Codex/key/token/.codex 부재 | `PARTIAL` |
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
| 6. offline double 제한 | V2 service container에 embedding/LLM double만 주입 | actual DB/parser/HTTP/SSE assertions |
| 7. test container 격리 | Dockerfile, `.dockerignore`, Compose sentinel | executable/env/mount runtime test |
| 8. DEC 상태 | DEC-010~012 `PROPOSED`, V1 보존 | decision/document audit |
| 9. structured output | 구현하지 않고 plain text delta 유지 | source/prompt regression |

## 5. 불합격 기준 gate

| ID | 현재 판단 | 남은 증거 |
|---|---|---|
| `DQ-01` 실행 불가 | `PARTIAL` | clean clone 제품 실행과 README 절차 |
| `DQ-02` hardcode/LLM 없음 | `PARTIAL` | current-SHA actual Codex/SSE |
| `DQ-03` vector/cache DB 없음 | `PARTIAL` | 현재 구현·local real-DB test, current-SHA 필요 |
| `DQ-04` 자동화 테스트 전무 | `PARTIAL` | 48 local, current-SHA 필요 |
| `DQ-05` OSS 통째 복사 | `PARTIAL` | V1 증분 history와 dependency provenance 최종 감사 |
| `DQ-06` 허위 문서 | `PARTIAL` | current-SHA 문서/code/test parity 재검증 |
| `DQ-07` 평가 창 밖 구현 | `DRAFT` | deadline 전 meaningful commit/push/remote SHA |

## 6. Gate ledger

| Gate | 상태 | 근거 또는 blocker |
|---|---|---|
| `G1` | `PASS` | branch + 설계 문서 remote parity |
| `G2` | `PARTIAL` | migration/Compose local PASS, remote parity pending |
| `G3` | `PARTIAL` | CorpusStore·transaction code + local DB tests, current-SHA pending |
| `G4` | `PARTIAL` | 공식 3경로 local Docker PASS, current-SHA pending |
| `G5` | `PARTIAL` | RAG/cache/SSE/concurrency local regression |
| `G6` | `PARTIAL` | 문서 정합과 CI/ready/logging 잔여 |
| `G7` | `DRAFT` | clean clone + actual Codex/SSE |
| `G8` | `DRAFT` | DQ audit와 V1/V2 비교·제출 결정 |

## 7. 승격 금지선

- local dirty-worktree test는 current-SHA receipt가 아닙니다.
- fake LLM SSE는 actual Codex incremental SSE가 아닙니다.
- schema field 존재만으로 lookup/write enforcement를 증명하지 않습니다.
- DEC-010~012는 `PROPOSED`를 유지합니다.
- current-SHA integration과 live Codex/SSE 전에는 DEC-002/007/008 또는 ADR-0002를
  `SUPERSEDED`로 바꾸거나 V2를 `VERIFIED`로 표현하지 않습니다.
