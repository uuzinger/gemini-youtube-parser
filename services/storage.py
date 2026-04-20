from __future__ import annotations

import json
import logging
import os

import aiofiles

from config.models import Config

logger = logging.getLogger(__name__)


class StorageService:
    """Async file storage service."""

    def __init__(self, config: Config):
        self.config = config
        self._processed_ids: set[str] = set()

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

    def is_processed(self, video_id: str) -> bool:
        return video_id in self._processed_ids

    def mark_processed(self, video_id: str) -> None:
        self._processed_ids.add(video_id)

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

            async with aiofiles.open(
                filename, "w", encoding="utf-8"
            ) as f:
                await f.write(content)
            logger.info("Saved summary to %s", filename)
        except OSError as e:
            logger.error(
                "Failed to save summary for %s: %s", video_id, e
            )
