"""Transparent lexical claim-coverage metrics for gold-case observations."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from officeagent.application.question_answering import NO_EVIDENCE_ANSWER
from officeagent.domain.models import AnswerResult
from officeagent.evaluation.dataset import GoldCase, QualityThresholds


@dataclass(frozen=True, slots=True)
class CaseScore:
    """Deterministic metrics for one question; semantic entailment is not inferred."""

    id: str
    answerable: bool
    retrieval_hit: bool | None
    reciprocal_rank: float | None
    answer_claim_coverage: float | None
    citation_claim_coverage: float | None
    abstained: bool | None
    passed: bool

    def to_dict(self) -> dict[str, object]:
        """Return stable JSON-ready fields."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class AggregateScore:
    """Split metrics and their explicit threshold decisions."""

    case_count: int
    answerable_count: int
    retrieval_negative_count: int
    case_pass_rate: float
    retrieval_hit_rate: float | None
    mean_reciprocal_rank: float | None
    answer_claim_coverage: float | None
    citation_claim_coverage: float | None
    abstention_accuracy: float | None
    threshold_results: dict[str, bool]
    passed: bool

    def to_dict(self) -> dict[str, object]:
        """Return stable JSON-ready fields."""

        return asdict(self)


def score_case(case: GoldCase, answer: AnswerResult) -> CaseScore:
    """Compare a product answer with explicit aliases; make no model-based judgment."""

    if not case.answerable:
        abstained = (
            not answer.grounded
            and not answer.citations
            and _normalized(answer.answer) == _normalized(NO_EVIDENCE_ANSWER)
        )
        return CaseScore(
            id=case.id,
            answerable=False,
            retrieval_hit=None,
            reciprocal_rank=None,
            answer_claim_coverage=None,
            citation_claim_coverage=None,
            abstained=abstained,
            passed=abstained,
        )

    expected_sources = set(case.expected_sources)
    first_expected_rank = next(
        (
            rank
            for rank, citation in enumerate(answer.citations, start=1)
            if citation.filename in expected_sources
        ),
        None,
    )
    retrieval_hit = first_expected_rank is not None
    answer_coverage = _claim_coverage(answer.answer, case.answer_claims)
    citation_coverage = max(
        (
            _claim_coverage(citation.quote, case.evidence_claims)
            for citation in answer.citations
            if citation.filename in expected_sources
        ),
        default=0.0,
    )
    pass_conditions = (
        answer.grounded,
        retrieval_hit,
        answer_coverage == 1.0,
        citation_coverage == 1.0,
    )
    passed = all(pass_conditions)
    return CaseScore(
        id=case.id,
        answerable=True,
        retrieval_hit=retrieval_hit,
        reciprocal_rank=(1.0 / first_expected_rank if first_expected_rank else 0.0),
        answer_claim_coverage=answer_coverage,
        citation_claim_coverage=citation_coverage,
        abstained=None,
        passed=passed,
    )


def aggregate_scores(
    scores: tuple[CaseScore, ...],
    thresholds: QualityThresholds,
) -> AggregateScore:
    """Aggregate with separate positive and retrieval-negative denominators."""

    if not scores:
        raise ValueError("at least one case score is required")
    positives = tuple(score for score in scores if score.answerable)
    negatives = tuple(score for score in scores if not score.answerable)
    metric_values = {
        "retrieval_hit_rate": _mean_bool(score.retrieval_hit for score in positives),
        "mean_reciprocal_rank": _mean(score.reciprocal_rank for score in positives),
        "answer_claim_coverage": _mean(score.answer_claim_coverage for score in positives),
        "citation_claim_coverage": _mean(score.citation_claim_coverage for score in positives),
        "abstention_accuracy": _mean_bool(score.abstained for score in negatives),
    }
    threshold_values = asdict(thresholds)
    threshold_results = {
        name: value is not None and value >= float(threshold_values[name])
        for name, value in metric_values.items()
    }
    return AggregateScore(
        case_count=len(scores),
        answerable_count=len(positives),
        retrieval_negative_count=len(negatives),
        case_pass_rate=sum(score.passed for score in scores) / len(scores),
        retrieval_hit_rate=metric_values["retrieval_hit_rate"],
        mean_reciprocal_rank=metric_values["mean_reciprocal_rank"],
        answer_claim_coverage=metric_values["answer_claim_coverage"],
        citation_claim_coverage=metric_values["citation_claim_coverage"],
        abstention_accuracy=metric_values["abstention_accuracy"],
        threshold_results=threshold_results,
        passed=all(threshold_results.values()),
    )


def _claim_coverage(text: str, claim_groups: tuple[tuple[str, ...], ...]) -> float:
    if not claim_groups:
        return 1.0
    matched = sum(any(_contains(text, alias) for alias in aliases) for aliases in claim_groups)
    return matched / len(claim_groups)


def _contains(text: str, alias: str) -> bool:
    normalized_text = _normalized(text)
    normalized_alias = _normalized(alias)
    return normalized_alias in normalized_text or normalized_alias.replace(" ", "") in (
        normalized_text.replace(" ", "")
    )


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    folded = re.sub(r"(?<=\d)[,_](?=\d)", "", folded)
    characters = [
        character if unicodedata.category(character)[0] in {"L", "N"} else " "
        for character in folded
    ]
    return re.sub(r"\s+", " ", "".join(characters)).strip()


def _mean(values: Iterable[float | None]) -> float | None:
    present = tuple(value for value in values if value is not None)
    return sum(present) / len(present) if present else None


def _mean_bool(values: Iterable[bool | None]) -> float | None:
    present = tuple(value for value in values if value is not None)
    return sum(present) / len(present) if present else None
