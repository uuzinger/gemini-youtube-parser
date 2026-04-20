from __future__ import annotations

import re


def sanitize_filename(filename: str, max_length: int = 150) -> str:
    filename = filename.replace("\ufffd", "_")
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename)
    sanitized = re.sub(r"\s+", "_", sanitized)
    return sanitized[:max_length].strip("_")


def parse_iso8601_duration(duration_string: str | None) -> int:
    if not duration_string or not duration_string.startswith("PT"):
        return 0
    duration_string = duration_string[2:]
    total_seconds = 0
    parts = re.findall(r"(\d+)([HMS])", duration_string)
    for value, unit in parts:
        value = int(value)
        if unit == "H":
            total_seconds += value * 3600
        elif unit == "M":
            total_seconds += value * 60
        elif unit == "S":
            total_seconds += value
    return total_seconds


def format_duration_seconds(seconds: int | None) -> str:
    if seconds is None:
        return "N/A"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02}:{secs:02}"
    return f"{minutes:02}:{secs:02}"
