"""Command-line entrypoint for the versioned product-pipeline evaluator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import anyio

from officeagent.bootstrap import build_services
from officeagent.evaluation.dataset import DatasetValidationError, load_gold_dataset
from officeagent.evaluation.isolation import (
    EvaluationIsolationError,
    disposable_evaluation_settings,
    verify_empty_evaluation_corpus,
)
from officeagent.evaluation.runner import EvaluationRunner
from officeagent.infrastructure.settings import get_settings


def main() -> int:
    """Load one split, run the real service composition, and emit a JSON receipt."""

    parser = _parser()
    args = parser.parse_args()
    try:
        return anyio.run(_run, args)
    except DatasetValidationError as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return 2
    except EvaluationIsolationError as exc:
        print(f"evaluation safety error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"evaluation runtime error: {type(exc).__name__}", file=sys.stderr)
        return 3


async def _run(args: argparse.Namespace) -> int:
    dataset = load_gold_dataset(
        args.dataset,
        split=args.split,
        require_approved=args.require_approved,
    )
    if args.verify_only:
        encoded = (
            json.dumps(
                {
                    "schema_version": 1,
                    "dataset_version": dataset.version,
                    "dataset_sha256": dataset.dataset_sha256,
                    "approval_status": dataset.approval_status,
                    "split": dataset.split,
                    "document_count": len(dataset.documents),
                    "case_count": len(dataset.cases),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        _emit(encoded, args.output)
        return 0
    with disposable_evaluation_settings(get_settings(), dataset.split) as settings:
        services = await build_services(settings)
        await anyio.to_thread.run_sync(
            verify_empty_evaluation_corpus,
            settings.database_url,
        )
        report = await EvaluationRunner(services.ingestion, services.questions).run(dataset)
    encoded = report.to_json()
    _emit(encoded, args.output)
    return 0 if report.passed or args.report_only else 1


def _emit(encoded: str, output: Path | None) -> None:
    if output is None:
        print(encoded, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic gold-set metrics through the production service boundaries. "
            "The selected split must use its own empty PostgreSQL database."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/gold/v1"),
        help="versioned dataset directory (default: evals/gold/v1)",
    )
    parser.add_argument("--split", choices=("dev", "holdout"), required=True)
    parser.add_argument("--output", type=Path, help="write JSON receipt to this path")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="validate hashes and print metadata without opening DB or Codex",
    )
    parser.add_argument(
        "--require-approved",
        action="store_true",
        help="refuse a dataset whose approval.json is still pending",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="return zero even when a declared quality threshold fails",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
