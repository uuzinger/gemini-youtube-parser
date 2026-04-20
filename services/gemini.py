from __future__ import annotations

import logging
import re
import time

from google import genai
from google.genai import types
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    WaitABC,
    wait_fixed,
)

from config.models import Config
from .exceptions import ModelNotFoundError

logger = logging.getLogger(__name__)


class _DynamicWait(WaitABC):
    """Wait strategy that respects Gemini's retryDelay from error responses."""

    def __init__(self, fallback_wait: WaitABC):
        self.fallback_wait = fallback_wait

    def __call__(self, retry_state):
        error = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(error, genai.errors.APIError):
            delay = _parse_retry_delay(error)
            if delay:
                logger.debug(
                    "Gemini requested wait of %.1fs (retry %d/%d)",
                    delay,
                    retry_state.attempt_number,
                    retry_state.statistics.attempt_number + 2,
                )
                return wait_fixed(delay)
        return self.fallback_wait(retry_state)


def _parse_retry_delay(error: genai.errors.APIError) -> float | None:
    """Extract retry delay from Gemini error response."""
    if not hasattr(error, 'message') or not error.message:
        return None
    match = re.search(r'retry in ([\d.]+)s', error.message)
    if match:
        return float(match.group(1)) + 1
    return None


class GeminiService:
    """Async Gemini API service with retry and circuit breaker."""

    def __init__(self, config: Config):
        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)
        self._consecutive_failures = 0
        self._circuit_breaker_threshold = 5
        self._safety_settings = (
            [
                types.SafetySetting(
                    category=s["category"],
                    threshold=s["threshold"],
                )
                for s in config.safety_settings
            ]
            if config.safety_settings
            else None
        )

    async def generate_summary(
        self, transcript: str, prompt: str
    ) -> str:
        """Generate a summary using Gemini with retry logic."""
        if self._consecutive_failures >= self._circuit_breaker_threshold:
            logger.error(
                "Circuit breaker open (%d consecutive failures). Skipping Gemini call.",
                self._circuit_breaker_threshold,
            )
            return "Error: Gemini service is temporarily unavailable (circuit breaker open)."

        if not transcript or not prompt:
            return "Error: Missing transcript or prompt."

        full_prompt = prompt.format(transcript=transcript)

        try:
            response = await self._generate_with_retry(full_prompt)
            self._consecutive_failures = 0
            return response.strip()
        except ModelNotFoundError:
            raise
        except Exception as e:
            self._consecutive_failures += 1
            logger.error(
                "Gemini generation failed (failure %d/%d): %s",
                self._consecutive_failures,
                self._circuit_breaker_threshold,
                e,
            )
            if self._consecutive_failures >= self._circuit_breaker_threshold:
                logger.critical(
                    "Circuit breaker tripped after %d consecutive failures.",
                    self._circuit_breaker_threshold,
                )
            return f"Error: Failed to generate summary - {e}"

    @retry(
        stop=stop_after_attempt(3),
        wait=_DynamicWait(wait_exponential(multiplier=1, min=5, max=60)),
        retry=retry_if_exception_type((genai.errors.APIError,)),
        reraise=True,
    )
    async def _generate_with_retry(self, prompt: str) -> str:
        """Internal method with retry logic for Gemini API calls."""
        try:
            response = await self.client.aio.models.generate_content(
                model=self.config.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    safety_settings=self._safety_settings,
                ),
            )
            return response.text
        except genai.errors.APIError as e:
            if e.code in (404, 400):
                raise ModelNotFoundError(
                    self.config.gemini_model
                ) from e
            raise

    async def validate_model_early(self) -> str | None:
        """Validate model is available before processing videos."""
        try:
            result = await self.client.aio.models.generate_content(
                model=self.config.gemini_model,
                contents="Say 'ok' in one word.",
                config=types.GenerateContentConfig(
                    temperature=0,
                    max_output_tokens=5,
                    safety_settings=self._safety_settings,
                ),
            )
            logger.info(
                "Model '%s' validated successfully.",
                self.config.gemini_model,
            )
            return None
        except ModelNotFoundError:
            raise
        except genai.errors.APIError as e:
            if e.code in (404, 400):
                raise ModelNotFoundError(
                    self.config.gemini_model
                ) from e
            logger.warning(
                "Model validation failed with %s: %s. Model may still work for longer prompts.",
                e.code,
                e.message,
            )
            return None
        except Exception as e:
            logger.warning("Model validation failed: %s. Continuing anyway.", e)
            return None
