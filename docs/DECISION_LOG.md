# Technology Decision Register

현재 기술 선택의 간결한 `What–Why–How–Trade-off` 정본입니다. README는 실행과
핵심 선택을 요약하고, `ARCHITECTURE.md`는 시스템 흐름과 실패 경계를 설명합니다.
프롬프트 고유 결정은 `PROMPT_DESIGN.md`, 요구사항별 검증 상태는
`REQUIREMENTS_TRACEABILITY.md`, 되돌리기 어렵거나 위험한 결정의 상세 근거는
연결된 ADR이 소유합니다.

`Status`는 decision lifecycle만 나타내며 구현 검증 상태가 아닙니다. 실행
영수증은 불변 source commit에 연결하고, 최신 Submission HEAD와 증거의
freshness는 Control에서 별도로 추적합니다.

## Register

| ID | Status | What | Why | How | Trade-off | Evidence | ADR |
|---|---|---|---|---|---|---|---|
| DEC-001 | ACTIVE | Python 3.11–3.13과 FastAPI의 async use case를 `api/application/domain/infrastructure` 단일 package로 구성한다. | HTTP, orchestration, domain contract, adapter를 분리해 BE 구조와 테스트 경계를 드러낸다. | `bootstrap.py`가 concrete adapter를 조립한다. ingestion과 API의 parser 직접 import는 작은 경계를 위한 명시적 예외다. | 책임은 선명하지만 파일 수가 늘고 strict hexagonal 구조는 아니다. | `pyproject.toml`, `src/officeagent/bootstrap.py`, `src/officeagent/api/app.py`, `src/officeagent/application/` | [ADR-0001](adr/ADR-0001-repository-structure-and-naming.md) |
| DEC-002 | ACTIVE | FastEmbed 다국어 384차원 embedding, Chroma PersistentClient cosine 검색, SQLite/aiosqlite metadata·answer cache를 함께 사용한다. | API key 없이 실제 persistent vector 검색과 문서 version·cache dependency 저장을 제공한다. | application이 passage/query vector를 만들고 Chroma는 chunk를, SQLite는 document·answer·dependency를 소유한다. | 최초 model 가용성이 필요하고 두 저장소는 cross-store atomic하지 않으며 SQLite는 single-node 경계다. | `src/officeagent/infrastructure/embeddings.py`, `src/officeagent/infrastructure/chroma_store.py`, `src/officeagent/infrastructure/sqlite_store.py`, `tests/test_chroma_store.py`, `tests/test_sqlite_store.py` | [ADR-0002](adr/ADR-0002-storage-retrieval-and-cache.md) |
| DEC-003 | ACTIVE | Codex App Server stdio JSON-RPC의 실제 agent-message delta를 FastAPI SSE로 전달한다. | API key 없이 host의 ChatGPT 구독을 사용하면서 실제 streaming 요구를 충족한다. | ephemeral·read-only·never-approve·no-tool thread를 만들고 첫 delta 전 transport/timeout만 한 번 재시도한다. | host Codex login/CLI에 의존하고 delta 이후 중단은 재시도하지 않으며 stream 시작 뒤 HTTP status를 바꿀 수 없다. | `src/officeagent/infrastructure/codex_app_server.py`, `src/officeagent/api/app.py`, `tests/test_codex_protocol.py`, `tests/test_streaming.py` | [ADR-0003](adr/ADR-0003-codex-app-server-streaming.md) |
| DEC-004 | ACTIVE | pypdf PDF와 UTF-8 TXT/Markdown을 page/offset provenance와 900자·150자 overlap chunk로 처리한다. | 최소 두 형식과 검증 가능한 문서 부분 출처를 결정론적으로 제공한다. | parser가 page 또는 문자 offset section을 만들고 chunker는 page를 넘지 않는 stable chunk ID를 생성한다. | OCR과 semantic chunking을 지원하지 않아 이미지 PDF와 의미 경계 품질을 포기한다. | `src/officeagent/infrastructure/parsers.py`, `src/officeagent/application/chunking.py`, `tests/test_ingestion.py`, `tests/test_parsers_and_chunking.py` | — |
| DEC-005 | ACTIVE | offline suite는 SQLite·Chroma·parser·FastAPI를 실제로 사용하고 embedding과 LLM 경계만 deterministic fake로 교체한다. | 구독, model download, live server 없이 persistence와 API behavior를 재현한다. | keyword vector와 async delta fake를 domain port에 주입하고 adapter별 test를 별도로 둔다. | 모델 의미 품질, live Codex 호환성, Chroma 내부 partial-upsert fault는 이 suite만으로 증명하지 못한다. | `tests/helpers.py`, `tests/test_ingestion.py`, `tests/test_retrieval.py`, `tests/test_cache_invalidation.py`, `tests/test_streaming.py`, `tests/test_prompts.py`, `tests/test_retrobot_hook.py` | — |
| DEC-006 | ACTIVE | uv lockfile과 Hatchling package build, `uv run officeagent` local-host 실행을 기본으로 하고 Docker는 기본 경로에서 제외한다. | 한 줄 실행과 dependency 고정을 유지하면서 host Codex login을 비표준 container mount 없이 재사용한다. | `pyproject.toml` console script와 `uv.lock`을 사용하고 runtime DB·Codex cwd는 `.data/`에 둔다. | evaluator host에 Python·uv·Node·Codex가 필요하고 container 격리가 없으며 최초 embedding model 가용성이 필요하다. | `pyproject.toml`, `uv.lock`, `src/officeagent/main.py`, `src/officeagent/infrastructure/settings.py`, `README.md` | — |
| DEC-007 | ACTIVE | exact 우선·cosine `0.95` similar cache와 pre-write invalidation/re-index 정책을 사용한다. `CREATED`·`UNCHANGED`는 corpus 전체, `UPDATED`는 document dependency만 무효화한다. | metadata-first ingest 실패와 same-byte retry 사이에 만들어진 old-corpus cache를 순차 retry에서 제거한다. | SQLite `valid`·dependency row와 `IngestionService`가 invalidation 후 embedding/vector 교체를 수행한다. | 정상 same-hash도 전체 cache를 비우며 cross-store atomicity와 동시 recache race는 해결하지 않는다. | `src/officeagent/application/ingestion.py`, `src/officeagent/infrastructure/sqlite_store.py`, `tests/test_cache_invalidation.py` | [ADR-0002](adr/ADR-0002-storage-retrieval-and-cache.md) |
| DEC-008 | ACTIVE | score-gated retrieval, untrusted-evidence prompt, no-evidence LLM skip, server-generated deterministic citation을 사용한다. | 문서 밖 답변과 모델이 만든 허위 출처를 제한한다. | top-5 중 기본 score `0.30` 이상만 prompt에 넣고 citation은 검색된 `Chunk`에서 만들며 client-controlled metadata를 attribute용으로 escape한다. | threshold는 gold-set 최적값이 아니다. citation은 retrieval provenance이며 model이 각 청크를 사용했는지 또는 answer를 지지하는지 post-generation으로 검증하지 않는다. | `src/officeagent/application/question_answering.py`, `src/officeagent/application/prompts.py`, `PROMPT_DESIGN.md`, `tests/test_retrieval.py`, `tests/test_prompts.py` | — |
| DEC-009 | ACTIVE | Retrobot은 활성화된 post-commit hook의 Codex-only·bounded-prompt KPT 생성 경로로 운영한다. | commit 단위 과정 증거를 남기되 전역 개인 로그와 다른 project 정보를 prompt payload로 넣지 않는다. | hook이 skill, 최신 `git show`, 선택적 `retros/WORKLOG.md`를 read-only·ephemeral `codex exec`에 전달하고 생성 회고·timeline explicit path만 자동 commit한다. | read-only는 repository read-isolation이 아니며 `core.hooksPath`와 Codex가 필요하다. skip/실패 시 회고가 없고 Claude fallback·별도 수동 경로도 없다. | `.githooks/post-commit`, `retrobot/SKILL.md`, `retrobot/README.md`, `tests/test_retrobot_hook.py` | — |
| DEC-010 | ACTIVE — V2 G2 | PostgreSQL 17.10+pgvector 0.8.6, Psycopg 3와 digest-pinned Compose를 V2의 단일 영속 기반으로 채택한다. | V1 SQLite/Chroma의 cross-store transaction·backup·health 경계를 줄이고 evaluator/CI에서 같은 DB topology를 재현한다. | immutable numbered SQL, SHA ledger, history prefix, advisory lock, migration rollback과 isolated DB test를 사용한다. | Docker·extension·migration 운영비가 추가되며 G3 CorpusStore, HA/PITR, vendor SLA와 제품 API는 아직 제공하지 않는다. | `compose.yaml`, `src/officeagent/infrastructure/migrations.py`, `tests/test_migrations.py`, `docs/verification/2026-07-30-v2-g2.md` | [ADR-0004](adr/ADR-0004-postgresql-pgvector-storage.md) |

## History Policy

- ACTIVE decision을 materially 변경할 때 기존 근거를 조용히 덮어쓰지 않는다.
  기존 entry를 `SUPERSEDED`로 남기고 새 ID 또는 명확한 supersession reference를
  기록한다.
- 단순 오탈자와 evidence path 갱신은 같은 entry에서 수정할 수 있다.
- 상세 분석과 대안은 ADR로 이동하고 이 register는 간결하게 유지한다.
- implementation claim은 실제 code/test와 일치해야 한다.
- evidence/receipt 상태는 불변 source commit에 연결하고 최신 Submission HEAD는 Control sync에서 갱신한다.
## Revision History

- 2026-07-30: `DEC-010`에 V2 G2의 PostgreSQL/pgvector, Psycopg와 Compose
  선택을 등록했다. 제품 composition 전환은 G3 evidence 전까지 주장하지 않는다.

- 2026-07-28: `DEC-001`~`DEC-006`의 기술 선택을 기록했다.
- 2026-07-29: `DEC-007`이 `DEC-002`에 함께 적혀 있던 cache invalidation/retry
  정책 부분을 supersede했다. `DEC-002`는 embedding·vector·metadata storage
  선택으로 계속 ACTIVE다.
- 2026-07-29: 기존 구현의 grounding/citation과 Retrobot 운영 경계를
  `DEC-008`, `DEC-009`로 정규화했다.
- 2026-07-29: cold review에서 citation의 retrieval-provenance 의미와
  post-generation 검증 부재를 명시하고 filename metadata attribute escape를
  추가했다. 이는 answer-level grounding verifier 구현이 아니다.
- 2026-07-30: Retrobot 자동 commit을 생성 회고·timeline explicit path로 좁히고
  bounded prompt payload와 repository read-isolation을 구분했다.
