from __future__ import annotations

import sys
import types

from services.transcript_cache import TranscriptCache
from services.youtube import get_transcript

VIDEO_ID = "dQw4w9WgXcQ"


class _NoTranscriptFound(Exception):
    pass


class _TranscriptsDisabled(Exception):
    pass


class _YouTubeRequestFailed(Exception):
    pass


class _IpBlocked(Exception):
    pass


def _install_fake_transcript_module(monkeypatch, api_class) -> None:
    fake_module = types.SimpleNamespace(
        YouTubeTranscriptApi=api_class,
        NoTranscriptFound=_NoTranscriptFound,
        TranscriptsDisabled=_TranscriptsDisabled,
        YouTubeRequestFailed=_YouTubeRequestFailed,
        IpBlocked=_IpBlocked,
    )
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_module)


def test_get_transcript_returns_cached_value_without_fetching(
    tmp_path, monkeypatch
) -> None:
    cache = TranscriptCache(str(tmp_path / "transcripts"))
    cache.put(VIDEO_ID, "cached transcript")

    class ExplodingApi:
        def __init__(self):
            raise AssertionError("cache hit should not instantiate API client")

    _install_fake_transcript_module(monkeypatch, ExplodingApi)

    assert get_transcript(VIDEO_ID, cache) == "cached transcript"


def test_get_transcript_fetches_and_caches_miss(tmp_path, monkeypatch) -> None:
    cache = TranscriptCache(str(tmp_path / "transcripts"))

    class FakeFetchedTranscript:
        def to_raw_data(self):
            return [{"text": "hello"}, {"text": "world"}]

    class FakeApi:
        def fetch(self, video_id, languages):
            assert video_id == VIDEO_ID
            assert languages == ["en", "en-US", "en-GB"]
            return FakeFetchedTranscript()

    _install_fake_transcript_module(monkeypatch, FakeApi)

    assert get_transcript(VIDEO_ID, cache) == "hello world"
    assert cache.get(VIDEO_ID) == "hello world"


def test_get_transcript_does_not_cache_missing_transcript(
    tmp_path, monkeypatch
) -> None:
    cache = TranscriptCache(str(tmp_path / "transcripts"))

    class FakeApi:
        def fetch(self, video_id, languages):
            raise _NoTranscriptFound()

    _install_fake_transcript_module(monkeypatch, FakeApi)

    assert get_transcript(VIDEO_ID, cache) is None
    assert cache.get(VIDEO_ID) is None


def test_get_transcript_rejects_unsafe_cache_key(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "transcripts"
    cache = TranscriptCache(str(cache_dir))

    class ExplodingApi:
        def __init__(self):
            raise AssertionError("unsafe IDs should not instantiate API client")

    _install_fake_transcript_module(monkeypatch, ExplodingApi)

    assert get_transcript("../unsafe", cache) is None
    assert not cache_dir.exists()
