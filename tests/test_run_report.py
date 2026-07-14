from datetime import datetime, timezone

from services.run_report import RunReport


def test_clean_report_has_no_problems() -> None:
    report = RunReport(started_at=datetime(2026, 7, 14, tzinfo=timezone.utc))

    assert report.has_problems is False


def test_report_renders_all_problem_types() -> None:
    report = RunReport(started_at=datetime(2026, 7, 14, tzinfo=timezone.utc))
    report.processed_count = 2
    report.retried_count = 1
    report.record_video_failure(
        video_id="video-123",
        title="Example video",
        error="Gemini generation failed",
        attempt=3,
        max_attempts=3,
    )
    report.add_model_issue("Configured model is unavailable.")
    report.add_service_issue("Gemini quota/rate limit (429).")

    body = report.render_text()

    assert report.has_problems is True
    assert report.render_subject() == "1 video(s) permanently failed"
    assert "video-123" in body
    assert "PERMANENTLY FAILED" in body
    assert "Configured model is unavailable." in body
    assert "Gemini quota/rate limit (429)." in body


def test_duplicate_service_issues_are_suppressed() -> None:
    report = RunReport()

    report.add_service_issue("quota exhausted")
    report.add_service_issue("quota exhausted")

    assert report.service_issues == ["quota exhausted"]
