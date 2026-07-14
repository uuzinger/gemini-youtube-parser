from __future__ import annotations

import configparser
import logging

from . import _parse_channel_ids, _parse_channel_recipients
from .models import WeeklyConfig

logger = logging.getLogger(__name__)


def validate_weekly_config(config: WeeklyConfig) -> list[str]:
    errors: list[str] = []
    if not config.channel_ids:
        errors.append("channels in the summary config file")
    if config.window_days < 1:
        errors.append("window_days must be >= 1")
    if config.max_results_per_channel < 1:
        errors.append("max_results_per_channel must be >= 1")

    missing_recipients = [
        cid
        for cid in config.channel_ids
        if not config.channel_recipients.get(cid) and not config.default_recipients
    ]
    if missing_recipients:
        errors.append(
            "recipients for channel(s) with no [CHANNEL_RECIPIENTS] entry and "
            f"no default_recipients fallback: {', '.join(missing_recipients)}"
        )
    return errors


def load_weekly_config(config_path: str = "summary.ini") -> WeeklyConfig:
    """Load the weekly digest configuration from summary.ini.

    Channel and API credentials (YouTube, LLM, SMTP) are intentionally not
    duplicated here; the weekly script reuses config.ini for those.
    """
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(config_path)

    channel_ids = _parse_channel_ids(parser)
    default_recipients, channel_recipients = _parse_channel_recipients(parser)

    config = WeeklyConfig(
        channel_ids=channel_ids,
        channel_recipients=channel_recipients,
        default_recipients=default_recipients,
        window_days=parser.getint("SETTINGS", "window_days", fallback=7),
        max_results_per_channel=parser.getint(
            "SETTINGS", "max_results_per_channel", fallback=25
        ),
        subject_prefix=parser.get(
            "SETTINGS", "subject_prefix", fallback="Weekly YouTube Digest"
        ).strip(),
        log_file=parser.get("SETTINGS", "log_file", fallback="logs/weekly.log"),
        log_level=parser.get("SETTINGS", "log_level", fallback="INFO").upper(),
    )

    errors = validate_weekly_config(config)
    if errors:
        raise ValueError(
            f"Weekly summary configuration errors: missing {', '.join(errors)}"
        )

    return config
