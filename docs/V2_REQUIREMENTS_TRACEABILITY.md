# V2 Requirements Traceability

| 항목 | 값 |
|---|---|
| 문서 상태 | `VERIFIED` — 최신 G8 final-audit bundle `PASS` |
| 대상 브랜치 | `feat/v2-postgres-pgvector-compose` |
| V1 rollback | `29a429f16616ff63dd6ef175b870ec042be10e71` |
| 최초 G8 evidence 기준선 | `4a596b008cfc0470c1f2d30ad59ce5f122d9995d` |
| 제출 결론 | V2 branch 제출 `YES`; `main` merge는 수행하지 않음 |
| 정직한 잔여 | `REQ-09` structured output, `PROC-04` worked-day push completeness는 `PARTIAL` |

V1의 과거 test/live smoke나 이전 V2 실행을 현재 증거로 재사용하지 않았습니다.
제출 HEAD가 바뀔 때마다 아래 여덟 profile을 같은 SHA에서 다시 실행하고, G8은 그
완전한 bundle만 수용합니다.

최초 G8 기준선은 위 full SHA로 고정했습니다. 이후 handoff commit은 평가자 문서와
Retrobot 산출물만 바꾸며, 최종 SHA는 Git `HEAD`와 live remote가 소유합니다. 아래
profile 결과는 그 최종 HEAD에서 재발급하고 문서에 변동 SHA를 중복 복사하지 않습니다.

| 외부 제출-control current-SHA profile | 확인 경계 | sanitized 최신 결과 |
|---|---|---|
| `v2-core-suite` | PostgreSQL/pgvector, migration, 공식 3경로, transaction | `167 passed, 6 skipped`; 필수 node `3/3` |
| `v2-product-readiness` | 실제 DB readiness | HTTP `200`, migration `0001`, pgvector `0.8.6` |
| `v2-fresh-readme-launch` | detached clean launch | README 한 줄 실행과 cleanup `PASS` |
| `v2-live-codex-sse` | actual Codex/Uvicorn SSE | `miss → exact hit → update → miss`, 복수 delta·citation `PASS` |
| `v2-document-parity` | 문서–코드–테스트 | claim group, link, Ruff, lock, Compose, hook `PASS` |
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
| `REQ-09` LLM 활용 | timeout, first-delta retry, no-tool 경계는 검증; structured output은 미구현 | `PARTIAL` |
| `REQ-10` exact cache | active + 세 current version exact predicate | `VERIFIED` |
| `REQ-11` similar cache | exact miss 후 pgvector cosine `>=0.95` | `VERIFIED` |
| `REQ-12` cache metadata | JSON/SSE hit/match/write/skip field | `VERIFIED` |
| `REQ-13` 무효화 | CREATED/UPDATED/UNCHANGED, dependency·similarity·version fallback | `VERIFIED` |
| `REQ-14` prompt | version SSOT, JSON envelope, delimiter round-trip, 설계 문서 | `VERIFIED` |

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
| `OPS-04` | raw question/evidence 없는 structured timing log | `PLANNED` — 제출 비목표 |

## 3. 공식 테스트와 double 경계

| ID | 실제 경계 | 허용 double | 상태 |
|---|---|---|---|
| `TEST-01` | PostgreSQL, migration, pgvector, parser, ASGI TXT/PDF ingestion | embedding, unused LLM | `VERIFIED` |
| `TEST-02` | PostgreSQL `<=>`, rank, provenance, ASGI/SSE | embedding, scripted LLM | `VERIFIED` |
| `TEST-03` | cache row/dependency/invalidation/revision/rollback | embedding, scripted LLM | `VERIFIED` |
| `P0-SEC-01` | container executable/env/mount/effective DB target | 없음 | `VERIFIED` |
| `P0-PROMPT-01` | deterministic JSON serialization과 breakout | 없음 | `VERIFIED` |
| `P0-STRUCT-01` | P0-09 stop rule 준수: structured output 미구현 | 해당 없음 | `VERIFIED`; `REQ-09` scoring은 `PARTIAL` |

공식 명령은 다음 한 줄입니다.

```bash
docker compose run --build --rm test
```

여섯 skip은 격리 image에 의도적으로 없는 Git·flock이 필요한 host-only Retrobot
runtime test입니다. 필수 `TEST-01`~`TEST-03` node는 collect-only로 `3/3` 존재를
별도 확인합니다.

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
| 8. DEC 상태 | DEC-010~012 `PROPOSED` | V1 DEC-002/003/007/008·ADR-0002 미 supersede; V2 구현 evidence와 분리 |
| 9. structured output | 구현하지 않고 plain-text delta 유지 | source/prompt regression |

## 5. 불합격 기준 gate

| ID | 판정 | current-SHA 근거 |
|---|---|---|
| `DQ-01` 실행 불가 | `PASS` | detached clean README launch |
| `DQ-02` hardcode/LLM 없음 | `PASS` | actual Codex/Uvicorn SSE |
| `DQ-03` vector/cache DB 없음 | `PASS` | real PostgreSQL/pgvector core suite |
| `DQ-04` 자동화 테스트 전무 | `PASS` | 167-test Docker suite와 필수 3경로 |
| `DQ-05` OSS 통째 복사 | `PASS` | template 증분 history, dependency provenance, 지원자 확인 |
| `DQ-06` 허위 문서 | `PASS` | targeted functional/status registry, independent cold-read와 runtime bundle |
| `DQ-07` 평가 창 밖 구현 | `PASS` | applicant window와 meaningful artifact/push 시각 |

## 6. 제출 process

| 규칙 | 상태 | 근거 또는 한계 |
|---|---|---|
| `DOC-01` README | `VERIFIED` | 한 줄 run/test, detached clean launch와 core/document bundle |
| `DOC-02` Architecture | `VERIFIED` | core/readiness/clean/live가 뒷받침하는 구조·SDK 설명 |
| `DOC-03` Prompt Design | `VERIFIED` | JSON/version code·test와 structured-output 비범위의 정합성 |
| `DOC-04` tests | `VERIFIED` | 공식 3경로와 자동화 suite |
| `DOC-05` 회고 산출물 | `VERIFIED` | 현재 자동 회고 pair와 timeline |
| `PROC-03` Retrobot 자동 회고 | `VERIFIED` | 활성 hook의 setup→retro→product chronology와 skip-free runtime test |
| `PROC-04` worked-day commit·push | `PARTIAL` | 8월 2일 commit의 당일 push를 증명하지 못함; 불합격이 아닌 Track C 과정 위험 |
| `PROC-05` 회고 내용 | `VERIFIED` | bounded latest WORKLOG H2와 자동 KPT artifact |
| `SUB-01` private final push | `VERIFIED` | local/tracking/live remote exact parity, clean, ahead/behind `0/0` |

## 7. Gate ledger

| Gate | 상태 | 핵심 증거 |
|---|---|---|
| `G1` | `PASS` | branch, 설계 baseline, remote parity |
| `G2` | `PASS` | pinned DB image, migration, Compose |
| `G3` | `PASS` | transaction, rollback, concurrency, revision race |
| `G4` | `PASS` | 공식 `TEST-01`~`TEST-03` |
| `G5` | `PASS` | RAG, cache, SSE, Retrobot runtime |
| `G6` | `PASS` | readiness, 격리, 문서 parity |
| `G7` | `PASS` | clean launch, disposable DB, actual Codex/SSE |
| `G8` | `PASS` | 여덟 current-SHA profile과 DQ·remote audit |

G8 `PASS`는 제출 가능한 evidence bundle을 뜻하며 모든 scoring/process 항목 만점을
뜻하지 않습니다. `REQ-09`와 `PROC-04`를 과대 승격하지 않습니다.

## 8. 승격 금지선

- HEAD가 바뀌면 여덟 profile과 final audit을 다시 실행합니다.
- fake LLM SSE를 actual Codex incremental SSE로 표현하지 않습니다.
- schema field 존재만으로 lookup/write enforcement를 주장하지 않습니다.
- `REQ-09`는 structured output 전용 구현·실패 회귀 전까지 `PARTIAL`입니다.
- `PROC-04`는 완전한 worked-day remote chronology 없이는 `PARTIAL`입니다.
- DEC-010~012는 P0-08에 따라 `main` supersession에는 `PROPOSED`, V2 branch
  evidence에는 `VERIFIED`를 유지합니다.
- CI, timing log, backup/restore drill, HA/PITR는 구현했다고 주장하지 않습니다.
