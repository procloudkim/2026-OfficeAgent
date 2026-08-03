"""The evaluator runs through product-owned ingestion and Q&A boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field

from officeagent.application.question_answering import NO_EVIDENCE_ANSWER
from officeagent.domain.models import AnswerResult, DocumentChange, DocumentRecord, IngestionResult
from officeagent.evaluation.dataset import load_gold_dataset
from officeagent.evaluation.runner import EvaluationRunner
from tests.test_evaluation_dataset import GOLD_V1


@dataclass
class RecordingIngestion:
    filenames: list[str] = field(default_factory=list)

    @property
    def max_upload_bytes(self) -> int:
        return 1_000_000

    async def ingest(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> IngestionResult:
        del content_type, content
        self.filenames.append(filename)
        document = DocumentRecord(
            id=filename,
            filename=filename,
            content_type="text/plain",
            content_hash="hash",
            version=1,
            created_at=_fixed_datetime(),
            updated_at=_fixed_datetime(),
        )
        return IngestionResult(
            document=document,
            change=DocumentChange.CREATED,
            chunk_count=1,
            invalidated_cache_entries=0,
        )


@dataclass
class AbstainingQuestions:
    questions: list[str] = field(default_factory=list)

    async def answer(self, question: str) -> AnswerResult:
        self.questions.append(question)
        return AnswerResult(
            answer=NO_EVIDENCE_ANSWER,
            citations=(),
            cache_hit=False,
            cache_match=None,
            grounded=False,
            cache_written=False,
            cache_skip_reason="no_evidence",
        )


async def test_runner_keeps_dev_and_process_holdout_inputs_separate() -> None:
    dataset = load_gold_dataset(GOLD_V1, split="holdout")
    ingestion = RecordingIngestion()
    questions = AbstainingQuestions()

    report = await EvaluationRunner(ingestion, questions).run(dataset)

    assert len(report.cases) == 15
    assert ingestion.filenames == [
        "holdout-incident-response.md",
        "holdout-travel-procurement.txt",
    ]
    assert questions.questions == [case.question for case in dataset.cases]
    assert report.split == "holdout"
    assert report.approval_status == "pending"
    assert report.to_json() == report.to_json()
    assert all(not filename.startswith("dev-") for filename in ingestion.filenames)


def _fixed_datetime():
    from datetime import UTC, datetime

    return datetime(2026, 8, 3, tzinfo=UTC)
