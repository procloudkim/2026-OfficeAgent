# Repository Map

tracked 파일을 역할과 생성 주체로 한 번씩만 분류한 MECE map입니다. 새 파일은
아래 한 범주에만 배치하며, runtime/generated data는 Git에 넣지 않습니다.

처음 실행하는 독자는 [Evaluator Manual](./EVALUATOR_MANUAL.md), 요구사항 상태를
확인하는 독자는 [V2 Traceability](./V2_REQUIREMENTS_TRACEABILITY.md)에서 시작합니다.
이 문서는 파일 색인이며 실행 절차나 현재 PASS 판정을 중복 소유하지 않습니다.

## A. 계약과 agent 운영

| 파일 | 책임 | 주체 |
|---|---|---|
| `docs/PRD.md` | 과제 요구·평가·불합격 기준의 최상위 계약 | upstream |
| `CLAUDE.md`, `AGENTS.md` symlink | AI agent용 PRD hard rule | upstream |
| `docs/TEMPLATE_GUIDE.md` | 최초 template 구성 설명 | upstream reference |
| `retrobot/README.md`, `retrobot/SKILL.md` | 회고 동작·생성 규칙 | upstream |
| `.githooks/post-commit` | SHA·시각·shortstat만 묶어 guard 뒤 회고·타임라인 commit | upstream + applicant hardening |

`docs/TEMPLATE_GUIDE.md`는 부트스트랩 당시 설명이므로 현재 구현 구조의 source of
truth가 아닙니다. 충돌 시 `docs/PRD.md`, 그다음 현재 코드/테스트를 따릅니다.

## B. 평가자용 제품 문서

| 파일 | 책임 |
|---|---|
| `README.md` | V2 평가자 실행·테스트·API와 상태 요약 |
| `docs/EVALUATOR_MANUAL.md` | 사전 조건→실행→예상 결과→실패 복구의 상세 평가 runbook |
| `ARCHITECTURE.md` | V1 한계, V2 product flow·transaction·runtime 경계 |
| `PROMPT_DESIGN.md` | grounding·abstention·injection 전략 |
| `docs/REQUIREMENTS_TRACEABILITY.md` | V1 rollback 기준선의 PRD → evidence |
| `docs/PRD_PHASE2.md` | V2 설계 계약과 수용 조건; 실제 상태는 V2 trace가 소유 |
| `docs/TRADEOFF_STORAGE_AND_DOCKER.md` | V2 storage/Docker 선택과 trade-off |
| `docs/V2_REQUIREMENTS_TRACEABILITY.md` | V2 요구사항·gate·증거 freshness의 단일 상태 정본 |
| `docs/V1_V2_COMPARISON.md` | G8의 evidence-bound V1/V2 비교·잔여 한계·제출 권고 |
| `docs/DECISION_LOG.md` | 짧은 Why–What–How 결정 이력 |
| `docs/adr/ADR-0001-*.md` | 구조·네이밍 governance |
| `docs/adr/ADR-0002-*.md` | V1 SQLite/Chroma 저장소·검색·캐시 기준선 |
| `docs/adr/ADR-0003-*.md` | Codex·SSE·retry 결정, 역사적 V1과 current V2 live evidence |
| `docs/adr/ADR-0004-*.md` | V2 PostgreSQL/pgvector product overlay와 승격 조건 |
| `docs/adr/ADR-0005-*.md` | structured Codex answer의 incremental plaintext·failure 계약 |
| `docs/verification/2026-07-28.md` | sanitized command receipt |
| `docs/verification/2026-07-30-v2-g0.md` | V2 R0/G0 Docker·V1 preflight sanitized receipt |
| `docs/verification/2026-07-30-v2-g2.md` | V2 migration/Compose/test sanitized runtime receipt |

## C. 실행 코드

| 경로 | 파일별 책임 |
|---|---|
| `src/officeagent/main.py` | console/uvicorn entrypoint |
| `src/officeagent/bootstrap.py` | production dependency composition |
| `src/officeagent/api/app.py` | route, lifespan, HTTP error, SSE serialization |
| `src/officeagent/api/schemas.py` | versioned request/response models |
| `src/officeagent/application/ingestion.py` | extract→version→embed→index→invalidate |
| `src/officeagent/application/transactional_ingestion.py` | V2 prepare→single transactional corpus commit |
| `src/officeagent/application/question_answering.py` | cache→retrieve→abstain/generate→cache |
| `src/officeagent/application/chunking.py` | deterministic page-safe chunks |
| `src/officeagent/application/prompts.py` | canonical grounded prompt |
| `src/officeagent/domain/models.py` | framework-independent values/events |
| `src/officeagent/domain/ports.py` | storage/embedding/LLM protocols |
| `src/officeagent/infrastructure/parsers.py` | PDF/TXT/MD extraction |
| `src/officeagent/infrastructure/embeddings.py` | FastEmbed adapter |
| `src/officeagent/infrastructure/chroma_store.py` | V1 rollback용 persistent vector adapter |
| `src/officeagent/infrastructure/sqlite_store.py` | V1 rollback용 document/cache/dependency adapter |
| `src/officeagent/infrastructure/postgres_corpus_store.py` | V2 document/vector/cache/revision 단일 store |
| `src/officeagent/infrastructure/codex_app_server.py` | allowlisted child environment의 Codex JSON-RPC process/delta adapter |
| `src/officeagent/infrastructure/structured_answer.py` | 한 필드 Codex JSON의 bounded incremental plaintext decoder |
| `src/officeagent/infrastructure/migrations.py` | forward-only apply/check와 read-only readiness probe |
| `src/officeagent/infrastructure/test_runner.py` | isolated PostgreSQL suite DB lifecycle·credential-name/effective-libpq-target guards |
| `src/officeagent/migrations/0001_v2_schema.sql` | initial document/vector/cache/revision schema |
| `src/` package `__init__.py` files | Python package boundaries only |
| `scripts/__init__.py` | verification helper package boundary; product wheel에서 제외 |
| `scripts/live_smoke.py` | out-of-repo cwd·local disposable DB·HTTP/Codex G7 live runner |
| `scripts/live_smoke_contract.py` | citation/cache/update/version receipt의 순수 offline validator |
| `scripts/retrobot_context.py` | committed WORKLOG에서 최신 날짜 H2 하나만 추출하는 8 KiB parser |
| `scripts/retrobot_guard.py` | preflight prompt·exact child-env·timeout·structured JSON 검증·canonical Markdown renderer |
| `scripts/retrobot_publish.py` | no-clobber 설치·owned index 교체·guarded timeline append primitive |

## D. 자동화 테스트

| 파일 | 단일 책임 |
|---|---|
| `tests/test_health.py` | loopback API host와 credential-free readiness success/failure/timeout boundary |
| `tests/test_ingestion.py` | multipart, 3 error classes, TXT/PDF persistence |
| `tests/test_retrieval.py` | expected source and no-evidence LLM skip |
| `tests/test_cache_invalidation.py` | exact/similar and dependency invalidation |
| `tests/test_streaming.py` | SSE sequence/cache/input과 structured-output failure/no-cache boundary |
| `tests/test_codex_protocol.py` | App Server schema/decoder/item/retry/tool/child-environment boundary |
| `tests/test_live_smoke.py` | disposable DB fail-closed, G7 citation/miss→hit→update→miss, proxy·receipt offline contract |
| `tests/test_prompts.py` | client-controlled prompt metadata attribute boundary |
| `tests/test_retrobot_hook.py` | hook 입력 metadata·timeline cell·explicit stage/commit 구조 계약 |
| `tests/test_retrobot_context.py` | 최신 날짜 H2 선택·후속 H2 차단·8 KiB 경계 계약 |
| `tests/test_retrobot_guard.py` | child env·timeout·UTF-8/size/heading·민감 출력 거부 계약 |
| `tests/test_retrobot_publish.py` | fd-origin ownership·공개 파일 보존·guarded append 단위 계약 |
| `tests/test_retrobot_runtime.py` | DrvFS mode·early index/HEAD lock·branch pin·사용자 staging/flag 보존·recovery journal 자동 commit 통합 계약 |
| `tests/test_sqlite_store.py` | version/cache/FK integrity |
| `tests/test_chroma_store.py` | replace/search cosine behavior |
| `tests/test_parsers_and_chunking.py` | formats, offsets, stable chunks |
| `tests/test_migrations.py` | apply/rerun/drift/rollback/history/concurrency integration |
| `tests/test_postgres_corpus_store.py` | transaction/rollback/cache/revision/concurrency integration |
| `tests/test_test_container_isolation.py` | Compose runtime·credential isolation |
| `tests/test_v2_offline_paths.py` | real PostgreSQL/pgvector + in-process FastAPI ASGI ingestion·RAG·cache·SSE contract |
| `tests/helpers.py` | deterministic fake ports/PDF builder |
| `tests/conftest.py` | cross-platform test data directory와 disposable effective DB guard |
| `tests/__init__.py` | stable helper import under `uv run pytest` |

## E. 빌드·형상·샘플·과정 증거

| 파일/경로 | 책임 |
|---|---|
| `compose.yaml` | digest-pinned DB, health, migrate와 isolated test topology |
| `Dockerfile.test`, `.dockerignore` | locked Linux image, bounded context, offline guard/live-harness import와 Node/Codex 배제 |
| `pyproject.toml`, `uv.lock` | Python build, locked dependencies, tool config |
| `.gitignore`, `.gitattributes` | generated exclusion, cross-platform LF contract |
| `sample-docs/company-policy.txt`, `development-guide.md` | manual/live fixtures |
| `retros/WORKLOG.md` | 지원자가 확인한 사실·실패·전환 |
| `retros/TIMELINE.md`, `retros/*_retro.md` | guard-validated Retrobot 출력; 사실 주장은 지원자가 재검토할 Track C evidence |

## F. Git 제외 runtime/generated

`.venv/`, `.data/`, `.test-data/`, `.pytest_cache/`, `.pytest-tmp*/`,
`.ruff_cache/`, `__pycache__/`, coverage/build 결과는 source가 아니며
`.gitignore`로 제외합니다. V2 영속 데이터는 Compose의 PostgreSQL volume이
소유하고 `.data/`는 host Codex workspace와 V1 rollback adapter의 local data만
포함합니다.
