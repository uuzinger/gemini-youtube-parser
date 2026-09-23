from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.article_summary import generate_article_summary
from services.article_summary_cache import ArticleSummaryCache


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        prompt_executive_summary="Exec prompt {transcript}",
        prompt_bullet_points="Bullets prompt {transcript}",
        prompt_notable_quotes="Quotes prompt {transcript}",
        llm_summary_max_output_tokens=512,
    )


class _Limiter:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self) -> None:
        self.calls += 1


@pytest.mark.asyncio
async def test_generate_article_summary_runs_three_queries() -> None:
    summarizer = SimpleNamespace(
        generate_summary=AsyncMock(
            side_effect=["Executive text", "- Bullet one", "- Quote one"]
        )
    )
    limiter = _Limiter()

    summary = await generate_article_summary(
        _config(), summarizer, "Transcript text", llm_limiter=limiter
    )

    assert summary == (
        "## Executive Summary\n\nExecutive text\n\n"
        "## Key Points\n\n- Bullet one\n\n"
        "## Notable Quotes\n\n- Quote one"
    )
    assert limiter.calls == 3
    assert [
        call.args[1] for call in summarizer.generate_summary.await_args_list
    ] == [
        "Exec prompt {transcript}",
        "Bullets prompt {transcript}",
        "Quotes prompt {transcript}",
    ]
    assert all(
        call.kwargs["max_output_tokens"] == 512
        for call in summarizer.generate_summary.await_args_list
    )


@pytest.mark.asyncio
async def test_generate_article_summary_stops_on_error() -> None:
    summarizer = SimpleNamespace(
        generate_summary=AsyncMock(
            side_effect=["Executive text", "Error: failed", "- Quote one"]
        )
    )
    limiter = _Limiter()

    summary = await generate_article_summary(
        _config(), summarizer, "Transcript text", llm_limiter=limiter
    )

    assert summary == "Error: failed"
    assert limiter.calls == 2
    assert summarizer.generate_summary.await_count == 2


@pytest.mark.asyncio
async def test_generate_article_summary_retries_only_missing_sections(
    tmp_path,
) -> None:
    cache = ArticleSummaryCache(str(tmp_path / "article_summaries"))
    config = _config()

    first_summarizer = SimpleNamespace(
        generate_summary=AsyncMock(side_effect=["Executive text", "Error: failed"])
    )
    first_limiter = _Limiter()

    first_summary = await generate_article_summary(
        config,
        first_summarizer,
        "Transcript text",
        video_id="dQw4w9WgXcQ",
        llm_limiter=first_limiter,
        summary_cache=cache,
    )

    assert first_summary == "Error: failed"
    assert first_limiter.calls == 2

    second_summarizer = SimpleNamespace(
        generate_summary=AsyncMock(side_effect=["- Bullet one", "- Quote one"])
    )
    second_limiter = _Limiter()

    second_summary = await generate_article_summary(
        config,
        second_summarizer,
        "Transcript text",
        video_id="dQw4w9WgXcQ",
        llm_limiter=second_limiter,
        summary_cache=cache,
    )

    assert second_summary == (
        "## Executive Summary\n\nExecutive text\n\n"
        "## Key Points\n\n- Bullet one\n\n"
        "## Notable Quotes\n\n- Quote one"
    )
    assert second_limiter.calls == 2
    assert [
        call.args[1] for call in second_summarizer.generate_summary.await_args_list
    ] == [
        "Bullets prompt {transcript}",
        "Quotes prompt {transcript}",
    ]
