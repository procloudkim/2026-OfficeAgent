"""End-to-end Retrobot publication in an isolated Git repository."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts.retrobot_guard import validate_retrobot_output

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
AUTO_COMMIT_SUBJECT = "docs(retro): 자동 회고 생성 by Retrobot"


def _git(repository: Path, *arguments: str, environment: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return "\n".join(
        output for output in (result.stdout.strip(), result.stderr.strip()) if output
    )


def _write_fake_codex(
    path: Path,
    *,
    pause_ready: Path | None = None,
    pause_release: Path | None = None,
) -> None:
    payload = json.dumps(
        {
            "main_goal": "자동 회고 게시 경계를 검증한다.",
            "validation_status": "제공된 실행 증거만 반영했다.",
            "performed": ["제한된 커밋 문맥으로 회고 값을 생성했다."],
            "keep": ["입력과 출력 책임이 분리됐다."],
            "problem": ["실제 provider 동작은 이 테스트 범위 밖이다."],
            "try_next": ["실제 Codex 실행 영수증을 별도로 확인한다."],
            "evidence": ["임시 Git 저장소 통합 테스트"],
        },
        ensure_ascii=False,
    ).encode()
    pause_code = ""
    if pause_ready is not None and pause_release is not None:
        pause_code = (
            f"pause_ready = pathlib.Path({str(pause_ready)!r})\n"
            f"pause_release = pathlib.Path({str(pause_release)!r})\n"
            "pause_ready.write_text('ready', encoding='utf-8')\n"
            "deadline = time.monotonic() + 10\n"
            "while not pause_release.exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.01)\n"
            "assert pause_release.exists()\n"
        )
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "import time\n"
        "prompt = sys.stdin.buffer.read()\n"
        "assert prompt\n"
        "assert 'RETROBOT_SECRET_SENTINEL' not in os.environ\n"
        "schema_path = pathlib.Path(sys.argv[sys.argv.index('--output-schema') + 1])\n"
        "final_path = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
        "schema = json.loads(schema_path.read_text(encoding='utf-8'))\n"
        "assert schema['additionalProperties'] is False\n"
        "assert set(schema['required']) == {\n"
        "    'main_goal', 'validation_status', 'performed', 'keep',\n"
        "    'problem', 'try_next', 'evidence'\n"
        "}\n"
        f"{pause_code}"
        "print('stdout must be ignored')\n"
        "print('stderr must be ignored', file=sys.stderr)\n"
        f"final_path.write_bytes({payload!r})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _initialize_runtime_repository(
    tmp_path: Path,
    *,
    pause_ready: Path | None = None,
    pause_release: Path | None = None,
) -> tuple[Path, dict[str, str]]:
    repository = tmp_path / "repository"
    repository.mkdir()
    for relative_path in (
        ".githooks/post-commit",
        "scripts/retrobot_context.py",
        "scripts/retrobot_guard.py",
        "scripts/retrobot_publish.py",
        "retrobot/SKILL.md",
    ):
        source = REPOSITORY_ROOT / relative_path
        target = repository / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    worklog = repository / "retros/WORKLOG.md"
    worklog.parent.mkdir(parents=True, exist_ok=True)
    worklog.write_text(
        "# Worklog\n\n"
        "## 2026-08-02 — runtime contract\n\n"
        "- 목표: 자동 회고 경로를 검증한다.\n"
        "- 검증: 임시 저장소에서 실제 hook을 실행한다.\n",
        encoding="utf-8",
    )
    (repository / "retros/TIMELINE.md").write_text(
        "# Retrobot 타임라인\n\n"
        "| 시각 (KST) | 회고 | 기준 커밋 |\n"
        "|---|---|---|\n",
        encoding="utf-8",
    )
    (repository / "product.txt").write_text("initial\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_codex(
        fake_bin / "codex",
        pause_ready=pause_ready,
        pause_release=pause_release,
    )
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    child_environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "CODEX_HOME": str(codex_home),
        "HOME": str(tmp_path / "neutral-home"),
        "RETROBOT_SECRET_SENTINEL": "never-forward-this-value",
    }

    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Retrobot Test")
    _git(repository, "config", "user.email", "retrobot@example.invalid")
    _git(repository, "add", ".")
    _git(repository, "update-index", "--chmod=+x", ".githooks/post-commit")
    _git(repository, "commit", "-qm", "test: initialize isolated repository")
    _git(repository, "config", "core.hooksPath", ".githooks")
    _git(repository, "config", "core.filemode", "false")
    (repository / "retros/TIMELINE.md").chmod(0o777)
    return repository, child_environment


@pytest.fixture
def repository_filesystem_tmp_path() -> Iterator[Path]:
    base_directory = REPOSITORY_ROOT / ".data"
    base_directory.mkdir(exist_ok=True)
    temporary_path = Path(
        tempfile.mkdtemp(prefix="retrobot-runtime-", dir=base_directory)
    )
    try:
        yield temporary_path
    finally:
        shutil.rmtree(temporary_path)


@pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("flock") is None,
    reason="Git and flock are required for the Retrobot runtime contract",
)
def test_post_commit_publishes_only_validated_retrospective_paths(
    repository_filesystem_tmp_path: Path,
) -> None:
    tmp_path = repository_filesystem_tmp_path
    repository, child_environment = _initialize_runtime_repository(tmp_path)
    _git(repository, "update-index", "--assume-unchanged", "retrobot/SKILL.md")
    assert _git(repository, "ls-files", "-v", "retrobot/SKILL.md").startswith("h ")

    (repository / "product.txt").write_text("initial\nchanged\n", encoding="utf-8")
    _git(repository, "add", "product.txt")
    commit_output = _git(
        repository,
        "commit",
        "-m",
        "test: trigger bounded retrospective",
        environment=child_environment,
    )

    assert _git(repository, "log", "-1", "--format=%s") == AUTO_COMMIT_SUBJECT, (
        commit_output
    )
    product_commit = _git(repository, "rev-parse", "HEAD^")
    assert _git(repository, "log", "-1", "--format=%s", product_commit) == (
        "test: trigger bounded retrospective"
    )
    generated_paths = sorted((repository / "retros").glob("*_retro.md"))
    assert len(generated_paths) == 1
    changed_paths = _git(
        repository,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "HEAD",
    ).splitlines()
    assert set(changed_paths) == {
        "retros/TIMELINE.md",
        generated_paths[0].relative_to(repository).as_posix(),
    }

    generated = generated_paths[0].read_bytes()
    validate_retrobot_output(generated, expected_commit_sha=product_commit)
    assert product_commit in (repository / "retros/TIMELINE.md").read_text(encoding="utf-8")
    assert _git(repository, "status", "--porcelain") == ""
    assert _git(repository, "ls-files", "-v", "retrobot/SKILL.md").startswith("h ")

    git_dir = Path(_git(repository, "rev-parse", "--absolute-git-dir"))
    leftovers = sorted(path.name for path in git_dir.glob("retrobot-*"))
    assert len(leftovers) == 1
    assert leftovers[0].startswith(f"retrobot-recovery.{product_commit}.")
    recovery_directory = git_dir / leftovers[0]
    assert (recovery_directory / "retro.owner").samefile(generated_paths[0])
    assert (recovery_directory / "timeline.owner").samefile(
        repository / "retros/TIMELINE.md"
    )
    assert (recovery_directory / "index.owner").is_file()
    assert not list((repository / "retros").glob(".retrobot-transaction.*"))


@pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("flock") is None,
    reason="Git and flock are required for the Retrobot runtime contract",
)
def test_post_commit_holds_the_index_lock_across_codex_execution(
    repository_filesystem_tmp_path: Path,
) -> None:
    tmp_path = repository_filesystem_tmp_path
    pause_ready = tmp_path / "codex-ready"
    pause_release = tmp_path / "codex-release"
    repository, child_environment = _initialize_runtime_repository(
        tmp_path,
        pause_ready=pause_ready,
        pause_release=pause_release,
    )
    original_branch = _git(repository, "symbolic-ref", "--short", "HEAD")
    (repository / "product.txt").write_text("initial\nchanged\n", encoding="utf-8")
    _git(repository, "add", "product.txt")

    first_commit = subprocess.Popen(
        ("git", "commit", "-m", "test: hold index during retrospective"),
        cwd=repository,
        env=child_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_stdout = ""
    first_stderr = ""
    try:
        deadline = time.monotonic() + 10
        while not pause_ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pause_ready.exists()
        git_dir = Path(_git(repository, "rev-parse", "--absolute-git-dir"))
        assert (git_dir / "index.lock").is_file()

        concurrent_commit = subprocess.run(
            ("git", "commit", "--allow-empty", "-m", "test: concurrent commit"),
            cwd=repository,
            env=child_environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert concurrent_commit.returncode != 0
        assert _git(repository, "symbolic-ref", "--short", "HEAD") == original_branch
    finally:
        pause_release.write_text("release", encoding="utf-8")
        first_stdout, first_stderr = first_commit.communicate(timeout=15)

    assert first_commit.returncode == 0, "\n".join((first_stdout, first_stderr))
    assert _git(repository, "log", "-1", "--format=%s") == AUTO_COMMIT_SUBJECT
    assert _git(repository, "status", "--porcelain") == ""


@pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("flock") is None,
    reason="Git and flock are required for the Retrobot runtime contract",
)
def test_post_commit_head_lock_blocks_branch_drift(
    repository_filesystem_tmp_path: Path,
) -> None:
    tmp_path = repository_filesystem_tmp_path
    pause_ready = tmp_path / "codex-ready"
    pause_release = tmp_path / "codex-release"
    repository, child_environment = _initialize_runtime_repository(
        tmp_path,
        pause_ready=pause_ready,
        pause_release=pause_release,
    )
    original_branch = _git(repository, "symbolic-ref", "--short", "HEAD")
    (repository / "product.txt").write_text("initial\nchanged\n", encoding="utf-8")
    _git(repository, "add", "product.txt")

    first_commit = subprocess.Popen(
        ("git", "commit", "-m", "test: pin retrospective branch"),
        cwd=repository,
        env=child_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_stdout = ""
    first_stderr = ""
    try:
        deadline = time.monotonic() + 10
        while not pause_ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pause_ready.exists()
        branch_switch = subprocess.run(
            ("git", "switch", "-c", "other"),
            cwd=repository,
            env=child_environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert branch_switch.returncode != 0
    finally:
        pause_release.write_text("release", encoding="utf-8")
        first_stdout, first_stderr = first_commit.communicate(timeout=15)

    assert first_commit.returncode == 0, "\n".join((first_stdout, first_stderr))
    assert _git(repository, "symbolic-ref", "--short", "HEAD") == original_branch
    assert _git(repository, "log", "-1", "--format=%s") == AUTO_COMMIT_SUBJECT
    assert _git(repository, "status", "--porcelain") == ""
    git_dir = Path(_git(repository, "rev-parse", "--absolute-git-dir"))
    assert not (git_dir / "index.lock").exists()


@pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("flock") is None,
    reason="Git and flock are required for the Retrobot runtime contract",
)
def test_post_commit_preserves_unrelated_intent_to_add_index_state(
    repository_filesystem_tmp_path: Path,
) -> None:
    repository, child_environment = _initialize_runtime_repository(
        repository_filesystem_tmp_path
    )
    intent_path = repository / "intent.txt"
    intent_path.write_text("not part of this commit\n", encoding="utf-8")
    _git(repository, "add", "--intent-to-add", "intent.txt")
    intent_before = _git(repository, "ls-files", "--debug", "intent.txt")

    (repository / "product.txt").write_text("initial\nchanged\n", encoding="utf-8")
    _git(repository, "add", "product.txt")
    _git(
        repository,
        "commit",
        "-m",
        "test: preserve unrelated index flags",
        environment=child_environment,
    )

    assert _git(repository, "log", "-1", "--format=%s") == AUTO_COMMIT_SUBJECT
    assert _git(repository, "ls-files", "--debug", "intent.txt") == intent_before
    assert intent_path.read_text(encoding="utf-8") == "not part of this commit\n"


@pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("flock") is None,
    reason="Git and flock are required for the Retrobot runtime contract",
)
def test_post_commit_preserves_staged_timeline_when_pathspec_commit_excludes_it(
    repository_filesystem_tmp_path: Path,
) -> None:
    repository, child_environment = _initialize_runtime_repository(
        repository_filesystem_tmp_path
    )
    timeline_path = repository / "retros/TIMELINE.md"
    committed_timeline = timeline_path.read_text(encoding="utf-8")
    timeline_path.write_text(
        f"{committed_timeline}\n사용자가 다음 커밋용으로 준비한 내용\n",
        encoding="utf-8",
    )
    _git(repository, "add", "retros/TIMELINE.md")
    staged_timeline_before = _git(
        repository, "rev-parse", ":retros/TIMELINE.md"
    )
    timeline_path.write_text(committed_timeline, encoding="utf-8")

    (repository / "product.txt").write_text("initial\nchanged\n", encoding="utf-8")
    _git(
        repository,
        "commit",
        "-m",
        "test: exclude staged timeline from pathspec commit",
        "--",
        "product.txt",
        environment=child_environment,
    )

    assert _git(repository, "log", "-1", "--format=%s") == (
        "test: exclude staged timeline from pathspec commit"
    )
    assert _git(repository, "rev-parse", ":retros/TIMELINE.md") == (
        staged_timeline_before
    )
    assert timeline_path.read_text(encoding="utf-8") == committed_timeline
    assert _git(repository, "diff", "--cached", "--name-only") == (
        "retros/TIMELINE.md"
    )
    assert not list((repository / "retros").glob("*_retro.md"))


@pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("flock") is None,
    reason="Git and flock are required for the Retrobot runtime contract",
)
def test_post_commit_preserves_timeline_assume_unchanged_flag(
    repository_filesystem_tmp_path: Path,
) -> None:
    repository, child_environment = _initialize_runtime_repository(
        repository_filesystem_tmp_path
    )
    _git(repository, "update-index", "--assume-unchanged", "retros/TIMELINE.md")
    timeline_tag_before = _git(
        repository, "ls-files", "-v", "--", "retros/TIMELINE.md"
    )
    assert timeline_tag_before == "h retros/TIMELINE.md"

    (repository / "product.txt").write_text("initial\nchanged\n", encoding="utf-8")
    _git(repository, "add", "product.txt")
    _git(
        repository,
        "commit",
        "-m",
        "test: preserve protected index flags",
        environment=child_environment,
    )

    assert _git(repository, "log", "-1", "--format=%s") == (
        "test: preserve protected index flags"
    )
    assert _git(repository, "ls-files", "-v", "--", "retros/TIMELINE.md") == (
        timeline_tag_before
    )
    assert not list((repository / "retros").glob("*_retro.md"))
