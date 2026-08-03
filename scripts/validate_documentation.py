"""Validate evaluator-facing documentation without network or credentials."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

_REQUIRED_DOCUMENTS = (
    Path("README.md"),
    Path("ARCHITECTURE.md"),
    Path("PROMPT_DESIGN.md"),
    Path("docs/EVALUATOR_MANUAL.md"),
    Path("docs/PRD_PHASE2.md"),
    Path("docs/REPOSITORY_MAP.md"),
    Path("docs/TRADEOFF_STORAGE_AND_DOCKER.md"),
    Path("docs/V2_REQUIREMENTS_TRACEABILITY.md"),
)
_WORKFLOW_PATH = Path(".github/workflows/quality-gates.yml")
_LINK = re.compile(r"!?\[[^\]]*\]\((?P<target>[^)]+)\)")
_REMOTE_SCHEMES = ("http://", "https://", "mailto:")
_STALE_CI_CLAIMS = (
    "structured timing log와 CI workflow는 이번 제출의 `PLANNED` 비목표",
    "CI workflow는 별도 `PLANNED` 범위다",
    "CI는 `PLANNED`",
)
_WORKFLOW_COMMANDS = (
    "uv lock --check",
    "uv sync --frozen --group dev",
    "ruff check --no-cache .",
    "pytest -q",
    "docker compose config --quiet",
    "docker compose --file compose.recovery-drill.yaml --profile tools config --quiet",
    "docker compose build test",
    "docker compose up -d --wait db",
    "docker compose run --rm migrate",
    "docker compose run --rm test",
    "scripts.live_smoke --readiness-only",
    "scripts.validate_documentation",
)
_FORBIDDEN_WORKFLOW_MARKERS = (
    "${{ secrets.",
    " codex ",
    "upload-artifact",
    "docker push",
    " deploy",
)


@dataclass(frozen=True, slots=True)
class DocumentationReport:
    """Credential-free result returned by the documentation validator."""

    checked_files: int
    checked_links: int
    errors: tuple[str, ...]


def validate_repository(root: Path) -> DocumentationReport:
    """Check required artifacts, local links, and stale CI status claims."""

    repository_root = root.resolve()
    errors: list[str] = []
    documents: list[Path] = []
    for relative_path in _REQUIRED_DOCUMENTS:
        path = repository_root / relative_path
        if not path.is_file():
            errors.append(f"missing required document: {relative_path.as_posix()}")
            continue
        documents.append(path)

    workflow = repository_root / _WORKFLOW_PATH
    if not workflow.is_file():
        errors.append(f"missing CI workflow: {_WORKFLOW_PATH.as_posix()}")
    else:
        errors.extend(_validate_workflow(workflow.read_text(encoding="utf-8")))

    checked_links = 0
    for document in documents:
        content = document.read_text(encoding="utf-8")
        relative_document = document.relative_to(repository_root).as_posix()
        for stale_claim in _STALE_CI_CLAIMS:
            if stale_claim in content:
                errors.append(f"stale CI status in {relative_document}: {stale_claim}")
        for match in _LINK.finditer(content):
            checked_links += 1
            target = _link_path(match.group("target"))
            if target is None:
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.is_relative_to(repository_root):
                errors.append(f"link escapes repository in {relative_document}: {target}")
            elif not resolved.exists():
                errors.append(f"broken local link in {relative_document}: {target}")

    readme = repository_root / "README.md"
    architecture = repository_root / "ARCHITECTURE.md"
    if (
        readme.is_file()
        and _WORKFLOW_PATH.as_posix() not in readme.read_text(encoding="utf-8")
    ):
        errors.append("README.md does not link the quality-gates workflow")
    if architecture.is_file() and "quality-gates" not in architecture.read_text(encoding="utf-8"):
        errors.append("ARCHITECTURE.md does not define the quality-gates boundary")

    return DocumentationReport(
        checked_files=len(documents),
        checked_links=checked_links,
        errors=tuple(errors),
    )


def _validate_workflow(content: str) -> tuple[str, ...]:
    errors: list[str] = []
    if "permissions:\n  contents: read" not in content:
        errors.append("quality-gates workflow does not use contents: read")
    if "persist-credentials: false" not in content:
        errors.append("quality-gates workflow persists checkout credentials")
    if "github.event.pull_request.head.sha || github.sha" not in content:
        errors.append("quality-gates workflow does not pin the candidate head SHA")

    action_references = re.findall(
        r"^\s*(?:-\s*)?uses:\s*[^@\s]+@([^\s#]+)",
        content,
        re.MULTILINE,
    )
    if not action_references or any(
        re.fullmatch(r"[0-9a-f]{40}", reference) is None for reference in action_references
    ):
        errors.append("quality-gates workflow contains a non-SHA action reference")

    positions = [content.find(command) for command in _WORKFLOW_COMMANDS]
    if any(position < 0 for position in positions):
        errors.append("quality-gates workflow is missing a required command")
    elif positions != sorted(positions):
        errors.append("quality-gates workflow commands are out of contract order")

    lowered = content.casefold()
    for marker in _FORBIDDEN_WORKFLOW_MARKERS:
        if marker.casefold() in lowered:
            errors.append(f"quality-gates workflow contains forbidden marker: {marker}")
    return tuple(errors)


def _link_path(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split(maxsplit=1)[0]
    if not target or target.startswith("#") or target.startswith(_REMOTE_SCHEMES):
        return None
    path = unquote(target.partition("#")[0].partition("?")[0])
    return path or None


def main(argv: list[str] | None = None) -> int:
    """Print a sanitized summary and return non-zero for documentation drift."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    report = validate_repository(arguments.root)
    if report.errors:
        for error in report.errors:
            print(error, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "checked_files": report.checked_files,
                "checked_links": report.checked_links,
                "status": "PASS",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
