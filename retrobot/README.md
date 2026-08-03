# Retrobot

> 당신의 회고를 자동으로 기록해주는 도구입니다.

> **독자와 목적:** 지원자와 reviewer가 자동 회고의 입력, 생성, 실패와 Git 안전
> 경계를 재현하기 위한 운영 매뉴얼입니다. 제품 API 평가는
> [Evaluator Manual](../docs/EVALUATOR_MANUAL.md)을 사용하십시오.

Retrobot은 활성화된 post-commit 훅이 Codex에 제한된 커밋 문맥을 전달하여
**KPT(Keep/Problem/Try) 회고**를 생성합니다. 훅이 실행되고 아래 skip 조건을
통과한 `git commit`의 회고를 타임라인으로 정리합니다.

## 동작 방식

1. `git commit` 완료 후 `.githooks/post-commit` 훅이 트리거됨
2. 훅이 commit blob에서 `retrobot/SKILL.md`와 `retros/WORKLOG.md`의 최신 날짜 H2
   하나를 읽고 SHA·시각·aggregate shortstat와 함께 전달. 이전 로그·commit 제목·
   파일명·작성자·diff는 전달하지 않음
3. 훅은 Codex를 read-only·ephemeral·never-approve 설정으로 실행하고,
   SKILL은 명시적으로 전달된 입력만 근거로 일곱 필드 JSON을 반환하도록 지시
4. guard가 JSON을 다시 검사하고 SHA·시각·heading·직접 보완 표식이 포함된
   canonical Markdown을 로컬에서 생성
5. Git directory를 read-only로 lock하고 HEAD/index/path preimage를 재검사합니다.
   커밋 blob의 helper를 직접 실행하고 회고 전용 private index로 두 path의 tree를
   만듭니다.
6. 표준 `index.lock`과 `HEAD.lock`을 Codex 호출 전에 bounded retry로 획득해 다른
   commit·branch 전환을 막습니다. commit tree용 index와 사용자 상태를 보존할 final
   index를 분리하고, 새 회고는 no-clobber로 설치하며 기존 timeline에는 검증된
   suffix만 append합니다. ownership hard link는 `.git/retrobot-recovery.*`에 남겨
   실패·동시 편집 때 공개 파일을 자동 삭제하지 않습니다. Git directory와
   worktree가 다른 filesystem이거나 회고·timeline index entry가 stage 또는
   `assume-unchanged`/`skip-worktree` 상태면 사용자 의도를 보존하고 안전하게
   중단합니다.

훅은 위 자동 회고 커밋, `RETROBOT_SKIP=1`, skill 파일 부재, Codex CLI 부재
또는 생성 실패 시 회고 생성을 건너뜁니다. Claude fallback은 구현되어 있지
않으며 훅은 전역 Codex/Claude 로그를 prompt payload로 제공하지 않습니다.
다만 read-only sandbox는 repository 파일 read 자체를 OS 수준에서 차단하지
않으므로, bounded prompt payload를 완전한 read-isolation으로 해석하지 않습니다.
WORKLOG 전체는 로컬 기록이며 최신 날짜 구간만 8 KiB 상한 안에서 prompt에 포함됩니다.

## 설정

사전 조건은 Git, `flock`, Python과 로그인된 Codex CLI다. 먼저 실행 파일을 확인한다.

```bash
git --version
python3 --version
flock --version
codex --version
```

프로젝트 clone마다 local Git config에 hook 경로를 한 번 설정한다.

```bash
git config core.hooksPath .githooks
```

설정 확인:

```bash
git config --get core.hooksPath
```

정상 출력은 `.githooks`다. 이후 일반 commit이 성공하면 제품 commit을 부모로 하는
`docs(retro): 자동 회고 생성 by Retrobot` commit이 하나 추가된다. hook은 push하지
않으므로 두 commit을 사용자가 함께 push해야 한다.

회고의 `## 직접 보완`만 사람이 정리하는 문서 maintenance commit은 새 메타 회고를
만드는 순환을 피하려고 명시적으로 skip할 수 있다.

```bash
RETROBOT_SKIP=1 git commit -m "docs(retro): refine applicant reflection"
```

제품·설계 변경에 편의상 skip을 사용하지 않는다. skip된 commit에는 자동 KPT가 없다는
사실을 WORKLOG나 commit 설명에서 숨기지 않는다.

## 실행 경계

현재 지원되는 자동 실행 경로는 `.githooks/post-commit`의 `codex exec`뿐입니다.
별도 수동 실행이나 Claude fallback은 이 리포지토리에서 제공하지 않습니다.

## 독립 검증

아래 명령은 활성 hook, fail-closed 회귀, 최신 자동 회고 commit의 부모와 변경
경계를 각각 확인합니다. 마지막 두 명령에서 최신 commit은 직전 제품 commit을
부모로 갖고 `retros/TIMELINE.md`와 새 회고 파일만 변경해야 합니다.

```bash
git config --get core.hooksPath
uv run pytest tests/test_retrobot_*.py
git show --no-patch --format='%H%n%P%n%s' HEAD
git diff-tree --no-commit-id --name-status -r HEAD
```

예상 조건:

- hook path가 `.githooks`다.
- 집중 test가 통과한다.
- 자동 회고 commit의 부모가 직전 일반 commit이다.
- 자동 commit은 새 회고 파일과 `retros/TIMELINE.md` 두 path만 바꾼다.

## 문제 해결

| 증상 | 먼저 확인 | 처리 |
|---|---|---|
| 회고 commit이 없음 | `core.hooksPath`, `codex --version`, `flock` | 누락된 사전 조건을 복구하고 다음 일반 commit에서 확인 |
| guard가 출력을 거부 | stderr의 schema·size·heading 사유 | Markdown을 강제로 commit하지 말고 structured JSON 계약을 수정 |
| 회고·timeline이 이미 stage됨 | `git status --short` | 사용자 변경을 먼저 정리; hook은 덮어쓰지 않음 |
| `.git/retrobot-recovery.*` 존재 | 중단 시점의 index/HEAD ownership | [독립 검증](#독립-검증) 후 보존·복구 경계를 확인; 임의 삭제 금지 |
| 자동 회고 뒤 재귀 | 최신 commit 제목 | 정확한 자동 제목과 hook test를 확인 |

자동 회고는 지원자의 이해 완료를 증명하지 않는다. 생성된 `Try`는 그 시점 제안이며,
후속 current evidence와 직접 보완을 확인한 뒤에만 현재 할 일로 해석한다.

## 출력 구조

```
retros/
├── TIMELINE.md                # 전체 회고 타임라인
├── 2026-04-09_143000_<full-sha>_retro.md # commit 고유 개별 회고
└── ...
```

## 커스터마이징

`retrobot/SKILL.md`에서 분석 기준과 각 JSON 필드의 의미를 조정할 수 있습니다.
schema나 최종 Markdown 형식을 바꾸려면 guard와 계약 테스트도 함께 갱신해야 합니다.
