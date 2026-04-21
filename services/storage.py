from __future__ import annotations

import asyncio
import json
import logging
import os

import aiofiles

from config.models import Config

logger = logging.getLogger(__name__)


def _write_file_sync(filepath: str, content: str) -> None:
    """Synchronously write string to file as UTF-8 bytes."""
    content_bytes = content.encode("utf-8", errors="replace")
    with open(filepath, "wb") as f:
        f.write(content_bytes)


class StorageService:
    """Async file storage service."""

    def __init__(self, config: Config, max_retries: int = 3):
        self.config = config
        self.max_retries = max_retries
        self._processed_ids: set[str] = set()
        self._failed_videos: dict[str, dict] = {}

    async def load_processed_videos(self) -> None:
        """Load processed video IDs from JSON file."""
        if not os.path.exists(self.config.processed_videos_file):
            return
        try:
            async with aiofiles.open(
                self.config.processed_videos_file,
                "r",
                encoding="utf-8",
            ) as f:
                content = await f.read()
                self._processed_ids = set(json.loads(content))
            logger.info(
                "Loaded %d processed video IDs.",
                len(self._processed_ids),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.error(
                "Could not load processed_videos.json: %s. Starting fresh.",
                e,
            )
            self._processed_ids = set()

    async def load_failed_videos(self) -> None:
        """Load failed video IDs from JSON file."""
        failed_file = "failed_videos.json"
        if not os.path.exists(failed_file):
            return
        try:
            async with aiofiles.open(
                failed_file,
                "r",
                encoding="utf-8",
            ) as f:
                content = await f.read()
                self._failed_videos = json.loads(content)
            logger.info(
                "Loaded %d failed video IDs for retry.",
                len(self._failed_videos),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.error(
                "Could not load failed_videos.json: %s. Starting fresh.",
                e,
            )
            self._failed_videos = {}

    async def save_processed_videos(self) -> None:
        """Save processed video IDs to JSON file."""
        try:
            async with aiofiles.open(
                self.config.processed_videos_file,
                "w",
                encoding="utf-8",
            ) as f:
                await f.write(json.dumps(list(self._processed_ids), indent=4))
        except OSError as e:
            logger.error("Failed to save processed videos: %s", e)

    async def save_failed_videos(self) -> None:
        """Save failed video IDs to JSON file."""
        try:
            async with aiofiles.open(
                "failed_videos.json",
                "w",
                encoding="utf-8",
            ) as f:
                await f.write(json.dumps(self._failed_videos, indent=4))
        except OSError as e:
            logger.error("Failed to save failed videos: %s", e)

    def is_processed(self, video_id: str) -> bool:
        return video_id in self._processed_ids

    def is_failed(self, video_id: str) -> bool:
        return video_id in self._failed_videos

    def should_retry(self, video_id: str) -> bool:
        if video_id not in self._failed_videos:
            return True
        attempt = self._failed_videos[video_id].get("attempt", 0)
        return attempt < self.max_retries

    def get_failed_videos(self) -> list[str]:
        """Get list of failed video IDs that should be retried."""
        return [
            video_id
            for video_id, data in self._failed_videos.items()
            if self.should_retry(video_id)
        ]

    def mark_processed(self, video_id: str) -> None:
        self._processed_ids.add(video_id)
        # Remove from failed if it was there
        self._failed_videos.pop(video_id, None)

    def mark_failed(self, video_id: str, error: str) -> None:
        """Mark a video as failed with error message."""
        if video_id in self._failed_videos:
            self._failed_videos[video_id]["attempt"] += 1
        else:
            self._failed_videos[video_id] = {
                "attempt": 1,
                "error": error,
                "last_attempt": "unknown",
            }
        logger.warning(
            "Video %s failed (attempt %d/%d): %s",
            video_id,
            self._failed_videos[video_id]["attempt"],
            self.max_retries,
            error,
        )

    async def save_summary(
        self,
        video_id: str,
        title: str,
        duration: str,
        exec_summary: str,
        detailed_summary: str,
        key_quotes: str,
    ) -> None:
        """Save summary to a local text file."""
        from utils.helpers import sanitize_filename

        try:
            if not os.path.exists(self.config.output_dir):
                os.makedirs(self.config.output_dir, exist_ok=True)

            safe_title = sanitize_filename(title)
            filename = os.path.join(
                self.config.output_dir,
                f"{video_id}_{safe_title}.txt",
            )

            content = (
                f"Video Title: {title}\n"
                f"Video ID: {video_id}\n"
                f"URL: https://www.youtube.com/watch?v={video_id}\n"
                f"Duration: {duration}\n\n"
                f"--- Executive Summary ---\n"
                f"{exec_summary}\n\n"
                f"--- Detailed Summary ---\n"
                f"{detailed_summary}\n\n"
                f"--- Key Quotes ---\n"
                f"{key_quotes}\n"
            )

            # Replace non-ASCII characters to avoid encoding errors
            content = "".join(c for c in content if ord(c) < 128)

            await asyncio.to_thread(_write_file_sync, filename, content)
            logger.info("Saved summary to %s", filename)
        except OSError as e:
            logger.error(
                "Failed to save summary for %s: %s", video_id, e
            )
