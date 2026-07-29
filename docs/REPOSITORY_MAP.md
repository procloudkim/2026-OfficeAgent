# Repository Map

tracked 파일을 역할과 생성 주체로 한 번씩만 분류한 MECE map입니다. 새 파일은
아래 한 범주에만 배치하며, runtime/generated data는 Git에 넣지 않습니다.

## A. 계약과 agent 운영

| 파일 | 책임 | 주체 |
|---|---|---|
| `docs/PRD.md` | 과제 요구·평가·불합격 기준의 최상위 계약 | upstream |
| `CLAUDE.md`, `AGENTS.md` symlink | AI agent용 PRD hard rule | upstream |
| `docs/TEMPLATE_GUIDE.md` | 최초 template 구성 설명 | upstream reference |
| `retrobot/README.md`, `retrobot/SKILL.md` | 회고 동작·생성 규칙 | upstream |
| `.githooks/post-commit` | commit 후 Retrobot 실행 | upstream + 호환성 수정 |

`docs/TEMPLATE_GUIDE.md`는 부트스트랩 당시 설명이므로 현재 구현 구조의 source of
truth가 아닙니다. 충돌 시 `docs/PRD.md`, 그다음 현재 코드/테스트를 따릅니다.

## B. 평가자용 제품 문서

| 파일 | 책임 |
|---|---|
| `README.md` | 한 줄 실행·테스트, API 사용, 핵심 선택 |
| `ARCHITECTURE.md` | 계층, 데이터 흐름, 실패·보안·trade-off |
| `PROMPT_DESIGN.md` | grounding·abstention·injection 전략 |
| `docs/REQUIREMENTS_TRACEABILITY.md` | PRD → 코드 → test/runtime evidence |
| `docs/DECISION_LOG.md` | 짧은 Why–What–How 결정 이력 |
| `docs/adr/ADR-0001-*.md` | 구조·네이밍 governance |
| `docs/adr/ADR-0002-*.md` | 저장소·검색·캐시 결정 |
| `docs/adr/ADR-0003-*.md` | Codex·SSE·retry 결정 |
| `docs/verification/2026-07-28.md` | sanitized command receipt |

## C. 실행 코드

| 경로 | 파일별 책임 |
|---|---|
| `src/officeagent/main.py` | console/uvicorn entrypoint |
| `src/officeagent/bootstrap.py` | production dependency composition |
| `src/officeagent/api/app.py` | route, lifespan, HTTP error, SSE serialization |
| `src/officeagent/api/schemas.py` | versioned request/response models |
| `src/officeagent/application/ingestion.py` | extract→version→embed→index→invalidate |
| `src/officeagent/application/question_answering.py` | cache→retrieve→abstain/generate→cache |
| `src/officeagent/application/chunking.py` | deterministic page-safe chunks |
| `src/officeagent/application/prompts.py` | canonical grounded prompt |
| `src/officeagent/domain/models.py` | framework-independent values/events |
| `src/officeagent/domain/ports.py` | storage/embedding/LLM protocols |
| `src/officeagent/infrastructure/parsers.py` | PDF/TXT/MD extraction |
| `src/officeagent/infrastructure/embeddings.py` | FastEmbed adapter |
| `src/officeagent/infrastructure/chroma_store.py` | persistent vector adapter |
| `src/officeagent/infrastructure/sqlite_store.py` | document/cache/dependency adapter |
| `src/officeagent/infrastructure/codex_app_server.py` | Codex JSON-RPC process/delta adapter |
| package `__init__.py` files | Python package boundaries only |
| `scripts/live_smoke.py` | optional paid/network production-boundary validator |

## D. 자동화 테스트

| 파일 | 단일 책임 |
|---|---|
| `tests/test_health.py` | liveness |
| `tests/test_ingestion.py` | multipart, 3 error classes, TXT/PDF persistence |
| `tests/test_retrieval.py` | expected source and no-evidence LLM skip |
| `tests/test_cache_invalidation.py` | exact/similar and dependency invalidation |
| `tests/test_streaming.py` | SSE sequence/cache/input validation |
| `tests/test_codex_protocol.py` | App Server protocol/retry/tool boundary |
| `tests/test_prompts.py` | client-controlled prompt metadata attribute boundary |
| `tests/test_retrobot_hook.py` | generated-retro explicit stage/commit path contract |
| `tests/test_sqlite_store.py` | version/cache/FK integrity |
| `tests/test_chroma_store.py` | replace/search cosine behavior |
| `tests/test_parsers_and_chunking.py` | formats, offsets, stable chunks |
| `tests/helpers.py` | deterministic fake ports/PDF builder |
| `tests/conftest.py` | cross-platform test data directory |
| `tests/__init__.py` | stable helper import under `uv run pytest` |

## E. 빌드·형상·샘플·과정 증거

| 파일/경로 | 책임 |
|---|---|
| `pyproject.toml`, `uv.lock` | Python build, locked dependencies, tool config |
| `.gitignore`, `.gitattributes` | generated exclusion, cross-platform LF contract |
| `sample-docs/company-policy.txt`, `development-guide.md` | manual/live fixtures |
| `retros/WORKLOG.md` | 지원자가 확인한 사실·실패·전환 |
| `retros/TIMELINE.md`, `retros/*_retro.md` | Retrobot-generated Track C evidence |

## F. Git 제외 runtime/generated

`.venv/`, `.data/`, `.test-data/`, `.pytest_cache/`, `.pytest-tmp*/`,
`.ruff_cache/`, `__pycache__/`, coverage/build 결과는 source가 아니며
`.gitignore`로 제외합니다. `.data/`만 운영 SQLite·Chroma·Codex 전용 cwd를
포함합니다.
