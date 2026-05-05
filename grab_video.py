from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time

from config import load_config
from services.youtube import build_youtube_client, get_transcript
from services.gemini import GeminiService
from utils.helpers import parse_iso8601_duration, format_duration_seconds


def extract_video_id(url: str) -> str:
    """Extract video ID from a YouTube URL."""
    patterns = [
        r'(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:www\.)?youtu\.be\/([a-zA-Z0-9_-]{11})',
        r'(?:www\.)?youtube\.com\/embed\/([a-zA-Z0-9_-]{11})',
        r'(?:www\.)?youtube\.com\/shorts\/([a-zA-Z0-9_-]{11})',
        r'(?:www\.)?youtube\.com\/v\/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


async def run(video_id: str, output_dir: str | None = None) -> None:
    config = load_config()

    from utils.logging import setup_logging
    setup_logging(log_file=config.log_file, log_level=config.log_level)

    gemini_service = GeminiService(config)

    # Fetch transcript
    transcript = get_transcript(video_id)
    if not transcript:
        print("ERROR: No transcript available for this video.")
        sys.exit(1)

    # Get video title via YouTube API
    youtube = build_youtube_client(config.youtube_api_key)
    video_response = youtube.videos().list(
        part="snippet,contentDetails", id=video_id
    ).execute()

    if not video_response.get("items"):
        print("ERROR: Could not find video details.")
        sys.exit(1)

    snippet = video_response["items"][0]["snippet"]
    title = snippet["title"]
    channel_title = snippet["channelTitle"]
    duration_iso = video_response["items"][0]["contentDetails"]["duration"]
    duration_s = parse_iso8601_duration(duration_iso)
    duration_str = format_duration_seconds(duration_s)

    # Generate executive summary in parallel with a brief description prompt
    exec_summary, description = await asyncio.gather(
        gemini_service.generate_summary(
            transcript,
            "Based on the following transcript, please provide a concise, one-paragraph executive summary.\nFocus on the main topic, key arguments, and the overall conclusion of the video.\nThe summary should be easy to understand for someone who has not seen the video.\nTRANSCRIPT:\n{transcript}",
        ),
        gemini_service.generate_summary(
            description if (description := snippet.get("description", "")) else transcript[:4000],
            "Provide a concise 2-3 sentence description of this video.\n\n{transcript}",
        ),
    )

    # Sanitize title for filename
    safe_title = re.sub(r'[^\w\s\-\.]', '', title)
    safe_title = re.sub(r'\s+', '_', safe_title).strip('_')

    output_path = os.path.join(
        output_dir or config.output_dir,
        f"{video_id}_{safe_title}.txt",
    )

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f"VIDEO: {title}\n")
        f.write(f"CHANNEL: {channel_title}\n")
        f.write(f"DURATION: {duration_str}\n")
        f.write(f"VIDEO ID: {video_id}\n")
        f.write(f"URL: https://www.youtube.com/watch?v={video_id}\n")
        f.write("=" * 80 + "\n\n")

        f.write("=" * 80 + "\n")
        f.write("EXECUTIVE SUMMARY\n")
        f.write("=" * 80 + "\n\n")
        f.write(exec_summary if not exec_summary.startswith("Error:") else exec_summary)
        f.write("\n\n")

        f.write("=" * 80 + "\n")
        f.write("DESCRIPTION\n")
        f.write("=" * 80 + "\n\n")
        f.write(description if not description.startswith("Error:") else "")
        f.write("\n\n")

        f.write("=" * 80 + "\n")
        f.write("FULL TRANSCRIPT\n")
        f.write("=" * 80 + "\n\n")
        f.write(transcript)
        f.write("\n")

    print(f"Output saved to: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python grab_video.py <youtube-url>")
        sys.exit(1)

    video_id = extract_video_id(sys.argv[1])
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(run(video_id, output_dir))
