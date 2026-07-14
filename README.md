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
9.  **Proactive Problem Alerts:** Sends one consolidated run-health email to `default_recipients` when videos fail, Gemini quotas change, a model becomes unavailable, or the run crashes.

## Features

*   Async processing with parallel Gemini API calls (3 summaries generated simultaneously).
*   Google Gen AI SDK (new `google-genai` package) with async support.
*   Configurable rate limiting for YouTube and Gemini APIs.
*   Automatic retry with exponential backoff for Gemini API calls.
*   Circuit breaker pattern to prevent cascading failures.
*   Model availability validation with automatic suggestions when models are deprecated.
*   Problem-only administrative alerts with model and quota diagnostics.
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

3.  **Configure `config.ini`:** Copy the example config and fill in your settings:
    ```bash
    cp config.ini.example config.ini
    ```
    Then edit `config.ini` and fill in:

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

## Upgrading from the Previous Version

Use these steps when upgrading an existing installation that already has a
production `config.ini`, processed-video state, and scheduled job.

1.  **Back up local state before pulling:**
    ```bash
    cp config.ini config.ini.backup
    cp processed_videos.json processed_videos.json.backup 2>/dev/null || true
    cp failed_videos.json failed_videos.json.backup 2>/dev/null || true
    git pull --ff-only
    ```
    Do not replace your existing `config.ini` with `config.ini.example`; the
    existing file contains your credentials and production settings.

2.  **Refresh the virtual environment dependencies:**
    ```bash
    .venv/bin/python -m pip install -r requirements.txt
    ```
    If the virtual environment references a Python version that is no longer
    installed, recreate it:
    ```bash
    rm -rf .venv
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    ```

3.  **Optionally add the new alert configuration:**
    ```ini
    [ALERTS]
    alerts_enabled = True
    alert_subject_prefix = [YT-Monitor ALERT]
    ```
    This section is optional. Alerts default to enabled when it is absent.
    Administrative alerts are sent to
    `[CHANNEL_RECIPIENTS] default_recipients`; channel-specific recipients do
    not receive administrative alerts.

4.  **Verify email prerequisites:** Ensure `default_recipients` is populated
    and the existing `[EMAIL]` SMTP settings are valid. `dry_run = True`
    suppresses both summary emails and administrative alert emails.

5.  **Review the configured Gemini model:** Compare `[GEMINI] model_name` with
    the models available to your API key. The application now checks the live
    Gemini model list on every run. If the configured model is unavailable,
    the alert includes a suggested replacement and up to 20 available
    text-generation models. It never changes `config.ini` automatically.

6.  **Review Gemini quota settings:** Set `gemini_rpm` and `gemini_rpd` to
    values appropriate for your current Google plan. Each processed video
    normally makes three Gemini generation requests, and startup model
    validation makes an additional request. Google does not expose your
    account's quota limits through the model-list API, so quota changes are
    detected and reported when Gemini returns HTTP 429.

7.  **Run once manually before re-enabling the scheduler:**
    ```bash
    ./run.sh
    echo "exit code: $?"
    ```
    Review `logs/monitor.log` and confirm that normal summary email delivery
    still works. Fatal runtime errors now return a non-zero exit code.

8.  **Optional developer verification:**
    ```bash
    .venv/bin/python -m pip install -r requirements-dev.txt
    .venv/bin/python -m pytest -q
    ```

The upgrade preserves `processed_videos.json`, `failed_videos.json`, and saved
summaries. `config.ini` is now ignored by Git to reduce the risk of committing
API keys or SMTP credentials.

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

### `[ALERTS]`

`alerts_enabled` defaults to `True` and controls consolidated problem emails.
`alert_subject_prefix` defaults to `[YT-Monitor ALERT]`. Alerts go only to
`default_recipients` and are sent once at the end of a problematic run.

## Model Validation

When the configured Gemini model is unavailable (deprecated or restricted), the application:

1.  Detects the issue at startup and during processing.
2.  Lists available models and suggests the best alternative.
3.  Writes a `.model_suggestion` file with details.
4.  Includes the model issue and available alternatives in the administrative alert.

The `.model_suggestion` file is cleared after the configured model is
successfully found in Gemini's current model list. Update `config.ini`
`[GEMINI] model_name` with an available model; the application does not switch
models automatically.

## Error Handling

The application uses a layered error handling approach:

*   **Retry with backoff:** Gemini API calls retry up to 3 times with exponential delays.
*   **Circuit breaker:** After 5 consecutive failures, Gemini calls are temporarily skipped.
*   **Rate limiting:** Sliding window trackers enforce per-minute and per-day limits.
*   **Custom exceptions:** Specific exception types (`ModelNotFoundError`, `RateLimitExceeded`, etc.) for targeted handling.
*   **Administrative alerts:** Failed videos, exhausted retries, model drift, quota errors, circuit-breaker events, and fatal crashes are consolidated into one email to `default_recipients`.
*   **Exit status:** Fatal configuration and runtime failures return a non-zero process exit code.

Alerts can only be sent after configuration and SMTP settings load
successfully. Failures that prevent Python from starting (for example, a
missing virtual environment, a stopped scheduler, or an offline host) require
external monitoring or a heartbeat service.

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
