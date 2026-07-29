"""Structural contract tests for the Retrobot hook."""

from pathlib import Path


def test_retrobot_commits_only_its_generated_paths() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    hook = (repository_root / ".githooks" / "post-commit").read_text(encoding="utf-8")

    explicit_paths = '"$RETRO_PATH" "$TIMELINE_PATH"'
    assert f"git add -- {explicit_paths}" in hook
    assert f'--no-verify -- {explicit_paths}' in hook
    assert "git add retros/" not in hook
