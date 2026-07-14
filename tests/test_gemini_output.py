from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai import types

from services.gemini import GeminiService


@pytest.mark.asyncio
async def test_max_tokens_finish_reason_is_reported_as_truncation() -> None:
    config = SimpleNamespace(
        gemini_api_key="gemini-example-key",
        gemini_model="gemini-example-model",
        safety_settings=None,
    )
    generate = AsyncMock(
        return_value=SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    finish_reason=types.FinishReason.MAX_TOKENS,
                )
            ],
            text="Partial summary",
        )
    )
    service = GeminiService(config)
    service.client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=generate),
        )
    )

    result = await service.generate_summary(
        "Transcript",
        "Summarize: {transcript}",
        max_output_tokens=8192,
    )

    assert result.startswith("Error:")
    assert "truncated" in result
    assert service.drain_alert_events() == [
        "Gemini output was truncated after reaching its 8192 token limit."
    ]
