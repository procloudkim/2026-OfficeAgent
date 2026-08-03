# 지원자 작업 로그

이 파일은 지원자가 직접 보완하는 사실 기록이다. 작업 중 확인한 결정·실패·검증만
기록하며, Retrobot은 최신 날짜 H2 구간 하나만 8 KiB 상한 안에서 외부로 전송한다.

## 2026-07-28 — 시작 전 조사와 부트스트랩

### 목표

- 공식 PRD와 평가 비중을 읽고 불합격 조건을 먼저 제거한다.
- 첫 지원자 커밋 전에 Private 템플릿 저장소와 평가자 초대를 완료한다.
- 실제 토큰 스트리밍이 가능한 Codex 통합 경로를 검증한다.

### 확인한 사실

- `<private-repository>`를 공식 템플릿에서 Private로 생성했다.
- `<evaluator-collaborator>` 초대를 첫 지원자 커밋 전에 전송했다.
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

- 인증된 GitHub 계정의 ID와 login에 대응하는 repository-local identity를 사용했다.
- author와 committer 주소는 `<github-noreply-address>`로 설정했으며 실제 주소는
  Retrobot 입력에서 제외했다.
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

## 2026-08-02 — V2 최종화와 WSL/Docker 재검증

### 목표

- clean checkout에서 재현 가능한 PostgreSQL/pgvector V2와 credential-free 검증
  경계를 마무리한다.
- 최종 SHA 전에 code, test, 문서와 Retrobot 증거를 일치시킨다.

### 변경과 판단

- API 기본 bind를 loopback으로 제한하고 `/health/ready`는 migration head와
  pgvector version만 반환하며 실패 세부를 숨기게 했다.
- Codex child environment는 allowlist로 제한했다. live smoke는 repository 밖
  workspace와 전용 disposable DB에서 `miss → exact hit → document update → miss`를
  검증하고 답변 본문, DB URL과 data path를 receipt에서 제외하도록 TDD했다.
- Docker test image는 필요한 smoke script만 copy하고 Node, Codex, credential env와
  `.codex` 부재 gate를 유지했다.
- Retrobot은 commit 제목·파일명·작성자 없이 commit blob의 최신 WORKLOG H2와 SHA,
  commit 시각, aggregate shortstat만 전송한다. child env는 neutral HOME과 실제 auth용 `CODEX_HOME`, credential-free
  proxy·certificate 경로만 허용하고 user config/rules와 shell tool을 끈다.
- Codex 실행 전 prompt의 환경 민감 값·identity·credential URI를 검사한다. 실행은
  300초로 제한한다. 모델은 schema로 제한된 일곱 필드 JSON만 반환하고 guard가
  중복·누락·타입·길이·민감 값을 재검사한 뒤 SHA·시각·heading이 고정된 Markdown을
  로컬 렌더링한다. 실패하면 소유한 temp만 정리하고 사용자 경로는 덮어쓰지 않는다.

### 검증

- WSL2 Docker client/server, Compose와 `hello-world`를 확인했다.
- 2026-08-03 current worktree의 Docker suite →
  `167 passed, 6 expected skips in 9.27s`. 6건은 격리 image에 의도적으로 없는
  Git·flock을 요구하는 host 전용 Retrobot runtime test다.
- 2026-08-03 current worktree의 host suite →
  `155 passed, 18 expected skips in 57.23s`.
- Retrobot context·hook·guard·publisher·runtime 집중 suite →
  `87 passed in 30.53s`.
- Ruff, `uv lock --check`, Compose config, hook syntax와 diff check를 통과했다.
- Codex를 호출하지 않는 readiness-only smoke의 실제 Uvicorn `/health/ready`는
  HTTP `200`, `ready`, migration head `0001`, pgvector `0.8.6`을 반환했고 종료 뒤
  `officeagent_test_%` database 잔존 수는 `0`이었다.
- 최종 감사 runner는 ignored `.env`를 배제하고 local database target만 allowlist하며,
  Codex workspace basename을 고정하고 disposable root containment를 검증한다.
- Retrobot은 commit blob의 skill과 WORKLOG에서 최신 날짜 H2 하나만 추출한다. 다음
  임의 H2에서 멈추고 8,192/8,193-byte 경계를 검사한다. helper는 blob을 quoted
  `python -c` 인자로 직접 실행한다. blocking Git-directory flock과 표준
  `index.lock`·`HEAD.lock`을 Codex 호출 전에 bounded retry로 획득한다. commit tree와
  final user index를 분리하고, lock 뒤 current shared index에서 final을 만들어
  unrelated flags·intent-to-add를 보존한다. 두 관리 경로의 staged blob이나
  `assume-unchanged`/`skip-worktree` flag는 clean preimage가 아니므로 Codex 호출 전에
  중단한다. no-clobber 회고 설치, fd-origin ownership과
  guarded timeline suffix append를 사용하며 실패 후 공개 파일과 inode는 자동
  삭제하지 않고 Git 내부 recovery journal에 남긴다. DrvFS의 non-atomic rename fallback은
  사용하지 않으며 Git dir/worktree가 다른 filesystem이면 fail-closed한다. 이
  경계는 provider-side privacy나 repository의 OS-level read isolation을 증명하지
  않는다.
- fresh-run 귀속용 64-hex instance nonce는 설정된 감사 실행에서만 readiness에
  echo하며, 기본 3필드 응답과 readiness-only receipt는 그대로 유지한다.

### 남은 경계

- 실제 Codex HTTP/SSE live smoke의 sample fixture context 외부 전송은 승인됐으며,
  final current-SHA 실행 전까지 live `VERIFIED`를 주장하지 않는다.
- `873e155`, `2111b2e`, `d3dbd0e`, `1a8d090`의 Retrobot 호출은 각각 기존
  Markdown 출력 계약을 통과하지 못해 회고를 publish하지 않았다. comment 예외를
  보완한 뒤에도 모델 heading 편차가 남아, 모델은 구조화 값만 만들고 형식은 로컬
  코드가 소유하도록 전환했다. 인증에는 실제 `CODEX_HOME` 접근이 필요하며 auth
  자체의 provider/CLI 경계는 이 로컬 guard가 증명하지 않는다.
- clean checkout, remote parity와 final recorder/audit은 최종 commit 뒤 실행한다.

## 2026-08-03 — 최종 증거와 평가자 문서 정합성

### Why

- 독립 zero-context 감사에서 구현·runtime은 통과했지만 README, Architecture와 V2
  trace 문서가 final evidence 이전의 `PARTIAL` 상태를 계속 노출했다.
- 평가자는 외부 Wiki를 보지 않아도 제출 repository만으로 실행 결과, 잔여 위험과
  제출 권고를 구분할 수 있어야 한다.

### What

- 최신 G8 bundle이 확인한 Docker, real readiness, detached clean launch, actual
  Codex/Uvicorn SSE, 문서·출처·Retrobot과 remote parity를 평가자 문서에 맞췄다.
- V2 Traceability와 V1/V2 비교를 현재 사실 중심으로 다시 구성하고 중복된 과거
  worktree 서술을 `HISTORIC` 경계로 압축했다.
- `REQ-09` structured output과 `PROC-04` worked-day push completeness는 근거 없이
  승격하지 않고 `PARTIAL`로 유지했다. DEC-010~012도 P0-08 lifecycle에 따라
  `PROPOSED`를 유지했다.

### How와 종료 조건

- 제품 코드나 runtime 계약은 바꾸지 않고 evaluator-facing 문서와 이 WORKLOG만
  수정했다.
- 직전 G8 evidence 기준선은 공식 Docker suite `167 passed, 6 skipped`, 필수 node
  `3/3`, readiness HTTP `200`/migration `0001`/pgvector `0.8.6`과 actual Codex/SSE를
  통과했다.
- 이 변경의 종료 조건을 문서 commit·자동 Retrobot commit 뒤 새 HEAD의 host/Docker
  suite, readiness, detached clean launch, actual Codex/SSE, 문서·provenance·
  Retrobot·remote profile 재실행으로 고정했다.
- Local, tracking과 live remote SHA가 같고 worktree가 clean인 non-force push만 새
  G8 final audit의 `PASS` 입력으로 인정하도록 했다.

### 배운 점

- 구현이 끝나도 오래된 상태 문구는 허위·미완료 인상을 만들 수 있다. 실행 영수증과
  평가자 문서는 같은 current-SHA 갱신 주기를 가져야 한다.
- scoring/process의 정직한 `PARTIAL`을 불합격처럼 확대하지 않으면서도 명시적으로
  공개하는 것이 제출 완성도와 증거 신뢰도를 함께 높인다.

## 2026-08-03 — 평가 상태 namespace와 Retrobot 독립 검증

### Why

- 최종 독립 감사에서 제출 source의 숫자형 gate와 별도 내부 계약 gate가 같은
  ID처럼 읽혔고, 생성 시점 회고의 후속 제안이 최신 상태처럼 해석됐다.
- Retrobot 구현은 검증됐지만 평가 입력이 설명 문서에 치우쳐 실제 hook, guard,
  회귀와 Git object를 한 번에 확인하기 어려웠다.

### What

- V2 Traceability를 현재 상태의 단일 정본으로 두고 `SOURCE-EVIDENCE`,
  `OFFICIAL-RULES`, `INTERNAL-V2`를 분리했다.
- 현재 문서의 숫자형 gate ledger를 제거하고, README와 Architecture의 중복된 실행
  결과는 Traceability 링크로 축약했다. 과거 Retrobot 회고 본문은 수정하지 않았다.
- Retrobot 문서에 활성 hook, 집중 회귀, 최신 commit의 부모와 두-path diff를 직접
  확인하는 네 명령을 추가했다.

### 검증과 종료 조건

- Retrobot context·guard·hook·publisher·runtime 집중 suite `87 passed`, Ruff와
  diff check를 통과했다.
- ignored evaluator 임시 파일은 Git index와 제출 history에 포함되지 않았고 감사
  종료 뒤 제거했다.
- 문서 commit 뒤 자동 Retrobot commit을 분리하고, 새 최종 HEAD에서 current-SHA
  profile, snapshot, validator와 non-force remote parity를 다시 확인해야 종료한다.

## 2026-08-03 — REQ-09 구조화 출력과 평가자 냉독 검증

### 목표와 선택

- 가산점 항목인 REQ-09를 필수 합격 gate로 바꾸지 않고, 실제 Codex structured
  output을 기존 plaintext SSE·server citation·cache 계약에 결합한다.
- `turn/start.outputSchema`는 `answer` string 한 필드만 허용하고, 전용 bounded
  decoder가 JSON escape와 Unicode를 incremental하게 해석한다. prompt version은
  v3로 올려 이전 plain-output cache를 재사용하지 않는다.

### TDD와 수정

- 기존 protocol 기준선 `13 passed` 뒤 RED는 raw JSON 노출, schema 부재와 invalid
  output 수용을 포함해 `15 failed, 6 passed`였다. 구현 뒤 집중 경로를 GREEN으로
  전환했다.
- 독립 코드 리뷰가 visible delta 뒤 raw OSError 재시도, cleanup 예외 masking,
  서로 다른 item splice, escaped NUL, fragment-list 메모리 증폭, stdio line limit과
  live/cache 공백 불일치를 찾았다. 각각 RED로 재현한 뒤 item binding, StringIO,
  로컬 상한, no-retry와 문자열 보존으로 수정했다.
- completion 전 SSE delta는 확정 답변이 아니라 provisional이다. 후속 protocol
  오류에서는 이미 보낸 text를 회수할 수 없지만 error로 끝내고 citation,
  completed와 cache write를 금지한다.

### Fact check와 Shower

- 공식 Codex App Server 문서에서 current-turn `outputSchema`, 사용한 정확한
  one-field 예시, item identity와 streamed agent-message delta를 확인했다.
- RFC 8259에서 duplicate member의 비결정적 상호운용성과 구현별 parser limit
  허용을 확인했다. 65,536 answer code point, 1,048,576 raw code point와 8 MiB line은
  upstream 사양이 아니라 로컬 정책으로만 문서화했다.
- 첫 무맥락 냉독은 provisional 성공 기준, retry cutoff와 evidence locator가
  불명확해 CONDITIONAL이었다. `answer.completed` 성공 기준, application yield
  cutoff, item mismatch, 문자 단위와 sanitized receipt의 증명 한계를 명시하고
  모든 split point·1문자 fragment differential 회귀를 추가했다.
- 두 번째 냉독은 구현·실패 계약에 모순이 없다고 봤고, 새 source SHA의 core/live/
  document 세 recorder receipt 발급만 최종 PASS 조건으로 남겼다.

### 검증 결과

- host suite: `175 passed, 18 expected skips`
- canonical Docker PostgreSQL/pgvector suite: `188 passed, 6 expected skips`
- actual Codex CLI 0.146.0 + Uvicorn + disposable DB: `status=ok`, migration `0001`,
  pgvector `0.8.6`, `miss → exact cache-key hit → update → miss`, plaintext delta
  `19 → 1 → 17`, citation document version `1 → 2`
- Ruff, lock check, Compose config, hook syntax와 diff check를 통과했다.

### Trade-off와 다음 종료 조건

- custom state machine의 유지보수 표면을 감수하는 대신 전체-buffer 응답 없이
  JSON grammar를 숨기고 완료 시 strict parser와 결과를 다시 대조한다.
- schema는 답변 사실성·citation entailment를 증명하지 않는다. tokenizer 최적화,
  latency·의미 품질 benchmark, CI와 운영 복구는 구현했다고 주장하지 않는다.
- 이 일반 commit과 자동 Retrobot commit 뒤 같은 최종 SHA의 core/live/document
  receipt, snapshot, validator와 non-force remote parity를 모두 확인해야 종료한다.

## 2026-08-03 — 역사 회고의 지원자 직접 보완 반영

### Why

- 자동 KPT는 commit별 과정 증거지만 지원자가 실제로 code path와 trade-off를
  이해했다는 증거는 아니다.
- 기존 회고 13건 중 7건과 새 structured-output 회고 1건의 `직접 보완`이 안내
  comment만 남아 있어, 면접에서 현재 상태와 역사적 Try를 혼동할 위험이 있었다.

### What

- 비어 있던 8개 `직접 보완`에 당시 목표, 후속 검증, 기각 대안, 남은 한계와
  AI-assisted 책임 경계를 추가했다.
- `11c6cea` 기준선과 후속 `c42e7c9` structured-output 구현을 소급하지 않고 각
  역사 시점과 별도 evidence로 구분했다.
- 제품 코드·테스트·설계 계약은 바꾸지 않았다. 회고 보완 commit이 다시 빈 메타
  회고를 만드는 순환을 피하기 위해 문서화된 `RETROBOT_SKIP=1`을 사용한다.

### 검증과 책임 경계

- 8개 대상 파일의 기준 commit이 Git object로 존재하고, 변경 범위가 각 파일의
  `## 직접 보완`과 이 WORKLOG section뿐인지 확인한다.
- Codex가 저장소 사실 대조와 문장 초안을 보조했다. 외부에서 1인칭으로 사용할
  문장은 지원자가 code path 하나, 실패 테스트 하나와 기각 대안 하나를 설명할 수
  있을 때만 유지한다.
- 이번 문서 commit 뒤 현재 branch의 focused/full 검증과 non-force remote parity를
  다시 확인하되, 문서 변경만으로 새로운 제품 성능을 주장하지 않는다.

## 2026-08-03 — 독자 중심 평가 매뉴얼과 문서 진입 경로

### Why

- README에는 실행 명령이 있었지만 사전 조건, 예상 응답, 실패 해석과 cleanup이 여러
  설계 문서와 test에 흩어져 처음 보는 평가자가 순서를 재구성해야 했다.
- 상세 설명을 모든 문서에 복제하면 숫자와 상태가 다시 drift하므로, 빠른 입구와
  상세 runbook, 상태 정본을 분리할 필요가 있었다.

### What

- `docs/EVALUATOR_MANUAL.md`에 평가 모드 선택, 구독 없는 Docker 검증, 제품 실행,
  HTTP/SSE 예상 결과, live data-egress 경계, 오류 복구와 cleanup을 단계별로 추가했다.
- README에는 독자별 5분 경로를 두고 Architecture, Prompt Design, Repository Map,
  decision·trade-off 문서에는 독자와 문서 역할을 명시했다.
- V2 Traceability는 runtime anchor와 handoff HEAD를 구분하고, path-scoped equivalence
  명령으로 docs-only commit이 실제 live를 새 HEAD에서 재실행한 것처럼 보이지 않게 했다.
- Retrobot 문서에는 사전 조건, 기대 결과, 문제 해결과 reflection-only skip의 사용
  경계를 추가했다.

### Fact check와 냉독

- 저장소 code·settings·test와 대조해 upload 기본 상한을 20 MiB로, `415`
  판정을 filename extension으로, FastEmbed를 최초 embedding의 lazy load로 바로잡았다.
- 냉독은 근거 없는 10분 예상치, global npm install·login의 무조건 명령,
  test count 중복을 지적했다. 시간 주장은 제거하고 global 변경은 선택 모드로
  한정했으며 실행 수치는 Traceability로 되돌렸다.
- 문서-only commit에서 과거 live 영수증을 최종 HEAD의 새 실행으로 재해석하지 않는다.
  대신 runtime path 동등성을 확인하고, live runtime anchor와 문서 handoff HEAD의
  증거 귀속을 분리했다.

### Technical Writing 완료 기준

- 독자가 `무엇→사전 조건→명령→정상 결과→실패 처리→한계` 순서로 따라갈 수 있다.
- 실행 숫자와 현재 판정은 Traceability 한 곳이 소유하고 다른 문서는 링크한다.
- 원본 PRD, 자동 KPT와 역사 verification receipt는 현재 매뉴얼처럼 다시 쓰지 않는다.
- 모든 Markdown link, command spelling, Docker test, readiness, runtime-equivalence와
  remote parity를 최종 commit에서 다시 확인한다.

## 2026-08-03 — 냉독 후 매뉴얼 사실·안전 경계 교정

### Why

- 첫 매뉴얼 초안은 test fixture의 1 MB 값을 제품 기본 업로드 한도로 잘못 읽었고,
  FastEmbed 초기화 시점과 업로드 허용 기준도 실제 code보다 넓게 표현했다.
- 실제 live 영수증은 structured runtime anchor에 귀속되므로 docs-only HEAD에서
  모든 profile을 다시 실행했다고 표현하면 안 된다.

### What

- 제품 설정의 20 MiB 한도, filename extension 판정, embedding lazy load를 code와
  대조해 수정했다.
- 문서 갱신이 같은 정규화 filename 재업로드라는 점과 무근거 JSON/SSE 결과,
  인증·TLS·사용자별 corpus 부재, 일반 질문의 외부 데이터 전송 경계를 추가했다.
- 실행 숫자는 Traceability가 소유하고 README·manual은 exit code와 링크를 안내한다.
- runtime anchor와 handoff HEAD의 path-scoped equivalence를 유지해 실제 실행 귀속을
  과장하지 않는다.

## 2026-08-03 — same-SHA 증거 정책 단순화와 최종 냉독

### Why

- `$factchk`에서 recorder가 최종 HEAD와 영수증의 exact-SHA 일치를 강제한다는 점을
  확인했다. 문서-only 예외를 두면 운영 정본과 제출 설명이 어긋나고 유지보수 분기가
  늘어난다.
- `$shower`는 매뉴얼의 고정 skip 개수가 Traceability 정본과 중복되고, Mode B를
  단독 실행할 때 PostgreSQL 기동 단계가 빠져 있음을 지적했다.

### What

- README와 V2 Traceability에서 runtime anchor·path-scoped equivalence 예외를 제거했다.
  HEAD가 바뀌면 코드·문서 구분 없이 여덟 profile을 exact same SHA에서 다시 실행한다.
- `REQ-09`는 core·actual live·document 세 profile의 same-SHA 영수증이 모두 있어야
  `VERIFIED`이고, 하나라도 없으면 `PARTIAL`인 닫힌 계약으로 표현했다.
- 매뉴얼의 skip 수는 Traceability 링크로 단일화하고, 독립적인 readiness 경로에
  `docker compose up -d --wait db`를 추가했다.

### 검증과 책임 경계

- 과거 live와 회고의 SHA는 역사 증거로 보존하되 최종 제출 증거로 승계하지 않는다.
- 이 문서 commit과 자동 Retrobot commit이 끝난 최종 HEAD에서 8-profile bundle,
  snapshot, validator, non-force remote parity와 final audit을 새로 확인해야 완료다.
- actual live smoke의 sample 문맥 외부 전송과 non-force push는 사용자 승인 범위에서만
  실행하며, 원문 답변·credential·전체 실행 log는 기록하지 않는다.

## 2026-08-03 — content-free pipeline timing 계약

### Why

- cache hit, no-evidence, 실제 생성, 오류와 취소 경로를 입력 원문 없이 구분할 측정
  경계가 없었다. 측정 없이 tokenizer, retrieval 또는 운영 성능을 개선했다고 말할 수
  없으므로 먼저 최소 receipt schema를 고정했다.
- timing은 성능 우위나 SLO 증거가 아니라 후속 benchmark의 입력이다. 제품 결과보다
  logger를 우선하지 않도록 sink는 best-effort로 한정했다.

### What과 TDD

- API의 UUID를 V1/V2 ingestion과 JSON/SSE question service에 전달하고
  `perf_counter_ns()` 차이로 실행된 stage만 누적하는 schema v1 timer를 추가했다.
- payload는 bounded operation/outcome/stage/path, duration, cache metadata와 sanitized
  error type만 허용한다. question, filename, document·prompt·answer·citation, DSN,
  credential과 exception message는 전달하지 않는다.
- QA는 cache hit·no-evidence·grounded, error와 `GeneratorExit`/`CancelledError`를 한
  terminal receipt로 구분한다. LLM stage는 모든 upstream `anext()` 대기만 합산하고
  downstream consumer suspension은 total에만 포함될 수 있다.
- deterministic clock 회귀가 V1/V2 stage, API request ID, privacy, recorder failure,
  cancellation, terminal 순서와 duration 불변식을 검증한다. focused timing·문서 suite
  `18 passed`, 관련 ingestion·streaming·V2 회귀 `35 passed, 3 expected skips`, Ruff와
  문서 validator를 통과했다.

### Fact check와 Shower

- Python 3.11 표준 문서에서 `perf_counter()`는 짧은 duration 측정용 고해상도
  counter이고 기준점 자체가 아니라 호출 간 차이만 의미가 있음을 확인했다.
- 근거 없는 gold-set 튜닝값과 성능 개선 문구가 README에 섞인 것을 발견해 실제 기본
  `900/150/0.30`으로 복구했다. timing만으로 속도·SLO·percentile을 주장하지 않는다.
- 무맥락 냉독은 receipt 생성과 sink delivery, SSE terminal, schema와 hosted 상태가
  섞여 있다고 판정했다. 필드·enum·sentinel·단위, 반복 stage 합산, service 진입 전
  실패 제외, success/error/cancelled와 best-effort delivery를 Architecture에 분리했다.

### 증거와 남은 경계

- C0 workflow의 `d86f4c4` hosted run #2 PASS는 역사 증거일 뿐 새 HEAD로 승계하지
  않는다. 이 source commit과 자동 Retrobot companion commit을 push한 뒤 exact
  companion SHA의 hosted check를 새로 확인한다.
- 제출-control Wiki snapshot/validator는 안정 브랜치 `3f1bc7d`를 고정하므로 현재
  feature HEAD와 dirty C1 병렬 작업을 `SOURCE_HEAD/CONTENT_DRIFT`로 정직하게 거부했다.
  source 완료 판정이나 raw 원문 변경으로 해석하지 않는다.
- 단일 process logger이므로 delivery exact-once, 영구 보관, 분산 trace, benchmark,
  HA/PITR를 구현했다고 주장하지 않는다.

## 2026-08-04 — versioned gold set과 fail-closed evaluator

### Why

- tokenizer·chunk·retrieval 변경을 체감으로 튜닝하면 holdout leakage와 성공 사례만
  남길 위험이 있다. 제품 기본값을 바꾸기 전에 exact bytes, 사람 승인과 failure
  signal을 가진 작은 품질 기준선을 먼저 만들었다.
- answerable 질문만 두면 무근거 회피를 측정하지 못하므로 각 split에 topic-adjacent
  `retrieval_negative`를 포함했다. 공개 파일은 blind benchmark가 아니라 iteration을
  분리하는 process holdout으로만 표현한다.

### What과 TDD

- gold v1은 dev 15건과 process-holdout 15건으로 구성하고 각 split을 answerable
  10건, retrieval-negative 5건으로 고정했다. manifest가 case와 모든 문서 digest를,
  portable dataset hash가 manifest·case·dataset-relative document path를 결합한다.
- `approval.json`은 exact dataset SHA에 owner와 timezone이 있는 ISO-8601 시각을
  결합한다. 현재 hash는
  `323edcd4591726d8ea1c8e36c4137fa2558d7b020260514eb7ea5ab6a0ac52a9`이고 승인은
  의도적으로 `pending`이다.
- runner는 기존 ingestion/Q&A port를 사용한다. 실제 CLI는 loopback `postgres`에서
  split-labelled random DB만 생성하고 migration 뒤 empty corpus를 확인하며 finally로
  삭제한다. remote·product·wrong-split·libpq service target은 fail-closed한다.
- 최초 evaluator module 부재는 collection 3건, isolation module 부재는 collection
  1건으로 RED를 확인했다. 냉독 뒤 malformed approval 3건과 secret-bearing runtime
  exception 1건도 RED로 재현하고 timezone validation과 sanitized exit code `3`으로
  GREEN 전환했다. 최종 focused suite는 `22 passed`다.

### Metric·Fact check·Shower

- retrieval hit/MRR은 expected filename의 첫 server citation rank를 사용한다.
  answer/citation claim coverage는 Unicode-normalized alias 포함 검사이고 exact
  abstention은 표준 no-evidence text, `grounded=false`, 빈 citation을 모두 요구한다.
  semantic entailment·NLI·factuality로 부르지 않는다.
- PostgreSQL 공식 문서에서 DB 생성은 superuser 또는 `CREATEDB`가 필요하고 extension
  설치는 구성 요소 생성 권한 또는 trusted-extension 조건을 따른다는 점을 확인했다.
  Python 3.11 문서의 `datetime.fromisoformat()`과 timezone-aware 결과를 승인 계약에
  사용했다.
- 무맥락 냉독은 pending 결론, 공개 holdout, metric oracle, DB 권한·비정상 종료,
  외부 Codex 변동과 exit code를 지적했다. runbook에 승인 체크리스트, metric 규칙,
  전체 split 실행, prefix residue 경고, output data와 exit `0/1/2/3` 경계를 추가했다.

### 검증과 정직한 잔여

- 두 split의 verify-only는 각각 15 case와 같은 dataset SHA, `pending`을 반환했다.
  냉독 후 RED까지 반영한 최종 host 전체는 `217 passed, 18 expected skips`, canonical
  Docker PostgreSQL/pgvector 전체는 `229 passed, 6 expected skips`였다. Ruff, lock,
  Compose config, 문서 validator와 diff check도 통과했다.
- 실제 FastEmbed/PostgreSQL/Codex evaluator는 실행하지 않았다. 선택된 합성 질문과
  검색 문맥을 사용자 구독에 전송하므로 별도 실행 승인·사용자 dataset 채택 뒤
  `--require-approved` evidence로 분리한다.
- 이 캡슐은 product chunk/threshold를 바꾸지 않는다. pending 데이터로 기본값을
  튜닝하거나 공개 holdout을 일반화·성능 우위 증거로 사용하지 않는다.

## 2026-08-04 — untruncated prompt proxy packing과 cache 안전성

### Why

- score gate를 통과한 top-k를 모두 prompt에 넣는 경로에는 완성 prompt 크기 경계가
  없었다. chunk나 metadata가 커질 때 LLM 호출 전 결정론적으로 줄이는 정책이 필요했다.
- 이 경계는 실제 Codex context limit을 아는 척하지 않고, 선택·citation·cache 의미가
  함께 움직여야 한다.

### TDD와 구현

- 최초 RED는 `build_prompt_cache_version` 부재로 test collection이 실패했다. 이후
  완성 prompt count, rank 순서의 keep/oversized-skip-and-continue, 경계값 `<=`, 아무
  근거도 못 넣는 별도 기권, 선택 citation과 cache version 계약을 GREEN으로 만들었다.
- canonical template를 v4로 올리고 template, counter identity digest와 budget을
  effective prompt cache version에 묶었다. raw operator/model identity는 prompt에
  넣지 않는다.
- 기본 proxy budget은 `4096`이고 설정 범위는 `512..65536`이다. 한 hit도 못 넣으면
  `prompt_budget_exceeded`, 일부만 넣으면 stale dependency를 만들지 않도록
  `prompt_evidence_reduced`로 cache write를 생략한다.
- tokenizer load/count 실패는 무제한 경로로 저하하지 않는다. LLM·citation·cache를
  호출하지 않고 error timing receipt만 남긴다.

### Fact check와 교정

- 첫 구현은 FastEmbed의 공개 `TextEmbedding.token_count()`를 재사용했다. 그러나 설치된
  source를 역검증하니 embedding tokenizer가 `model_max_length` truncation을 활성화한
  뒤 count하므로 `4096` 보호가 긴 입력을 과소 계수할 수 있었다. 커밋 전에 이 경로를
  폐기했다.
- OpenAI tiktoken 0.13.0의 `o200k_base`와 `encode_ordinary()`로 교체해 special-token
  모양의 untrusted text를 일반 text로, 완성 문자열 전체를 센다. 실제 adapter에서
  `"token " * 10000`이 `10001`로 계산돼 truncation 없는 경계를 확인했다.
- OpenAI 문서대로 model마다 encoding이 다르므로 이는 Codex App Server의 정확한
  tokenizer, system context, output reserve나 비용 상한이 아니다. 첫 encoding 준비는
  network 또는 cache가 필요할 수 있다.

### Shower와 검증

- 무맥락 냉독은 proxy 과장, canonical render 순서, skip 후 계속 여부, counter 실패,
  budget 기권 외부 계약과 제외 hit의 cache dependency 위험을 지적했다. 규범
  pseudocode·JSON/SSE 실패 의미·version bump 규칙을 문서화하고 일부 제외 답변의
  cache를 금지했다.
- 최종 코드리뷰에서 비정상 count의 `ValueError`가 사용자 입력 `422`로 오분류될 수
  있음을 발견했다. 전용 `PromptTokenCountError`로 원인 예외를 감싸 JSON `502`와 SSE
  `answer.error`가 내부 메시지를 숨기는 transport 회귀를 추가했다.
- 핵심 test `10 passed`, prompt/retrieval/stream/cache 회귀 `22 passed`, timing/settings/
  문서 회귀 `34 passed`와 대상 Ruff를 통과했다. 실제 Codex live와 final exact-SHA
  hosted check는 이 commit 뒤 새 HEAD에서 다시 확인해야 한다.

## 2026-08-04 — README runtime 기본값 정합성 복구

### 발견

- 평가자 문서를 다시 읽는 과정에서 README가 runtime의 실제 `900/150/0.30` 대신
  `200/50/0.40`을 기본값으로 표시하고, 실행하지 않은 gold 튜닝 개선 수치까지
  주장하는 drift를 발견했다.
- `Settings`와 README를 양방향으로 대조해 코드가 source of truth임을 확인했다.
  사용자 채택 전 gold v1과 미실행 actual evaluator는 기본값 개선 증거가 아니다.

### 교정과 재발 방지

- README를 실제 설정으로 되돌리고 최적화·성능 우위 문구를 제거했다.
- 문서 테스트가 `Settings(_env_file=None)`의 chunk size, overlap과 minimum score를
  README 표와 직접 비교하도록 해 같은 drift를 hosted suite에서 차단한다.
- 이 교정은 runtime 동작이나 gold dataset을 바꾸지 않으며 `DQ-06` 문서-코드 정합성
  위험만 닫는다.

## 2026-08-04 — 논리 복원과 physical PITR의 분리 검증

### Why와 범위

- PostgreSQL을 단일 저장소로 선택했지만 backup 명령의 존재와 실제 복구 가능성은
  달랐다. 가산점 증거는 제품 DB를 건드리지 않는 opt-in synthetic drill로 한정했다.
- `pg_dump`/`pg_restore`는 논리 snapshot restore로, `pg_basebackup` 뒤 archived WAL을
  지정 시점까지 replay하는 경로만 PITR로 분류한다. 같은 local Docker backend에서의
  결과를 production HA·RPO/RTO로 승격하지 않는다.

### TDD와 구현

- offline contract가 receipt 분류, 모든 proof의 fail-closed, child environment
  allowlist, local endpoint, Compose topology, command 순서, interrupt·cleanup retry를
  고정한다. 최종 focused suite는 `24 passed`다.
- 실제 runner는 random `officeagent-recovery-<12hex>` project에서 PostgreSQL 17과
  pgvector migration을 적용한다. custom dump를 별도 DB에 복원한 뒤 physical base
  backup을 `pg_verifybackup`하고, target 전·후 marker 사이로 WAL recovery한다.
- CI는 recovery Compose model과 offline contract만 검사한다. 실제 volume 생성·복구
  drill을 hosted CI가 실행했다고 주장하지 않는다.
- canonical Docker RED는 새 Compose와 init script가 test image에 없음을 드러냈다.
  두 파일을 명시적으로 복사하되 기존 Retrobot 격리 COPY 계약은 유지했고, 재실행은
  `263 passed, 6 skipped`로 GREEN이었다.

### Fact check와 보안 냉독

- PostgreSQL 17 공식 문서는 logical dump가 WAL replay용 backup이 아니며 PITR에는
  physical base backup과 그 이후의 연속 WAL archive가 필요하다고 설명한다.
  `pg_verifybackup`도 실제 test restore를 대신하지 않으므로 두 검증을 모두 수행한다.
- 냉독에서 `DOCKER_HOST` override가 context 검사와 실제 실행 대상을 갈라 원격 daemon을
  건드릴 수 있음을 발견했다. 비어 있지 않은 override를 거부하고 repository default
  Compose만 CLI에서 허용했다. recovery health는 단순 `pg_isready`가 아니라
  `pg_is_in_recovery() = false`까지 기다린다.
- 실제 Compose 파일 자체의 regression, cleanup 실패 시 random project 식별자,
  init script의 fresh-clone executable mode `100755` 검증을 추가했다.

### Candidate runtime 증거와 잔여

- 보안 교정된 candidate tree에서 실제 명령이 `PASS`를 반환했다. PostgreSQL major
  `17`, pgvector `0.8.6`, logical marker `1`, base manifest verified, archived WAL `6`,
  `base`·`before_target` 존재와 `after_target` 부재, promotion·signal 제거·schema를
  확인했다. receipt 전 cleanup 뒤 recovery prefix container·volume 잔여는 없었다.
- 이 실행은 아직 commit SHA에 묶이지 않았으므로 `OPS-05`는 `PARTIAL`이다. source와
  Retrobot commit 뒤 exact SHA에서 다시 실행하고 hosted check를 확인해야 한다.
- Phase 2 표에 남아 있던 비표준 `IMPLEMENTED` 상태도 허용된 `PARTIAL`로 정규화했다.
- schedule·retention·off-host copy·encryption·monitoring, cluster-global object,
  production RPO/RTO와 HA는 계속 증명되지 않았다.

## 2026-08-04 — dual-bound chunking과 policy-aware 재수집

### Why와 경계

- 900자 상한만으로는 한국어·CJK·emoji처럼 token 밀도가 높은 입력의 chunk
  크기를 일관되게 제한하지 못한다. 하지만 평가 없이 기본 문자 크기를
  바꾸면 회귀와 품질 변화를 분리하기 어렵다.
- 따라서 기본 900 code point·150 code-point overlap은 유지하고, 같은 900을 tiktoken
  `o200k_base` proxy-token 상한으로 추가했다. overlap은 문자 단위이며
  grapheme·semantic 경계, 정확한 Codex tokenizer, gold-tuned 품질 계약은 아니다.

### TDD와 구현

- source-exact Unicode code-point offset, page 경계, 문자·proxy-token 상한과
  policy digest 변화를 회귀로 고정했다. grapheme cluster 보존은
  주장하지 않는다.
- tokenizer load·prefix 실패는 사용자 `422`로 오분류하지 않고, embedding·DB
  commit 전에 내부 메시지를 숨긴 `502`로 fail-closed하는 회귀를 추가했다.
- chunking policy는 algorithm, 문자·token 상한, overlap과 tokenizer identity를
  digest한다. retrieval version도 이 digest를 포함해 청크 경계가 다른
  answer cache를 재사용하지 않는다.
- `0002` forward migration은 `documents.chunking_version`을 추가하고 기존 row를
  `legacy-page-safe-character-v1:unknown-settings`로 표시한다. 기존 chunk와
  dependency를 유지하며 background rechunk는 하지 않는다.
- recovery drill도 제품 migration runner로 발견된 모든 migration을 적용하고,
  logical·physical 복원의 `schema_migrations` head·checksum과
  `documents.chunking_version`을 source와 대조하도록 확장했다.
- 같은 filename·bytes·policy만 `UNCHANGED`이다. same-file reupload의 policy가
  다르면 `UPDATED`로 재청킹하고 active cache를 전체 무효화한다.
- 리뷰에서 찾은 다섯 가지 결함을 함께 고정했다. 저장 전 `strip()`이
  BPE 수를 늘리는 경우를 재검사했고, 청킹·tokenization을 AnyIO worker로
  옮겨 event loop blocking을 피했다. 기존 prompt-counter import alias를 보존했고,
  recovery에 source migration ledger 비교를 추가했으며, grapheme-safe로 읽히던
  문구를 code-point/source-exact 오프셋 계약으로 바꾸었다.

### 실행 증거와 상태

- host에서 Ruff `PASS`, pytest `262 passed, 20 skipped`, documentation validator
  8 files·69 links `PASS`를 확인했다.
- canonical Docker suite는 `276 passed, 6 skipped`로 완료됐다.
- dirty-tree actual recovery drill은 PostgreSQL `17`, pgvector `0.8.6`, logical/PITR
  `migration_head=0002`와 양쪽 `migration_checksums_verified=true`, archived WAL `6`을
  확인했다. receipt 전 cleanup 후 recovery prefix container·volume·image는 모두 `0`이었다.
- 위 실행은 current dirty-tree 증거이다. exact committed SHA의 재실행·영수증
  전까지 `OPS-05`는 `PARTIAL`이며, 실제 Codex/gold run이 아니므로
  `INT-CHUNK-01`과 `REQ-09`를 승격하지 않는다.

### Fact check·냉독 경계와 남은 증거

- Settings, composition root, chunker, migration, PostgreSQL write path와 평가자 문서를
  양방향으로 대조했다. `900`의 이중 상한, `150`의 문자 overlap,
  `0002` readiness와 same-file 이관 이외의 배경 변환은 주장하지 않는다.
- tokenizer proxy는 C3에서 검증한 untruncated `encode_ordinary()` 경계를 재사용한다.
  이번 변경은 chunk 상한과 stale-cache 정합성을 보강하지만 retrieval·answer
  품질 향상이나 `REQ-09` 가산점을 증명하지 않는다.
- evaluator 문서 8개를 이전 대화 문맥 없이 다시 읽은 최종 냉독은 tokenizer
  proxy, `0001` historical/`0002` current, recovery 한계, 환경별 test count와
  `REQ-09=PARTIAL` 사이에 material contradiction이 없다고 판정했다.
- 사용자 승인 gold actual run과 final exact-SHA hosted/runtime 영수증 전에는
  `INT-CHUNK-01`을 `PARTIAL`로 유지한다.
