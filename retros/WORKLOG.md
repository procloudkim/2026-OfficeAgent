# 지원자 작업 로그

이 파일은 Retrobot이 회고를 만들 때 사용하는 사실 기반 입력이다. 작업 중 확인한
결정·실패·검증만 기록하고, 면접 전에 지원자가 직접 사실관계를 보완한다.

## 2026-07-28 — 시작 전 조사와 부트스트랩

### 목표

- 공식 PRD와 평가 비중을 읽고 불합격 조건을 먼저 제거한다.
- 첫 지원자 커밋 전에 Private 템플릿 저장소와 평가자 초대를 완료한다.
- 실제 토큰 스트리밍이 가능한 Codex 통합 경로를 검증한다.

### 확인한 사실

- `procloudkim/officeagent-onboarding-challenge`를 공식 템플릿에서 Private로 생성했다.
- `serithemage` collaborator 초대를 첫 지원자 커밋 전에 전송했다.
- 템플릿 최초 커밋만 존재하며 이 커밋은 168시간 계산에서 제외된다.
- 로컬에는 Python 3.11/3.12, Docker, uv, Codex CLI가 설치되어 있다.
- Codex App Server 스모크에서 `item/agentMessage/delta` 14개를 수신했고,
  재조합한 답변은 `교육비 지원 한도는 연간 200만 원입니다.`였다.

### 문제와 전환

- 템플릿 훅의 `codex --quiet`는 현재 Codex CLI에서 지원되지 않아 exit 2로 실패했다.
- `codex exec --json`은 진행 이벤트를 주지만 최종 답변의 진짜 토큰 델타 계약이
  부족했다. 그래서 공식 App Server의 `item/agentMessage/delta`를 사용하기로 했다.
- 기존 Retrobot은 전역 Codex 로그를 광범위하게 탐색해 다른 작업이나 민감정보가
  섞일 위험이 있었다. 최신 커밋과 명시적인 WORKLOG만 읽는 방식으로 범위를 줄였다.

### 다음 검증

- `uv lock`과 구독 없는 health 테스트를 통과시킨다.
- Retrobot 훅이 Windows Git 환경에서 실제 회고 커밋을 만드는지 확인한다.
- ingestion, retrieval, 캐시 무효화의 실패하는 테스트부터 작성한다.

## 2026-07-28 — Retrobot 활성화 검증

### 확인한 사실

- 공식 `retrobot/README.md`와 `retrobot/SKILL.md`를 다시 확인했다.
- 로컬 Git 설정은 `core.hooksPath=.githooks`이며, 훅 파일은 실행 모드로 추적된다.
- Git for Windows의 셸에서 `git`, `date`, `codex` 명령을 모두 찾았다.
- 일반 커밋 `4bdafcf` 뒤 회고 `f444621`이 자동 생성됐고, 회고 커밋에서는
  재귀 실행이 멈췄다.
- `uv run pytest` 결과 health 테스트 1개가 통과했다.

### 문제와 수정

- 재귀 방지 조건 `*Retrobot*`이 자동 회고뿐 아니라 제목에 Retrobot이 들어간 일반
  커밋까지 건너뛰었다. 자동 생성 커밋의 정확한 제목만 제외하도록 범위를 좁혔다.
- Windows Git Bash에서는 `TZ=Asia/Seoul`이 UTC로 처리되어 파일명과 타임라인이
  9시간 어긋났다. POSIX 시간대 문자열 `TZ=KST-9`로 교체하고 기존 기록을 정정했다.

### 남은 위험

- 현재 자동화 테스트는 health 경로만 덮는다.
- ingestion, retrieval, 캐시 무효화 테스트와 실제 애플리케이션 구현은 아직 없다.

## 2026-07-28 — GitHub 이메일 보호 대응

### 문제

- 첫 push가 `GH007: Your push would publish a private email address`로 거절됐다.
- 공개되지 않은 지원자 커밋 5개가 개인 이메일을 author와 committer 주소로 사용했다.

### 대응과 검증

- 인증된 GitHub 계정의 ID와 login으로 이 리포에만
  `227958568+procloudkim@users.noreply.github.com`을 설정했다.
- `origin/main` 이후 공개되지 않은 5개 커밋의 이메일만 재작성했다.
- 재작성 전 복구용 로컬 ref를 만들고, 커밋 수·트리·메시지·작성자명·커밋 시각이
  모두 보존됐는지 커밋별로 비교했다.
- 회고 문서의 과거 SHA 참조를 재작성된 현재 SHA와 일치시켰다.

### 다음 확인

- 변경된 커밋을 push하고 원격 `main`과 로컬 `main`의 SHA가 일치하는지 확인한다.

## 2026-07-28 — 평가 경로 중심의 수직 구현

### 목표

- PDF/TXT 업로드부터 실제 vector 검색, Codex 답변, 출처, cache까지 하나의
  수직 경로로 구현한다.
- ingestion·retrieval·cache invalidation 필수 테스트와 SSE/protocol 테스트를
  구독 없이 실행한다.
- 기술 선택을 ADR과 Why–What–How decision log로 남기고 문서와 코드를 맞춘다.

### 선택과 trade-off

- 문서 metadata와 answer cache dependency는 SQLite, 청크 vector는 Chroma로
  분리했다. 저장소가 둘이 되지만 관계형 무효화와 vector 검색을 각각 실제
  adapter test로 검증할 수 있다.
- 운영 embedding은 한국어를 지원하는 FastEmbed 다국어 MiniLM 384차원,
  테스트는 결정론적 keyword vector를 사용한다. 모델 의미 품질과 코드 회귀
  증거를 분리했다.
- Docker 안으로 host Codex credential을 전달하는 비표준 절차 대신 PRD가 허용한
  `uv run officeagent`를 한 줄 실행으로 택했다.
- citation은 모델 출력에서 파싱하지 않고 검색된 chunk metadata로 서버가 만든다.
  표현 자유는 줄지만 허위 출처를 구조적으로 막는다.

### 문제와 전환

- `uv run pytest`에서는 `tests.helpers`가 import되지 않았지만 직접 venv 실행은
  통과했다. 실제 README 명령을 기준으로 `tests/__init__.py`를 추가했고 최종
  명령을 다시 검증했다.
- 첫 FastEmbed live run은 sandbox network 차단으로 실패해 네트워크 허용
  경계에서 모델을 내려받았다.
- 첫 Codex live run은
  `thread/start.environments requires experimentalApi capability`로 실패했다.
  initialize capability를 명시하고 empty environment/dynamic tool 계약을
  적용했다.
- 초기 live smoke는 service generator만 소비해 HTTP flush 증거가 약했다.
  실제 Uvicorn socket에 upload와 SSE 질의를 보내는 방식으로 교체했다.
- 적대적 검토에서 빈 delta의 retry 경계, repository cwd 노출, 무제한 process/
  notification queue를 발견했다. 빈 delta 무시, 전용 빈 cwd, tool item 차단,
  동시 process 2개, queue 100개 제한으로 보강했다.
- SQLite foreign key 설정이 connection-scoped임을 확인해 모든 연결에서
  `PRAGMA foreign_keys=ON`을 적용하고 무결성 회귀 테스트를 추가했다.

### 검증

- `uv run pytest -q`: 20 passed.
- Ruff: source/tests/scripts 전체 통과.
- `uv run officeagent`: 실제 application startup 후 `/health/live` HTTP 200,
  정상 shutdown.
- 실제 FastEmbed + Chroma + Codex CLI 0.145.0 + Uvicorn HTTP SSE:
  cache miss, 최종 재검증 delta 17개, 첫 delta가 completed보다 먼저 관찰됨,
  `company-policy.txt` citation 확인.
- 상세 최소 영수증은 `docs/verification/2026-07-28.md`에 기록했다.

### 다음 확인

- 최종 diff·문서 링크·PRD 추적표를 다시 대조한다.
- Retrobot 회고가 생성되는 milestone commit을 만들고 같은 날 원격에 push한다.

## 2026-07-29 — 7월 28일 기록의 지원자 재검토

### 목표

- 7월 28일 자동 회고 4개를 현재 code·Git·공식 문서와 다시 대조한다.
- 지원자가 직접 이해한 내용과 Codex가 보조한 범위를 회고에 명시한다.
- 역사적 사실과 이후에 얻은 증거를 섞거나 7월 28일 작업으로 소급하지 않는다.

### 확인과 판단

- 최신 PRD가 요구 정본이고 현재 README/CLAUDE도 첫 지원자 commit 전 evaluator
  초대를 운영 순서로 둔다. 초대를 제출 때 하라는 과거 template 절차는 기각했다.
  다만 PRD상 늦은 초대만으로 자동 불이익은 아니며 미초대는 평가 대상이 아니다.
- 역사적 invitation 생성 시각은 T0 전으로 기록돼 있지만, 현재 collaborator 수락·
  접근 상태는 유효한 GitHub 인증으로 재확인하지 못해 `UNKNOWN`으로 유지한다.
- Retrobot은 push가 아니라 post-commit에서 실행되고 정확한 자동 회고 제목으로
  recursion을 멈춘다. push는 evaluator가 commit을 보게 하는 별도 단계다.
- 14-delta App Server probe는 통합 경로 선택의 사전 증거이고, OfficeAgent 전체
  Uvicorn SSE 17-delta 영수증은 뒤의 `e838cc7` 역사적 실행과 구분한다.
- SQLite는 document metadata·answer cache·dependency, Chroma는 chunk·embedding·
  retrieval metadata를 소유한다. 당시 구현 속도에 집중해 이 기본 구조를 내 말로
  설명하지 못한 점을 회고 보완에 솔직히 남겼다.

### AI 보조와 증거 경계

- Codex가 규칙 충돌 조사, code/Git 대조, 문장 초안을 보조했다.
- 지원자가 최종 책임을 지는 `## 직접 보완`만 기존 자동 KPT 아래에 추가했고 원래
  Retrobot 본문은 다시 쓰지 않았다.
- 7월 28일 snapshot의 cold review와 2026-07-29 공식 자료 fact check를 수행했다.
  현재 수정·검증 결과는 다음 7월 30일 구현·검토 묶음에서 별도로 기록한다.

## 2026-07-30 — 구현·문서 최종 검토와 Retrobot 경계 보강

### 목표

- 7월 29일 cache 보완 이후의 code와 모든 Markdown을 cold review·fact check한다.
- 지원자가 면접에서 설명할 수 있도록 과장된 evidence 의미와 backend 경계를
  바로잡는다.
- 현재 작업을 code·test·문서·회고가 함께 있는 한 개의 MECE 묶음으로 검증한다.

### Cold review에서 발견하고 고친 것

- 모든 relevance threshold 통과 hit가 citation이 되고 `grounded=true`가 되는
  현재 구현은 retrieval provenance를 제공하지만 answer-level entailment나 model이
  각 chunk를 사용했다는 보장은 아니다. README, Architecture, Prompt Design,
  traceability와 decision log에서 이 의미를 명시했다.
- client-controlled filename이 XML-like prompt attribute를 닫을 수 있어 metadata를
  `html.escape(..., quote=True)`로 escape하고 회귀 테스트를 추가했다. document body의
  의미적 prompt injection 안전성을 증명한 것은 아니다.
- Retrobot hook의 `git add retros/`는 다른 unstaged/modified 회고까지 자동 commit할
  수 있었다. 방금 생성한 회고와 timeline 두 explicit path만 stage·commit하도록
  좁히고 구조 테스트와 Git Bash `sh -n` 구문 검사를 통과시켰다.
- Retrobot의 bounded 범위는 prompt payload이고 read-only sandbox는 repository read를
  OS 수준에서 차단하지 않는다. App Server의 `.data/codex-workspace`도 실행마다
  자동으로 비우지 않으므로 secret을 두지 않는다고 문서를 정정했다.
- `CREATED`·`UNCHANGED`는 corpus 전체, `UPDATED`는 dependency cache만 무효화한다.
  README의 “관련 문서만”이라는 넓은 표현을 이 실제 policy로 교정했다.

### 검증과 증거 경계

- `.venv/Scripts/python.exe -m pytest tests -p no:cacheprovider -q` → `27 passed`.
- `.venv/Scripts/ruff.exe check --no-cache .` → PASS.
- workspace-local 임시 `UV_CACHE_DIR`에서 `uv lock --check` → PASS, lock 변경 없음.
- Git Bash `sh -n .githooks/post-commit`과 Markdown 상대 link 25개 파일 점검 → PASS.
- 일반 commit `251bf3c` 뒤 Retrobot이 `0548cec`를 자동 생성했고, 해당 auto
  commit에는 새 회고와 timeline 두 file만 포함됐다. 남은 dirty 변경은 섞이지
  않았고 push 뒤 origin/main과 local HEAD가 `0548cec`로 일치했다.
- 7월 28일·29일 complete snapshot Shower와 최종 16-file delta Shower를 수행했고,
  마지막 delta verdict는 PASS였다.
- App Server, Git post-commit, SQLite FK, Chroma cosine, FastEmbed model, FastAPI SSE,
  POSIX `TZ=KST-9` 설명을 official source와 현재 code 양방향으로 fact check했다.
- 최신 FastEmbed/Codex/Uvicorn live smoke는 다시 실행하지 않았다. 실제 17-delta
  영수증은 `e838cc7`의 역사적 증거로만 유지한다.

### 남은 한계와 AI 보조

- SQLite–Chroma cross-store 원자성, Chroma delete→upsert partial failure,
  ingest/query race, identical miss single-flight, 실제 한국어 검색 품질, citation
  entailment는 해결·검증하지 않았다.
- Codex가 cold review orchestration, defect 분석, code/test/doc 초안과 fact check를
  보조했다. 지원자는 면접용 local 학습서로 각 owner 함수·입력·출력·실패 경계를
  다시 공부하고, 이해하지 못한 결정을 독자적으로 내렸다고 주장하지 않는다.

## 2026-07-30 — V2 G0 재개와 저장소 전환 설계

### 복구와 기준선 검증

- 제한된 R0 복구 계약으로 실제 Windows 사용자 컨텍스트를 확인했다. Docker
  Desktop은 이미 running이었고 Engine 29.6.2 `linux/amd64`, context
  `desktop-linux`, Buildx default/desktop-linux node가 모두 running이었다.
  따라서 `docker desktop start`는 실행하지 않았다.
- V1 `main`, local/origin/remote SHA `29a429f`, clean worktree, Retrobot hook과
  평가 창을 다시 확인했다.
- CreatorTemp 격리 cwd와 cache/bytecode off 설정에서 V1 27 tests와 Ruff를
  통과했다. pgvector 0.8.6 PostgreSQL 17 bookworm image의 linux/amd64 manifest도
  실제 registry에서 확인했다.
- G0 PASS 뒤에만 `feat/v2-postgres-pgvector-compose` branch를 생성했다.

### 선택과 증거 경계

- V1 SQLite+Chroma는 빠른 초기 구현과 실제 vector/cache 요구를 충족했지만 두
  store 사이 transaction이 없어 metadata/cache와 vector partial state 및 stale
  recache 경계가 남는다.
- Chroma 공식 문서의 PersistentClient production 경계, PostgreSQL transaction/
  backup/support lifecycle, Docker Compose health dependency를 대조했다.
- V2는 PostgreSQL 17.10+pgvector 0.8.6 한 DB에서 document, chunk/vector,
  cache invalidation과 corpus revision을 한 transaction으로 관리하도록 설계했다.
  Redis, ANN, 전문 vector DB는 측정된 규모·SLA 요구가 없으므로 제외했다.
- Docker는 DB, migration, offline integration test와 CI만 소유하고 API/Codex는
  host에 둔다. 이는 성능 우위 주장이 아니라 transaction·backup·health 대상과
  credential 경계를 줄이는 선택이다.
- Phase2 PRD, storage/Docker Trade-off, ADR-0004, V2 Traceability는 모두
  `DRAFT`다. PostgreSQL code/test가 없으므로 구현·검증 완료로 표시하지 않았다.

### 다음 검증

- Digest-pinned Compose, immutable migration runner와 실제 PostgreSQL/pgvector
  runtime version을 검증한다.
- 이어서 unified CorpusStore rollback/concurrency test를 먼저 만들고 구현한다.

## 2026-07-30 — V2 G2 PostgreSQL/pgvector migration과 Compose 검증

### Why — 이번 게이트의 문제

- V1 SQLite와 Chroma는 각자 동작하지만 metadata/cache와 vector를 한 transaction으로
  묶지 못한다. G2는 G3가 사용할 단일 PostgreSQL 경계의 migration·rollback·
  재현 기반을 실제로 만드는 단계다.
- evaluator/CI가 같은 DB, extension과 readiness 조건에서 구독 없이 검증할 경로가
  필요했다.

### What — 실제 추가한 것

- Psycopg 3.3.4, session advisory lock, SHA-256 ledger, forward-prefix 검사,
  migration별 transaction/rollback과 credential-free JSON receipt를 추가했다.
- `0001`은 vector extension, `vector(384)` chunk/cache embedding, document version,
  corpus revision, answer cache/dependency relation을 만든다.
- Compose는 digest-pinned PostgreSQL 17.10+pgvector 0.8.6, healthcheck, migration과
  구독 없는 test를 제공한다. test runner는 검증된 이름의 temporary DB만 drop한다.

### How — 실행·검증한 경로

- `docker compose up -d --wait db` 뒤 migration head `0001`, PostgreSQL `17.10`,
  pgvector `0.8.6`을 확인했다.
- `docker compose run --rm migrate officeagent-migrate --check`는 재적용 없이 같은
  head/version을 반환했다.
- `docker compose run --rm test` 최종 결과는 `33 passed`다. 여섯 migration test가
  first apply/rerun, checksum drift, failed SQL rollback, non-prefix history
  fail-before-apply, advisory-lock serialization을 검증한다.
- host 경로는 `27 passed, 6 skipped`다. 관리자 DB URL은 Docker test service에만
  주므로 canonical DB 증거는 Compose path다.

### Trade-off — 채택하지 않은 확장

- asyncpg를 별도 도입하지 않았다. sync migration과 다음 async pool을 Psycopg 3로
  연결해 driver/transaction/error boundary를 하나로 둔다. 측정하지 않은 성능
  우위는 주장하지 않는다.
- Redis, ANN, 전문 vector DB와 API/Codex containerization은 추가하지 않았다.
  PostgreSQL/pgvector 선택도 enterprise vendor SLA 자체를 증명하지 않는다.

### 배운 점 — pin-point

- checksum과 idempotence만으로는 부족하고 applied history prefix를 먼저 검사해야
  과거 schema backfill 오류를 막는다.
- Compose의 start order와 readiness는 다르므로 healthcheck와 `service_healthy`가
  실제 계약이다.
- ‘Docker가 된다’는 주장은 host port, registry tag와 build context까지 통과해야
  한다. `55432` 예약, 존재하지 않는 uv bookworm tag, 잘못된 `--check` command,
  누락된 `.githooks`를 각각 수정하고 재검증했다.

### 아직 주장하지 않는 것

- G3 CorpusStore transaction, retrieval, cache invalidation, concurrent ingest/query,
  FastAPI wiring, Codex 호출과 SSE live smoke는 G2에서 구현·검증하지 않았다.

## 2026-07-30 — V2 P0 cache·prompt 수용 계약

### Why → What → How → Trade-off → 배운 점

- **Why**: prompt/embedding/retrieval 설정이 바뀐 뒤 stale answer를 재사용하거나,
  delimiter-like input이 prompt 구조를 흐리는 위험을 G3 전에 계약으로 고정한다.
- **What**: 세 version 동시 일치 cache identity, `PROMPT_TEMPLATE_VERSION` 단일 정본,
  JSON untrusted-data envelope, retrieval-provenance citation 의미를 `PLANNED`로
  기록했다. DEC-010~012는 모두 `PROPOSED`다.
- **How**: G3에서 real PostgreSQL cache lookup/write와 HTTP/parser/pgvector/cache
  transaction을 사용하고 LLM/embedding만 double로 둔다. question/evidence delimiter
  breakout과 version mismatch miss를 regression으로 추가한다.
- **Trade-off**: cache reuse가 줄고 test surface가 넓어진다. JSON serialization은
  injection 의미 안전성이나 answer entailment verifier를 제공하지 않는다.
- **배운 점**: schema version field 존재와 Docker local receipt만으로 policy enforcement나
  durable gate PASS를 주장할 수 없다. current-SHA integration과 remote parity, 별도
  live Codex/SSE evidence를 분리해야 한다.

### 명시적 비범위

- Structured LLM output은 별도 spike 전 구현하지 않는다.
- V1 DEC-002/007/008 및 ADR-0002를 SUPERSEDED/VERIFIED로 바꾸지 않는다.

## 2026-07-30 — V2 P0 구현과 local-worktree 통합

### Why

- cache schema field만 있고 lookup/write가 강제하지 않거나 prompt data가 delimiter
  구조를 깰 수 있으면 P0 계약은 문서상 선언에 그친다.
- G3가 실제 PostgreSQL transaction을 사용한다는 증거는 mock store가 아니라
  parser·HTTP·pgvector·cache row와 failure injection을 함께 통과해야 한다.

### What

- prompt/embedding/retrieval 세 version cache identity와 prompt version SSOT
- deterministic JSON untrusted-data envelope와 delimiter breakout 회귀
- PostgreSQL CorpusStore, revision-guarded cache write, V2 bootstrap
- TXT/PDF HTTP RAG, exact/similar cache, update invalidation과 SSE offline path
- test container의 Node/Codex/API key/token/.codex 차단

### How

- 실제 임시 PostgreSQL database에 migration을 적용하는 Compose test runner를
  사용했다.
- chunk insert 실패와 cache invalidation 이후 revision 실패를 각각 주입해 이전
  ready document/chunk, active cache와 revision의 전체 rollback을 확인했다.
- 동일/서로 다른 document 동시 ingest, 세 version mismatch와 stale revision
  cache-write skip을 검증했다.
- 최종 로컬 dirty-worktree 명령
  `docker compose run --build --rm test`는 `48 passed`였다.

### Trade-off와 배운 점

- correctness를 위해 config drift 시 cache reuse를 포기하고 soft invalidation한다.
- offline SSE PASS는 실제 Codex delta 증거가 아니다. current-SHA, remote parity,
  clean clone과 live Codex/SSE 전에는 DEC-010~012를 `PROPOSED`로 유지한다.
- 배운 점: schema와 Docker 파일 존재보다 post-invalidation rollback과
  current-match SQL predicate가 유지보수 경제성의 직접 증거다.
