"""Deterministic answer, citation, retrieval, and abstention scoring."""

from __future__ import annotations

from officeagent.application.question_answering import NO_EVIDENCE_ANSWER
from officeagent.domain.models import AnswerResult, Citation
from officeagent.evaluation.dataset import GoldCase, QualityThresholds
from officeagent.evaluation.metrics import aggregate_scores, score_case


def test_answerable_case_requires_source_retrieval_and_claim_coverage() -> None:
    case = GoldCase(
        id="dev-education-budget",
        split="dev",
        question="교육비 한도는?",
        answerable=True,
        expected_sources=("dev-policy.txt",),
        answer_claims=(("2,000,000원", "200만 원"), ("연간", "1년")),
        evidence_claims=(("교육비",), ("2,000,000원", "200만 원")),
    )
    answer = _answer(
        "교육비는 1년에 200만 원까지 지원됩니다.",
        citations=(
            _citation(
                filename="unrelated.txt",
                quote="교육비 신청은 인사 포털에서 진행합니다.",
            ),
            _citation(
                filename="dev-policy.txt",
                quote="연간 교육비 한도는 2,000,000원입니다.",
            ),
        ),
    )

    score = score_case(case, answer)

    assert score.retrieval_hit is True
    assert score.reciprocal_rank == 0.5
    assert score.answer_claim_coverage == 1.0
    assert score.citation_claim_coverage == 1.0
    assert score.passed is True


def test_expected_filename_without_supporting_quote_does_not_pass_grounding() -> None:
    case = GoldCase(
        id="dev-review-exception",
        split="dev",
        question="긴급 배포는 누가 리뷰를 면제할 수 있나요?",
        answerable=True,
        expected_sources=("dev-guide.md",),
        answer_claims=(("면제할 수 없습니다",),),
        evidence_claims=(("면제 불가",),),
    )
    answer = _answer(
        "면제할 수 없습니다.",
        citations=(_citation(filename="dev-guide.md", quote="배포는 수요일입니다."),),
    )

    score = score_case(case, answer)

    assert score.retrieval_hit is True
    assert score.answer_claim_coverage == 1.0
    assert score.citation_claim_coverage == 0.0
    assert score.passed is False


def test_topic_adjacent_negative_requires_the_product_abstention_contract() -> None:
    case = GoldCase(
        id="dev-review-bypass",
        split="dev",
        question="긴급 배포 때 리뷰를 면제할 수 있는 직급은?",
        answerable=False,
        expected_sources=(),
        answer_claims=(),
        evidence_claims=(),
    )

    abstention = score_case(case, _answer(NO_EVIDENCE_ANSWER, grounded=False))
    hallucination = score_case(
        case,
        _answer("팀장은 면제할 수 있습니다.", grounded=False),
    )

    assert abstention.abstained is True
    assert abstention.passed is True
    assert hallucination.abstained is False
    assert hallucination.passed is False


def test_aggregate_applies_declared_thresholds_by_metric_denominator() -> None:
    positive = GoldCase(
        id="positive",
        split="dev",
        question="q",
        answerable=True,
        expected_sources=("policy.txt",),
        answer_claims=(("yes",),),
        evidence_claims=(("support",),),
    )
    negative = GoldCase(
        id="negative",
        split="dev",
        question="q2",
        answerable=False,
        expected_sources=(),
        answer_claims=(),
        evidence_claims=(),
    )
    scores = (
        score_case(
            positive,
            _answer(
                "yes",
                citations=(_citation(filename="policy.txt", quote="support"),),
            ),
        ),
        score_case(negative, _answer("invented", grounded=False)),
    )
    thresholds = QualityThresholds(
        retrieval_hit_rate=1.0,
        mean_reciprocal_rank=1.0,
        answer_claim_coverage=1.0,
        citation_claim_coverage=1.0,
        abstention_accuracy=1.0,
    )

    summary = aggregate_scores(scores, thresholds)

    assert summary.retrieval_hit_rate == 1.0
    assert summary.abstention_accuracy == 0.0
    assert summary.threshold_results["abstention_accuracy"] is False
    assert summary.passed is False


def _answer(
    text: str,
    *,
    citations: tuple[Citation, ...] = (),
    grounded: bool = True,
) -> AnswerResult:
    return AnswerResult(
        answer=text,
        citations=citations,
        cache_hit=False,
        cache_match=None,
        grounded=grounded,
        cache_written=False,
        cache_skip_reason=None,
    )


def _citation(*, filename: str, quote: str) -> Citation:
    return Citation(
        document_id="document-id",
        document_version=1,
        filename=filename,
        chunk_id=f"{filename}:0",
        chunk_index=0,
        page_number=None,
        start_offset=0,
        end_offset=len(quote),
        quote=quote,
        score=0.9,
    )
