from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class VideoFailure:
    video_id: str
    title: str
    error: str
    attempt: int
    max_attempts: int

    @property
    def abandoned(self) -> bool:
        return self.attempt >= self.max_attempts


@dataclass
class RunReport:
    """Collect alert-worthy events from one monitor run."""

    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    processed_count: int = 0
    retried_count: int = 0
    video_failures: list[VideoFailure] = field(default_factory=list)
    model_issues: list[str] = field(default_factory=list)
    service_issues: list[str] = field(default_factory=list)
    fatal_error: str | None = None

    def record_video_failure(
        self,
        video_id: str,
        title: str,
        error: str,
        attempt: int,
        max_attempts: int,
    ) -> None:
        self.video_failures.append(
            VideoFailure(
                video_id=video_id,
                title=title,
                error=error,
                attempt=attempt,
                max_attempts=max_attempts,
            )
        )

    def add_model_issue(self, issue: str) -> None:
        if issue and issue not in self.model_issues:
            self.model_issues.append(issue)

    def add_service_issue(self, issue: str) -> None:
        if issue and issue not in self.service_issues:
            self.service_issues.append(issue)

    @property
    def has_problems(self) -> bool:
        return bool(
            self.video_failures
            or self.model_issues
            or self.service_issues
            or self.fatal_error
        )

    def render_subject(self) -> str:
        abandoned_count = sum(
            failure.abandoned for failure in self.video_failures
        )
        if self.fatal_error:
            return "monitor run failed"
        if abandoned_count:
            return f"{abandoned_count} video(s) permanently failed"
        return "monitor run encountered problems"

    def render_text(self) -> str:
        finished_at = datetime.now(timezone.utc)
        lines = [
            "YouTube monitor run report",
            f"Started:  {self.started_at.isoformat()}",
            f"Finished: {finished_at.isoformat()}",
            (
                "Activity: "
                f"{self.processed_count} new video(s), "
                f"{self.retried_count} retried"
            ),
        ]

        if self.fatal_error:
            lines.extend(["", "FATAL ERROR", self.fatal_error])

        if self.model_issues:
            lines.extend(["", "MODEL CONFIGURATION"])
            lines.extend(f"- {issue}" for issue in self.model_issues)

        if self.service_issues:
            lines.extend(["", "API / SERVICE ISSUES"])
            lines.extend(f"- {issue}" for issue in self.service_issues)

        if self.video_failures:
            lines.extend(["", "VIDEO FAILURES"])
            for failure in self.video_failures:
                status = "PERMANENTLY FAILED" if failure.abandoned else "retry pending"
                lines.append(
                    f"- {failure.video_id} | {failure.title} | "
                    f"attempt {failure.attempt}/{failure.max_attempts} | "
                    f"{status} | {failure.error}"
                )

        lines.extend(
            [
                "",
                "Review the production logs and config.ini before the next run.",
            ]
        )
        return "\n".join(lines)
