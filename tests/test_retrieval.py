"""Required retrieval path and unsupported-claim behavior."""

from __future__ import annotations

from officeagent.application.question_answering import NO_EVIDENCE_ANSWER
from tests.helpers import FakeLlmGateway, build_test_runtime


async def test_retrieval_returns_expected_source_and_abstains_without_evidence(
    test_data_dir,
) -> None:
    llm = FakeLlmGateway(("연간 ", "200만 원입니다."))
    runtime = await build_test_runtime(test_data_dir, llm=llm)
    await runtime.services.ingestion.ingest(
        filename="benefits.txt",
        content_type="text/plain",
        content=b"Education benefit limit is 2,000,000 KRW per year.",
    )
    await runtime.services.ingestion.ingest(
        filename="deployment.txt",
        content_type="text/plain",
        content=b"Deployment requires two reviewers.",
    )

    answer = await runtime.services.questions.answer(
        "What is the education benefit limit?"
    )
    prompt_count = len(llm.prompts)
    unsupported = await runtime.services.questions.answer(
        "What is the cafeteria menu?"
    )

    assert answer.answer == "연간 200만 원입니다."
    assert answer.grounded is True
    assert answer.cache_hit is False
    assert answer.citations[0].filename == "benefits.txt"
    assert "2,000,000 KRW" in answer.citations[0].quote
    assert "2,000,000 KRW" in llm.prompts[0]
    assert unsupported.answer == NO_EVIDENCE_ANSWER
    assert unsupported.grounded is False
    assert unsupported.citations == ()
    assert len(llm.prompts) == prompt_count
