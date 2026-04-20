from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Video:
    id: str
    title: str
    channel_id: str
    published_at: str = ""
    duration_iso: str = ""
    duration_seconds: int = 0


@dataclass
class ChannelRecipient:
    channel_id: str
    emails: list[str] = field(default_factory=list)


@dataclass
class Config:
    youtube_api_key: str
    gemini_api_key: str
    channel_ids: list[str]
    gemini_model: str
    prompt_exec_summary: str
    prompt_detailed_summary: str
    prompt_key_quotes: str
    safety_settings: list[dict[str, str]] | None
    smtp_server: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    sender_email: str
    default_recipients: list[str]
    channel_recipients: dict[str, list[str]]
    processed_videos_file: str
    log_file: str
    output_dir: str
    max_results_per_channel: int
    min_video_duration_minutes: int
    log_level: str = "INFO"
    youtube_rpm: int = 300
    youtube_rpd: int = 10000
    gemini_rpm: int = 1000
    gemini_rpd: int = 1000000
    dry_run: bool = False
