# OfficeAgent Document Q&A API

PDF·TXT·Markdown를 수집해 PostgreSQL+pgvector에서 검색하고, 근거가 있을 때만
Codex App Server를 호출해 retrieval-provenance citation과 함께 답하는 FastAPI
서버입니다. exact/similar answer cache, 문서 변경 무효화와 실제 SSE delta를
지원합니다.

> V2 상태: PostgreSQL 제품 조립과 P0 계약은 현재 로컬 워크트리에 구현되어 있고
> 구독 없는 Docker suite `48 passed`를 확인했습니다. 아직 current-SHA commit,
> remote parity, clean-clone 제품 실행과 실제 Codex/SSE 영수증은 없습니다.
> 따라서 DEC-010~012는 `PROPOSED`이며 V1 결정을 대체하거나 V2를 `VERIFIED`로
> 표현하지 않습니다.

## 평가자용 구독 없는 한 줄 테스트

Docker Desktop 또는 Docker Engine과 Compose v2만 필요합니다. 테스트 컨테이너는
Codex, Node, API key와 `.codex` mount 없이 실행되며 LLM과 embedding만
deterministic double로 교체합니다.

```bash
docker compose run --build --rm test
```

이 명령은 임시 PostgreSQL database를 만들고 migration을 적용한 뒤 실제
PostgreSQL, pgvector, parser, HTTP/SSE와 cache transaction을 사용해 전체 suite를
실행하고 database를 제거합니다. 현재 로컬 워크트리 영수증은 `48 passed`입니다.

## 제품 실행

제품 API와 Codex는 host에서, PostgreSQL/pgvector는 Docker에서 실행합니다. 로컬
Codex 로그인 상태를 컨테이너에 mount하지 않기 위한 의도적 경계입니다.

요구 환경:

- Python 3.11~3.13과 [uv](https://docs.astral.sh/uv/)
- Docker Compose v2
- Node.js와 ChatGPT 구독으로 로그인한 `@openai/codex` CLI

```bash
docker compose up -d --wait db
npm install -g @openai/codex
codex login
uv sync --locked
uv run officeagent
```

`uv run officeagent`가 startup migration을 확인하고 `0.0.0.0:8000`에 bind합니다.
로컬 접속 주소는 `http://127.0.0.1:8000`, OpenAPI 문서는 `/docs`입니다. 최초
실행에는 FastEmbed 모델 다운로드가 필요합니다.

실제 구독을 사용하는 current-SHA smoke는 최종 gate에서 별도로 실행합니다.

```bash
uv run python scripts/live_smoke.py
```

## API

문서 업로드:

```bash
curl -F "file=@sample-docs/company-policy.txt;type=text/plain" \
  http://127.0.0.1:8000/v1/documents
```

실시간 SSE:

```bash
curl -N -H "Content-Type: application/json" \
  -d '{"question":"직무 관련 교육비의 연간 지원 한도는?"}' \
  http://127.0.0.1:8000/v1/questions/stream
```

비스트리밍 JSON:

```bash
curl -H "Content-Type: application/json" \
  -d '{"question":"직무 관련 교육비의 연간 지원 한도는?"}' \
  http://127.0.0.1:8000/v1/questions
```

SSE 이벤트 순서는 다음과 같습니다.

```text
answer.started
answer.delta
answer.citation
answer.completed
```

실패가 stream 시작 뒤 발생하면 `answer.error`로 종료합니다. 모든 이벤트에는 같은
`request_id`가 있으며 started/completed에서 `cache_hit`, `cache_match`,
`cache_written`, `cache_skip_reason`을 확인할 수 있습니다.

`answer.citation`은 score gate를 통과한 검색 청크의 retrieval provenance입니다.
모델이 모든 후보 청크를 사용했거나 각 생성 문장이 citation에 의해 entail된다는
검증은 아닙니다.

## Why–What–How 기술 선택

| 관심사 | What | Why | How / Trade-off |
|---|---|---|---|
| 웹/API | FastAPI + async use case | HTTP/SSE와 application 규칙 분리 | 얇은 route지만 host Codex와 DB 두 runtime이 필요 |
| 추출 | pypdf + UTF-8 TXT/MD | 세 형식과 page/offset provenance | OCR·semantic chunking은 비범위 |
| 임베딩 | FastEmbed 다국어 MiniLM 384차원 | 한국어 로컬 추론과 API-key 불필요 | 최초 모델 다운로드와 CPU 비용 |
| 저장·검색 | PostgreSQL 17.10 + pgvector 0.8.6 + Psycopg 3 | document/vector/cache/revision을 한 transaction에 유지 | Docker·migration 운영비, 단일 노드 |
| 캐시 | exact 우선, cosine similar `0.95`, 세 version identity | 설정 변경 후 stale answer 재사용 방지 | cache reuse 감소, active row soft invalidation |
| LLM | Codex App Server stdio JSON-RPC | ChatGPT 구독과 실제 incremental delta | host Node/Codex 로그인 의존 |
| 재현성 | digest-pinned DB image + isolated test database | evaluator/CI topology 고정 | API/Codex는 credential 격리를 위해 host 유지 |

배운 점은 기술 개수보다 실패 경계를 줄이는 것이 중요하다는 것입니다.
SQLite+Chroma의 두 저장·복구 경계를 PostgreSQL transaction 하나로 줄였고,
성능 우위나 vendor SLA는 측정·계약 없이 주장하지 않습니다.

## P0 무결성 계약

- `answer_cache`는 `prompt_version`, `embedding_version`,
  `retrieval_version`을 저장합니다.
- exact/similar hit는 active row와 세 현재 version이 모두 일치할 때만
  허용합니다.
- `PROMPT_TEMPLATE_VERSION`의 단일 정본은
  `src/officeagent/application/prompts.py`입니다.
- question, evidence text와 metadata는 하나의 JSON untrusted-data envelope로
  직렬화하고 `<`, `>`, `&`를 JSON escape합니다.
- delimiter breakout 회귀는 구조적 serialization을 증명하며 prompt injection의
  의미적 완전 차단을 뜻하지 않습니다.
- structured LLM output은 별도 spike 전까지 구현하지 않습니다. 현재 계약은
  plain text delta와 server-generated provenance입니다.

## 테스트

| 파일 | 실제 경계 |
|---|---|
| `tests/test_v2_offline_paths.py` | HTTP TXT/PDF 수집, parser, PostgreSQL/pgvector RAG, exact/similar cache, 갱신 무효화, SSE |
| `tests/test_postgres_corpus_store.py` | transaction rollback, ready corpus, 세 version, revision guard, 동일/서로 다른 문서 동시성 |
| `tests/test_migrations.py` | empty DB, 재실행, checksum/history fail-closed, rollback, advisory lock |
| `tests/test_test_container_isolation.py` | Node/Codex/API key/token/.codex 부재 |
| `tests/test_prompts.py` | JSON round-trip, question/evidence/metadata delimiter breakout, version SSOT |
| 기존 V1 adapter tests | SQLite/Chroma 기준선 회귀; V2 제품 증거와 구분 |

host의 빠른 unit/legacy 회귀는 DB test를 skip합니다.

```bash
uv run pytest
uv run ruff check src tests
```

공식 PostgreSQL 경로는 위 Docker 한 줄 명령입니다.

## 설정

모든 환경 변수는 `OFFICEAGENT_` 접두사를 사용합니다.

| 변수 | 기본값 |
|---|---|
| `OFFICEAGENT_DATABASE_URL` | `postgresql://officeagent:…@127.0.0.1:15432/officeagent` |
| `OFFICEAGENT_CHUNK_SIZE` / `OFFICEAGENT_CHUNK_OVERLAP` | `900` / `150` |
| `OFFICEAGENT_RETRIEVAL_LIMIT` | `5` |
| `OFFICEAGENT_MINIMUM_RELEVANCE_SCORE` | `0.30` |
| `OFFICEAGENT_CACHE_SIMILARITY_THRESHOLD` | `0.95` |
| `OFFICEAGENT_CODEX_FIRST_DELTA_TIMEOUT_SECONDS` | `45` |
| `OFFICEAGENT_CODEX_OVERALL_TIMEOUT_SECONDS` | `120` |
| `OFFICEAGENT_CODEX_MAX_CONCURRENT_REQUESTS` | `2` |

DB port는 기본 `127.0.0.1:15432`에만 공개됩니다. migration 상태 검사는 다음
명령으로 수행합니다.

```bash
docker compose run --rm migrate officeagent-migrate --check
```

## 남은 gate와 한계

- current-SHA commit/remote parity, clean clone과 실제 Codex incremental SSE smoke
- `/health/ready`, structured timing log와 CI workflow
- 이미지 PDF OCR, 문서 삭제 API, 사용자별 corpus, HA/PITR
- 동일 질문 cache miss single-flight와 answer-level entailment verifier

상세 근거:

- [Architecture](./ARCHITECTURE.md)
- [Prompt Design](./PROMPT_DESIGN.md)
- [V2 Traceability](./docs/V2_REQUIREMENTS_TRACEABILITY.md)
- [Decision Log](./docs/DECISION_LOG.md)
- [ADR-0004](./docs/adr/ADR-0004-postgresql-pgvector-storage.md)
- [원본 PRD](./docs/PRD.md)
