# Retrobot

> 당신의 회고를 자동으로 기록해주는 도구입니다.

Retrobot은 활성화된 post-commit 훅이 Codex에 제한된 커밋 문맥을 전달하여
**KPT(Keep/Problem/Try) 회고**를 생성합니다. 훅이 실행되고 아래 skip 조건을
통과한 `git commit`의 회고를 타임라인으로 정리합니다.

## 동작 방식

1. `git commit` 완료 후 `.githooks/post-commit` 훅이 트리거됨
2. 훅이 `codex exec`에 `retrobot/SKILL.md`, 최신 커밋 정보, 존재하는 경우
   `retros/WORKLOG.md`를 전달
3. 훅은 Codex를 read-only·ephemeral·never-approve 설정으로 실행하고,
   SKILL은 명시적으로 전달된 입력만 근거로 사용하도록 지시
4. 생성 성공 시 방금 만든 KPT 회고와 `retros/TIMELINE.md` 두 path만 stage해
   `docs(retro): 자동 회고 생성 by Retrobot` 메시지로 자동 커밋

훅은 위 자동 회고 커밋, `RETROBOT_SKIP=1`, skill 파일 부재, Codex CLI 부재
또는 생성 실패 시 회고 생성을 건너뜁니다. Claude fallback은 구현되어 있지
않으며 훅은 전역 Codex/Claude 로그를 prompt payload로 제공하지 않습니다.
다만 read-only sandbox는 repository 파일 read 자체를 OS 수준에서 차단하지
않으므로, bounded prompt payload를 완전한 read-isolation으로 해석하지 않습니다.

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
├── TIMELINE.md              # 전체 회고 타임라인
├── 2026-04-09_1430_retro.md  # 개별 회고
└── ...
```

## 커스터마이징

`retrobot/SKILL.md`를 편집하여 회고 형식, 분석 기준, 출력 포맷을 자유롭게 조정할 수 있습니다.
