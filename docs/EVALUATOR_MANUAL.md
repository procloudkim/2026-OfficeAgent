# OfficeAgent Evaluator Manual

> **독자:** 저장소를 처음 보는 평가자, 리뷰어, 인수인계 개발자
> **목표:** 구독 없는 자동 검증부터 시작하고, 선택적으로 실제 제품과 Codex
> 스트리밍까지 안전하게 확인한다.
> **정본:** 짧은 명령은 [README](../README.md), 현재 판정은
> [V2 Requirements Traceability](./V2_REQUIREMENTS_TRACEABILITY.md)가 소유한다.

이 매뉴얼은 각 단계의 사전 조건, 정상 결과, 증명 범위와 실패 시 확인할 위치를 함께
설명한다. 과제 원문은 [`docs/PRD.md`](./PRD.md)이며 이 문서가 요구사항을 바꾸지는
않는다.

## 1. 먼저 평가 모드를 고른다

| 모드 | 필요한 것 | 외부 LLM 전송 | 확인 범위 | 권장 상황 |
|---|---|---:|---|---|
| A. Docker 자동 검증 | Docker Compose, 최초 download network 또는 cache | 없음 | DB·parser·RAG·cache·HTTP/SSE 계약 | **첫 평가 경로** |
| B. Readiness smoke | Docker, Python, uv | 없음 | 일회용 DB·migration·실제 Uvicorn readiness | 실행 준비 확인 |
| C. 제품 수동 실행 | 위 항목 + Codex 로그인 | 질문 시 있음 | upload→retrieve→Codex→SSE·citation | API 관찰 |
| D. Actual live smoke | 위 항목 + 명시적 승인 | 샘플 문서·질문 | miss→hit→update→miss와 실제 delta | 최종 통합 확인 |

평가자는 A부터 시작한다. D는 필수 단계가 아니다. `scripts.live_smoke`는 공개 sample
fixture를 사용하더라도 검색 문맥과 질문을 외부 Codex 서비스로 보내므로, 전송을
승인한 환경에서만 실행한다.

## 2. 사전 조건을 확인한다

검증된 환경은 WSL2와 Docker Desktop의 Linux `x86_64` backend, Compose v2다. 다른
architecture와 별도 WSL Docker Engine은 실행 영수증 범위 밖이다.

저장소 root에서 다음을 확인한다.

```bash
docker version
docker compose version
git status --short
```

정상 조건:

- Docker client와 server가 모두 응답한다.
- `docker compose version`이 v2를 출력한다.
- 제출 commit을 그대로 평가한다면 `git status --short` 출력이 비어 있다.

최초 실행에는 container image와 Python package를 받을 network가 필요할 수 있다.
소요 시간은 Docker·package cache와 network 상태에 따라 달라지므로 고정된 완료 시간을
전제로 하지 않는다.

제품 수동 실행이나 readiness smoke에는 Python 3.11 이상 3.14 미만과 uv가 필요하다.

```bash
python3 --version
uv --version
```

실제 Codex 모드 C/D에는 Node.js와 고정된 Codex CLI가 추가로 필요하다.

```bash
node --version
codex --version
```

CLI가 없거나 이 저장소의 검증 version을 재현해야 할 때만 다음을 실행한다.
`npm install -g`는 host의 global package를 변경하고, `codex login`은 사용자
credential session을 생성한다.

```bash
npm install -g @openai/codex@0.146.0
codex login
```

`codex --version`이 `codex-cli 0.146.0`을 출력하고 로그인이 완료돼야 C·D 모드를
실행한다.

## 3. 구독 없이 자동 검증한다

### 3.1 전체 suite 실행

```bash
docker compose run --build --rm test
```

첫 실행은 image와 package를 내려받으므로 이후 실행보다 오래 걸린다. 명령은 digest로
고정한 PostgreSQL 17.10+pgvector 0.8.6을 준비하고, 임시 database에 migration을
적용한 뒤 실제 DB·parser·FastAPI ASGI 경로를 실행하고 database를 제거한다.

정상 조건은 process exit code `0`이다. 최신 pass/skip 수와 영수증 경계는
[V2 Requirements Traceability](./V2_REQUIREMENTS_TRACEABILITY.md)가 소유한다.

의도된 skip은 격리 image에 없는 host Git·`flock`이 필요한 Retrobot runtime test다.
최신 skip 수는 Traceability에서 확인하며, 필수 ingestion·retrieval·cache 경로가
skip됐다는 뜻은 아니다.

이 suite가 증명하는 것:

- migration과 PostgreSQL/pgvector transaction 경계
- TXT/PDF/Markdown parser와 document version
- score gate, no-evidence LLM skip, prompt proxy packing과 citation mapping
- exact/similar cache, version mismatch, invalidation과 revision guard
- structured output decoder와 SSE/JSON 실패 시 no-cache

이 suite가 증명하지 않는 것:

- 실제 Codex 구독·network·모델 품질
- 한국어 retrieval 설정의 최적성
- 답변 문장과 citation의 의미적 entailment
- HA, backup/restore, 대규모 부하 성능

### 3.2 정적 검사와 빠른 host suite

```bash
uv sync --locked
uv run ruff check src tests
uv run pytest
```

host suite는 Compose DB URL과 isolation sentinel이 없으면 DB 경로를 명시적으로
skip한다. host와 Docker test 개수를 합산하거나 서로의 대체 증거로 사용하지 않는다.

## 4. 제품 API를 처음부터 실행한다

### 4.1 의존성과 DB 준비

```bash
uv sync --locked
docker compose up -d --wait db
uv run officeagent
```

정상 시작 시 기본 주소는 `http://127.0.0.1:8000`, OpenAPI UI는 `/docs`다.
첫 문서 업로드나 질의에서 FastEmbed model을 lazy load하므로 최초 embedding이
길어질 수 있다.

기본 DB 계정과 비밀번호는 local development 전용이며 API에는 인증·TLS·사용자별
corpus 격리가 없다. 기본 loopback bind를 유지하고, 인증과 network 경계 없이
`OFFICEAGENT_API_HOST`를 외부 interface로 바꾸지 않는다. 기본 실행에는 `.env`가
필수는 아니며, 환경 값을 추가했다면 `.env`와 credential을 commit하지 않는다.

### 4.2 readiness 확인

다른 terminal에서 실행한다.

```bash
curl -sS http://127.0.0.1:8000/health/ready
```

기본 정상 응답:

```json
{"status":"ready","migration_head":"0001","vector_version":"0.8.6"}
```

이 응답은 DB·migration·pgvector 준비만 뜻한다. Codex 로그인, 모델 품질 또는 외부
network 준비를 뜻하지 않는다. 내부 검사가 실패하거나 기본 5초를 넘으면 HTTP `503`을
반환한다.

### 4.3 sample 문서 업로드

```bash
curl -i -F "file=@sample-docs/company-policy.txt;type=text/plain" \
  http://127.0.0.1:8000/v1/documents
```

정상 응답은 HTTP `201`이다.

| 필드 | 확인할 내용 |
|---|---|
| `document_id` | 서버가 만든 식별자이므로 고정값을 기대하지 않는다. |
| `document_version` | 최초 업로드 `1`; 내용 변경 뒤 증가한다. |
| `change` | 최초 `created`, 동일 bytes `unchanged`, 변경 `updated` |
| `chunk_count` | 1 이상 |
| `invalidated_cache_entries` | 문서 변경으로 무효화한 cache 수 |

문서 identity는 경로를 제거한 filename을 NFKC·casefold·공백 정규화한 값이다. 같은
filename과 같은 bytes를 다시 올리면 `unchanged`로 version을 유지하고, 같은 filename의
내용을 바꾸어 다시 올리면 별도 update endpoint 없이 `updated`와 다음 version을 만든다.
다른 filename은 새 문서다.

### 4.4 실제 SSE 질문

제품의 일반 질문도 질문과 score gate를 통과한 검색 문맥을 외부 Codex 서비스로
보낸다. sample이 아닌 문서를 업로드했다면 해당 데이터 전송 승인을 먼저 확인한다.

```bash
curl -N -H "Content-Type: application/json" \
  -d '{"question":"직무 관련 교육비의 연간 지원 한도는?"}' \
  http://127.0.0.1:8000/v1/questions/stream
```

정상 cache miss의 event 순서:

```text
answer.started
answer.delta        # 한 번 이상
answer.citation     # score gate를 통과한 검색 근거
answer.completed
```

샘플 근거 값은 연간 `200만원`이지만 LLM 문장은 매 실행 동일하다고 가정하지 않는다.
citation의 `filename`이 `company-policy.txt`, `document_version`이 현재 version인지
확인한다. `answer.delta`는 provisional이므로 `answer.completed`를 받아야 성공이다.

동일 질문을 다시 보내면 `answer.started`의 `cache_hit=true`와
`cache_match="exact"`를 확인할 수 있다. cache replay delta는 live generation과 같은
의미가 아니다.

### 4.5 비스트리밍 JSON 확인

```bash
curl -sS -H "Content-Type: application/json" \
  -d '{"question":"직무 관련 교육비의 연간 지원 한도는?"}' \
  http://127.0.0.1:8000/v1/questions
```

정상 응답에는 `request_id`, `answer`, `citations`, `cache_hit`, `cache_match`,
`grounded`, `cache_written`, `cache_skip_reason`이 있다. 모델의 raw JSON grammar는
API 밖으로 노출하지 않는다.

### 4.6 근거가 없는 질문 확인

```bash
curl -sS -H "Content-Type: application/json" \
  -d '{"question":"오늘 구내식당 메뉴는 무엇인가요?"}' \
  http://127.0.0.1:8000/v1/questions
```

score `0.30` 이상인 검색 결과가 없으면 HTTP `200`과 고정된 한국어 거절 답변을
반환한다. 이때 `grounded=false`, `citations=[]`, `cache_written=false`,
`cache_skip_reason="no_evidence"`이며 Codex를 호출하지 않는다. SSE도
`answer.started → answer.delta → answer.completed`로 끝나고 citation은 없다.

검색 결과는 있지만 완성 prompt의 tiktoken `o200k_base` proxy budget에 청크 하나도
들어가지 않으면 다른 고정 기권 답변을 반환한다. 이때도 `grounded=false`,
`citations=[]`, `cache_written=false`이고 Codex를 호출하지 않지만
`cache_skip_reason="prompt_budget_exceeded"`로 원인을 구분한다. 이 budget은 실제
Codex tokenizer나 전체 App Server context 한도를 보장하지 않는다. 기본값 변경은
`OFFICEAGENT_PROMPT_INPUT_TOKEN_BUDGET`으로 하며 기존 cache identity도 함께 바뀐다.
일부 청크만 들어가면 선택된 citation으로 정상 생성하되
`cache_skip_reason="prompt_evidence_reduced"`와 `cache_written=false`로 끝난다.

## 5. readiness와 actual live smoke를 구분한다

외부 전송 없는 readiness smoke:

```bash
docker compose up -d --wait db
uv run python -m scripts.live_smoke --readiness-only
```

이 모드는 repository 밖 일회용 workspace와 local PostgreSQL database에서 실제
Uvicorn `/health/ready`를 호출하고 정리한다. Codex, sample 문서와 질문은 사용하지
않는다.

외부 Codex를 사용하는 live smoke:

```bash
uv run python -m scripts.live_smoke
```

실행 전 `codex login`, sample fixture의 외부 전송 승인과 local `postgres` admin
target을 확인한다. runner는 `officeagent_test_<12hex>` database만 만들고 종료 시
삭제한다. host가 loopback이 아니거나 admin database가 `postgres`가 아니면 시작 전에
fail-closed한다. 정상 결과는 답변 원문·DB URL·workspace path를 제외한 JSON 한 줄이며
`miss → exact hit → update → miss`, 복수 delta와 citation version `1 → 2`를 확인한다.

## 6. HTTP와 SSE 실패를 해석한다

| 결과 | 의미 | 먼저 확인할 것 |
|---|---|---|
| upload `413` | 기본 20 MiB(20,971,520 bytes) 초과 | 파일 크기와 `OFFICEAGENT_MAX_UPLOAD_BYTES` |
| upload `415` | `.pdf`·`.txt`·`.md` 외 filename extension | extension; MIME은 현재 허용 판정 기준이 아님 |
| upload/query `422` | 빈 문서·질문 또는 validation 실패 | response `detail`과 입력 |
| query JSON `502` | LLM transport·timeout·structured validation 실패 | API log의 error type, Codex 상태 |
| query JSON `502` before Codex | tokenizer proxy load/count 실패 | tiktoken encoding cache·runtime log |
| readiness `503` | DB·migration·pgvector 준비 실패 또는 timeout | DB health와 migration check |
| SSE `answer.error` | stream 시작 뒤 최종 성공 계약 실패 | 앞선 delta를 확정 답변으로 저장하지 않음 |

structured 응답이 중간에 실패하면 이미 보낸 provisional delta는 회수할 수 없다.
대신 citation, `answer.completed`와 cache write를 금지한다. client는 HTTP `200`만으로
성공을 판단하지 말고 마지막 SSE event를 확인해야 한다.

## 7. 문제 해결

| 증상 | 가능한 원인 | 확인·해결 |
|---|---|---|
| `port is already allocated` | host `15432` 사용 중 | `OFFICEAGENT_DB_PORT`와 DB URL port를 같은 값으로 변경 |
| DB가 healthy가 되지 않음 | image pull·volume·password 문제 | `docker compose logs db` 확인 후 DB 재시작 |
| migration check 실패 | schema drift 또는 미적용 migration | 아래 check 실행; ledger를 수동 수정하지 않음 |
| 첫 업로드·질의가 오래 걸림 | FastEmbed model의 최초 lazy download | API log를 확인하고 download 완료 대기 |
| `codex: command not found` | CLI 미설치 | 고정 version 설치 후 `codex --version` 확인 |
| Codex 인증 오류 | login 만료·잘못된 `CODEX_HOME` | host에서 다시 로그인; credential을 container에 mount하지 않음 |
| 첫 delta timeout | process/network 지연 | 첫 visible delta 전 한 번만 retry; 원인 확인 뒤 timeout 조정 |
| 반복 `answer.error` | malformed output·item 불일치 | 실패 답을 cache하지 말고 protocol test로 재현 |
| Retrobot 회고가 없음 | hook 미설정·skip·guard 거부 | [Retrobot manual](../retrobot/README.md)의 독립 검증 실행 |

migration check:

```bash
docker compose run --rm migrate officeagent-migrate --check
```

문제 해결 과정에서 `.env`, Codex 원문 응답, DB connection string이나 모델 답변을
공개 issue·회고에 붙이지 않는다.

## 8. 종료와 정리

foreground API는 `Ctrl-C`로 중지하고 컨테이너는 다음처럼 종료한다.

```bash
docker compose down
```

제품 PostgreSQL volume 삭제는 기존 문서·cache를 지우므로 기본 cleanup에 포함하지
않는다. test/live runner의 임시 database는 runner가 직접 정리한다.

## 9. 더 깊게 읽는 순서

1. [Architecture](../ARCHITECTURE.md): request 흐름, transaction과 runtime 경계
2. [Prompt Design](../PROMPT_DESIGN.md): untrusted input, schema, SSE·cache 의미
3. [ADR-0004](./adr/ADR-0004-postgresql-pgvector-storage.md): 저장소 선택과 대안
4. [ADR-0005](./adr/ADR-0005-codex-structured-output-streaming.md): decoder·실패 계약
5. [V2 Traceability](./V2_REQUIREMENTS_TRACEABILITY.md): 요구사항별 상태와 증거 한계
6. [Repository Map](./REPOSITORY_MAP.md): 주장별 code/test 위치

역사 문서는 생성 시점의 증거다. 현재 상태와 충돌하면 Traceability와 현재 code/test를
우선하고, 과거 문장을 현재 완료 claim으로 자동 승격하지 않는다.
