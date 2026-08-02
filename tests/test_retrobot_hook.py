"""Structural and privacy contracts for the Retrobot hook."""

import re
from pathlib import Path

from scripts.retrobot_guard import MAX_PROMPT_BYTES, validate_retrobot_prompt

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (REPOSITORY_ROOT / "Dockerfile.test").read_text(encoding="utf-8")
DOCKERIGNORE = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
HOOK = (REPOSITORY_ROOT / ".githooks" / "post-commit").read_text(encoding="utf-8")
SKILL = (REPOSITORY_ROOT / "retrobot" / "SKILL.md").read_text(encoding="utf-8")
WORKLOG = (REPOSITORY_ROOT / "retros" / "WORKLOG.md").read_text(encoding="utf-8")

_EMAIL_ADDRESS = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_GITHUB_URL = re.compile(r"https?://(?:www\.)?github\.com/[^\s)`]+", re.IGNORECASE)
_BACKTICKED_REPOSITORY_SLUG = re.compile(
    r"`([A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99}))`"
)
_BACKTICKED_USERNAME = re.compile(r"`[A-Za-z0-9][A-Za-z0-9_-]{0,38}`")
_IDENTITY_ROLE_FIELDS = (
    "collaborator",
    "repository owner",
    "viewer",
    "저장소 소유자",
    "평가자 계정",
)
def _assert_identity_free(bound_prompt: str) -> None:
    if _EMAIL_ADDRESS.search(bound_prompt):
        raise AssertionError("Retrobot prompt input contains an email address")
    if _GITHUB_URL.search(bound_prompt):
        raise AssertionError("Retrobot prompt input contains a GitHub URL")
    for line in bound_prompt.splitlines():
        folded = line.casefold()
        if "private" in folded and _BACKTICKED_REPOSITORY_SLUG.search(line):
            raise AssertionError("Retrobot prompt input contains a private repository slug")
        if any(field in folded for field in _IDENTITY_ROLE_FIELDS):
            if _BACKTICKED_USERNAME.search(line):
                raise AssertionError("Retrobot prompt input contains an identity-bearing role")


def test_retrobot_runs_codex_from_a_disposable_empty_directory() -> None:
    assert "mktemp -d /tmp/officeagent-retrobot.XXXXXX" in HOOK
    assert '--run-directory "$RETROBOT_RUN_DIR"' in HOOK

    guard = (REPOSITORY_ROOT / "scripts" / "retrobot_guard.py").read_text(encoding="utf-8")
    assert "cwd=run_directory" in guard


def test_retrobot_cleans_its_disposable_directory_safely() -> None:
    assert "trap cleanup_retrobot EXIT" in HOOK
    assert "/tmp/officeagent-retrobot.??????)" in HOOK
    assert 'find "$RETROBOT_RUN_DIR" -xdev -mindepth 1 -delete' in HOOK
    assert 'rmdir -- "$RETROBOT_RUN_DIR"' in HOOK
    assert 'rm -rf "$RETROBOT_RUN_DIR"' not in HOOK


def test_retrobot_keeps_prompt_and_output_out_of_the_disposable_directory() -> None:
    assert 'SKILL_RELPATH="retrobot/SKILL.md"' in HOOK
    assert 'WORKLOG_RELPATH="retros/WORKLOG.md"' in HOOK
    assert 'CONTEXT_PATH="${REPO_ROOT}/scripts/retrobot_context.py"' in HOOK
    assert 'SKILL_SNAPSHOT_PATH=$(mktemp "${GIT_DIR}/retrobot-skill.XXXXXX")' in HOOK
    assert 'WORKLOG_SECTION_PATH=$(mktemp "${GIT_DIR}/retrobot-worklog.XXXXXX")' in HOOK
    assert 'TEMP_ABS_PATH=$(mktemp "${GIT_DIR}/retrobot-output.XXXXXX")' in HOOK
    assert 'GUARD_PATH="${REPO_ROOT}/scripts/retrobot_guard.py"' in HOOK
    assert "retrobot-last.log" not in HOOK


def test_retrobot_latest_commit_input_is_identity_free() -> None:
    assert 'COMMIT_SHA=$(git rev-parse HEAD' in HOOK
    assert 'COMMITTED_AT=$(git log -1 --format=%cI' in HOOK
    assert 'COMMIT_SHORTSTAT=$(LC_ALL=C git diff-tree --shortstat' in HOOK
    assert 'printf "full_sha=%s\\n" "$COMMIT_SHA"' in HOOK
    assert 'printf "committed_at=%s\\n" "$COMMITTED_AT"' in HOOK
    assert 'printf "aggregate_shortstat=%s\\n" "$COMMIT_SHORTSTAT"' in HOOK
    assert "--format=fuller" not in HOOK
    assert "subject=%s" not in HOOK
    assert "--name-status" not in HOOK

    identity_fields = ("%an", "%aN", "%ae", "%aE", "%cn", "%cN", "%ce", "%cE")
    assert not any(field in HOOK for field in identity_fields)


def test_retrobot_complete_bound_prompt_inputs_are_identity_free() -> None:
    prompt_builder = HOOK[
        HOOK.index('{\n    cat "$SKILL_SNAPSHOT_PATH"') : HOOK.index('} | "$PYTHON_BIN"')
    ]
    assert prompt_builder.count("cat ") == 2
    assert 'cat "$SKILL_SNAPSHOT_PATH"' in prompt_builder
    assert 'cat "$WORKLOG_SECTION_PATH"' in prompt_builder
    assert 'cat "$WORKLOG_PATH"' not in prompt_builder
    assert 'git show "${COMMIT_SHA}:${SKILL_RELPATH}"' in HOOK
    assert 'git show "${COMMIT_SHA}:${WORKLOG_RELPATH}"' in HOOK
    assert '"$PYTHON_BIN" "$CONTEXT_PATH"' in HOOK
    assert 'cat "$WORKLOG_RELPATH"' not in HOOK
    assert HOOK.index('COMMIT_SHA=$(git rev-parse HEAD') < HOOK.index(
        'git show "${COMMIT_SHA}:${WORKLOG_RELPATH}"'
    )

    identity_free_commit_metadata = (
        f"full_sha={'0' * 40}\n"
        "committed_at=2026-08-02T00:00:00+09:00\n"
        "aggregate_shortstat=2 files changed, 10 insertions(+), 3 deletions(-)"
    )
    latest_worklog = "## " + WORKLOG.rsplit("\n## ", maxsplit=1)[1]
    bound_prompt = "\n\n".join((SKILL, identity_free_commit_metadata, latest_worklog))

    assert len(bound_prompt.encode()) <= MAX_PROMPT_BYTES
    assert "## 2026-07-28" not in latest_worklog
    assert "127 passed" in latest_worklog
    _assert_identity_free(bound_prompt)
    validate_retrobot_prompt(bound_prompt.encode())


def test_retrobot_uses_guarded_codex_execution() -> None:
    assert '"$PYTHON_BIN" "$GUARD_PATH"' in HOOK
    assert '--timeout-seconds 300' in HOOK
    assert '--ignore-user-config' in (REPOSITORY_ROOT / "scripts" / "retrobot_guard.py").read_text(
        encoding="utf-8"
    )
    assert '--ignore-rules' in (REPOSITORY_ROOT / "scripts" / "retrobot_guard.py").read_text(
        encoding="utf-8"
    )
    assert '--strict-config' in (
        REPOSITORY_ROOT / "scripts" / "retrobot_guard.py"
    ).read_text(encoding="utf-8")
    assert '"--disable",\n        "shell_tool"' in (
        REPOSITORY_ROOT / "scripts" / "retrobot_guard.py"
    ).read_text(encoding="utf-8")


def test_retrobot_validates_before_publish_stage_and_commit() -> None:
    guard = HOOK.index('"$PYTHON_BIN" "$GUARD_PATH"')
    publish = HOOK.index('mv -- "$TEMP_ABS_PATH" "$RETRO_ABS_PATH"')
    stage = HOOK.index('git add -- "$RETRO_PATH" "$TIMELINE_PATH"')
    commit = HOOK.index('git commit-tree "$RETRO_TREE" -p "$COMMIT_SHA"')

    assert guard < publish < stage < commit
    assert 'rm -f -- "$TEMP_ABS_PATH" "$RETRO_ABS_PATH"' in HOOK


def test_retrobot_serializes_hooks_and_rechecks_publish_preimages() -> None:
    assert 'GIT_DIR=$(git rev-parse --absolute-git-dir' in HOOK
    assert 'LOCK_PATH="${GIT_DIR}/retrobot.lock"' in HOOK
    assert '"$FLOCK_BIN" -n 9' in HOOK
    assert HOOK.count("timeline_matches_preimage") >= 3
    assert 'RETRO_PREEXISTED=0' in HOOK
    assert 'published_paths_are_owned' in HOOK
    assert 'git update-ref -m "commit: docs(retro): 자동 회고 생성 by Retrobot"' in HOOK


def test_timeline_uses_only_validated_non_model_cells() -> None:
    timeline_append = HOOK[
        HOOK.index('printf "| %s | [%s]') : HOOK.index(
            'PUBLISHED_RETRO_HASH=$(git hash-object "$TEMP_ABS_PATH")'
        )
    ]
    assert '"$COMMIT_SHA"' in timeline_append
    assert "COMMIT_SUBJECT" not in HOOK
    assert "TEMP_ABS_PATH" not in timeline_append


def test_docker_test_image_copies_only_sanitized_retrobot_prompt_inputs() -> None:
    assert "COPY Dockerfile.test .dockerignore ./" in DOCKERFILE
    assert "COPY retrobot/SKILL.md ./retrobot/" in DOCKERFILE
    assert "COPY retros/WORKLOG.md ./retros/" in DOCKERFILE
    assert "scripts/retrobot_guard.py" in DOCKERFILE
    assert "scripts/retrobot_context.py" in DOCKERFILE
    assert "COPY retrobot " not in DOCKERFILE
    assert "COPY retrobot/ " not in DOCKERFILE
    assert "COPY retros " not in DOCKERFILE
    assert "COPY retros/ " not in DOCKERFILE

    for entry in (
        "!retrobot/",
        "retrobot/*",
        "!retrobot/SKILL.md",
        "retros/",
        "!retros/",
        "retros/*",
        "!retros/WORKLOG.md",
    ):
        assert entry in DOCKERIGNORE.splitlines()


def test_retrobot_preserves_recursion_guard() -> None:
    guard = '"docs(retro): 자동 회고 생성 by Retrobot") exit 0 ;;'
    assert guard in HOOK


def test_retrobot_commits_only_its_generated_paths() -> None:
    explicit_paths = '"$RETRO_PATH" "$TIMELINE_PATH"'
    assert f"git add -- {explicit_paths}" in HOOK
    assert 'git commit-tree "$RETRO_TREE" -p "$COMMIT_SHA"' in HOOK
    assert 'ACTUAL_STAGED_PATHS=$(git diff --cached --name-only "$COMMIT_SHA")' in HOOK
    assert "git add retros/" not in HOOK
