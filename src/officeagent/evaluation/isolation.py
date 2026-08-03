"""Disposable local PostgreSQL boundary for evaluator runs that mutate a corpus."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from officeagent.evaluation.dataset import Split
from officeagent.infrastructure.settings import Settings

EVALUATION_DATABASE_NAME = re.compile(r"^officeagent_eval_(?:dev|holdout)_[0-9a-f]{12}$")
_ADMIN_DATABASE_NAME = "postgres"
_MAX_CONNINFO_LENGTH = 4_096
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1"})


class EvaluationIsolationError(RuntimeError):
    """Raised before or during a fail-closed disposable evaluation lifecycle."""


@contextmanager
def disposable_evaluation_settings(
    base_settings: Settings,
    split: Split,
) -> Iterator[Settings]:
    """Yield settings for a new split-labelled DB and always remove it afterward."""

    admin_url = resolve_local_admin_url(
        base_settings.database_url,
        base_settings.database_admin_url,
    )
    database_name = f"officeagent_eval_{split}_{uuid4().hex[:12]}"
    with TemporaryDirectory(prefix=f"officeagent-eval-{split}-") as directory:
        data_dir = Path(directory).resolve()
        database_url = _create_database(admin_url, database_name)
        try:
            validate_evaluation_database_url(database_url, split=split)
            yield base_settings.model_copy(
                update={
                    "data_dir": data_dir,
                    "database_url": database_url,
                    "database_admin_url": None,
                    "codex_workspace_directory_name": "codex-workspace",
                }
            )
        finally:
            _drop_database(admin_url, database_name)


def resolve_local_admin_url(
    database_url: str,
    explicit_admin_url: str | None,
) -> str:
    """Resolve only a loopback connection to the PostgreSQL admin database."""

    candidate = explicit_admin_url
    if candidate is None:
        if len(database_url) > _MAX_CONNINFO_LENGTH:
            raise EvaluationIsolationError("evaluation requires a local postgres admin database")
        try:
            candidate = make_conninfo(database_url, dbname=_ADMIN_DATABASE_NAME)
        except psycopg.ProgrammingError:
            raise EvaluationIsolationError(
                "evaluation requires a local postgres admin database"
            ) from None
    if len(candidate) > _MAX_CONNINFO_LENGTH:
        raise EvaluationIsolationError("evaluation requires a local postgres admin database")
    try:
        parameters = conninfo_to_dict(candidate)
    except psycopg.ProgrammingError:
        raise EvaluationIsolationError(
            "evaluation requires a local postgres admin database"
        ) from None
    if parameters.get("dbname") != _ADMIN_DATABASE_NAME:
        raise EvaluationIsolationError("evaluation requires a local postgres admin database")
    _validate_loopback(parameters)
    return candidate


def validate_evaluation_database_url(database_url: str, *, split: Split | None = None) -> None:
    """Reject any effective target outside the disposable evaluator namespace."""

    if len(database_url) > _MAX_CONNINFO_LENGTH:
        raise EvaluationIsolationError("evaluation database target is not disposable")
    try:
        parameters = conninfo_to_dict(database_url)
    except psycopg.ProgrammingError:
        raise EvaluationIsolationError("evaluation database target is not disposable") from None
    database_name = parameters.get("dbname", "")
    if EVALUATION_DATABASE_NAME.fullmatch(database_name) is None:
        raise EvaluationIsolationError("evaluation database target is not disposable")
    if split is not None and not database_name.startswith(f"officeagent_eval_{split}_"):
        raise EvaluationIsolationError("evaluation database target does not match selected split")
    _validate_loopback(parameters)


def verify_empty_evaluation_corpus(database_url: str) -> None:
    """After migrations, require zero product rows and corpus revision zero."""

    validate_evaluation_database_url(database_url)
    try:
        with psycopg.connect(database_url) as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM documents),
                    (SELECT count(*) FROM document_chunks),
                    (SELECT count(*) FROM answer_cache),
                    (SELECT count(*) FROM cache_dependencies),
                    (SELECT revision FROM corpus_state WHERE singleton)
                """
            ).fetchone()
    except psycopg.Error:
        raise EvaluationIsolationError(
            "evaluation could not verify the disposable corpus"
        ) from None
    if row is None or tuple(int(value) for value in row) != (0, 0, 0, 0, 0):
        raise EvaluationIsolationError("evaluation disposable corpus is not empty")


def _create_database(admin_url: str, database_name: str) -> str:
    if EVALUATION_DATABASE_NAME.fullmatch(database_name) is None:
        raise ValueError("unsafe evaluation database name")
    database_url = make_conninfo(admin_url, dbname=database_name)
    validate_evaluation_database_url(database_url)
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            connection.execute(statement)
    except psycopg.Error:
        raise EvaluationIsolationError("evaluation database creation failed") from None
    return database_url


def _drop_database(admin_url: str, database_name: str) -> None:
    if EVALUATION_DATABASE_NAME.fullmatch(database_name) is None:
        raise ValueError("unsafe evaluation database name")
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (database_name,),
            )
            identifier = sql.Identifier(database_name)
            statement = sql.SQL("DROP DATABASE IF EXISTS {}").format(identifier)
            connection.execute(statement)
    except psycopg.Error:
        raise EvaluationIsolationError("evaluation database cleanup failed") from None


def _validate_loopback(parameters: Mapping[str, str]) -> None:
    if "service" in parameters:
        raise EvaluationIsolationError("evaluation requires a local postgres admin database")
    hosts = parameters.get("host", "").split(",")
    if not hosts or any(host not in _LOOPBACK_HOSTS for host in hosts):
        raise EvaluationIsolationError("evaluation requires a local postgres admin database")
    host_addresses = parameters.get("hostaddr", "").split(",")
    if parameters.get("hostaddr") and any(
        address not in _LOOPBACK_ADDRESSES for address in host_addresses
    ):
        raise EvaluationIsolationError("evaluation requires a local postgres admin database")
