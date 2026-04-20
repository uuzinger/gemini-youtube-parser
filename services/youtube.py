from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from google.api_core.exceptions import GoogleAPIError
from googleapiclient.discovery import build

from config.models import Video
from .exceptions import APIError, TranscriptError

logger = logging.getLogger(__name__)


def build_youtube_client(api_key: str):
    """Build and return a YouTube API client."""
    try:
        return build(
            "youtube",
            "v3",
            developerKey=api_key,
            cache_discovery=False,
        )
    except GoogleAPIError as e:
        logger.error("Failed to build YouTube API client: %s", e)
        raise APIError(f"Failed to initialize YouTube API: {e}") from e


def get_channel_name(youtube, channel_id: str) -> str:
    """Fetch the official channel name."""
    try:
        response = (
            youtube.channels()
            .list(part="snippet", id=channel_id)
            .execute()
        )
        return response["items"][0]["snippet"]["title"]
    except (GoogleAPIError, IndexError, KeyError) as e:
        logger.error("Error fetching channel name for %s: %s", channel_id, e)
        return channel_id


def get_latest_videos(
    youtube, channel_id: str, max_results: int
) -> list[Video]:
    """Get the latest videos from a channel, filtered to last 25 hours."""
    try:
        response = (
            youtube.channels()
            .list(part="contentDetails", id=channel_id)
            .execute()
        )
        uploads_id = response["items"][0]["contentDetails"]["relatedPlaylists"][
            "uploads"
        ]
    except (GoogleAPIError, IndexError, KeyError) as e:
        logger.error("Failed to get uploads playlist for %s: %s", channel_id, e)
        return []

    try:
        response = (
            youtube.playlistItems()
            .list(
                part="snippet,contentDetails",
                playlistId=uploads_id,
                maxResults=max_results,
            )
            .execute()
        )
    except GoogleAPIError as e:
        logger.error(
            "Failed to get playlist items for %s: %s", channel_id, e
        )
        return []

    recent_threshold = datetime.now(timezone.utc) - timedelta(hours=25)
    videos: list[Video] = []
    for item in response.get("items", []):
        snippet = item["snippet"]
        published_at = datetime.fromisoformat(
            snippet["publishedAt"].replace("Z", "+00:00")
        )
        if published_at >= recent_threshold:
            videos.append(
                Video(
                    id=item["contentDetails"]["videoId"],
                    title=snippet["title"],
                    channel_id=channel_id,
                    published_at=snippet["publishedAt"],
                )
            )

    videos.sort(key=lambda v: v.published_at, reverse=True)
    return videos


def get_video_details(youtube, video_id: str) -> str | None:
    """Get the ISO 8601 duration for a video."""
    try:
        response = (
            youtube.videos()
            .list(part="contentDetails", id=video_id)
            .execute()
        )
        return response["items"][0]["contentDetails"]["duration"]
    except (GoogleAPIError, IndexError, KeyError) as e:
        logger.error("Could not get details for video %s: %s", video_id, e)
        return None


def get_transcript(video_id: str) -> str | None:
    """Fetch transcript for a video using youtube-transcript-api."""
    from youtube_transcript_api import (
        YouTubeTranscriptApi,
        TranscriptsDisabled,
        NoTranscriptFound,
    )

    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["en", "en-US", "en-GB"]
        )
        transcript_text = " ".join(item["text"] for item in transcript_list)
        logger.info("Successfully fetched transcript for video ID: %s", video_id)
        return transcript_text
    except (TranscriptsDisabled, NoTranscriptFound):
        logger.warning(
            "No English transcript found or transcripts disabled for %s.",
            video_id,
        )
        return None
    except Exception as e:
        logger.error(
            "Unexpected error fetching transcript for %s: %s", video_id, e
        )
        return None
