"""Required cache path: exact/similar reuse and dependency invalidation."""

from __future__ import annotations

from tests.helpers import FakeLlmGateway, build_test_runtime


async def test_document_update_invalidates_related_cache_only(test_data_dir) -> None:
    llm = FakeLlmGateway(("grounded ", "answer"))
    runtime = await build_test_runtime(test_data_dir, llm=llm)
    first = await runtime.services.ingestion.ingest(
        filename="benefits.txt",
        content_type="text/plain",
        content=b"Education allowance is 100 dollars.",
    )
    await runtime.services.ingestion.ingest(
        filename="deployment.txt",
        content_type="text/plain",
        content=b"Deployment requires two reviewers.",
    )

    first_answer = await runtime.services.questions.answer(
        "Explain the education allowance"
    )
    second_answer = await runtime.services.questions.answer(
        "Explain the deployment rule"
    )
    similar_answer = await runtime.services.questions.answer(
        "Education details, please"
    )
    calls_before_update = len(llm.prompts)

    updated = await runtime.services.ingestion.ingest(
        filename="benefits.txt",
        content_type="text/plain",
        content=b"Education allowance is now 200 dollars.",
    )
    unrelated_after_update = await runtime.services.questions.answer(
        "Explain the deployment rule"
    )
    related_after_update = await runtime.services.questions.answer(
        "Explain the education allowance"
    )

    assert first.change.value == "created"
    assert first_answer.cache_hit is False
    assert second_answer.cache_hit is False
    assert similar_answer.cache_hit is True
    assert similar_answer.cache_match == "similar"
    assert calls_before_update == 2
    assert updated.change.value == "updated"
    assert updated.document.version == 2
    assert updated.invalidated_cache_entries == 1
    assert unrelated_after_update.cache_hit is True
    assert related_after_update.cache_hit is False
    assert len(llm.prompts) == calls_before_update + 1
