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
