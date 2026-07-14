from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from google.api_core.exceptions import GoogleAPIError

from services.youtube import get_videos_published_since


def _make_youtube(pages: list[dict]) -> MagicMock:
    youtube = MagicMock()
    youtube.channels.return_value.list.return_value.execute.return_value = {
        "items": [
            {"contentDetails": {"relatedPlaylists": {"uploads": "UUexample"}}}
        ]
    }
    pages_iter = iter(pages)
    youtube.playlistItems.return_value.list.return_value.execute.side_effect = (
        lambda: next(pages_iter)
    )
    return youtube


def _item(video_id: str, title: str, published_at: datetime) -> dict:
    return {
        "snippet": {
            "title": title,
            "publishedAt": published_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "contentDetails": {"videoId": video_id},
    }


def test_returns_videos_within_window_oldest_first() -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)
    youtube = _make_youtube(
        [
            {
                "items": [
                    _item("new1", "Newest", now - timedelta(days=1)),
                    _item("old1", "Oldest of the recent ones", now - timedelta(days=6)),
                ]
            }
        ]
    )

    videos = get_videos_published_since(youtube, "UCxxxx", since)

    assert [v.id for v in videos] == ["old1", "new1"]


def test_stops_paginating_once_older_video_found() -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)
    youtube = _make_youtube(
        [
            {
                "items": [_item("recent", "Recent", now - timedelta(days=1))],
                "nextPageToken": "page2",
            },
            {
                "items": [
                    _item("too_old", "Too old", now - timedelta(days=10)),
                ]
            },
        ]
    )

    videos = get_videos_published_since(youtube, "UCxxxx", since)

    assert [v.id for v in videos] == ["recent"]
    # Both pages are fetched: the second page is what reveals the cutoff.
    assert youtube.playlistItems.return_value.list.return_value.execute.call_count == 2


def test_returns_empty_list_when_uploads_playlist_lookup_fails() -> None:
    youtube = MagicMock()
    youtube.channels.return_value.list.return_value.execute.side_effect = (
        GoogleAPIError("boom")
    )

    videos = get_videos_published_since(
        youtube, "UCxxxx", datetime.now(timezone.utc) - timedelta(days=7)
    )

    assert videos == []


def test_respects_max_results() -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)
    youtube = _make_youtube(
        [
            {
                "items": [
                    _item("v1", "One", now - timedelta(days=1)),
                    _item("v2", "Two", now - timedelta(days=2)),
                    _item("v3", "Three", now - timedelta(days=3)),
                ]
            }
        ]
    )

    videos = get_videos_published_since(youtube, "UCxxxx", since, max_results=2)

    assert len(videos) == 2
