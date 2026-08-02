"""Fail-closed contracts for Retrobot's Codex boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.retrobot_guard import (
    MAX_RETRO_BYTES,
    RetrobotGuardError,
    build_codex_command,
    build_codex_environment,
    execute_retrobot,
    render_structured_retrospective,
    validate_retrobot_output,
    validate_retrobot_prompt,
)

COMMIT_SHA = "a" * 40
GENERATED_AT_KST = "2026-08-02_123456"


def _safe_retro(*, commit_sha: str = COMMIT_SHA, evidence: str = "검증된 로컬 테스트") -> str:
    return f"""# Retrobot 회고 — 2026-08-02 12:34 KST

> 최신 커밋 메타데이터와 제한된 작업 로그를 바탕으로 자동 생성했습니다.

## 요약

| 항목 | 값 |
|---|---|
| 기준 커밋 | `{commit_sha}` |
| 주요 목표 | Retrobot 경계를 작게 유지한다. |
| 검증 상태 | 입력에서 확인된 사실만 기록했다. |

## 수행 내용

- 고정 입력을 바탕으로 회고를 생성했다.

## KPT 회고

### Keep (좋았던 것)

- 증거와 주장을 분리했다.

### Problem (나빴던 것)

- 실제 외부 실행은 별도 검증이 필요하다.

### Try (개선해야 할 것)

- 최종 커밋에서 제한된 실행을 확인한다.

## 증거

- {evidence}

## 직접 보완

<!-- 지원자가 면접 전에 사실관계와 배운 점을 직접 보완하세요. -->
"""


def _write_fake_codex(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _safe_structured(*, evidence: str = "검증된 로컬 테스트") -> bytes:
    return json.dumps(
        {
            "main_goal": "Retrobot 경계를 작게 유지한다.",
            "validation_status": "입력에서 확인된 사실만 기록했다.",
            "performed": ["고정 입력을 바탕으로 회고를 생성했다."],
            "keep": ["증거와 주장을 분리했다."],
            "problem": ["실제 외부 실행은 별도 검증이 필요하다."],
            "try_next": ["최종 커밋에서 제한된 실행을 확인한다."],
            "evidence": [evidence],
        },
        ensure_ascii=False,
    ).encode()


def _structured_codex_script(payload: bytes, *, stdout: str = "") -> str:
    return (
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "assert 'RETROBOT_SECRET_SENTINEL' not in os.environ\n"
        f"sys.stdout.write({stdout!r})\n"
        "target = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
        f"target.write_bytes({payload!r})\n"
    )


def test_codex_environment_is_an_explicit_allowlist(tmp_path: Path) -> None:
    source = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/private-user",
        "CODEX_HOME": "/auth/codex",
        "HTTPS_PROXY": "http://proxy.internal:8080",
        "NO_PROXY": "localhost,127.0.0.1",
        "SSL_CERT_FILE": "/certs/company.pem",
        "TMPDIR": "/tmp/inherited-private-location",
        "OPENAI_API_KEY": "sentinel-api-key",
        "GITHUB_TOKEN": "sentinel-github-token",
        "DATABASE_URL": "postgresql://secret@database/private",
        "UNRELATED_SENTINEL": "sentinel-unrelated-value",
    }

    child = build_codex_environment(
        source,
        run_directory=tmp_path,
        codex_path=Path("/opt/codex"),
    )

    assert child["HOME"] == str(tmp_path)
    assert child["CODEX_HOME"] == "/auth/codex"
    assert child["PATH"] == "/usr/bin:/bin"
    assert child["HTTPS_PROXY"] == "http://proxy.internal:8080"
    assert child["NO_PROXY"] == "localhost,127.0.0.1"
    assert child["SSL_CERT_FILE"] == "/certs/company.pem"
    assert child["TMPDIR"] == str(tmp_path)
    assert child["TEMP"] == str(tmp_path)
    assert child["TMP"] == str(tmp_path)
    assert "OPENAI_API_KEY" not in child
    assert "GITHUB_TOKEN" not in child
    assert "DATABASE_URL" not in child
    assert "UNRELATED_SENTINEL" not in child


def test_codex_argv_disables_local_configuration_rules_and_shell() -> None:
    assert build_codex_command(
        Path("/opt/codex"),
        schema_path=Path("/private/schema.json"),
        final_message_path=Path("/private/final.json"),
    ) == [
        "/opt/codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--disable",
        "shell_tool",
        "--color",
        "never",
        "-c",
        'approval_policy="never"',
        "-c",
        "shell_environment_policy.inherit=none",
        "--output-schema",
        "/private/schema.json",
        "--output-last-message",
        "/private/final.json",
        "-",
    ]


def test_proxy_credentials_are_not_forwarded(tmp_path: Path) -> None:
    child = build_codex_environment(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/private-user",
            "HTTPS_PROXY": "http://proxy-user:proxy-pass@proxy.internal:8080",
        },
        run_directory=tmp_path,
        codex_path=Path("/opt/codex"),
    )

    assert "HTTPS_PROXY" not in child


def test_valid_retro_output_is_accepted() -> None:
    validate_retrobot_output(_safe_retro().encode(), expected_commit_sha=COMMIT_SHA)


def test_structured_output_is_rendered_to_canonical_markdown() -> None:
    assert render_structured_retrospective(
        _safe_structured(),
        expected_commit_sha=COMMIT_SHA,
        generated_at_kst=GENERATED_AT_KST,
    ).decode() == _safe_retro()


@pytest.mark.parametrize(
    "payload",
    [
        b'{"main_goal":"first","main_goal":"second"}',
        b'{"main_goal":NaN}',
        b'{"unexpected":"value"}',
        b'[]',
        b'not-json',
        b'{"main_goal":' + b"1" * 5_000 + b"}",
    ],
)
def test_malformed_structured_output_is_rejected(payload: bytes) -> None:
    with pytest.raises(RetrobotGuardError):
        render_structured_retrospective(
            payload,
            expected_commit_sha=COMMIT_SHA,
            generated_at_kst=GENERATED_AT_KST,
        )


@pytest.mark.parametrize(
    "evidence",
    [
        "https://attacker.invalid/collect",
        "OPENAI_API_KEY=sk-private-value",
        "first line\nsecond line",
        "first line\u2028second line",
        "first line\u2029second line",
        " table | breakout ",
    ],
)
def test_unsafe_structured_values_are_rejected(evidence: str) -> None:
    with pytest.raises(RetrobotGuardError):
        render_structured_retrospective(
            _safe_structured(evidence=evidence),
            expected_commit_sha=COMMIT_SHA,
            generated_at_kst=GENERATED_AT_KST,
        )


def test_public_documentation_url_is_allowed_in_prompt() -> None:
    validate_retrobot_prompt(b"See https://docs.python.org/3/library/subprocess.html")


@pytest.mark.parametrize(
    ("unsafe", "replacement"),
    [
        ("missing heading", "## 증거\n"),
        ("duplicate heading", "## 증거\n\n## 증거\n"),
        ("reordered heading", "### Try (개선해야 할 것)\n"),
        ("code fence", "```text\nsecret\n```"),
        ("HTML", '<img src="https://attacker.invalid/collect">'),
        ("external URL", "https://attacker.invalid/collect"),
        ("Markdown image", "![pixel](./pixel.png)"),
        ("email", "private-user@example.com"),
        ("GitHub profile", "github.com/private-user"),
        ("GitHub handle", "@private-user"),
        ("private repository", "`private-user/private-repository`"),
        ("credential", "OPENAI_API_KEY=sk-private-value"),
        ("database URI", "postgresql://private-user:secret@db/private"),
        ("private home", "/home/private-user/private-repository"),
    ],
)
def test_unsafe_generated_content_is_rejected(unsafe: str, replacement: str) -> None:
    output = _safe_retro(evidence=replacement)
    if unsafe == "missing heading":
        output = output.replace("## 증거\n", "", 1)
    elif unsafe == "duplicate heading":
        output = output.replace("## 증거\n", replacement, 1)
    elif unsafe == "reordered heading":
        output = output.replace(
            "### Keep (좋았던 것)\n",
            "### Try (개선해야 할 것)\n",
            1,
        )

    with pytest.raises(RetrobotGuardError):
        validate_retrobot_output(output.encode(), expected_commit_sha=COMMIT_SHA)


def test_invalid_utf8_and_oversized_output_are_rejected() -> None:
    with pytest.raises(RetrobotGuardError):
        validate_retrobot_output(b"\xff", expected_commit_sha=COMMIT_SHA)
    with pytest.raises(RetrobotGuardError):
        validate_retrobot_output(b"a" * (MAX_RETRO_BYTES + 1), expected_commit_sha=COMMIT_SHA)


def test_summary_must_reference_the_exact_commit() -> None:
    with pytest.raises(RetrobotGuardError):
        validate_retrobot_output(
            _safe_retro(commit_sha="b" * 40).encode(),
            expected_commit_sha=COMMIT_SHA,
        )


def test_execution_does_not_forward_sentinel_environment(tmp_path: Path) -> None:
    codex = tmp_path / "fake-codex"
    run_directory = tmp_path / "empty-run"
    run_directory.mkdir()
    output_path = tmp_path / "retro.tmp"
    output_path.touch()
    _write_fake_codex(
        codex,
        _structured_codex_script(
            _safe_structured(evidence="child environment was allowlisted"),
            stdout="ignored stdout with https://attacker.invalid/collect",
        ),
    )

    execute_retrobot(
        prompt=b"bounded prompt",
        codex_path=codex,
        run_directory=run_directory,
        output_path=output_path,
        expected_commit_sha=COMMIT_SHA,
        generated_at_kst=GENERATED_AT_KST,
        source_environment={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path / "real-home"),
            "CODEX_HOME": str(tmp_path / "real-codex-home"),
            "RETROBOT_SECRET_SENTINEL": "do-not-forward-or-print",
        },
        timeout_seconds=5,
    )

    assert output_path.read_text(encoding="utf-8") == _safe_retro(
        evidence="child environment was allowlisted"
    )
    assert not any(run_directory.iterdir())


@pytest.mark.parametrize(
    "private_prompt",
    [
        b"secret-value-from-parent",
        b"private-user@example.com",
        b"github.com/private-user",
        b"`private-user/private-repository`",
        b"/home/private-user/private-repository",
        b"postgresql://private-user:password@localhost/private",
    ],
)
def test_private_prompt_is_rejected_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    private_prompt: bytes,
) -> None:
    codex = tmp_path / "unused-codex"
    run_directory = tmp_path / "empty-run"
    run_directory.mkdir()
    output_path = tmp_path / "retro.tmp"
    output_path.touch()
    _write_fake_codex(codex, "#!/bin/sh\nexit 0\n")

    def fail_if_started(*args: object, **kwargs: object) -> None:
        pytest.fail("Codex subprocess started with a private prompt")

    monkeypatch.setattr("scripts.retrobot_guard.subprocess.Popen", fail_if_started)
    with pytest.raises(RetrobotGuardError):
        execute_retrobot(
            prompt=private_prompt,
            codex_path=codex,
            run_directory=run_directory,
            output_path=output_path,
            expected_commit_sha=COMMIT_SHA,
            generated_at_kst=GENERATED_AT_KST,
            source_environment={
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path),
                "RETROBOT_SECRET_SENTINEL": "secret-value-from-parent",
            },
            timeout_seconds=5,
        )

    assert not output_path.exists()


def test_timeout_removes_partial_output_without_leaking_stderr(tmp_path: Path) -> None:
    codex = tmp_path / "slow-codex"
    run_directory = tmp_path / "empty-run"
    run_directory.mkdir()
    output_path = tmp_path / "retro.tmp"
    output_path.touch()
    _write_fake_codex(
        codex,
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "import time\n"
        "sys.stderr.write('do-not-leak-this-secret')\n"
        "sys.stderr.flush()\n"
        "sys.stdout.write('partial output')\n"
        "sys.stdout.flush()\n"
        "time.sleep(5)\n",
    )

    with pytest.raises(RetrobotGuardError) as raised:
        execute_retrobot(
            prompt=b"bounded prompt",
            codex_path=codex,
            run_directory=run_directory,
            output_path=output_path,
            expected_commit_sha=COMMIT_SHA,
            generated_at_kst=GENERATED_AT_KST,
            source_environment={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
            timeout_seconds=0.05,
        )

    assert "do-not-leak-this-secret" not in str(raised.value)
    assert not output_path.exists()
    assert not any(run_directory.iterdir())


def test_invalid_model_output_is_removed(tmp_path: Path) -> None:
    codex = tmp_path / "unsafe-codex"
    run_directory = tmp_path / "empty-run"
    run_directory.mkdir()
    output_path = tmp_path / "retro.tmp"
    output_path.touch()
    _write_fake_codex(
        codex,
        _structured_codex_script(
            _safe_structured(evidence="https://attacker.invalid/collect")
        ),
    )

    with pytest.raises(RetrobotGuardError):
        execute_retrobot(
            prompt=b"bounded prompt",
            codex_path=codex,
            run_directory=run_directory,
            output_path=output_path,
            expected_commit_sha=COMMIT_SHA,
            generated_at_kst=GENERATED_AT_KST,
            source_environment={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
            timeout_seconds=5,
        )

    assert not output_path.exists()
    assert not any(run_directory.iterdir())


def test_private_artifact_cleanup_failure_rejects_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex = tmp_path / "fake-codex"
    run_directory = tmp_path / "empty-run"
    run_directory.mkdir()
    output_path = tmp_path / "retro.tmp"
    output_path.touch()
    _write_fake_codex(codex, _structured_codex_script(_safe_structured()))
    original_unlink = Path.unlink

    def reject_private_json_unlink(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        if path.name == "last-message.json":
            raise PermissionError("simulated private cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject_private_json_unlink)

    with pytest.raises(RetrobotGuardError, match="private response cleanup failed"):
        execute_retrobot(
            prompt=b"bounded prompt",
            codex_path=codex,
            run_directory=run_directory,
            output_path=output_path,
            expected_commit_sha=COMMIT_SHA,
            generated_at_kst=GENERATED_AT_KST,
            source_environment={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
            timeout_seconds=5,
        )

    assert not output_path.exists()
    assert (run_directory / "last-message.json").is_file()
