# OfficeAgent Document Q&A API

업로드한 문서를 실제 벡터 검색하고, 근거가 있을 때만 Codex CLI를 호출해
출처와 함께 답하는 FastAPI 서버입니다. PDF·TXT·Markdown, 실제 SSE delta,
동일·유사 질문 캐시, 문서 변경에 따른 의존성 기반 무효화를 구현했습니다.

## 빠른 실행

요구 환경은 Python 3.11~3.13, [uv](https://docs.astral.sh/uv/), Node.js,
ChatGPT 구독으로 로그인한 `@openai/codex` CLI입니다. 별도 API 키는 사용하지
않습니다.

```bash
npm install -g @openai/codex
codex login
uv sync --locked
uv run officeagent
```

위 마지막 줄이 서버를 띄우는 **한 줄 실행 명령**입니다. 서버는
`0.0.0.0:8000`에 bind하며 로컬 접속 주소는 `http://127.0.0.1:8000`,
OpenAPI 문서는 `/docs`입니다. 최초 문서 업로드 때 FastEmbed 모델 약 220 MB를
내려받으므로 네트워크가 한 번 필요합니다.

자동화 테스트는 Codex 로그인·LLM 구독·임베딩 모델 다운로드 없이 실행됩니다.

```bash
uv run pytest
```

## API 사용

문서 업로드:

```bash
curl -F "file=@sample-docs/company-policy.txt;type=text/plain" \
  http://127.0.0.1:8000/v1/documents
```

실시간 SSE 답변:

```bash
curl -N -H "Content-Type: application/json" \
  -d '{"question":"직무 관련 교육비의 연간 지원 한도는?"}' \
  http://127.0.0.1:8000/v1/questions/stream
```

비스트리밍 JSON 답변:

```bash
curl -H "Content-Type: application/json" \
  -d '{"question":"직무 관련 교육비의 연간 지원 한도는?"}' \
  http://127.0.0.1:8000/v1/questions
```

SSE 이벤트 순서는 다음과 같습니다.

```text
answer.started
answer.delta       # Codex App Server가 보낸 실제 delta, 1회 이상
answer.citation    # score gate를 통과한 검색 청크의 서버 생성 출처
answer.completed
```

`answer.citation`은 실제 검색 청크의 provenance를 보존하지만, 모델이 반환된 모든
청크를 답변에 실제 사용했는지까지 검증하지는 않습니다. `grounded=true`도 현재는
근거 후보가 prompt에 제공되고 turn이 완료됐다는 뜻이며 answer-level entailment
판정은 아닙니다.

스트림 시작 후 실패는 HTTP 상태를 바꿀 수 없으므로 `answer.error` 이벤트로
전달합니다. 모든 이벤트에는 같은 `request_id`가 포함되며, 시작·완료 이벤트에서
`cache_hit`와 `cache_match`를 확인할 수 있습니다.

## 핵심 선택

| 관심사 | 선택 | 이유 |
|---|---|---|
| 웹/API | FastAPI + 비동기 유스케이스 | 얇은 HTTP 계층과 테스트 가능한 코어 분리 |
| 추출 | pypdf + UTF-8 텍스트 | PDF·TXT·MD 세 형식과 page/offset 출처 보존 |
| 임베딩 | FastEmbed 다국어 MiniLM, 384차원 | 한국어 포함 로컬 추론, API 키 불필요 |
| 검색 | Chroma PersistentClient, cosine | 실제 영속 벡터 DB와 명시적 임베딩 |
| 메타·캐시 | SQLite/aiosqlite | 문서 버전·답변·의존성을 트랜잭션으로 관리 |
| LLM | Codex App Server stdio JSON-RPC | ChatGPT 구독을 사용하면서 실제 delta 수신 |
| 실행 | 로컬 `uv run officeagent` | Codex 구독 세션을 호스트에서 그대로 사용 |

Docker는 의도적으로 기본 실행 경로로 선택하지 않았습니다. 컨테이너 안으로 로컬
Codex 로그인 상태를 전달하는 비표준 마운트를 필수화하는 것보다, PRD가 허용한
README 한 줄 실행을 사용해 재현성을 높였습니다.

## 테스트 의도

| 파일 | 검증 범위 |
|---|---|
| `tests/test_ingestion.py` | multipart 업로드 → TXT/PDF 추출 → SQLite·Chroma 저장 |
| `tests/test_retrieval.py` | 기대 문서 상위 검색, 출처, 근거 없음 시 LLM 미호출 |
| `tests/test_cache_invalidation.py` | exact/similar hit, UPDATED dependency·CREATED/UNCHANGED 전체 무효화 |
| `tests/test_streaming.py` | 두 fake gateway delta의 SSE 전달 순서, 출처, cache metadata |
| `tests/test_codex_protocol.py` | JSON-RPC 순서, timeout, retry 경계, delta 필터 |
| `tests/test_prompts.py` | client-controlled filename metadata의 prompt attribute 경계 |
| `tests/test_retrobot_hook.py` | 자동 회고의 generated-file-only stage·commit path |
| `tests/test_sqlite_store.py` | 문서 버전과 캐시 DB의 실제 SQL 동작 |
| `tests/test_chroma_store.py` | 실제 Chroma replace/query와 cosine score |

프로덕션 경계를 실제 구독으로 확인하려면 다음을 별도로 실행합니다. 실제 Uvicorn
소켓에 문서를 업로드하고 FastEmbed·Chroma·Codex를 거친 HTTP SSE를 검사합니다.
이 명령은 모델 다운로드와 Codex 사용량을 발생시킵니다.

```bash
uv run python scripts/live_smoke.py
```

## 설정

모든 환경 변수는 `OFFICEAGENT_` 접두사를 씁니다.

| 변수 | 기본값 |
|---|---|
| `OFFICEAGENT_DATA_DIR` | `.data` |
| `OFFICEAGENT_CHUNK_SIZE` / `OFFICEAGENT_CHUNK_OVERLAP` | `900` / `150` 문자 |
| `OFFICEAGENT_RETRIEVAL_LIMIT` | `5` |
| `OFFICEAGENT_MINIMUM_RELEVANCE_SCORE` | `0.30` |
| `OFFICEAGENT_CACHE_SIMILARITY_THRESHOLD` | `0.95` |
| `OFFICEAGENT_DATABASE_URL` | V2 G2 기본 `postgresql://…@127.0.0.1:15432/officeagent`; 제품 사용은 G3 이후 |
| `OFFICEAGENT_CODEX_FIRST_DELTA_TIMEOUT_SECONDS` | `45` |
| `OFFICEAGENT_CODEX_OVERALL_TIMEOUT_SECONDS` | `120` |
| `OFFICEAGENT_CODEX_MAX_CONCURRENT_REQUESTS` | `2` |

런타임 데이터는 `.data/`의 SQLite와 Chroma에 영속화되며 Git에는 포함되지
않습니다.

## 설계 및 평가 근거

- [ARCHITECTURE.md](./ARCHITECTURE.md): 전체 흐름, 경계, 실패·trade-off
- [PROMPT_DESIGN.md](./PROMPT_DESIGN.md): 근거 제한과 프롬프트 인젝션 완화 경계
- [요구사항 추적표](./docs/REQUIREMENTS_TRACEABILITY.md): PRD → 코드 → 테스트
- [Repository Map](./docs/REPOSITORY_MAP.md): 모든 파일의 MECE 책임·생성 주체
- [ADR](./docs/adr/): 구조·저장소·Codex 계약의 Why–What–How
- [Decision Log](./docs/DECISION_LOG.md): 짧은 기술 선택과 검증 상태
- [Retros](./retros/): AI 협업 과정과 전환 기록
- [원본 PRD](./docs/PRD.md)

현재 한계는 스캔 PDF OCR 미지원, 단일 노드 로컬 저장소, 고정 문자 청킹입니다.
SQLite–Chroma cross-store 원자성, ingest/query 동시성, 동일 질문 cache miss의
single-flight, citation과 생성 문장 사이의 의미 검증도 구현하지 않았습니다.
구현하지 않은 기능을 문서에서 구현 완료로 표시하지 않습니다.

## V2 브랜치: PostgreSQL/pgvector 인프라 (G2)

이 브랜치는 V1을 보존하면서 단계적으로 전환한다. 현재 검증 범위는
PostgreSQL 17.10 + pgvector 0.8.6 Compose, checksum을 강제하는 forward-only
migration `0001`, 그리고 LLM 구독 없이 격리 DB에서 실행한 Docker test
`33 passed`다. **G3 CorpusStore와 API 제품 통합은 아직 구현하지 않았다.** 따라서
현재 `uv run officeagent` 제품 composition은 계속 V1 SQLite+Chroma를 사용한다.
Docker는 이 단계에서 DB, migration과 test만 소유하며 Codex App Server는 host에
남는다.

```bash
# 구독·LLM 호출 없이 실행하는 V2 canonical one-line integration test
docker compose run --rm test

# migration 적용 및 현재 head/version 검사
docker compose run --rm migrate officeagent-migrate
docker compose run --rm migrate officeagent-migrate --check
```

DB는 기본적으로 `127.0.0.1:15432`에만 공개된다. Windows Application Control이
생성된 `officeagent-migrate` shim을 차단하는 환경에서는 다음 policy-safe module
경로를 사용할 수 있다. Linux Compose의 console-script 경로는 위 명령으로 실제
검증했다. 이 host fallback 역시 `docker compose up -d --wait db`로 DB가 먼저
healthy여야 한다.

```powershell
uv run --frozen python -m officeagent.infrastructure.migrations --check
```

- **Why**: V1 SQLite metadata/cache와 Chroma vector write를 한 transaction으로
  묶을 수 없어 partial-state 복구 경계가 둘이다.
- **What**: PostgreSQL 17.10, pgvector 0.8.6, Psycopg 3.3.4와 numbered SQL을
  선택했다.
- **How**: image digest, DB healthcheck, migration advisory lock, checksum ledger,
  forward-prefix 검사, migration별 rollback, test마다 고유 DB 생명주기로 검증한다.
- **Trade-off**: Docker·migration 운영비가 추가되며, 아직 HA/PITR나 V2 API
  composition을 제공하지 않는다.
- **배운 점**: 측정하지 않은 성능 우위보다 driver·transaction·backup·health의
  failure surface를 줄이는 편이 이번 유지보수 경제성을 더 직접적으로 증명한다.
