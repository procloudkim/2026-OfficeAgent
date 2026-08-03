# Technology Decision Register

> **사용법:** 이 파일은 결정의 색인입니다. 아래 register에서 관심 결정을 고른 뒤
> 연결된 ADR의 Context→Decision→Consequences를 읽으십시오. 실행·문제 해결은
> [Evaluator Manual](./EVALUATOR_MANUAL.md), 현재 증거는
> [V2 Traceability](./V2_REQUIREMENTS_TRACEABILITY.md)가 소유합니다.

현재 기술 선택의 간결한 `What–Why–How–Trade-off` 정본입니다. README는 실행과
핵심 선택을 요약하고, `ARCHITECTURE.md`는 시스템 흐름과 실패 경계를 설명합니다.
프롬프트 고유 결정은 `PROMPT_DESIGN.md`, 현재 V2 요구사항별 검증 상태는
`V2_REQUIREMENTS_TRACEABILITY.md`, V1 역사 증거는 `REQUIREMENTS_TRACEABILITY.md`,
되돌리기 어렵거나 위험한 결정의 상세 근거는 연결된 ADR이 소유합니다.

`Status`는 decision lifecycle만 나타내며 구현 검증 상태가 아닙니다. 변동 SHA,
remote parity와 runtime freshness는
[V2 Traceability](V2_REQUIREMENTS_TRACEABILITY.md)만 소유합니다.

## Register

`DEC-001`~`DEC-009`는 제출 가능한 V1 기준선의 ACTIVE 결정입니다.
`DEC-010`, `DEC-011`, `DEC-013`~`DEC-018`은 P0-08 계약에 따라 `PROPOSED` overlay로
유지하며 V1 결정을 대체하지 않습니다. `DEC-012`의 structured-output 보류는
V2 branch에서 `DEC-013`이 supersede했습니다. 이 lifecycle과 별개로 현재
구현·검증 evidence는 V2 Traceability에서 판정합니다.

P0-08은 구현 미완료 표지가 아니라 V1 rollback 결정을 `main`에서 자동 supersede하지
않도록 미리 고정한 review 경계입니다. DEC-010·011·013~017은 `main`
supersession에 대해 `PROPOSED`, V2 branch 구현 evidence는 `VERIFIED`입니다.
DEC-018도 lifecycle은 `PROPOSED`이며 구현·품질 evidence는 V2 Traceability의
`INT-CHUNK-01` 판정을 따릅니다.

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
| DEC-009 | ACTIVE | Retrobot은 활성화된 post-commit hook의 Codex-only·bounded-prompt KPT 생성 경로로 운영한다. | commit 단위 과정 증거를 남기되 전역 개인 로그와 다른 project 정보를 prompt payload로 넣지 않는다. | hook이 commit blob의 고정 skill, helper, SHA·시각·aggregate shortstat와 WORKLOG 최신 날짜 H2 하나만 사용한다. 모델의 일곱 필드 JSON을 로컬 재검사·Markdown 렌더링하고, blocking Git-directory flock과 조기 `index.lock`·`HEAD.lock`, pinned HEAD ref, 분리된 tree/final index, no-clobber 회고 설치와 guarded timeline append 뒤 두 path만 commit한다. final index는 lock 뒤 current shared index에서 출발해 unrelated flags와 staged intent를 보존한다. 두 관리 경로 자체가 stage됐거나 special index flag를 가지면 fail-closed한다. fd-origin ownership hard link는 Git 내부 recovery journal에 보존하고 실패 시 공개 path를 자동 삭제하지 않는다. | 이 structured JSON은 Retrobot 전용 제품 schema와 별도 계약이다. 16 KiB prompt·8 KiB 로그 상한으로 과거 맥락은 제한된다. read-only는 repository read-isolation이 아니며 `core.hooksPath`, `flock`과 Codex가 필요하다. Git directory와 worktree가 다른 filesystem이면 fail-closed하며, lock 경합은 bounded retry 뒤 실패한다. skip/실패 시 회고가 없고 Claude fallback·별도 수동 경로도 없다. | `.githooks/post-commit`, `scripts/retrobot_*.py`, `retrobot/SKILL.md`, `retrobot/README.md`, `tests/test_retrobot_*.py` | — |
| DEC-010 | PROPOSED | PostgreSQL 17.10+pgvector 0.8.6, Psycopg 3와 digest-pinned Compose를 V2의 단일 영속 기반으로 전환한다. | V1 SQLite/Chroma의 cross-store transaction·backup·health 경계를 줄이고 evaluator에서 같은 DB topology를 재현한다. | migration 위에 unified CorpusStore, revision guard와 V2 bootstrap을 구현한다. | Docker·extension·migration 운영비가 추가되며 production HA·recovery control plane과 vendor SLA는 비범위다. | `src/officeagent/infrastructure/postgres_corpus_store.py`, `compose.yaml`, [V2 Traceability](V2_REQUIREMENTS_TRACEABILITY.md) | [ADR-0004](adr/ADR-0004-postgresql-pgvector-storage.md) |
| DEC-011 | PROPOSED | answer cache hit는 prompt/embedding/retrieval version이 현재 설정과 모두 일치할 때만 허용한다. | prompt, embedding model 또는 retrieval policy 변경 뒤 오래된 answer를 재사용하지 않는다. | `PROMPT_TEMPLATE_VERSION`을 `prompts.py` 단일 정본으로 두고 PostgreSQL/SQLite lookup·write·invalidation이 세 version을 비교한다. | version 조합별 cache reuse가 줄고 stale row는 soft invalidation 기록으로 남는다. | `tests/test_postgres_corpus_store.py`, `tests/test_v2_offline_paths.py`, [V2 Traceability](V2_REQUIREMENTS_TRACEABILITY.md) | [ADR-0004](adr/ADR-0004-postgresql-pgvector-storage.md) |
| DEC-012 | SUPERSEDED by DEC-013 | question/evidence/metadata를 JSON untrusted-data envelope로 만들고 structured LLM output은 별도 spike 전까지 보류한다. | 당시 검증되지 않은 output contract 변경을 막으면서 delimiter breakout을 구조적으로 줄인다. | deterministic JSON serialization과 breakout regression을 먼저 구현했다. | 보류 조건은 DEC-013의 protocol/API/live spike가 충족되어 종료됐다. JSON input envelope와 server-generated citation 결정은 유지한다. | `PROMPT_DESIGN.md`, `tests/test_prompts.py` | — |
| DEC-013 | PROPOSED | Codex의 한 필드 `{"answer": string}` output schema를 strict incremental decoder로 plaintext SSE에 projection한다. | structured-output 가산점과 기계 검증을 추가하면서 실제 streaming과 server-generated citation 계약을 유지한다. | 같은 turn/item만 bind하고 JSON escape·Unicode를 incremental decode한 뒤 completion에서 duplicate/additional key, type, blank, NUL, 크기와 전체 일치를 재검증한다. visible answer 전 transport/timeout만 한 번 재시도한다. | custom parser와 로컬 65,536자/1,048,576자/8 MiB 상한의 유지보수 책임이 생긴다. 이미 전송한 일부 SSE는 오류 시 회수할 수 없으며 schema가 factuality를 보장하지 않는다. | `src/officeagent/infrastructure/structured_answer.py`, `tests/test_codex_protocol.py`, `tests/test_streaming.py`, [V2 Traceability](V2_REQUIREMENTS_TRACEABILITY.md) | [ADR-0005](adr/ADR-0005-codex-structured-output-streaming.md) |
| DEC-014 | PROPOSED | upload와 question pipeline에 content-free terminal timing receipt를 한 번 생성한다. | cache/no-evidence/live/error/cancel 경로의 병목을 입력 원문 없이 비교하고 후속 benchmark의 측정 계약을 고정한다. | API UUID를 전달하고 monotonic nanosecond 차이로 실행 stage만 누적한다. LLM stage는 upstream delta 대기만 재며 total은 consumer suspension을 포함할 수 있다. bounded schema를 best-effort logger로 내보낸다. | 단일 process wall-time 계측이며 분산 trace·percentile·SLO가 아니다. logger 실패는 제품을 중단하지 않아 receipt 유실 가능성이 있다. | `src/officeagent/application/observability.py`, `tests/test_timing_observability.py` | — |
| DEC-015 | PROPOSED | versioned synthetic gold set을 제품 ingestion/Q&A 경계로 실행하고 deterministic metric을 낸다. | retrieval·abstention 회귀를 재현하고 사람의 dataset 채택을 정확한 bytes에 결합한다. | manifest가 case·문서를 hash하고 dev/process-holdout을 분리한다. runner는 disposable loopback DB만 사용하며 retrieval hit/MRR, lexical claim coverage와 exact abstention을 계산한다. | 현재 승인은 `pending`이고 holdout은 공개돼 있다. lexical proxy는 semantic entailment·NLI·factuality가 아니며 실제 Codex 평가는 별도 승인·실행이 필요하다. | `evals/gold/v1`, `src/officeagent/evaluation`, `tests/test_evaluation_*.py` | — |
| DEC-016 | PROPOSED | 완성 application prompt를 tiktoken `o200k_base` proxy로 세고, 기본 `4096` 안에 드는 retrieval-ordered evidence만 Codex·citation에 사용한다. | 비정상적으로 큰 검색 문맥을 LLM에 그대로 보내는 경로를 제한하면서 작은 top-k를 유지한다. | 전체 prompt fast path 뒤 rank 순서의 greedy keep/oversized skip을 적용한다. 하나도 못 넣으면 `prompt_budget`으로 LLM/cache를 건너뛰고, 일부 제외 답변은 cache하지 않는다. template·counter digest·budget을 effective prompt version에 묶는다. | exact Codex tokenizer·system context·output reserve 보장이 아니며, encoding 최초 load와 repeated count 비용, cache reuse 감소, 큰 상위 hit를 건너뛰는 품질 trade-off가 있다. 의미 entailment도 검증하지 않는다. | `src/officeagent/application/prompts.py`, `src/officeagent/infrastructure/tokenization.py`, `tests/test_prompt_packing.py`, `PROMPT_DESIGN.md` | — |
| DEC-017 | PROPOSED | 논리 snapshot restore와 physical archived-WAL PITR를 분리한 opt-in disposable PostgreSQL 17 drill을 둔다. | backup 생성 명령의 존재보다 실제 restore 가능성과 PITR 용어 경계를 실행으로 확인한다. | 제품 migration runner와 source ledger, dedicated replication role, custom dump restore, verified base backup, target 전·후 marker replay, promotion과 cleanup-before-receipt를 검사한다. | 같은 local Docker backend의 합성 증거이며 schedule·retention·off-host copy·encryption·monitoring, production RPO/RTO와 HA를 제공하지 않는다. | `compose.recovery-drill.yaml`, `src/officeagent/infrastructure/postgres_recovery_drill.py`, `tests/test_postgres_recovery_drill.py`, [Evaluator Manual](EVALUATOR_MANUAL.md#33-일회용-postgresql-복구-drill) | — |
| DEC-018 | PROPOSED | page-safe chunk에 기본 900 code point·900 `o200k_base` proxy-token 이중 상한과 150 code-point overlap을 적용하고 policy를 versioning한다. | token-dense 입력의 과대 chunk를 제한하고 경계 변경 후 이전 vector·answer cache를 조용히 재사용하지 않는다. | algorithm, 상한, overlap, tokenizer identity를 digest해 `documents.chunking_version`과 retrieval identity에 연결한다. `0002`는 legacy row를 unknown-settings로 표시하고 same-file reupload의 policy mismatch만 `UPDATED`·재청킹·전체 cache invalidation으로 이관한다. | overlap은 code-point 단위고 grapheme 경계·background rechunk는 제공하지 않는다. semantic 경계, 정확한 Codex tokenizer, gold-set 품질 향상을 보장하지 않고 policy 변경 시 embedding·cache reuse가 줄어든다. | `src/officeagent/application/chunking.py`, `src/officeagent/migrations/0002_document_chunking_version.sql`, `tests/test_parsers_and_chunking.py`, `tests/test_postgres_corpus_store.py` | — |

## History Policy

- ACTIVE decision을 materially 변경할 때 기존 근거를 조용히 덮어쓰지 않는다.
  기존 entry를 `SUPERSEDED`로 남기고 새 ID 또는 명확한 supersession reference를
  기록한다.
- 단순 오탈자와 evidence path 갱신은 같은 entry에서 수정할 수 있다.
- 상세 분석과 대안은 ADR로 이동하고 이 register는 간결하게 유지한다.
- implementation claim은 실제 code/test와 일치해야 한다.
- evidence/receipt 상태는 불변 source commit에 연결하고 최신 Submission HEAD는 Control sync에서 갱신한다.
## Revision History

- 2026-08-04: page-safe 문자·proxy-token 이중 상한, chunking policy digest와
  forward-only same-file reupload 이관 경계를 DEC-018로 기록했다. 품질 향상은
  실행하지 않은 gold 평가 전까지 주장하지 않는다.
- 2026-08-04: logical snapshot restore와 physical archived-WAL PITR를 구분하는 local
  disposable recovery drill을 DEC-017로 기록했다. production 복구 체계나 HA 주장은
  포함하지 않는다.
- 2026-08-04: tiktoken `o200k_base` proxy의 완성 prompt packing과 cache identity,
  prompt-budget 기권 경계를 DEC-016으로 기록했다.
- 2026-08-04: hash·승인·split·disposable DB 경계를 가진 gold evaluator를 DEC-015로
  기록했다. 사용자 채택과 실제 Codex 품질 결과는 아직 주장하지 않는다.
- 2026-08-03: privacy-safe stage timing source와 failure/cancellation 계약을 DEC-014로
  기록했다. 성능 우위나 SLO는 별도 benchmark 전까지 주장하지 않는다.
- 2026-08-03: DEC-012의 structured-output 보류 조건을 protocol/API/cache/live spike로
  해소하고 DEC-013으로 supersede했다. JSON input envelope와 server citation은 유지한다.
- 2026-08-03: DEC-010~012의 current-SHA PostgreSQL, clean launch, 실제 Codex/SSE와
  문서 evidence를 검증했다. P0-08 계약에 따라 decision lifecycle은 `PROPOSED`로
  유지하고, 제품 evidence의 `VERIFIED` 상태와 분리했다.

- 2026-07-30: P0 addendum에 따라 `DEC-010`~`DEC-012`를 모두 `PROPOSED`로
  기록했다. V2 수용 evidence가 모두 충족되기 전에는 V1 결정을 supersede하거나
  V2 제품을 `VERIFIED`로 표현하지 않는다.
- 2026-07-30: unified CorpusStore, three-version cache와 JSON prompt envelope를
  구현했다. 실행 evidence는 V2 Traceability에서 관리하며 decision lifecycle은
  계속 `PROPOSED`다.

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
