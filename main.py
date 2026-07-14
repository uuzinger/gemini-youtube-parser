from __future__ import annotations

import asyncio
import logging
import sys
import time
import traceback

from config import load_config
from config.models import Config
from services.youtube import (
    build_youtube_client,
    get_channel_name,
    get_latest_videos,
    get_video_details,
    get_transcript,
)
from services.email import EmailService
from services.llm import SummarizerBackend, build_summarizer
from services.storage import StorageService
from services.rate_limiter import RateLimiter
from services.model_validator import (
    validate_model,
    read_model_suggestion,
)
from services.run_report import RunReport
from services.exceptions import ModelNotFoundError, YouTubeMonitorError
from utils.helpers import parse_iso8601_duration, format_duration_seconds

logger = logging.getLogger(__name__)


async def record_video_failure(
    storage_service: StorageService,
    report: RunReport,
    video_id: str,
    title: str,
    error: str,
) -> None:
    """Persist a video failure and add it to the run alert."""
    attempt = storage_service.mark_failed(video_id, error)
    report.record_video_failure(
        video_id=video_id,
        title=title,
        error=error,
        attempt=attempt,
        max_attempts=storage_service.max_retries,
    )
    await storage_service.save_failed_videos()


async def check_gemini_model(config: Config, report: RunReport) -> None:
    """Check Gemini's live model list when Gemini is the active provider."""
    try:
        model_result = await asyncio.to_thread(
            validate_model,
            config.gemini_api_key,
            config.gemini_model,
        )
    except Exception as e:
        issue = f"Could not check the current Gemini model list: {e}"
        report.add_model_issue(issue)
        logger.warning("%s", issue)
    else:
        if not model_result.configured_available:
            available = ", ".join(model_result.available_models[:20])
            issue = (
                f"Configured model '{config.gemini_model}' is unavailable. "
                f"Suggested model: "
                f"'{model_result.suggested_model or 'none'}'. "
                f"Available text models: {available or 'none returned'}."
            )
            report.add_model_issue(issue)
            logger.warning("%s", issue)

    suggestion = read_model_suggestion()
    if suggestion:
        logger.warning(
            "Gemini model suggestion: %s",
            suggestion.get("message", "No details available."),
        )
        report.add_model_issue(
            suggestion.get("message", "Gemini model update required.")
        )


async def process_video(
    config: Config,
    youtube,
    summarizer: SummarizerBackend,
    email_service: EmailService,
    storage_service: StorageService,
    youtube_limiter: RateLimiter,
    llm_limiter: RateLimiter,
    channel_name: str,
    video,
    report: RunReport,
    is_retry: bool = False,
) -> None:
    """Process a single video: fetch details, transcript, generate summaries, send email."""
    video_id = video.id
    prefix = "Retrying" if is_retry else "Processing"
    logger.info(
        "%s video: '%s' (ID: %s)", prefix, video.title, video_id
    )

    # Rate limit YouTube API
    await youtube_limiter.acquire()

    # Get video duration
    duration_iso = get_video_details(youtube, video_id)
    duration_s = parse_iso8601_duration(duration_iso)

    if config.min_video_duration_minutes > 0 and duration_s < (
        config.min_video_duration_minutes * 60
    ):
        logger.info("Skipping short video: %s (%ds)", video_id, duration_s)
        storage_service.mark_processed(video_id)
        await storage_service.save_processed_videos()
        await storage_service.save_failed_videos()
        return

    # Rate limit the configured LLM provider.
    await llm_limiter.acquire()

    # Fetch transcript
    transcript = get_transcript(video_id)
    if not transcript:
        await record_video_failure(
            storage_service,
            report,
            video_id,
            video.title,
            "No transcript available",
        )
        return

    # Generate all three summaries in parallel
    try:
        exec_summary, detailed_summary, key_quotes = (
            await asyncio.gather(
                summarizer.generate_summary(
                    transcript,
                    config.prompt_exec_summary,
                    max_output_tokens=(
                        config.llm_executive_max_output_tokens
                    ),
                ),
                summarizer.generate_summary(
                    transcript,
                    config.prompt_detailed_summary,
                    max_output_tokens=(
                        config.llm_detailed_max_output_tokens
                    ),
                ),
                summarizer.generate_summary(
                    transcript,
                    config.prompt_key_quotes,
                    max_output_tokens=config.llm_quotes_max_output_tokens,
                ),
            )
        )
    except ModelNotFoundError as e:
        error_msg = f"Model not found: {e}"
        report.add_model_issue(error_msg)
        await record_video_failure(
            storage_service,
            report,
            video_id,
            video.title,
            error_msg,
        )
        return
    finally:
        for issue in summarizer.drain_alert_events():
            report.add_service_issue(issue)

    # Check for errors in summaries
    is_error = any(
        s.startswith("Error:") for s in [exec_summary, detailed_summary, key_quotes] if s
    )

    if is_error:
        error_msg = "LLM generation failed"
        await record_video_failure(
            storage_service,
            report,
            video_id,
            video.title,
            error_msg,
        )
        return

    duration_str = format_duration_seconds(duration_s)

    # Save summary locally
    await storage_service.save_summary(
        video_id,
        video.title,
        duration_str,
        exec_summary,
        detailed_summary,
        key_quotes,
    )

    # Send email notification
    try:
        await email_service.send_notification(
            channel_name,
            video,
            duration_str,
            exec_summary,
            detailed_summary,
            key_quotes,
        )
    except Exception as e:
        error_msg = f"Email send failed: {e}"
        await record_video_failure(
            storage_service,
            report,
            video_id,
            video.title,
            error_msg,
        )
        return

    # All steps succeeded - mark as processed
    storage_service.mark_processed(video_id)
    await storage_service.save_processed_videos()
    await storage_service.save_failed_videos()

    # Small delay between videos to be respectful of APIs
    await asyncio.sleep(5)


async def run_monitor(
    config: Config,
    report: RunReport,
    email_service: EmailService,
) -> None:
    """Run one monitoring cycle and collect alert-worthy problems."""
    start_time = time.time()
    processed_count = 0
    retry_count = 0

    if config.llm_provider == "gemini":
        await check_gemini_model(config, report)

    # Build YouTube client
    try:
        youtube = build_youtube_client(config.youtube_api_key)
    except YouTubeMonitorError as e:
        logger.critical("Failed to initialize YouTube API: %s", e)
        raise

    # Initialize services
    summarizer = build_summarizer(config)
    storage_service = StorageService(config)

    # Rate limiters
    youtube_limiter = RateLimiter(
        rpm=config.youtube_rpm, rpd=config.youtube_rpd
    )
    llm_limiter = RateLimiter(
        rpm=config.gemini_rpm, rpd=config.gemini_rpd
    )

    # Load processed videos and failed videos
    await storage_service.load_processed_videos()
    await storage_service.load_failed_videos()

    # Validate the configured LLM provider before processing videos.
    try:
        await summarizer.validate_model_early()
    except ModelNotFoundError as e:
        report.add_model_issue(str(e))
        logger.warning(
            "Gemini model validation failed: %s. "
            "Processing will continue but may fail.",
            e,
        )
    except Exception as e:
        report.add_service_issue(
            f"LLM provider validation encountered an issue: {e}"
        )
        logger.warning(
            "LLM provider validation encountered an issue: %s. "
            "Processing will continue.",
            e,
        )
    finally:
        for issue in summarizer.drain_alert_events():
            report.add_service_issue(issue)

    # Phase 1: Retry failed videos first
    failed_videos = storage_service.get_failed_videos()
    if failed_videos:
        logger.info(
            "--- Retrying %d failed videos ---", len(failed_videos)
        )
        for video_id in failed_videos:
            logger.info("Retrying failed video: %s", video_id)
            # Fetch actual video details
            try:
                video_response = (
                    youtube.videos()
                    .list(part="snippet,contentDetails", id=video_id)
                    .execute()
                )
                if not video_response.get("items"):
                    storage_service.mark_processed(video_id)
                    await storage_service.save_processed_videos()
                    await storage_service.save_failed_videos()
                    continue

                snippet = video_response["items"][0]["snippet"]
                content_details = video_response["items"][0]["contentDetails"]
                title = snippet["title"]
                channel_id = snippet.get("channelId", "")
                channel_name = snippet.get("channelTitle", "Unknown Channel")
                duration_iso = content_details.get("duration", "")
            except Exception as e:
                error_msg = (
                    f"Failed to fetch video details for retry: {e}"
                )
                logger.error(
                    "%s (%s)",
                    error_msg,
                    video_id,
                )
                await record_video_failure(
                    storage_service,
                    report,
                    video_id,
                    "Unknown title",
                    error_msg,
                )
                continue

            if not duration_iso:
                storage_service.mark_processed(video_id)
                await storage_service.save_processed_videos()
                await storage_service.save_failed_videos()
                continue

            retry_video = type('Video', (), {
                'id': video_id,
                'title': title,
                'channel_id': channel_id,
            })()

            await process_video(
                config,
                youtube,
                summarizer,
                email_service,
                storage_service,
                youtube_limiter,
                llm_limiter,
                channel_name,
                retry_video,
                report,
                is_retry=True,
            )
            retry_count += 1

    # Phase 2: Process new videos
    all_channel_names = {
        cid: get_channel_name(youtube, cid) for cid in config.channel_ids
    }

    for channel_id, channel_name in all_channel_names.items():
        logger.info(
            "--- Checking Channel: %s (%s) ---",
            channel_name,
            channel_id,
        )
        latest_videos = get_latest_videos(
            youtube,
            channel_id,
            config.max_results_per_channel + 5,
        )

        for video in latest_videos:
            if storage_service.is_processed(video.id):
                continue
            if storage_service.is_failed(video.id):
                continue

            await process_video(
                config,
                youtube,
                summarizer,
                email_service,
                storage_service,
                youtube_limiter,
                llm_limiter,
                channel_name,
                video,
                report,
                is_retry=False,
            )
            processed_count += 1

    elapsed = time.time() - start_time
    report.processed_count = processed_count
    report.retried_count = retry_count
    logger.info(
        "--- Script Finished. Processed %d new videos, retried %d failed videos in %.2f seconds. ---",
        processed_count,
        retry_count,
        elapsed,
    )


async def main() -> int:
    """Load configuration, run the monitor, and send one problem report."""
    try:
        config = load_config()
    except ValueError as e:
        print(f"CRITICAL: Configuration error: {e}", file=sys.stderr)
        return 1

    from utils.logging import setup_logging

    setup_logging(
        log_file=config.log_file,
        log_level=config.log_level,
    )

    report = RunReport()
    email_service = EmailService(config)
    exit_code = 0

    try:
        await run_monitor(config, report, email_service)
    except Exception:
        exit_code = 1
        report.fatal_error = traceback.format_exc()
        logger.critical("Monitor run failed.", exc_info=True)
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
