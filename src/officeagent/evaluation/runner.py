"""Execute gold cases through the product's ingestion and answer services."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import anyio

from officeagent.application.ingestion import DocumentIngestion
from officeagent.domain.models import AnswerResult
from officeagent.evaluation.dataset import GoldDataset
from officeagent.evaluation.metrics import AggregateScore, CaseScore, aggregate_scores, score_case


class QuestionAnswering(Protocol):
    """The existing application answer boundary used by the evaluator."""

    async def answer(self, question: str) -> AnswerResult:
        """Return the product answer contract for one question."""


@dataclass(frozen=True, slots=True)
class EvaluationCaseResult:
    """Reviewer-readable observation and deterministic score for one case."""

    id: str
    question: str
    answer: str
    citation_filenames: tuple[str, ...]
    score: CaseScore

    def to_dict(self) -> dict[str, object]:
        """Return stable JSON-ready fields."""

        return {
            "id": self.id,
            "question": self.question,
            "answer": self.answer,
            "citation_filenames": list(self.citation_filenames),
            "score": self.score.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Deterministic report with no timestamp or environment-specific path."""

    dataset_version: str
    dataset_sha256: str
    approval_status: str
    split: str
    documents: tuple[str, ...]
    cases: tuple[EvaluationCaseResult, ...]
    summary: AggregateScore

    @property
    def passed(self) -> bool:
        """Return whether every declared metric threshold passed."""

        return self.summary.passed

    def to_dict(self) -> dict[str, object]:
        """Return stable JSON-ready fields."""

        return {
            "schema_version": 1,
            "dataset_version": self.dataset_version,
            "dataset_sha256": self.dataset_sha256,
            "approval_status": self.approval_status,
            "split": self.split,
            "documents": list(self.documents),
            "cases": [case.to_dict() for case in self.cases],
            "summary": self.summary.to_dict(),
        }

    def to_json(self) -> str:
        """Serialize with stable key ordering for diffable evaluator receipts."""

        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )


class EvaluationRunner:
    """Use production-owned ports while keeping scoring deterministic and local."""

    def __init__(
        self,
        ingestion: DocumentIngestion,
        questions: QuestionAnswering,
    ) -> None:
        self._ingestion = ingestion
        self._questions = questions

    async def run(self, dataset: GoldDataset) -> EvaluationReport:
        """Ingest only the selected split, then answer and score in manifest order."""

        for document in dataset.documents:
            if document.split != dataset.split:
                raise ValueError("dataset contains a document from another split")
            content = await anyio.to_thread.run_sync(document.path.read_bytes)
            await self._ingestion.ingest(
                filename=document.filename,
                content_type=document.content_type,
                content=content,
            )

        results: list[EvaluationCaseResult] = []
        scores: list[CaseScore] = []
        for case in dataset.cases:
            if case.split != dataset.split:
                raise ValueError("dataset contains a case from another split")
            answer = await self._questions.answer(case.question)
            score = score_case(case, answer)
            scores.append(score)
            results.append(
                EvaluationCaseResult(
                    id=case.id,
                    question=case.question,
                    answer=answer.answer,
                    citation_filenames=tuple(citation.filename for citation in answer.citations),
                    score=score,
                )
            )

        summary = aggregate_scores(tuple(scores), dataset.thresholds)
        return EvaluationReport(
            dataset_version=dataset.version,
            dataset_sha256=dataset.dataset_sha256,
            approval_status=dataset.approval_status,
            split=dataset.split,
            documents=tuple(document.filename for document in dataset.documents),
            cases=tuple(results),
            summary=summary,
        )
