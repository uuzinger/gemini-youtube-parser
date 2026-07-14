from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AuthenticationError

from services.gemini import GeminiService
from services.llm import build_summarizer
from services.openai_compatible import OpenAICompatibleService


def _config(**overrides):
    values = {
        "llm_host": "llm.internal.example",
        "llm_port": 8080,
        "llm_use_tls": False,
        "llm_api_key": "local-example-key",
        "llm_model": "qwen3.6-a35b",
        "llm_temperature": 0.7,
        "llm_max_output_tokens": 2048,
        "llm_request_timeout": 300.0,
        "llm_context_tokens": 262144,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _chat_client(create: AsyncMock):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )
    )


@pytest.mark.asyncio
async def test_generates_summary_with_remote_server() -> None:
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Local summary")
                )
            ]
        )
    )
    service = OpenAICompatibleService(_config())
    service.client = _chat_client(create)

    result = await service.generate_summary(
        "Transcript text",
        "Summarize: {transcript}",
    )

    assert result == "Local summary"
    request = create.await_args.kwargs
    assert request["model"] == "qwen3.6-a35b"
    assert request["temperature"] == 0.7
    assert request["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_authentication_failure_is_not_retried() -> None:
    request = httpx.Request(
        "POST",
        "http://llm.internal.example:8080/v1/chat/completions",
    )
    response = httpx.Response(401, request=request)
    error = AuthenticationError(
        "Invalid API key",
        response=response,
        body=None,
    )
    create = AsyncMock(side_effect=error)
    service = OpenAICompatibleService(_config())
    service.client = _chat_client(create)

    result = await service.generate_summary(
        "Transcript text",
        "Summarize: {transcript}",
    )

    assert result.startswith("Error:")
    assert create.await_count == 1
    events = service.drain_alert_events()
    assert events == [
        "llama.cpp authentication failed (401). Check [LLM] api_key."
    ]
    assert "local-example-key" not in result


@pytest.mark.asyncio
async def test_likely_context_overflow_is_rejected_before_request() -> None:
    create = AsyncMock()
    service = OpenAICompatibleService(
        _config(llm_context_tokens=10, llm_max_output_tokens=5)
    )
    service.client = _chat_client(create)

    result = await service.generate_summary(
        "A transcript that is longer than the configured context.",
        "{transcript}",
    )

    assert result.startswith("Error: Prompt may exceed")
    create.assert_not_awaited()
    assert "context" in service.drain_alert_events()[0]


@pytest.mark.asyncio
async def test_model_label_mismatch_is_warning_only() -> None:
    service = OpenAICompatibleService(_config())
    service.client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(
                return_value=SimpleNamespace(
                    data=[SimpleNamespace(id="loaded-model")]
                )
            )
        )
    )

    result = await service.validate_model_early()

    assert result is None
    assert service.drain_alert_events() == []


def test_base_url_supports_tls_and_ipv6() -> None:
    service = OpenAICompatibleService(
        _config(llm_host="2001:db8::1", llm_port=8443, llm_use_tls=True)
    )

    assert service.base_url == "https://[2001:db8::1]:8443/v1"


def test_factory_builds_llama_cpp_backend() -> None:
    config = _config(llm_provider="llama_cpp")

    service = build_summarizer(config)

    assert isinstance(service, OpenAICompatibleService)


def test_factory_keeps_gemini_as_default_provider() -> None:
    config = SimpleNamespace(
        llm_provider="gemini",
        gemini_api_key="gemini-example-key",
        safety_settings=None,
    )

    service = build_summarizer(config)

    assert isinstance(service, GeminiService)
