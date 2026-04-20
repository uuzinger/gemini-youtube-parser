# YouTube Channel Monitor & Gemini Summarizer

A Python application that monitors specified YouTube channels for new video uploads, fetches transcripts, generates AI-powered summaries using Google Gemini, saves them locally, and sends email notifications.

## Overview

The application performs the following actions:

1.  **Monitors Multiple Channels:** Checks a list of specified YouTube channel IDs on each run.
2.  **Detects New Videos:** Identifies videos published within the last 25 hours.
3.  **Fetches Video Details:** Retrieves video duration and filters by minimum length.
4.  **Retrieves Transcripts:** Fetches English transcripts for eligible videos.
5.  **Generates Summaries via Gemini:** Uses the Google Gemini API to create:
    *   A short executive summary.
    *   A detailed bulleted summary.
    *   A list of key quotes.
6.  **Saves Locally:** Stores generated summaries in text files within a configurable output directory. Tracks processed videos in `processed_videos.json`.
7.  **Sends Email Notifications:** Dispatches formatted HTML emails with summaries to configured recipients.
8.  **Channel-Specific Recipients:** Supports per-channel email routing with a default fallback list.

## Features

*   Async processing with parallel Gemini API calls (3 summaries generated simultaneously).
*   Google Gen AI SDK (new `google-genai` package) with async support.
*   Configurable rate limiting for YouTube and Gemini APIs.
*   Automatic retry with exponential backoff for Gemini API calls.
*   Circuit breaker pattern to prevent cascading failures.
*   Model availability validation with automatic suggestions when models are deprecated.
*   Structured logging with file rotation.
*   Custom exception hierarchy for robust error handling.
*   Dry run mode for testing email notifications without sending.
*   INI configuration file for all settings.
*   Wrapper scripts for easy execution (`run.py` for Windows, `run.sh` for Linux/macOS).

## Prerequisites

1.  **Python 3.10+**: Required for modern type hints and async features.
2.  **pip**: Python package installer.
3.  **Google Cloud Account & YouTube Data API v3 Key:**
    *   Go to [Google Cloud Console](https://console.cloud.google.com/).
    *   Create a project and enable "YouTube Data API v3".
    *   Create an API key under "Credentials".
4.  **Google AI Studio Account & Gemini API Key:**
    *   Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
    *   Create an API key. Usage may incur costs.
5.  **Email Account for SMTP:**
    *   An email account capable of sending via SMTP.
    *   For Gmail with 2FA: generate an [App Password](https://support.google.com/accounts/answer/185833).

## Project Structure

```
gemini-youtube-parser/
├── config/                    # Configuration loading and validation
│   ├── __init__.py
│   └── models.py              # Data classes (Config, Video, etc.)
├── services/                  # Core business logic
│   ├── exceptions.py          # Custom exceptions
│   ├── rate_limiter.py        # Sliding window rate limiter
│   ├── model_validator.py     # Gemini model validation
│   ├── youtube.py             # YouTube API wrapper
│   ├── gemini.py              # Async Gemini service
│   ├── email.py               # Async email service
│   └── storage.py             # Async file storage
├── utils/                     # Utility functions
│   ├── logging.py             # Structured logging setup
│   └── helpers.py             # String/date utilities
├── main.py                    # Async entry point
├── run.py                     # Windows wrapper
├── run.sh                     # Linux/macOS wrapper
├── setup.py                   # Setup script
├── config.ini                 # Configuration file
├── requirements.txt           # Python dependencies
└── .gitignore
```

## Setup Instructions

1.  **Navigate to Directory:**
    ```bash
    cd /path/to/your/script/directory
    ```

2.  **Run Setup Script:**
    ```bash
    python setup.py
    ```
    This creates a virtual environment (`.venv`) and installs dependencies.

3.  **Configure `config.ini`:** Open the file and fill in:

    *   **`[API_KEYS]`**: `youtube_api_key` and `gemini_api_key`.
    *   **`[CHANNELS]`**: One YouTube Channel ID per line (e.g., `My Channel = UCxxxxxxxxxxxxxx`).
    *   **`[GEMINI]`**: `model_name` (e.g., `gemini-2.5-flash`), prompts, and optional `safety_settings`.
    *   **`[EMAIL]`**: SMTP server, port, credentials, and sender email.
    *   **`[CHANNEL_RECIPIENTS]`**: `default_recipients` and optional per-channel recipients.
    *   **`[SETTINGS]`**: File paths, `max_results_per_channel`, `min_video_duration_minutes`, `log_level`.
    *   **`[RATE_LIMITS]`**: API rate limits (RPM/RPD) for YouTube and Gemini.

4.  **Make Run Script Executable (Linux/macOS):**
    ```bash
    chmod +x run.sh
    ```

## Usage

### Manual Execution

```bash
# Windows
python run.py

# Linux/macOS
./run.sh
```

### Scheduling

*   **Linux/macOS:** Use cron to run `python3 /path/to/project/run.sh` at your desired interval.
*   **Windows:** Use Task Scheduler to run `python C:\path\to\project\run.py`. Set the "Start in" directory to your project path.

## Configuration Reference

### `[RATE_LIMITS]`

Controls API request rates to avoid hitting quotas:

| Setting | Default | Description |
|---------|---------|-------------|
| `youtube_rpm` | 300 | YouTube API requests per minute |
| `youtube_rpd` | 10000 | YouTube API requests per day |
| `gemini_rpm` | 1000 | Gemini API requests per minute |
| `gemini_rpd` | 1000000 | Gemini API requests per day |

### `[SETTINGS]`

| Setting | Default | Description |
|---------|---------|-------------|
| `processed_videos_file` | `processed_videos.json` | File tracking processed video IDs |
| `log_file` | `logs/monitor.log` | Log file path |
| `output_dir` | `output_summaries` | Directory for saved summaries |
| `max_results_per_channel` | 3 | Videos to check per channel per run |
| `min_video_duration_minutes` | 5 | Skip videos shorter than this |
| `log_level` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `dry_run` | `False` | If True, logs email content instead of sending |

## Model Validation

When the configured Gemini model is unavailable (deprecated or restricted), the application:

1.  Detects the issue at startup and during processing.
2.  Lists available models and suggests the best alternative.
3.  Writes a `.model_suggestion` file with details.
4.  Prints a warning on subsequent runs via `run.py`/`run.sh`.

The `.model_suggestion` file is cleared after a successful run. Update `config.ini` `[GEMINI] model_name` with the suggested value.

## Error Handling

The application uses a layered error handling approach:

*   **Retry with backoff:** Gemini API calls retry up to 3 times with exponential delays.
*   **Circuit breaker:** After 5 consecutive failures, Gemini calls are temporarily skipped.
*   **Rate limiting:** Sliding window trackers enforce per-minute and per-day limits.
*   **Custom exceptions:** Specific exception types (`ModelNotFoundError`, `RateLimitExceeded`, etc.) for targeted handling.

## Logging

Logs are written to both the console and a rotating file (`logs/monitor.log` by default):

*   **Rotation:** 10MB max per file, 5 backup files kept.
*   **Format:** `YYYY-MM-DD HH:MM:SS | LEVEL    | module | message`
*   **Encoding:** UTF-8 with replacement for invalid characters.

## Dry Run Mode

Enable dry run mode to test the entire pipeline without actually sending emails:

1.  Set `dry_run = True` in `config.ini` under `[SETTINGS]`.
2.  Run the script normally.

In dry run mode, the application will:
*   Process all videos normally (fetch transcripts, generate summaries).
*   Save summaries locally as usual.
*   **NOT send any emails.**
*   Log the full email content to the console and log file, including:
    *   To/From addresses and BCC recipients
    *   Subject line
    *   Executive Summary
    *   Detailed Summary
    *   Key Quotes

This is useful for testing your configuration and verifying email content before enabling production sends. Set `dry_run = False` when ready for live notifications.

## Requirements

| Package | Purpose |
|---------|---------|
| `google-genai` | Google Gemini API client (async) |
| `google-api-python-client` | YouTube Data API client |
| `youtube-transcript-api` | YouTube transcript fetching |
| `aiosmtplib` | Async SMTP for email |
| `tenacity` | Retry logic with exponential backoff |
| `aiofiles` | Async file I/O |
| `markdown` | HTML rendering for email |
