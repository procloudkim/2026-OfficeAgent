"""Fail-closed disposable database lifecycle for product-pipeline evaluation."""

from __future__ import annotations

import argparse
import importlib
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import officeagent.evaluation.isolation as isolation
from officeagent.evaluation.runner import EvaluationReport
from officeagent.infrastructure.settings import Settings
from tests.test_evaluation_dataset import GOLD_V1


def test_disposable_evaluation_database_is_split_scoped_and_always_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = Settings(
        database_url="postgresql://officeagent:not-logged@127.0.0.1:15432/officeagent",
        database_admin_url=None,
        _env_file=None,
    )
    lifecycle: list[tuple[str, str]] = []

    def fake_create(admin_url: str, database_name: str) -> str:
        assert conninfo_to_dict(admin_url)["dbname"] == "postgres"
        lifecycle.append(("create", database_name))
        return make_conninfo(admin_url, dbname=database_name)

    def fake_drop(admin_url: str, database_name: str) -> None:
        assert conninfo_to_dict(admin_url)["dbname"] == "postgres"
        lifecycle.append(("drop", database_name))

    monkeypatch.setattr(isolation, "_create_database", fake_create)
    monkeypatch.setattr(isolation, "_drop_database", fake_drop)

    with pytest.raises(RuntimeError, match="synthetic failure"):
        with isolation.disposable_evaluation_settings(base, "holdout") as active:
            database_name = conninfo_to_dict(active.database_url)["dbname"]
            assert isolation.EVALUATION_DATABASE_NAME.fullmatch(database_name)
            assert database_name.startswith("officeagent_eval_holdout_")
            assert active.database_url != base.database_url
            assert active.database_admin_url is None
            assert active.data_dir.is_dir()
            assert active.codex_workspace_path.is_relative_to(active.data_dir)
            raise RuntimeError("synthetic failure")

    assert lifecycle[0][0] == "create"
    assert lifecycle[1] == ("drop", lifecycle[0][1])
    assert conninfo_to_dict(base.database_url)["dbname"] == "officeagent"


@pytest.mark.parametrize(
    "database_url,admin_url",
    [
        ("postgresql://officeagent:x@db.internal:5432/officeagent", None),
        (
            "postgresql://officeagent:x@127.0.0.1:15432/officeagent",
            "postgresql://officeagent:x@127.0.0.1:15432/officeagent",
        ),
    ],
)
def test_evaluation_admin_target_must_be_loopback_postgres(
    database_url: str,
    admin_url: str | None,
) -> None:
    with pytest.raises(RuntimeError, match="local postgres admin database"):
        isolation.resolve_local_admin_url(database_url, admin_url)


def test_effective_evaluation_target_rejects_product_and_wrong_split() -> None:
    product_url = "postgresql://officeagent:x@127.0.0.1:15432/officeagent"
    database_name = "officeagent_eval_holdout_a1b2c3d4e5f6"
    holdout_url = f"postgresql://officeagent:x@127.0.0.1:15432/{database_name}"

    with pytest.raises(RuntimeError, match="not disposable"):
        isolation.validate_evaluation_database_url(product_url, split="dev")
    with pytest.raises(RuntimeError, match="selected split"):
        isolation.validate_evaluation_database_url(holdout_url, split="dev")


def test_empty_corpus_guard_rejects_any_preexisting_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "postgresql://officeagent:x@127.0.0.1:15432/officeagent_eval_dev_a1b2c3d4e5f6"

    class Result:
        def fetchone(self) -> tuple[int, int, int, int, int]:
            return (1, 0, 0, 0, 0)

    class Connection:
        def execute(self, statement: str) -> Result:
            assert "document_chunks" in statement
            return Result()

    monkeypatch.setattr(isolation.psycopg, "connect", lambda _: nullcontext(Connection()))

    with pytest.raises(RuntimeError, match="not empty"):
        isolation.verify_empty_evaluation_corpus(database_url)


def test_empty_corpus_guard_accepts_fresh_migrated_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_name = "officeagent_eval_holdout_a1b2c3d4e5f6"
    database_url = f"postgresql://officeagent:x@localhost:15432/{database_name}"

    class Result:
        def fetchone(self) -> tuple[int, int, int, int, int]:
            return (0, 0, 0, 0, 0)

    class Connection:
        def execute(self, statement: str) -> Result:
            assert "answer_cache" in statement
            return Result()

    monkeypatch.setattr(isolation.psycopg, "connect", lambda _: nullcontext(Connection()))

    isolation.verify_empty_evaluation_corpus(database_url)


async def test_cli_runs_build_empty_check_and_evaluator_inside_disposable_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evaluator_cli = importlib.import_module("officeagent.evaluation.__main__")
    events: list[str] = []
    base = Settings(_env_file=None)
    database_name = "officeagent_eval_dev_a1b2c3d4e5f6"
    eval_url = f"postgresql://officeagent:x@127.0.0.1:15432/{database_name}"

    @contextmanager
    def fake_disposable(settings: Settings, split: str):
        assert settings is base
        assert split == "dev"
        events.append("database-enter")
        yield settings.model_copy(update={"database_url": eval_url})
        events.append("database-exit")

    async def fake_build(settings: Settings) -> SimpleNamespace:
        assert settings.database_url == eval_url
        events.append("build-and-migrate")
        return SimpleNamespace(ingestion=object(), questions=object())

    def fake_empty_guard(database_url: str) -> None:
        assert database_url == eval_url
        events.append("empty-check")

    class FakeRunner:
        def __init__(self, ingestion: object, questions: object) -> None:
            del ingestion, questions

        async def run(self, dataset) -> EvaluationReport:
            events.append(f"run-{dataset.split}")
            return SimpleNamespace(passed=True, to_json=lambda: "{}\n")

    monkeypatch.setattr(evaluator_cli, "get_settings", lambda: base)
    monkeypatch.setattr(evaluator_cli, "disposable_evaluation_settings", fake_disposable)
    monkeypatch.setattr(evaluator_cli, "build_services", fake_build)
    monkeypatch.setattr(evaluator_cli, "verify_empty_evaluation_corpus", fake_empty_guard)
    monkeypatch.setattr(evaluator_cli, "EvaluationRunner", FakeRunner)
    args = argparse.Namespace(
        dataset=GOLD_V1,
        split="dev",
        output=tmp_path / "report.json",
        require_approved=False,
        verify_only=False,
        report_only=False,
    )

    exit_code = await evaluator_cli._run(args)

    assert exit_code == 0
    assert events == [
        "database-enter",
        "build-and-migrate",
        "empty-check",
        "run-dev",
        "database-exit",
    ]


def test_cli_separates_and_sanitizes_unexpected_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evaluator_cli = importlib.import_module("officeagent.evaluation.__main__")
    secret = "postgresql://officeagent:secret@127.0.0.1/product"

    def fail_run(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError(secret)

    monkeypatch.setattr(evaluator_cli.anyio, "run", fail_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["officeagent-evaluation", "--split", "dev", "--verify-only"],
    )

    assert evaluator_cli.main() == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "evaluation runtime error: RuntimeError\n"
    assert secret not in captured.err
