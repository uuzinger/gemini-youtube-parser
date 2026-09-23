import pytest

from services.transcript_cache import TranscriptCache


def test_transcript_cache_round_trips_text(tmp_path) -> None:
    cache = TranscriptCache(str(tmp_path / "transcripts"))

    cache.put("dQw4w9WgXcQ", "First line\nSecond line")

    assert cache.get("dQw4w9WgXcQ") == "First line\nSecond line"


def test_transcript_cache_rejects_unsafe_video_ids(tmp_path) -> None:
    cache_dir = tmp_path / "transcripts"
    cache = TranscriptCache(str(cache_dir))

    with pytest.raises(ValueError):
        cache.get("../unsafe")

    assert not cache_dir.exists()


def test_transcript_cache_does_not_write_empty_transcripts(tmp_path) -> None:
    cache_dir = tmp_path / "transcripts"
    cache = TranscriptCache(str(cache_dir))

    cache.put("dQw4w9WgXcQ", "")

    assert cache.get("dQw4w9WgXcQ") is None
