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
- 일반 커밋 `bee1fd3` 뒤 회고 `b7b2b70`이 자동 생성됐고, 회고 커밋에서는
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
