from __future__ import annotations

import os
import re
import tempfile

_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


class TranscriptCache:
    """Local file-backed cache for successful YouTube transcript fetches."""

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir

    def _path_for(self, video_id: str) -> str:
        if not _VIDEO_ID_PATTERN.fullmatch(video_id):
            raise ValueError(f"Invalid YouTube video ID for transcript cache: {video_id}")
        return os.path.join(self.cache_dir, f"{video_id}.txt")

    def get(self, video_id: str) -> str | None:
        path = self._path_for(video_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def put(self, video_id: str, transcript: str) -> None:
        if not transcript:
            return

        path = self._path_for(video_id)
        os.makedirs(self.cache_dir, exist_ok=True)

        fd, temp_path = tempfile.mkstemp(
            prefix=f".{video_id}.",
            suffix=".tmp",
            dir=self.cache_dir,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(transcript)
            os.replace(temp_path, path)
        except Exception:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
            raise
