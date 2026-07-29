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
  현재 수정·검증 결과는 다음 7월 29일 작업 묶음에서 별도로 기록한다.
