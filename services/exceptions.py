from __future__ import annotations


class YouTubeMonitorError(Exception):
    """Base exception for all YouTube Monitor errors."""


class ConfigError(YouTubeMonitorError):
    """Raised when configuration is invalid or missing."""


class APIError(YouTubeMonitorError):
    """Raised when an external API call fails."""


class ModelNotFoundError(APIError):
    """Raised when the configured Gemini model is not available."""

    def __init__(self, model_name: str, suggested_model: str | None = None):
        self.model_name = model_name
        self.suggested_model = suggested_model
        msg = f"Model '{model_name}' is not available."
        if suggested_model:
            msg += f" Suggested model: '{suggested_model}'."
        super().__init__(msg)


class TranscriptError(YouTubeMonitorError):
    """Raised when transcript retrieval fails."""


class EmailError(YouTubeMonitorError):
    """Raised when email sending fails."""


class StorageError(YouTubeMonitorError):
    """Raised when file storage operations fail."""


class RateLimitExceeded(YouTubeMonitorError):
    """Raised when rate limits are exceeded."""
