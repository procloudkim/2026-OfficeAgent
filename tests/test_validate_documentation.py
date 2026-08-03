"""Documentation drift checks used by hosted CI."""

from __future__ import annotations

from pathlib import Path

from officeagent.infrastructure.settings import Settings
from scripts.validate_documentation import validate_repository


def _write_minimum_repository(root: Path) -> None:
    (root / ".github/workflows").mkdir(parents=True)
    (root / "docs").mkdir()
    workflow = """permissions:
  contents: read
steps:
  - uses: actions/checkout@1111111111111111111111111111111111111111
    with:
      persist-credentials: false
      ref: ${{ github.event.pull_request.head.sha || github.sha }}
  - run: uv lock --check
  - run: uv sync --frozen --group dev
  - run: uv run ruff check --no-cache .
  - run: uv run pytest -q
  - run: docker compose config --quiet
  - run: docker compose build test
  - run: docker compose up -d --wait db
  - run: docker compose run --rm migrate
  - run: docker compose run --rm test
  - run: python -m scripts.live_smoke --readiness-only
  - run: python -m scripts.validate_documentation
"""
    (root / ".github/workflows/quality-gates.yml").write_text(workflow)
    (root / "README.md").write_text(
        "[CI](.github/workflows/quality-gates.yml)\n",
        encoding="utf-8",
    )
    (root / "ARCHITECTURE.md").write_text("quality-gates\n", encoding="utf-8")
    (root / "PROMPT_DESIGN.md").write_text("[Architecture](ARCHITECTURE.md)\n")
    (root / "docs/EVALUATOR_MANUAL.md").write_text("[README](../README.md)\n")
    (root / "docs/PRD_PHASE2.md").write_text("CI source implemented\n")
    (root / "docs/REPOSITORY_MAP.md").write_text("CI source implemented\n")
    (root / "docs/TRADEOFF_STORAGE_AND_DOCKER.md").write_text("CI source implemented\n")
    (root / "docs/V2_REQUIREMENTS_TRACEABILITY.md").write_text("current evidence\n")


def test_documentation_validator_accepts_a_self_contained_handoff(tmp_path: Path) -> None:
    _write_minimum_repository(tmp_path)

    report = validate_repository(tmp_path)

    assert report.errors == ()
    assert report.checked_files == 8
    assert report.checked_links == 3


def test_documentation_validator_rejects_broken_or_escaping_links(tmp_path: Path) -> None:
    _write_minimum_repository(tmp_path)
    (tmp_path / "README.md").write_text(
        "[CI](.github/workflows/quality-gates.yml)\n"
        "[Missing](docs/missing.md)\n"
        "[Escape](../private.md)\n",
        encoding="utf-8",
    )

    report = validate_repository(tmp_path)

    assert any("broken local link" in error for error in report.errors)
    assert any("link escapes repository" in error for error in report.errors)


def test_documentation_validator_rejects_stale_ci_status(tmp_path: Path) -> None:
    _write_minimum_repository(tmp_path)
    (tmp_path / "ARCHITECTURE.md").write_text(
        "quality-gates\nCI workflow는 별도 `PLANNED` 범위다\n",
        encoding="utf-8",
    )

    report = validate_repository(tmp_path)

    assert any("stale CI status" in error for error in report.errors)


def test_documentation_validator_rejects_unpinned_or_secret_using_workflow(
    tmp_path: Path,
) -> None:
    _write_minimum_repository(tmp_path)
    workflow = tmp_path / ".github/workflows/quality-gates.yml"
    content = workflow.read_text().replace(
        "actions/checkout@1111111111111111111111111111111111111111",
        "actions/checkout@v7",
    )
    workflow.write_text(content + "\n  - run: echo ${{ secrets.DEPLOY_TOKEN }}\n")

    report = validate_repository(tmp_path)

    assert any("non-SHA action reference" in error for error in report.errors)
    assert any("forbidden marker" in error for error in report.errors)


def test_readme_runtime_defaults_match_settings() -> None:
    settings = Settings(_env_file=None)
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert (
        "| `OFFICEAGENT_CHUNK_SIZE` / `OFFICEAGENT_CHUNK_OVERLAP` | "
        f"`{settings.chunk_size}` / `{settings.chunk_overlap}` |"
    ) in readme
    assert (
        "| `OFFICEAGENT_MINIMUM_RELEVANCE_SCORE` | "
        f"`{settings.minimum_relevance_score:.2f}` |"
    ) in readme
