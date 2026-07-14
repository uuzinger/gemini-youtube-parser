from __future__ import annotations

import logging

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt
from tenacity import wait_exponential

from config.models import Config

logger = logging.getLogger(__name__)

_RETRYABLE_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)


class OpenAICompatibleService:
    """Summarization service for a remote OpenAI-compatible llama.cpp server."""

    def __init__(self, config: Config):
        self.config = config
        self._consecutive_failures = 0
        self._circuit_breaker_threshold = 5
        self._alert_events: list[str] = []
        self.client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=config.llm_api_key,
            timeout=config.llm_request_timeout,
            max_retries=0,
        )

    @property
    def base_url(self) -> str:
        scheme = "https" if self.config.llm_use_tls else "http"
        host = self.config.llm_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{scheme}://{host}:{self.config.llm_port}/v1"

    def _record_alert_event(self, issue: str) -> None:
        if issue and issue not in self._alert_events:
            self._alert_events.append(issue)

    def drain_alert_events(self) -> list[str]:
        events = self._alert_events.copy()
        self._alert_events.clear()
        return events

    @staticmethod
    def _safe_error(error: Exception) -> str:
        return " ".join(str(error).split())[:500]

    def _record_failure(self, error: Exception) -> None:
        if isinstance(error, AuthenticationError):
            issue = (
                "llama.cpp authentication failed (401). "
                "Check [LLM] api_key."
            )
        elif isinstance(error, (APIConnectionError, APITimeoutError)):
            issue = f"llama.cpp server is unreachable at {self.base_url}."
        elif isinstance(error, BadRequestError):
            issue = (
                "llama.cpp rejected the request (400): "
                f"{self._safe_error(error)}"
            )
        elif isinstance(error, APIStatusError):
            issue = (
                f"llama.cpp API error ({error.status_code}): "
                f"{self._safe_error(error)}"
            )
        else:
            issue = (
                f"llama.cpp generation failed: "
                f"{type(error).__name__}: {self._safe_error(error)}"
            )
        self._record_alert_event(issue)

    async def generate_summary(
        self,
        transcript: str,
        prompt: str,
    ) -> str:
        if self._consecutive_failures >= self._circuit_breaker_threshold:
            issue = (
                "llama.cpp circuit breaker is open after "
                f"{self._circuit_breaker_threshold} consecutive failures."
            )
            self._record_alert_event(issue)
            logger.error("%s", issue)
            return "Error: Local LLM service is temporarily unavailable."

        if not transcript or not prompt:
            return "Error: Missing transcript or prompt."

        full_prompt = prompt.format(transcript=transcript)
        estimated_input_tokens = max(1, (len(full_prompt) + 3) // 4)
        estimated_total_tokens = (
            estimated_input_tokens + self.config.llm_max_output_tokens
        )
        if estimated_total_tokens > self.config.llm_context_tokens:
            issue = (
                "Prompt may exceed the configured llama.cpp context: "
                f"approximately {estimated_total_tokens} tokens requested, "
                f"{self.config.llm_context_tokens} configured."
            )
            self._record_alert_event(issue)
            logger.error("%s", issue)
            return f"Error: {issue}"

        try:
            response = await self._generate_with_retry(full_prompt)
            self._consecutive_failures = 0
            return response
        except Exception as e:
            self._consecutive_failures += 1
            self._record_failure(e)
            logger.error(
                "llama.cpp generation failed (failure %d/%d): %s",
                self._consecutive_failures,
                self._circuit_breaker_threshold,
                e,
            )
            if self._consecutive_failures >= self._circuit_breaker_threshold:
                self._record_alert_event(
                    "llama.cpp circuit breaker tripped after "
                    f"{self._circuit_breaker_threshold} consecutive failures."
                )
            return f"Error: Failed to generate summary - {self._safe_error(e)}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE_ERRORS),
        reraise=True,
    )
    async def _generate_with_retry(self, prompt: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.config.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.config.llm_temperature,
            max_tokens=self.config.llm_max_output_tokens,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("llama.cpp returned an empty response")
        return content.strip()

    async def validate_model_early(self) -> str | None:
        try:
            models = await self.client.models.list()
        except Exception as e:
            self._record_failure(e)
            logger.warning(
                "llama.cpp validation failed at %s: %s",
                self.base_url,
                e,
            )
            raise

        model_ids = [model.id for model in models.data]
        if self.config.llm_model not in model_ids:
            logger.warning(
                "Configured llama.cpp model label '%s' was not returned by "
                "/v1/models (available: %s). Continuing because llama.cpp "
                "serves its loaded model regardless of the request label.",
                self.config.llm_model,
                ", ".join(model_ids) or "none",
            )
        else:
            logger.info(
                "llama.cpp model '%s' validated at %s.",
                self.config.llm_model,
                self.base_url,
            )
        return None
