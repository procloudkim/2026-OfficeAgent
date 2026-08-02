"""Run Retrobot's Codex subprocess and validate its Markdown fail-closed."""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

MAX_PROMPT_BYTES = 16 * 1024
MAX_RETRO_BYTES = 64 * 1024

_OPTIONAL_ENVIRONMENT = (
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "LANG",
    "LC_ALL",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "WINDIR",
)
_PROXY_ENVIRONMENT = frozenset(
    {
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
    }
)
_SENSITIVE_ENVIRONMENT_NAME = re.compile(
    r"(?i)(?:access.?key|api.?key|auth|cookie|credential|database.?url|"
    r"pass(?:word|phrase)?|private.?key|secret|token)"
)
_TITLE = re.compile(r"# Retrobot 회고 — (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) KST")
_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_EXTERNAL_URI = re.compile(r"(?i)\b(?:data|file|ftp|https?|javascript|mailto):[^\s)]+")
_GITHUB_LOCATION = re.compile(r"(?i)(?:www\.)?github\.com/[A-Za-z0-9_.-]+")
_GITHUB_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9][A-Za-z0-9_-]{0,38}\b")
_CONNECTION_URI = re.compile(
    r"(?i)\b(?:amqps?|mongodb(?:\+srv)?|mysql|postgres(?:ql)?|redis)://[^\s]+"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(?:[a-z0-9_-]*(?:api[_ -]?key|authorization|database[_ -]?url|"
    r"pass(?:word|phrase)?|secret|token))"
    r"\s*[:=]\s*\S+"
)
_KNOWN_SECRET = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_PRIVATE_HOME = re.compile(r"(?:/(?:home|Users)/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+)")
_PRIVATE_REPOSITORY_SLUG = re.compile(
    r"(?i)(?:`)?[A-Za-z0-9][A-Za-z0-9-]{0,38}/"
    r"(?:officeagent-onboarding-challenge|[A-Za-z0-9_.-]*private[A-Za-z0-9_.-]*)(?:`)?"
)
_CREDENTIALED_CONNECTION_URI = re.compile(
    r"(?i)\b(?:amqps?|mongodb(?:\+srv)?|mysql|postgres(?:ql)?|redis)://"
    r"[^\s/@:]+(?::[^@\s]+)?@"
)
_DIRECT_COMPLETION_COMMENT = (
    "<!-- 지원자가 면접 전에 사실관계와 배운 점을 직접 보완하세요. -->"
)
_REQUIRED_HEADINGS = (
    "## 요약",
    "## 수행 내용",
    "## KPT 회고",
    "### Keep (좋았던 것)",
    "### Problem (나빴던 것)",
    "### Try (개선해야 할 것)",
    "## 증거",
    "## 직접 보완",
)


class RetrobotGuardError(RuntimeError):
    """A sanitized Retrobot execution or output rejection."""


def build_codex_environment(
    source: Mapping[str, str],
    *,
    run_directory: Path,
    codex_path: Path,
) -> dict[str, str]:
    """Return the exact environment required by the isolated Codex subprocess."""

    source_home = source.get("HOME") or source.get("USERPROFILE")
    codex_home = source.get("CODEX_HOME")
    if not codex_home and source_home:
        codex_home = str(Path(source_home) / ".codex")
    if not codex_home:
        raise RetrobotGuardError("Codex authentication location is unavailable")

    path = source.get("PATH") or os.pathsep.join((str(codex_path.parent), os.defpath))
    environment = {
        "CODEX_HOME": _validated_environment_value(codex_home),
        "HOME": str(run_directory),
        "NO_COLOR": "1",
        "PATH": _validated_environment_value(path),
        "TERM": "dumb",
        "TEMP": str(run_directory),
        "TMP": str(run_directory),
        "TMPDIR": str(run_directory),
    }
    for name in _OPTIONAL_ENVIRONMENT:
        value = source.get(name)
        if not value:
            continue
        if name in _PROXY_ENVIRONMENT and not _is_credential_free_proxy(value):
            continue
        environment[name] = _validated_environment_value(value)
    return environment


def validate_retrobot_output(
    raw_output: bytes,
    *,
    expected_commit_sha: str,
    forbidden_values: Sequence[str] = (),
) -> None:
    """Validate bounded Markdown structure and reject sensitive or active content."""

    if not 0 < len(raw_output) <= MAX_RETRO_BYTES:
        raise RetrobotGuardError("Generated retrospective size is outside the allowed range")
    try:
        output = raw_output.decode("utf-8")
    except UnicodeDecodeError:
        raise RetrobotGuardError("Generated retrospective is not valid UTF-8") from None
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected_commit_sha):
        raise RetrobotGuardError("Commit identifier is invalid")

    _validate_text_safety(output, forbidden_values=forbidden_values)
    lines = output.splitlines()
    _validate_heading_contract(lines)
    _validate_summary(lines, expected_commit_sha=expected_commit_sha)
    _validate_required_bullets(lines)
    _validate_completion_section(lines)


def validate_retrobot_prompt(
    raw_prompt: bytes,
    *,
    forbidden_values: Sequence[str] = (),
) -> None:
    """Reject private prompt data before any external Codex process starts."""

    if not 0 < len(raw_prompt) <= MAX_PROMPT_BYTES:
        raise RetrobotGuardError("Retrobot prompt size is outside the allowed range")
    try:
        prompt = raw_prompt.decode("utf-8")
    except UnicodeDecodeError:
        raise RetrobotGuardError("Retrobot prompt is not valid UTF-8") from None
    if any(
        unicodedata.category(character).startswith("C") and character != "\n"
        for character in prompt
    ):
        raise RetrobotGuardError("Retrobot prompt contains unsafe control characters")
    private_patterns = (
        _EMAIL,
        _GITHUB_LOCATION,
        _GITHUB_HANDLE,
        _CREDENTIALED_CONNECTION_URI,
        _CREDENTIAL_ASSIGNMENT,
        _KNOWN_SECRET,
        _PRIVATE_HOME,
        _PRIVATE_REPOSITORY_SLUG,
    )
    if any(pattern.search(prompt) for pattern in private_patterns):
        raise RetrobotGuardError("Retrobot prompt contains private data")
    if any(value in prompt for value in forbidden_values):
        raise RetrobotGuardError("Retrobot prompt contains inherited private data")


def execute_retrobot(
    *,
    prompt: bytes,
    codex_path: Path,
    run_directory: Path,
    output_path: Path,
    expected_commit_sha: str,
    source_environment: Mapping[str, str],
    timeout_seconds: float,
) -> None:
    """Execute Codex with bounded resources and leave only a validated output file."""

    try:
        _validate_commit_sha(expected_commit_sha)
        _validate_execution_inputs(
            prompt=prompt,
            codex_path=codex_path,
            run_directory=run_directory,
            output_path=output_path,
            timeout_seconds=timeout_seconds,
        )
        forbidden_values = _forbidden_environment_values(source_environment)
        validate_retrobot_prompt(prompt, forbidden_values=forbidden_values)
        environment = build_codex_environment(
            source_environment,
            run_directory=run_directory,
            codex_path=codex_path,
        )
        _run_codex_process(
            prompt=prompt,
            codex_path=codex_path,
            run_directory=run_directory,
            output_path=output_path,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        raw_output = output_path.read_bytes()
        validate_retrobot_output(
            raw_output,
            expected_commit_sha=expected_commit_sha,
            forbidden_values=forbidden_values,
        )
    except RetrobotGuardError:
        output_path.unlink(missing_ok=True)
        raise
    except (OSError, subprocess.SubprocessError):
        output_path.unlink(missing_ok=True)
        raise RetrobotGuardError("Retrobot execution failed") from None


def _validated_environment_value(value: str) -> str:
    if len(value) > 4096 or any(character in value for character in ("\0", "\n", "\r")):
        raise RetrobotGuardError("An allowed environment value is invalid")
    return value


def _is_credential_free_proxy(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme.casefold() in {"http", "https", "socks5", "socks5h"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _forbidden_environment_values(source: Mapping[str, str]) -> tuple[str, ...]:
    values: set[str] = set()
    for name, value in source.items():
        if value and len(value) >= 6 and _SENSITIVE_ENVIRONMENT_NAME.search(name):
            values.add(value)
    for name in ("CODEX_HOME", "HOME", "USERPROFILE"):
        value = source.get(name)
        if value and len(value) >= 6:
            values.add(value)
    private_location_names = _PROXY_ENVIRONMENT | {
        "NODE_EXTRA_CA_CERTS",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
    }
    for name in private_location_names:
        value = source.get(name)
        if value and len(value) >= 6:
            values.add(value)
    return tuple(sorted(values))


def _validate_text_safety(output: str, *, forbidden_values: Sequence[str]) -> None:
    if "\r" in output or any(
        unicodedata.category(character).startswith("C") and character != "\n"
        for character in output
    ):
        raise RetrobotGuardError("Generated retrospective contains unsafe control characters")
    if output.count(_DIRECT_COMPLETION_COMMENT) != 1:
        raise RetrobotGuardError("Generated retrospective completion marker is invalid")
    without_completion_comment = output.replace(_DIRECT_COMPLETION_COMMENT, "")
    unsafe_patterns = (
        _EMAIL,
        _EXTERNAL_URI,
        _GITHUB_LOCATION,
        _GITHUB_HANDLE,
        _CONNECTION_URI,
        _CREDENTIAL_ASSIGNMENT,
        _KNOWN_SECRET,
        _PRIVATE_HOME,
    )
    if any(pattern.search(without_completion_comment) for pattern in unsafe_patterns):
        raise RetrobotGuardError("Generated retrospective contains sensitive content")
    if any(value in output for value in forbidden_values):
        raise RetrobotGuardError("Generated retrospective contains inherited private data")
    active_content = r"```|~~~|!\[[^\]]*\]\(|<[^>\n]+>|\[[^\]]+\]\([^)]*\)"
    if re.search(active_content, without_completion_comment):
        raise RetrobotGuardError("Generated retrospective contains active Markdown or HTML")
    if _PRIVATE_REPOSITORY_SLUG.search(output):
        raise RetrobotGuardError("Generated retrospective contains a repository identifier")


def _validate_heading_contract(lines: list[str]) -> None:
    headings = [line for line in lines if re.match(r"^#{1,6}(?: |$)", line)]
    if len(headings) != len(_REQUIRED_HEADINGS) + 1:
        raise RetrobotGuardError("Generated retrospective headings are incomplete")
    title = _TITLE.fullmatch(headings[0])
    if title is None or tuple(headings[1:]) != _REQUIRED_HEADINGS:
        raise RetrobotGuardError("Generated retrospective headings are invalid")
    try:
        datetime.strptime(" ".join(title.groups()), "%Y-%m-%d %H:%M")
    except ValueError:
        raise RetrobotGuardError("Generated retrospective title time is invalid") from None

    nonempty = [line for line in lines if line]
    expected_attribution = (
        "> 최신 커밋 메타데이터와 제한된 작업 로그를 바탕으로 자동 생성했습니다."
    )
    if len(nonempty) < 2 or nonempty[1] != expected_attribution:
        raise RetrobotGuardError("Generated retrospective attribution is invalid")


def _validate_summary(lines: list[str], *, expected_commit_sha: str) -> None:
    summary = _section_lines(lines, "## 요약", "## 수행 내용")
    nonempty = [line for line in summary if line]
    if len(nonempty) != 5 or nonempty[0] != "| 항목 | 값 |":
        raise RetrobotGuardError("Generated retrospective summary table is invalid")
    if re.fullmatch(r"\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|", nonempty[1]) is None:
        raise RetrobotGuardError("Generated retrospective summary separator is invalid")
    expected_rows = ("기준 커밋", "주요 목표", "검증 상태")
    rows = [_parse_table_row(line) for line in nonempty[2:]]
    if tuple(label for label, _ in rows) != expected_rows or any(not value for _, value in rows):
        raise RetrobotGuardError("Generated retrospective summary rows are invalid")
    if rows[0][1] != f"`{expected_commit_sha}`":
        raise RetrobotGuardError("Generated retrospective references a different commit")


def _parse_table_row(line: str) -> tuple[str, str]:
    cells = [cell.strip() for cell in line.split("|")]
    if len(cells) != 4 or cells[0] or cells[-1]:
        raise RetrobotGuardError("Generated retrospective table row is invalid")
    return cells[1], cells[2]


def _validate_required_bullets(lines: list[str]) -> None:
    bounded_sections = (
        ("## 수행 내용", "## KPT 회고"),
        ("### Keep (좋았던 것)", "### Problem (나빴던 것)"),
        ("### Problem (나빴던 것)", "### Try (개선해야 할 것)"),
        ("### Try (개선해야 할 것)", "## 증거"),
        ("## 증거", "## 직접 보완"),
    )
    for start, end in bounded_sections:
        content = _section_lines(lines, start, end)
        if not any(line.startswith("- ") and len(line) > 2 for line in content):
            raise RetrobotGuardError("Generated retrospective section has no evidence-bearing item")


def _validate_completion_section(lines: list[str]) -> None:
    completion = _section_lines(lines, "## 직접 보완", None)
    if [line for line in completion if line] != [_DIRECT_COMPLETION_COMMENT]:
        raise RetrobotGuardError("Generated retrospective completion section is invalid")


def _section_lines(lines: list[str], start: str, end: str | None) -> list[str]:
    start_index = lines.index(start) + 1
    end_index = lines.index(end) if end is not None else len(lines)
    return lines[start_index:end_index]


def _validate_execution_inputs(
    *,
    prompt: bytes,
    codex_path: Path,
    run_directory: Path,
    output_path: Path,
    timeout_seconds: float,
) -> None:
    if not 0 < len(prompt) <= MAX_PROMPT_BYTES:
        raise RetrobotGuardError("Retrobot prompt size is outside the allowed range")
    if not codex_path.is_file() or not os.access(codex_path, os.X_OK):
        raise RetrobotGuardError("Codex executable is unavailable")
    if not run_directory.is_dir() or any(run_directory.iterdir()):
        raise RetrobotGuardError("Retrobot run directory is not empty")
    if not output_path.parent.is_dir() or output_path.is_symlink():
        raise RetrobotGuardError("Retrobot output location is invalid")
    if output_path.exists() and (not output_path.is_file() or output_path.stat().st_size != 0):
        raise RetrobotGuardError("Retrobot output location is not empty")
    if not 0 < timeout_seconds <= 600:
        raise RetrobotGuardError("Retrobot timeout is outside the allowed range")


def _validate_commit_sha(commit_sha: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit_sha) is None:
        raise RetrobotGuardError("Commit identifier is invalid")


def build_codex_command(codex_path: Path) -> list[str]:
    """Build the fixed, tool-free Codex invocation used by Retrobot."""

    return [
        str(codex_path),
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
        "-",
    ]


def _run_codex_process(
    *,
    prompt: bytes,
    codex_path: Path,
    run_directory: Path,
    output_path: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
) -> None:
    command = build_codex_command(codex_path)
    popen_options: dict[str, object] = {}
    if os.name == "posix":
        popen_options["start_new_session"] = True
    with output_path.open("wb") as output:
        process = subprocess.Popen(  # noqa: S603 - exact executable is resolved by the hook.
            command,
            cwd=run_directory,
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.DEVNULL,
            **popen_options,
        )
        try:
            process.communicate(input=prompt, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _terminate_process(process)
            raise RetrobotGuardError("Codex execution exceeded its time limit") from None
    if process.returncode != 0:
        raise RetrobotGuardError("Codex execution failed")


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.communicate(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGKILL)
    else:
        process.kill()
    process.communicate()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", required=True, type=Path)
    parser.add_argument("--run-directory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--timeout-seconds", required=True, type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    prompt = sys.stdin.buffer.read(MAX_PROMPT_BYTES + 1)
    try:
        execute_retrobot(
            prompt=prompt,
            codex_path=args.codex,
            run_directory=args.run_directory,
            output_path=args.output,
            expected_commit_sha=args.commit_sha,
            source_environment=os.environ,
            timeout_seconds=args.timeout_seconds,
        )
    except RetrobotGuardError as error:
        print(f"Retrobot guard rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
