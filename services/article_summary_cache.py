from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from typing import Any

_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SECTION_KEY_PATTERN = re.compile(r"^[a-z_]+$")


class ArticleSummaryCache:
    """Local cache for completed article-summary sections."""

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir

    def _path_for(self, video_id: str) -> str:
        if not _VIDEO_ID_PATTERN.fullmatch(video_id):
            raise ValueError(f"Invalid YouTube video ID for summary cache: {video_id}")
        return os.path.join(self.cache_dir, f"{video_id}.json")

    @staticmethod
    def _source_hash(prompt: str, transcript: str) -> str:
        source = f"{prompt}\0{transcript}".encode("utf-8", errors="replace")
        return hashlib.sha256(source).hexdigest()

    def _read(self, video_id: str) -> dict[str, Any]:
        path = self._path_for(video_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _write(self, video_id: str, data: dict[str, Any]) -> None:
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
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(temp_path, path)
        except Exception:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
            raise

    def get(
        self,
        video_id: str,
        section_key: str,
        prompt: str,
        transcript: str,
    ) -> str | None:
        if not _SECTION_KEY_PATTERN.fullmatch(section_key):
            raise ValueError(f"Invalid summary section key: {section_key}")

        data = self._read(video_id)
        section = data.get(section_key)
        if not isinstance(section, dict):
            return None
        if section.get("source_hash") != self._source_hash(prompt, transcript):
            return None
        content = section.get("content")
        return content if isinstance(content, str) else None

    def put(
        self,
        video_id: str,
        section_key: str,
        prompt: str,
        transcript: str,
        content: str,
    ) -> None:
        if not content:
            return
        if not _SECTION_KEY_PATTERN.fullmatch(section_key):
            raise ValueError(f"Invalid summary section key: {section_key}")

        data = self._read(video_id)
        data[section_key] = {
            "source_hash": self._source_hash(prompt, transcript),
            "content": content,
        }
        self._write(video_id, data)
