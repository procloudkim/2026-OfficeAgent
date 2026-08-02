# Retrobot

> 당신의 회고를 자동으로 기록해주는 도구입니다.

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

프로젝트 클론 후 아래 명령어를 실행하세요:

```bash
git config core.hooksPath .githooks
```

## 실행 경계

현재 지원되는 자동 실행 경로는 `.githooks/post-commit`의 `codex exec`뿐입니다.
별도 수동 실행이나 Claude fallback은 이 리포지토리에서 제공하지 않습니다.

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
