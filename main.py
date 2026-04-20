from __future__ import annotations

import asyncio
import logging
import time

from config import load_config
from config.models import Config
from services.youtube import (
    build_youtube_client,
    get_channel_name,
    get_latest_videos,
    get_video_details,
    get_transcript,
)
from services.gemini import GeminiService
from services.email import EmailService
from services.storage import StorageService
from services.rate_limiter import RateLimiter
from services.model_validator import (
    read_model_suggestion,
    clear_model_suggestion,
)
from services.exceptions import ModelNotFoundError, YouTubeMonitorError
from utils.helpers import parse_iso8601_duration, format_duration_seconds

logger = logging.getLogger(__name__)


async def process_video(
    config: Config,
    youtube,
    gemini_service: GeminiService,
    email_service: EmailService,
    storage_service: StorageService,
    youtube_limiter: RateLimiter,
    gemini_limiter: RateLimiter,
    channel_name: str,
    video,
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

    # Rate limit Gemini API
    await gemini_limiter.acquire()

    # Fetch transcript
    transcript = get_transcript(video_id)
    if not transcript:
        error_msg = "No transcript available"
        storage_service.mark_failed(video_id, error_msg)
        await storage_service.save_failed_videos()
        return

    # Generate all three summaries in parallel
    try:
        exec_summary, detailed_summary, key_quotes = (
            await asyncio.gather(
                gemini_service.generate_summary(
                    transcript, config.prompt_exec_summary
                ),
                gemini_service.generate_summary(
                    transcript, config.prompt_detailed_summary
                ),
                gemini_service.generate_summary(
                    transcript, config.prompt_key_quotes
                ),
            )
        )
    except ModelNotFoundError as e:
        error_msg = f"Model not found: {e}"
        storage_service.mark_failed(video_id, error_msg)
        await storage_service.save_failed_videos()
        return

    # Check for errors in summaries
    is_error = any(
        s.startswith("Error:") for s in [exec_summary, detailed_summary, key_quotes] if s
    )

    if is_error:
        error_msg = "Gemini generation failed"
        storage_service.mark_failed(video_id, error_msg)
        await storage_service.save_failed_videos()
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
        storage_service.mark_failed(video_id, error_msg)
        await storage_service.save_failed_videos()
        return

    # All steps succeeded - mark as processed
    storage_service.mark_processed(video_id)
    await storage_service.save_processed_videos()
    await storage_service.save_failed_videos()

    # Small delay between videos to be respectful of APIs
    await asyncio.sleep(5)


async def main() -> None:
    """Main async entry point."""
    start_time = time.time()
    processed_count = 0
    retry_count = 0

    # Load configuration
    try:
        config = load_config()
    except ValueError as e:
        print(f"CRITICAL: Configuration error: {e}")
        return

    # Setup logging early so all messages are captured
    from utils.logging import setup_logging
    setup_logging(
        log_file=config.log_file,
        log_level=config.log_level,
    )

    # Check for model suggestion file
    suggestion = read_model_suggestion()
    if suggestion:
        logger.warning(
            "============================================================"
        )
        logger.warning("MODEL SUGGESTION")
        logger.warning(
            "Your configured model '%s' is no longer available.",
            suggestion.get("old_model", "unknown"),
        )
        logger.warning(
            "Suggested model: '%s'",
            suggestion.get("suggested_model", "unknown"),
        )
        logger.warning("%s", suggestion.get("message", ""))
        logger.warning(
            "============================================================"
        )

    # Build YouTube client
    try:
        youtube = build_youtube_client(config.youtube_api_key)
    except YouTubeMonitorError as e:
        logger.critical("Failed to initialize YouTube API: %s", e)
        return

    # Initialize services
    gemini_service = GeminiService(config)
    email_service = EmailService(config)
    storage_service = StorageService(config)

    # Rate limiters
    youtube_limiter = RateLimiter(
        rpm=config.youtube_rpm, rpd=config.youtube_rpd
    )
    gemini_limiter = RateLimiter(
        rpm=config.gemini_rpm, rpd=config.gemini_rpd
    )

    # Load processed videos and failed videos
    await storage_service.load_processed_videos()
    await storage_service.load_failed_videos()

    # Validate Gemini model early
    try:
        await gemini_service.validate_model_early()
    except ModelNotFoundError as e:
        logger.warning(
            "Gemini model validation failed: %s. "
            "Processing will continue but may fail.",
            e,
        )
    except Exception as e:
        logger.warning(
            "Gemini model validation encountered an issue: %s. "
            "Processing will continue.",
            e,
        )

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
                logger.error(
                    "Failed to fetch video details for retry %s: %s",
                    video_id,
                    e,
                )
                storage_service.mark_processed(video_id)
                await storage_service.save_processed_videos()
                await storage_service.save_failed_videos()
                continue

            if not duration_iso:
                storage_service.mark_processed(video_id)
                await storage_service.save_processed_videos()
                await storage_service.save_failed_videos()
                continue

            duration_s = parse_iso8601_duration(duration_iso)
            retry_video = type('Video', (), {
                'id': video_id,
                'title': title,
                'channel_id': channel_id,
            })()

            await process_video(
                config,
                youtube,
                gemini_service,
                email_service,
                storage_service,
                youtube_limiter,
                gemini_limiter,
                channel_name,
                retry_video,
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
                gemini_service,
                email_service,
                storage_service,
                youtube_limiter,
                gemini_limiter,
                channel_name,
                video,
                is_retry=False,
            )
            processed_count += 1

    elapsed = time.time() - start_time
    logger.info(
        "--- Script Finished. Processed %d new videos, retried %d failed videos in %.2f seconds. ---",
        processed_count,
        retry_count,
        elapsed,
    )

    # Clear model suggestion file after successful run
    if suggestion:
        clear_model_suggestion()


if __name__ == "__main__":
    asyncio.run(main())
