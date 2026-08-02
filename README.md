# OfficeAgent Document Q&A API

PDF·TXT·Markdown 문서를 저장하고 PostgreSQL+pgvector에서 관련 내용을 검색하는
FastAPI 문서 Q&A 서버입니다. 근거가 없으면 답변하지 않으며, 근거가 있으면 Codex
App Server 응답을 SSE로 전달하고 문서명·버전·청크 위치를 출처로 함께 반환합니다.
동일·유사 질문 cache와 문서 변경 시 cache 무효화를 지원합니다.

> V2의 구현·검증 상태는 `PARTIAL`입니다. 변동 SHA, remote parity, runtime 영수증과
> 남은 gate는 [V2 Traceability](./docs/V2_REQUIREMENTS_TRACEABILITY.md)만 정본으로
> 관리합니다.

## 구현 출처

지원자 확인: 제공된 템플릿 8ca57661f7a9b54e8bac2ed8957e5c3dacc77502에서 증분 구현했으며, 기존 오픈소스 프로젝트를 통째로 복사하지 않았고 제3자 의존성은 pyproject.toml과 uv.lock에 선언했습니다.

## 평가자용 구독 없는 한 줄 테스트

현재 실행 영수증 범위는 WSL2에 통합된 Docker Desktop의 Linux `x86_64`
(`linux/amd64`) backend와 Compose v2입니다. 별도 WSL Docker Engine과 다른
architecture는 `UNKNOWN`입니다. 테스트 컨테이너는 Codex, Node, API key와
`.codex` mount 없이 실행되며 LLM과 embedding만 deterministic double로 교체합니다.

```bash
docker compose run --build --rm test
```

이 명령은 임시 PostgreSQL database를 만들고 migration을 적용한 뒤 실제
PostgreSQL, pgvector, parser, cache transaction과 in-process FastAPI ASGI
HTTP/SSE 계약으로 전체 suite를 실행하고 database를 제거합니다. 최신 실행 상태는
[V2 Traceability](./docs/V2_REQUIREMENTS_TRACEABILITY.md)에서 확인합니다.

## 제품 실행

제품 API와 Codex는 host에서, PostgreSQL/pgvector는 Docker에서 실행합니다. 로컬
Codex 로그인 상태를 컨테이너에 mount하지 않기 위한 의도적 경계입니다.

요구 환경:

- Python 3.11~3.13과 [uv](https://docs.astral.sh/uv/)
- Docker Compose v2
- Node.js와 ChatGPT 구독으로 로그인한 `@openai/codex` CLI

Codex 설치·로그인은 최초 한 번 수행합니다.

```bash
npm install -g @openai/codex@0.146.0 && codex login
```

Retrobot은 식별자를 치환한 제한 입력과 commit 요약만 외부 Codex에 보냅니다.
고정 skill, 환경 allowlist, 빈 cwd, read-only sandbox, shell 비활성화, timeout과
출력 검증을 fail-closed로 적용합니다. 인증에는 실제 `CODEX_HOME`만 사용하며,
provider-side privacy까지 증명하는 경계는 아니므로 hook 실행 전 입력을 검토합니다.

이후 제품은 다음 한 줄로 실행합니다.

```bash
uv sync --locked && docker compose up -d --wait db && uv run officeagent
```

API를 중지한 뒤 `docker compose down`으로 PostgreSQL 컨테이너를 종료할 수 있습니다.
기본 `15432` port가 사용 중이면 실행 전에 `OFFICEAGENT_DB_PORT`와
`OFFICEAGENT_DATABASE_URL`의 port를 같은 값으로 설정합니다.

`uv run officeagent`가 startup migration을 확인하고 기본 `127.0.0.1:8000`에
bind합니다. 다른 host에 공개해야 할 때만 `OFFICEAGENT_API_HOST`를, port를
바꿀 때는 `OFFICEAGENT_API_PORT`를 명시적으로 설정합니다. 로컬 접속 주소는
`http://127.0.0.1:8000`, OpenAPI 문서는 `/docs`입니다.
최초 실행에는 FastEmbed 모델 다운로드가 필요합니다.

실제 구독을 사용하는 current-SHA smoke는 최종 gate에서 별도로 실행합니다.

```bash
uv run python -m scripts.live_smoke
```

이 명령은 repository 밖의 일회용 workspace와
`officeagent_test_<12hex>` database를 만듭니다. 로컬 `postgres` admin
database에서만 생성·migration·사용한 뒤 실패해도 삭제하므로 기존 제품
database의 문서나 cache를 읽거나 변경하지 않습니다. admin target의 host가
`127.0.0.1`, `localhost`, `::1` 중 하나가 아니거나 database가
`postgres`가 아니면 실행 전에 실패합니다.

live 계약은 citation, `miss → exact hit → 문서 update → miss`, 두 miss의
복수 Codex delta를 모두 검증합니다. 따라서 초기 miss와 update 후 miss에서
실제 Codex 구독 호출이 발생합니다. 출력 JSON은 검증된 `codex --version`과
상태·출처 metadata만 담고 모델 답변·작업 경로·DB conninfo는 제외합니다.
검색 문맥은 외부 Codex 서비스로 전송되므로 승인된 샘플로만 실행합니다.

외부 LLM 호출 전 최종 감사용 내부 readiness smoke는 다음 명령으로 실행합니다.

```bash
uv run python -m scripts.live_smoke --readiness-only
```

이 모드는 repository 밖 workspace와 일회용 local PostgreSQL database에서 실제
Uvicorn HTTP `/health/ready`만 확인하고 정리 완료 뒤 credential-free JSON 한 줄을
출력합니다. 감사 runner는 repository의 ignored `.env`를 읽지 않고 database URL과
admin URL만 환경 override로 허용합니다. Codex version, 샘플 문서와 질문·LLM
endpoint는 호출하지 않습니다.

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

DB·migration·pgvector readiness:

```bash
curl http://127.0.0.1:8000/health/ready
```

기본 성공 응답은 credential 없이 `status`, `migration_head`, `vector_version`만 반환하고,
DB·migration·pgvector 검사가 실패하거나 기본 5초 bound를 넘으면 내부 예외를 숨긴
`503`을 반환합니다. Codex 로그인과 LLM 호출은 readiness 조건이 아닙니다.
Fresh-run 감사가 `OFFICEAGENT_READINESS_INSTANCE_NONCE`에 64자 소문자 hex
상관관계 토큰을 지정한 경우만 응답에 `instance_nonce`를 추가합니다. 미지정
시 기존 3필드 계약이 유지되며, 이 값은 credential이 아닌 일회성 감사
식별자로서 로그와 오류에는 기록하지 않습니다.

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
| `tests/test_v2_offline_paths.py` | in-process FastAPI ASGI TXT/PDF 수집·SSE, parser, PostgreSQL/pgvector RAG, exact/similar cache, 갱신 무효화 |
| `tests/test_postgres_corpus_store.py` | transaction rollback, ready corpus, 세 version, revision guard, 동일/서로 다른 문서 동시성 |
| `tests/test_migrations.py` | empty DB, 재실행, checksum/history fail-closed, rollback, advisory lock |
| `tests/test_test_container_isolation.py` | Node/Codex/API key/token/.codex 부재와 disposable effective libpq database target |
| `tests/test_prompts.py` | JSON round-trip, question/evidence/metadata delimiter breakout, version SSOT |
| `tests/test_live_smoke.py` | disposable local DB, G7 citation/cache/update 전이, sanitized receipt의 offline 계약 |
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
| `OFFICEAGENT_API_HOST` | `127.0.0.1` |
| `OFFICEAGENT_API_PORT` | `8000` (valid range `1..65535`) |
| `OFFICEAGENT_DATABASE_URL` | `postgresql://officeagent:…@127.0.0.1:15432/officeagent` |
| `OFFICEAGENT_DATABASE_ADMIN_URL` | unset; live smoke만 로컬 product URL에서 `postgres` target을 유도 |
| `OFFICEAGENT_READINESS_TIMEOUT_SECONDS` | `5` (maximum `30`) |
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

## 남은 검증과 비목표

- 최종 변경 SHA의 공식 Docker suite, clean checkout과 실제 Codex incremental SSE
- 최종 변경 SHA의 `/health/ready` 실제 DB/migration/vector 검증
- structured timing log와 CI workflow는 이번 제출의 `PLANNED` 비목표
- 이미지 PDF OCR, 문서 삭제 API, 사용자별 corpus, HA/PITR
- 동일 질문 cache miss single-flight와 answer-level entailment verifier

상세 근거:

- [Architecture](./ARCHITECTURE.md)
- [Prompt Design](./PROMPT_DESIGN.md)
- [V2 Traceability](./docs/V2_REQUIREMENTS_TRACEABILITY.md)
- [V1/V2 Comparison](./docs/V1_V2_COMPARISON.md)
- [Decision Log](./docs/DECISION_LOG.md)
- [ADR-0004](./docs/adr/ADR-0004-postgresql-pgvector-storage.md)
- [원본 PRD](./docs/PRD.md)
