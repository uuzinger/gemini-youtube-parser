from __future__ import annotations

from typing import Protocol

from config.models import Config


class SummarizerBackend(Protocol):
    async def generate_summary(self, transcript: str, prompt: str) -> str:
        """Generate one summary from a transcript and prompt template."""

    async def validate_model_early(self) -> str | None:
        """Validate provider connectivity before processing videos."""

    def drain_alert_events(self) -> list[str]:
        """Return and clear provider issues collected for run reporting."""


def build_summarizer(config: Config) -> SummarizerBackend:
    """Build the configured summarization provider."""
    if config.llm_provider == "gemini":
        from .gemini import GeminiService

        return GeminiService(config)

    if config.llm_provider in {"llama_cpp", "openai_compatible"}:
        from .openai_compatible import OpenAICompatibleService

        return OpenAICompatibleService(config)

    raise ValueError(f"Unsupported LLM provider: {config.llm_provider}")
