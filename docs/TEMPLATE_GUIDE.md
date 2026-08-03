# 템플릿 리포지토리 가이드

이 문서는 최초 template에서 이어받은 구성 요소와 현재 제출 저장소에서 유효한
운영 경계를 구분합니다. 과제 요구의 source of truth는 `docs/PRD.md`, 시작·실행
절차는 현재 `README.md`와 `CLAUDE.md`, 실제 동작은 code와 test입니다.

> 최초 template에는 Claude fallback, 전역 Claude/Codex log 분석, 넓은 Retrobot
> skip 조건, 제출 시점 collaborator 초대 설명이 있었습니다. 현재 구현·공식
> PRD/README와 충돌하므로 실행 절차로 사용하지 않습니다. 이 문서는 그 낡은
> 절차를 반복하지 않고 현재 유효한 경계만 적습니다.

## 디렉터리 구조

```text
.
├── README.md                  # 현재 실행·검증 가이드
├── CLAUDE.md                  # AI agent 공통 과제 지침
├── AGENTS.md → CLAUDE.md      # Git object mode 120000 symlink
├── .githooks/post-commit      # 현재 Retrobot 자동 실행 경로
├── docs/
│   ├── PRD.md                 # 과제 요구 정본
│   └── TEMPLATE_GUIDE.md      # template/current 경계 설명
├── sample-docs/               # manual/live smoke fixture
├── retrobot/
│   ├── SKILL.md               # bounded KPT 생성 계약
│   └── README.md              # 현재 사용법
└── retros/                    # 회고·타임라인·작업 로그
```

Windows에서 `core.symlinks=false`이면 `AGENTS.md`가 `CLAUDE.md`라는 9-byte 일반
파일처럼 materialize될 수 있습니다. repository object는 다음 명령으로 mode
`120000`인지 확인합니다.

```bash
git ls-files -s AGENTS.md
```

standalone zip/copy에서 보인 9-byte 파일만으로 Git symlink 자체가 잘못됐다고
판정하지 않습니다. 반대로 Windows tool이 symlink를 따라야 한다면 해당 client의
symlink 지원 여부는 별도로 확인해야 합니다.

## 구성 요소

### 1. 과제 명세 (`docs/PRD.md`)

문서 수집, RAG 질의응답, LLM cache, prompt 설계, 평가 기준과 제출 방법을
정의합니다. 구현·문서 주장이 충돌하면 PRD 요구와 실제 code/test를 먼저
대조합니다.

### 2. Agent 지침 (`CLAUDE.md` / `AGENTS.md`)

과제 시작 절차, 필수 산출물, 평가 비중, 현재 Retrobot 경계를 agent가 읽도록
합니다. `AGENTS.md`는 repository에서 `CLAUDE.md`를 가리키는 symlink입니다.

### 3. Sample 문서 (`sample-docs/`)

| 파일 | 용도 |
|---|---|
| `company-policy.txt` | 교육비·근무·휴가 등 manual/live fixture |
| `development-guide.md` | review·branch·deploy·incident fixture |

이 fixture는 deterministic regression 예시이지 실제 한국어 semantic retrieval
품질을 대표하는 gold dataset이 아닙니다.

### 4. Retrobot (`retrobot/`)

현재 지원되는 동작은 다음 하나입니다.

1. 지원자가 일반 `git commit`을 완료합니다.
2. `.githooks/post-commit`이 `codex exec`를 실행합니다.
3. prompt payload는 commit blob의 `retrobot/SKILL.md`, 최신 커밋 SHA·시각·aggregate
   shortstat와 `retros/WORKLOG.md`의 최신 날짜 H2 하나로 제한됩니다. 이전 로그·commit 제목·
   파일명·작성자·diff와 전역 `~/.codex`·`~/.claude` log는 전달하지 않습니다.
   read-only는 repository read-isolation 보장이 아닙니다.
4. 모델의 일곱 필드 JSON을 guard가 재검사하고 canonical Markdown으로 로컬
   렌더링합니다. 이는 제품의 한 필드 structured answer→plaintext SSE 계약과
   별개인 Retrobot 전용 schema입니다.
5. Git directory read-only lock과 branch/HEAD/index/path preimage를 재검사합니다.
   표준 `index.lock`·`HEAD.lock`을 Codex 호출 전에 bounded retry로 획득하고
   committed helper, tree/final private index, no-clobber copy와 guarded timeline
   append를 사용해 Markdown과
   `retros/TIMELINE.md` 두 path만 `docs(retro): 자동 회고 생성 by Retrobot`
   제목으로 자동 commit합니다. ownership inode는 Git 내부 recovery journal에
   보존하며 실패 경로에서 공개 파일을 자동 삭제하지 않습니다. 두 관리 경로가
   stage됐거나 special index flag를 가지면 사용자 index를 바꾸지 않고 중단합니다.
6. hook은 그 정확한 자동 제목, `RETROBOT_SKIP=1`, skill/CLI 부재 또는 생성
   실패에서 종료합니다. Claude fallback과 별도 수동 실행은 없습니다.
7. hook은 push하지 않습니다. 사용자가 제품 commit과 자동 회고 commit을
   원격에 push해야 evaluator가 볼 수 있습니다.

정본은 `.githooks/post-commit`, `retrobot/SKILL.md`, `retrobot/README.md`입니다.

### 5. Git hook 설정

clone마다 local Git config에 hook 경로를 한 번 설정합니다.

```bash
git config core.hooksPath .githooks
```

설정 존재와 실제 동작은 다릅니다. 일반 commit 뒤 회고 commit이 한 번 생기고
그 자동 commit에서 recursion이 멈추는지를 commit history로 확인합니다.

## 현재 시작 순서

```text
1. GitHub에서 private repository 생성
2. 첫 지원자 commit 전에 serithemage collaborator 초대
3. clone하고 core.hooksPath=.githooks 설정
4. 구현한 날마다 일반 commit 수행
5. 자동 생성 Retrobot 회고를 확인
6. 일반 commit과 자동 회고 commit을 같은 작업일에 push
7. README의 제출 절차와 기한을 다시 확인
```

초대를 제출 시점으로 미루는 최초 template 순서는 current PRD/README와 충돌해
기각했습니다. 다만 PRD상 늦은 초대만으로 자동 불이익은 아닙니다. 현재
collaborator의 수락·접근 상태는 유효한 GitHub 권한으로 직접
확인하지 않았다면 `UNKNOWN`으로 둡니다.
