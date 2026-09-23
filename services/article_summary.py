from __future__ import annotations

import logging

from config.models import Config

from .article_summary_cache import ArticleSummaryCache
from .llm import SummarizerBackend
from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


async def generate_article_summary(
    config: Config,
    summarizer: SummarizerBackend,
    transcript: str,
    *,
    video_id: str | None = None,
    llm_limiter: RateLimiter | None = None,
    summary_cache: ArticleSummaryCache | None = None,
) -> str:
    """Generate the article summary with three separate LLM queries."""
    sections = [
        ("executive_summary", "Executive Summary", config.prompt_executive_summary),
        ("key_points", "Key Points", config.prompt_bullet_points),
        ("notable_quotes", "Notable Quotes", config.prompt_notable_quotes),
    ]
    rendered_sections: list[str] = []

    for section_key, heading, prompt in sections:
        content = None
        if summary_cache is not None and video_id is not None:
            try:
                content = summary_cache.get(video_id, section_key, prompt, transcript)
            except (OSError, ValueError) as e:
                logger.warning(
                    "Could not read %s summary cache for %s: %s",
                    section_key,
                    video_id,
                    e,
                )
            else:
                if content is not None:
                    logger.info(
                        "Article summary cache hit for %s section of %s",
                        section_key,
                        video_id,
                    )

        if content is not None:
            rendered_sections.append(f"## {heading}\n\n{content}")
            continue

        if llm_limiter is not None:
            await llm_limiter.acquire()

        content = await summarizer.generate_summary(
            transcript,
            prompt,
            max_output_tokens=config.llm_summary_max_output_tokens,
        )
        if content.startswith("Error:"):
            return content

        if summary_cache is not None and video_id is not None:
            try:
                summary_cache.put(video_id, section_key, prompt, transcript, content)
            except (OSError, ValueError) as e:
                logger.warning(
                    "Could not write %s summary cache for %s: %s",
                    section_key,
                    video_id,
                    e,
                )

        rendered_sections.append(f"## {heading}\n\n{content}")

    return "\n\n".join(rendered_sections)
