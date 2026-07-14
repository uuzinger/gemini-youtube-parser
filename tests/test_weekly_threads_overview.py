from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config.models import Video, WeeklyVideoEntry
from services.rate_limiter import RateLimiter
from services.run_report import RunReport
from weekly_summary import _build_combined_transcript, _build_threads_overview


def _config(**overrides):
    values = {
        "prompt_weekly_threads": "Synthesize threads.\n{transcript}",
        "llm_weekly_threads_max_output_tokens": 4096,
        "llm_context_tokens": 262144,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _entry(video_id: str, title: str, transcript: str) -> WeeklyVideoEntry:
    return WeeklyVideoEntry(
        channel_name="Example Channel",
        video=Video(id=video_id, title=title, channel_id="UCxxxx"),
        duration="10:00",
        exec_summary="Exec summary",
        detailed_summary="Detailed summary",
        transcript=transcript,
    )


def test_build_combined_transcript_includes_all_videos() -> None:
    entries = [
        _entry("v1", "First Video", "Transcript one."),
        _entry("v2", "Second Video", "Transcript two."),
    ]

    combined = _build_combined_transcript(entries, _config(), RunReport())

    assert "First Video" in combined
    assert "Transcript one." in combined
    assert "Second Video" in combined
    assert "Transcript two." in combined


def test_build_combined_transcript_skips_entries_without_transcript() -> None:
    entries = [_entry("v1", "First Video", "")]

    combined = _build_combined_transcript(entries, _config(), RunReport())

    assert combined == ""


def test_build_combined_transcript_truncates_when_over_budget() -> None:
    entries = [
        _entry("v1", "First Video", "A" * 1000),
        _entry("v2", "Second Video", "B" * 1000),
    ]
    # Tiny context window forces truncation.
    config = _config(llm_context_tokens=50, llm_weekly_threads_max_output_tokens=1)
    report = RunReport()

    combined = _build_combined_transcript(entries, config, report)

    assert len(combined) < 2000
    assert report.service_issues
    assert "truncated" in report.service_issues[0]


@pytest.mark.asyncio
async def test_build_threads_overview_returns_generated_text() -> None:
    entries = [_entry("v1", "First Video", "Transcript one.")]
    summarizer = SimpleNamespace(
        generate_summary=AsyncMock(return_value="### Topics\n- AI"),
        drain_alert_events=lambda: [],
    )

    overview = await _build_threads_overview(
        _config(), summarizer, RateLimiter(), entries, RunReport()
    )

    assert overview == "### Topics\n- AI"
    summarizer.generate_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_threads_overview_returns_none_on_llm_error() -> None:
    entries = [_entry("v1", "First Video", "Transcript one.")]
    summarizer = SimpleNamespace(
        generate_summary=AsyncMock(return_value="Error: something broke"),
        drain_alert_events=lambda: [],
    )
    report = RunReport()

    overview = await _build_threads_overview(
        _config(), summarizer, RateLimiter(), entries, report
    )

    assert overview is None
    assert report.service_issues


@pytest.mark.asyncio
async def test_build_threads_overview_returns_none_without_entries() -> None:
    summarizer = SimpleNamespace(
        generate_summary=AsyncMock(return_value="should not be called"),
        drain_alert_events=lambda: [],
    )

    overview = await _build_threads_overview(
        _config(), summarizer, RateLimiter(), [], RunReport()
    )

    assert overview is None
    summarizer.generate_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_threads_overview_returns_none_without_prompt() -> None:
    entries = [_entry("v1", "First Video", "Transcript one.")]
    summarizer = SimpleNamespace(
        generate_summary=AsyncMock(return_value="should not be called"),
        drain_alert_events=lambda: [],
    )

    overview = await _build_threads_overview(
        _config(prompt_weekly_threads=""), summarizer, RateLimiter(), entries, RunReport()
    )

    assert overview is None
    summarizer.generate_summary.assert_not_awaited()
