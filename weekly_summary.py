"""Weekly digest script: intended to be run once per week by cron.

Reads summary.ini for the list of channels and their recipients, pulls
transcripts for videos published in the last N days from each channel, and
sends each recipient a single consolidated email with every video's title,
executive summary, and detailed bullets, in chronological order.

API credentials (YouTube, LLM, SMTP) are read from config.ini, the same file
used by the daily monitor (main.py).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from datetime import datetime, timedelta, timezone

from config import load_config
from config.models import Config, WeeklyConfig, WeeklyVideoEntry
from config.summary import load_weekly_config
from services.email import EmailService
from services.llm import SummarizerBackend, build_summarizer
from services.rate_limiter import RateLimiter
from services.run_report import RunReport
from services.youtube import (
    build_youtube_client,
    get_channel_name,
    get_transcript,
    get_video_details,
    get_videos_published_since,
)
from utils.helpers import format_duration_seconds, parse_iso8601_duration

logger = logging.getLogger(__name__)


async def _summarize_video(
    config: Config,
    summarizer: SummarizerBackend,
    llm_limiter: RateLimiter,
    channel_name: str,
    video,
    duration_str: str,
    report: RunReport,
) -> WeeklyVideoEntry | None:
    """Fetch a transcript and generate the two summaries for one video."""
    transcript = get_transcript(video.id)
    if not transcript:
        report.record_video_failure(
            video_id=video.id,
            title=video.title,
            error="No transcript available",
            attempt=1,
            max_attempts=1,
        )
        return None

    await llm_limiter.acquire()

    try:
        exec_summary, detailed_summary = await asyncio.gather(
            summarizer.generate_summary(
                transcript,
                config.prompt_exec_summary,
                max_output_tokens=config.llm_executive_max_output_tokens,
            ),
            summarizer.generate_summary(
                transcript,
                config.prompt_detailed_summary,
                max_output_tokens=config.llm_detailed_max_output_tokens,
            ),
        )
    finally:
        for issue in summarizer.drain_alert_events():
            report.add_service_issue(issue)

    if any(
        s.startswith("Error:") for s in (exec_summary, detailed_summary) if s
    ):
        report.record_video_failure(
            video_id=video.id,
            title=video.title,
            error="LLM generation failed",
            attempt=1,
            max_attempts=1,
        )
        return None

    return WeeklyVideoEntry(
        channel_name=channel_name,
        video=video,
        duration=duration_str,
        exec_summary=exec_summary,
        detailed_summary=detailed_summary,
    )


async def run_weekly_summary(
    config: Config,
    weekly_config: WeeklyConfig,
    report: RunReport,
    email_service: EmailService,
) -> None:
    """Summarize the past week's videos per channel and email one digest per recipient."""
    youtube = build_youtube_client(config.youtube_api_key)
    summarizer = build_summarizer(config)

    youtube_limiter = RateLimiter(rpm=config.youtube_rpm, rpd=config.youtube_rpd)
    llm_limiter = RateLimiter(rpm=config.gemini_rpm, rpd=config.gemini_rpd)

    try:
        await summarizer.validate_model_early()
    except Exception as e:
        report.add_service_issue(
            f"LLM provider validation encountered an issue: {e}"
        )
        logger.warning(
            "LLM provider validation encountered an issue: %s. Continuing.", e
        )
    finally:
        for issue in summarizer.drain_alert_events():
            report.add_service_issue(issue)

    since = datetime.now(timezone.utc) - timedelta(days=weekly_config.window_days)

    entries_by_email: dict[str, list[WeeklyVideoEntry]] = {}
    processed_count = 0

    for channel_id in weekly_config.channel_ids:
        recipients = (
            weekly_config.channel_recipients.get(channel_id)
            or weekly_config.default_recipients
        )
        await youtube_limiter.acquire()
        channel_name = get_channel_name(youtube, channel_id)

        if not recipients:
            logger.warning(
                "No recipients configured for channel %s (%s); skipping.",
                channel_name,
                channel_id,
            )
            continue

        logger.info(
            "--- Checking Channel: %s (%s) ---", channel_name, channel_id
        )
        videos = get_videos_published_since(
            youtube,
            channel_id,
            since,
            max_results=weekly_config.max_results_per_channel,
        )
        if not videos:
            logger.info(
                "No videos published in the last %d day(s) for %s.",
                weekly_config.window_days,
                channel_name,
            )
            continue

        for video in videos:
            await youtube_limiter.acquire()
            duration_iso = get_video_details(youtube, video.id)
            duration_str = format_duration_seconds(
                parse_iso8601_duration(duration_iso)
            )

            entry = await _summarize_video(
                config,
                summarizer,
                llm_limiter,
                channel_name,
                video,
                duration_str,
                report,
            )
            if entry is None:
                continue

            processed_count += 1
            for email in recipients:
                entries_by_email.setdefault(email, []).append(entry)

    report.processed_count = processed_count

    for email, entries in entries_by_email.items():
        entries.sort(key=lambda e: e.video.published_at)
        subject = f"{weekly_config.subject_prefix} ({len(entries)} video(s))"
        await email_service.send_weekly_digest([email], subject, entries)

    logger.info(
        "--- Weekly summary finished. Summarized %d video(s) across %d recipient(s). ---",
        processed_count,
        len(entries_by_email),
    )


async def main() -> int:
    """Load configuration, run the weekly digest, and send one problem report."""
    try:
        config = load_config()
        weekly_config = load_weekly_config()
    except ValueError as e:
        print(f"CRITICAL: Configuration error: {e}", file=sys.stderr)
        return 1

    from utils.logging import setup_logging

    setup_logging(
        log_file=weekly_config.log_file,
        log_level=weekly_config.log_level,
    )

    report = RunReport()
    email_service = EmailService(config)
    exit_code = 0

    try:
        await run_weekly_summary(config, weekly_config, report, email_service)
    except Exception:
        exit_code = 1
        report.fatal_error = traceback.format_exc()
        logger.critical("Weekly summary run failed.", exc_info=True)
    finally:
        if report.has_problems:
            alert_sent = await email_service.send_admin_alert(
                report.render_subject(),
                report.render_text(),
            )
            if (
                config.alerts_enabled
                and not config.dry_run
                and config.default_recipients
                and not alert_sent
            ):
                exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
