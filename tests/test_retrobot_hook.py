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
PUBLISHER = (REPOSITORY_ROOT / "scripts" / "retrobot_publish.py").read_text(
    encoding="utf-8"
)

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
    assert 'CONTEXT_RELPATH="scripts/retrobot_context.py"' in HOOK
    assert 'GUARD_RELPATH="scripts/retrobot_guard.py"' in HOOK
    assert 'PUBLISH_RELPATH="scripts/retrobot_publish.py"' in HOOK
    assert 'SKILL_CONTENT=$(git show "${COMMIT_SHA}:${SKILL_RELPATH}"' in HOOK
    assert 'WORKLOG_SECTION=$(git show "${COMMIT_SHA}:${WORKLOG_RELPATH}"' in HOOK
    assert 'TEMP_ABS_PATH=$(mktemp "${GIT_DIR}/retrobot-output.XXXXXX")' in HOOK
    assert 'TEMP_OWNER_PATH=$(mktemp "${GIT_DIR}/retrobot-output-owner.XXXXXX")' in HOOK
    assert "SNAPSHOT_PATH" not in HOOK
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
        HOOK.index('{\n    printf "%s\\n" "$SKILL_CONTENT"') : HOOK.index(
            '} | "$PYTHON_BIN"'
        )
    ]
    assert "cat " not in prompt_builder
    assert 'printf "%s\\n" "$SKILL_CONTENT"' in prompt_builder
    assert 'printf "%s\\n" "$WORKLOG_SECTION"' in prompt_builder
    assert '"$PYTHON_BIN" -I -c "$CONTEXT_CODE"' in HOOK
    assert 'git show "${COMMIT_SHA}:${SKILL_RELPATH}"' in HOOK
    assert 'git show "${COMMIT_SHA}:${WORKLOG_RELPATH}"' in HOOK
    assert "제공된 JSON schema의 일곱 필드만 반환한다" in prompt_builder
    assert HOOK.index('COMMIT_SHA=$(git rev-parse HEAD') < HOOK.index(
        'WORKLOG_SECTION=$(git show "${COMMIT_SHA}:${WORKLOG_RELPATH}"'
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
    assert "167 passed" in latest_worklog
    _assert_identity_free(bound_prompt)
    validate_retrobot_prompt(bound_prompt.encode())


def test_retrobot_uses_guarded_codex_execution() -> None:
    assert '"$PYTHON_BIN" -I -c "$GUARD_CODE"' in HOOK
    assert '--timeout-seconds 300' in HOOK
    assert '--generated-at-kst "$STAMP"' in HOOK
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
    guard = HOOK.index('"$PYTHON_BIN" -I -c "$GUARD_CODE"')
    stage = HOOK.index('GIT_INDEX_FILE="$RETROBOT_TREE_INDEX" git update-index --add')
    publish = HOOK.index('--source "$TEMP_ABS_PATH"')
    commit = HOOK.index('git commit-tree "$RETRO_TREE" -p "$PUBLICATION_BASE"')

    assert guard < stage < commit < publish
    assert 'rm -f -- "$TEMP_ABS_PATH" "$RETRO_ABS_PATH"' not in HOOK
    assert '[ "$TEMP_OWNER_PATH" -ef "$TEMP_ABS_PATH" ]' in HOOK
    assert "git reset" not in HOOK


def test_retrobot_publishes_and_restores_without_overwriting_paths() -> None:
    assert "os.O_EXCL" in PUBLISHER
    assert "os.O_NOFOLLOW" in PUBLISHER
    assert "os.link(owner_link, destination" in PUBLISHER
    assert 'mktemp -d "${GIT_DIR}/retrobot-recovery.${COMMIT_SHA}.XXXXXX"' in HOOK
    assert '"$PYTHON_BIN" -I -c "$PUBLISH_CODE" append' in HOOK
    assert '--destination "$TIMELINE_ABS_PATH"' in HOOK
    assert '--expected "$TIMELINE_BASE"' in HOOK
    assert '--replacement "$TIMELINE_NEXT"' in HOOK
    assert 'rm -f -- "$RETRO_ABS_PATH"' not in HOOK
    assert 'rm -f -- "$TIMELINE_ABS_PATH"' not in HOOK
    assert '[ "$RETRO_OWNER_PATH" -ef "$RETRO_ABS_PATH" ]' in HOOK
    assert '[ "$TIMELINE_OWNER_PATH" -ef "$TIMELINE_ABS_PATH" ]' in HOOK


def test_retrobot_serializes_hooks_and_rechecks_publish_preimages() -> None:
    assert 'GIT_DIR=$(git rev-parse --absolute-git-dir' in HOOK
    assert 'exec 9<"$GIT_DIR"' in HOOK
    assert "retrobot.lock" not in HOOK
    assert 'exec 9>"' not in HOOK
    assert '"$FLOCK_BIN" 9' in HOOK
    assert '"$FLOCK_BIN" -n 9' not in HOOK
    assert HOOK.count("publication_preimage_matches") >= 3
    assert "protected_index_preimage_matches" in HOOK
    assert 'GIT_INDEX_FILE="$INDEX_LOCK_OWNER_PATH" git diff --cached --quiet' in HOOK
    assert '"$PUBLICATION_BASE" -- "$TIMELINE_PATH" "$RETRO_PATH"' in HOOK
    assert 'git ls-files -v -- "$TIMELINE_PATH"' in HOOK
    assert '[ "$timeline_tag" = "H $TIMELINE_PATH" ]' in HOOK
    assert 'published_paths_are_owned' in HOOK
    assert 'tree_contains_only_publication "$RETRO_TREE"' in HOOK
    assert HOOK.index(
        'RETRO_TREE=$(GIT_INDEX_FILE="$RETROBOT_TREE_INDEX" git write-tree)'
    ) < HOOK.index(
        'tree_contains_only_publication "$RETRO_TREE"'
    )
    assert 'git ls-tree "$candidate_tree" -- "$RETRO_PATH"' in HOOK
    assert 'git ls-tree "$candidate_tree" -- "$TIMELINE_PATH"' in HOOK
    assert '--destination "$SHARED_INDEX_LOCK"' in HOOK
    assert 'GIT_INDEX_FILE="$RETROBOT_TREE_INDEX" git read-tree "$PUBLICATION_BASE"' in HOOK
    assert 'cp -- "$SHARED_INDEX_PATH" "$RETROBOT_INDEX"' in HOOK
    assert 'GIT_INDEX_FILE="$RETROBOT_INDEX" git ls-files --stage -- "$RETRO_PATH"' in HOOK
    assert 'git update-ref -m "commit: docs(retro): 자동 회고 생성 by Retrobot"' in HOOK


def test_retrobot_executes_committed_helpers_and_builds_committed_timeline() -> None:
    for helper in ("GUARD", "CONTEXT", "PUBLISH"):
        assert f'{helper}_CODE=$(git show "${{COMMIT_SHA}}:${{{helper}_RELPATH}}"' in HOOK
        assert f'"$PYTHON_BIN" -I -c "${helper}_CODE"' in HOOK
    assert 'git show "${PUBLICATION_BASE}:${TIMELINE_PATH}" >"$TIMELINE_BASE"' in HOOK
    assert 'cp -- "$TIMELINE_BASE" "$TIMELINE_NEXT"' in HOOK
    assert 'cp -- "$TIMELINE_ABS_PATH" "$TIMELINE_NEXT"' not in HOOK
    assert '[ ! -x "$TIMELINE_ABS_PATH" ]' not in HOOK


def test_retrobot_paths_and_cleanup_are_commit_unique_and_recovery_first() -> None:
    assert 'RETRO_PATH="retros/${STAMP}_${COMMIT_SHA}_retro.md"' in HOOK
    assert "refresh_ref_state" in HOOK
    assert 'git merge-base --is-ancestor "$RETRO_COMMIT" "$actual_target"' in HOOK
    assert "자동 삭제하지 않고 보존합니다" in HOOK
    assert "Retrobot publication is preserved for recovery" in PUBLISHER


def test_retrobot_locks_index_before_codex_and_pins_the_head_ref() -> None:
    index_lock = HOOK.index('--destination "$SHARED_INDEX_LOCK"')
    head_lock = HOOK.index('--destination "$SHARED_HEAD_LOCK"')
    guard = HOOK.index('"$PYTHON_BIN" -I -c "$GUARD_CODE"')
    final_index = HOOK.index('"$PYTHON_BIN" -I -c "$PUBLISH_CODE" replace-owned')
    update_ref = HOOK.index('git update-ref -m "commit: docs(retro): 자동 회고 생성 by Retrobot"')

    assert index_lock < head_lock < guard < final_index < update_ref
    assert 'HEAD_REF=$(git symbolic-ref -q HEAD' in HOOK
    assert 'head_ref_matches "$PUBLICATION_BASE"' in HOOK
    assert HOOK.count('head_ref_matches "$RETRO_COMMIT"') >= 2
    assert '"$REF_TARGET" "$RETRO_COMMIT" "$PUBLICATION_BASE"' in HOOK
    assert 'git merge-base --is-ancestor "$COMMIT_SHA" "$candidate"' in HOOK
    assert 'head_lock_is_owned' in HOOK
    assert 'acquire_index_lock' in HOOK
    assert 'while [ "$attempt" -lt 200 ]' in HOOK
    assert 'release_head_lock_to_recovery' in HOOK
    assert '--owner-link "$HEAD_LOCK_OWNER_PATH"' in HOOK


def test_timeline_uses_only_validated_non_model_cells() -> None:
    timeline_append = HOOK[
        HOOK.index('printf "| %s | [%s]') : HOOK.index(
            'PUBLISHED_RETRO_HASH=$(git hash-object -w "$TEMP_ABS_PATH")'
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
    assert "scripts/retrobot_publish.py" in DOCKERFILE
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
    assert '--cacheinfo "100644,$PUBLISHED_RETRO_HASH,$RETRO_PATH"' in HOOK
    assert '--cacheinfo "100644,$PUBLISHED_TIMELINE_HASH,$TIMELINE_PATH"' in HOOK
    assert 'git commit-tree "$RETRO_TREE" -p "$PUBLICATION_BASE"' in HOOK
    assert "git add retros/" not in HOOK
    assert "git add --" not in HOOK
