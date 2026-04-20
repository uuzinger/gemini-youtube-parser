from __future__ import annotations

import os
import configparser
import logging

from .models import Config

logger = logging.getLogger(__name__)

VALID_HARM_CATEGORIES = frozenset([
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
])


def _parse_safety_settings(raw: str | None) -> list[dict[str, str]] | None:
    if not raw:
        return None
    settings: list[dict[str, str]] = []
    for item in raw.split(","):
        if ":" not in item:
            continue
        key, value = item.strip().split(":", 1)
        key, value = key.strip(), value.strip()
        if key in VALID_HARM_CATEGORIES:
            settings.append({"category": key, "threshold": value})
        else:
            logger.warning("Ignoring invalid safety setting: %s", key)
    return settings if settings else None


def _parse_channel_recipients(
    config: configparser.ConfigParser,
) -> tuple[list[str], dict[str, list[str]]]:
    default_recipients: list[str] = []
    channel_recipients: dict[str, list[str]] = {}
    if not config.has_section("CHANNEL_RECIPIENTS"):
        return default_recipients, channel_recipients
    for key, value in config.items("CHANNEL_RECIPIENTS"):
        emails = [e.strip() for e in value.split(",") if e.strip()]
        if not emails:
            continue
        if key.lower() == "default_recipients":
            default_recipients = emails
        elif key.startswith("UC") and len(key) == 24:
            channel_recipients[key] = emails
        else:
            logger.warning("Invalid key in [CHANNEL_RECIPIENTS]: %s", key)
    return default_recipients, channel_recipients


def _parse_channel_ids(config: configparser.ConfigParser) -> list[str]:
    if not config.has_section("CHANNELS"):
        return []
    ids: list[str] = []
    for _name, channel_id in config.items("CHANNELS"):
        cid = channel_id.strip()
        if cid.startswith("UC") and len(cid) == 24:
            ids.append(cid)
    return ids


def validate_config(config: Config) -> list[str]:
    errors: list[str] = []
    if not config.youtube_api_key or config.youtube_api_key == "YOUR_YOUTUBE_DATA_API_V3_KEY":
        errors.append("youtube_api_key")
    if not config.gemini_api_key or config.gemini_api_key == "YOUR_GEMINI_API_KEY":
        errors.append("gemini_api_key")
    if not config.channel_ids:
        errors.append("channels in the config file")
    if not config.smtp_server or not config.smtp_user or not config.smtp_password or not config.sender_email:
        errors.append("Email settings (smtp_server, smtp_user, smtp_password, sender_email)")
    if not config.default_recipients and not config.channel_recipients:
        errors.append("Email recipients (default_recipients or channel-specific)")
    if not config.gemini_model:
        errors.append("gemini model_name")
    if config.min_video_duration_minutes < 0:
        errors.append("min_video_duration_minutes must be >= 0")
    if config.max_results_per_channel < 1:
        errors.append("max_results_per_channel must be >= 1")
    return errors


def load_config(config_path: str = "config.ini") -> Config:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(config_path)

    channel_ids = _parse_channel_ids(parser)
    default_recipients, channel_recipients = _parse_channel_recipients(parser)
    safety_settings = _parse_safety_settings(
        parser.get("GEMINI", "safety_settings", fallback=None)
    )

    config = Config(
        youtube_api_key=parser.get("API_KEYS", "youtube_api_key", fallback=""),
        gemini_api_key=parser.get("API_KEYS", "gemini_api_key", fallback=""),
        channel_ids=channel_ids,
        gemini_model=parser.get("GEMINI", "model_name", fallback="gemini-2.5-flash"),
        prompt_exec_summary=parser.get(
            "GEMINI", "prompt_executive_summary", fallback=""
        ).strip(),
        prompt_detailed_summary=parser.get(
            "GEMINI", "prompt_detailed_summary", fallback=""
        ).strip(),
        prompt_key_quotes=parser.get(
            "GEMINI", "prompt_key_quotes", fallback=""
        ).strip(),
        safety_settings=safety_settings,
        smtp_server=parser.get("EMAIL", "smtp_server", fallback=""),
        smtp_port=parser.getint("EMAIL", "smtp_port", fallback=587),
        smtp_user=parser.get("EMAIL", "smtp_user", fallback=""),
        smtp_password=parser.get("EMAIL", "smtp_password", fallback=""),
        sender_email=parser.get("EMAIL", "sender_email", fallback=""),
        default_recipients=default_recipients,
        channel_recipients=channel_recipients,
        processed_videos_file=parser.get(
            "SETTINGS", "processed_videos_file", fallback="processed_videos.json"
        ),
        log_file=parser.get("SETTINGS", "log_file", fallback="logs/monitor.log"),
        output_dir=parser.get("SETTINGS", "output_dir", fallback="output_summaries"),
        max_results_per_channel=parser.getint(
            "SETTINGS", "max_results_per_channel", fallback=3
        ),
        min_video_duration_minutes=parser.getint(
            "SETTINGS", "min_video_duration_minutes", fallback=5
        ),
        log_level=parser.get("SETTINGS", "log_level", fallback="INFO").upper(),
        youtube_rpm=parser.getint("RATE_LIMITS", "youtube_rpm", fallback=300),
        youtube_rpd=parser.getint("RATE_LIMITS", "youtube_rpd", fallback=10000),
        gemini_rpm=parser.getint("RATE_LIMITS", "gemini_rpm", fallback=1000),
        gemini_rpd=parser.getint("RATE_LIMITS", "gemini_rpd", fallback=1000000),
        dry_run=parser.getboolean("SETTINGS", "dry_run", fallback=False),
    )

    errors = validate_config(config)
    if errors:
        raise ValueError(f"Configuration errors: missing {', '.join(errors)}")

    return config
