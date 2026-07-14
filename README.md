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
10. **Weekly Digest (optional):** A separate script (`weekly_summary.py`) can be scheduled once per week to email each recipient one consolidated digest of every video posted on their channels in the last 7 days, in chronological order.

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
│   ├── llm.py                 # LLM provider interface and factory
│   ├── openai_compatible.py   # Remote llama.cpp service
│   ├── email.py               # Async email service
│   └── storage.py             # Async file storage
├── utils/                     # Utility functions
│   ├── logging.py             # Structured logging setup
│   └── helpers.py             # String/date utilities
├── main.py                    # Async entry point (daily monitor)
├── weekly_summary.py          # Async entry point (weekly digest)
├── run.py                     # Windows wrapper
├── run.sh                     # Linux/macOS wrapper
├── setup.py                   # Setup script
├── config.ini                 # Configuration file (daily monitor)
├── summary.ini                # Configuration file (weekly digest)
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

    *   **`[API_KEYS]`**: `youtube_api_key`; `gemini_api_key` is required only when using Gemini.
    *   **`[LLM]`**: Provider selection and remote llama.cpp connection settings.
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

5.  **Choose and configure an LLM provider:** Existing installations continue
    using Gemini when `[LLM]` is absent. To use a remote llama.cpp server, add:
    ```ini
    [LLM]
    provider = llama_cpp
    host = 192.168.1.50
    port = 8080
    use_tls = False
    api_key = YOUR_LLAMACPP_API_KEY
    model_name = qwen3.6-a35b
    temperature = 0.7
    executive_max_output_tokens = 1024
    detailed_max_output_tokens = 8192
    quotes_max_output_tokens = 2048
    request_timeout = 300
    context_tokens = 262144
    ```
    Replace the host, model label, and API key with the values used by your
    server. Do not include `http://`, a port, or `/v1` in `host`.

6.  **If continuing with Gemini, review the configured model:** Compare
    `[GEMINI] model_name` with the models available to your API key. The
    application checks the live Gemini model list on every run. If the
    configured model is unavailable, the alert includes a suggested
    replacement and up to 20 available text-generation models. It never
    changes `config.ini` automatically.

7.  **Review request-rate settings:** Set `gemini_rpm` and `gemini_rpd` to
    values appropriate for your current Google plan. Each processed video
    normally makes three Gemini generation requests, and startup model
    validation makes an additional request. Google does not expose your
    account's quota limits through the model-list API, so quota changes are
    detected and reported when Gemini returns HTTP 429. These legacy setting
    names also limit llama.cpp requests; their high defaults generally require
    no adjustment for a local server.

8.  **Run once manually before re-enabling the scheduler:**
    ```bash
    ./run.sh
    echo "exit code: $?"
    ```
    Review `logs/monitor.log` and confirm that normal summary email delivery
    still works. Fatal runtime errors now return a non-zero exit code.

9.  **Optional developer verification:**
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

## Weekly Digest

`weekly_summary.py` is a separate, independent script intended to run once a
week (for example, Monday morning) via cron. Unlike the daily monitor, it does
not track processed/failed video state and does not send a per-video email;
instead it looks back over a configurable window and sends each recipient one
combined email covering every video posted across their channels.

### Setup

1.  **Configure `summary.ini`:**
    ```bash
    cp summary.ini.example summary.ini
    ```
    Edit `summary.ini` and fill in:
    *   **`[CHANNELS]`**: The channels to include in the digest (can differ
        from `config.ini`'s `[CHANNELS]`).
    *   **`[CHANNEL_RECIPIENTS]`**: Which email address(es) receive each
        channel's videos, plus an optional `default_recipients` fallback for
        channels with no specific entry. All channels routed to the same
        email address are combined into that recipient's single weekly email.
    *   **`[SETTINGS]`**: `window_days` (default 7), `max_results_per_channel`,
        `subject_prefix`, `log_file`, `log_level`.

    `summary.ini` is ignored by Git, like `config.ini`.

2.  **Credentials come from `config.ini`:** `weekly_summary.py` loads
    `config.ini` for the YouTube API key, the configured LLM provider, prompts
    (`prompt_executive_summary` and `prompt_detailed_summary`), per-summary
    token limits, and SMTP settings. There is nothing else to configure for
    credentials.

3.  **Run it manually to test:**
    ```bash
    .venv/bin/python weekly_summary.py
    ```
    Set `dry_run = True` in `config.ini`'s `[SETTINGS]` first to log the
    digest content instead of sending it.

4.  **Schedule with cron (Linux/macOS):**
    ```cron
    # Every Monday at 7:00 AM
    0 7 * * 1 cd /path/to/gemini-youtube-parser && .venv/bin/python weekly_summary.py >> logs/weekly_cron.log 2>&1
    ```

Failures (missing transcripts, LLM errors, a fatal crash) are collected the
same way as the daily monitor and sent as one administrative alert to
`config.ini`'s `default_recipients`; they do not stop the digest from being
sent to unaffected recipients.

## LLM Providers

Gemini remains the default provider for backward compatibility. Set
`[LLM] provider = llama_cpp` to use a remote llama.cpp server through its
OpenAI-compatible API.

### Remote llama.cpp server

Start `llama-server` on the model host with an API key and the desired context
window:

```bash
export LLAMACPP_API_KEY="replace-with-a-long-random-value"

llama-server \
  --model /path/to/qwen3.6-a35b.gguf \
  --ctx-size 262144 \
  --host 0.0.0.0 \
  --port 8080 \
  --api-key "$LLAMACPP_API_KEY"
```

Restrict port 8080 at the host firewall so only the monitor machine can reach
it. The client configuration is:

```ini
[LLM]
provider = llama_cpp
host = 192.168.1.50
port = 8080
use_tls = False
api_key = replace-with-the-same-api-key
model_name = qwen3.6-a35b
temperature = 0.7
executive_max_output_tokens = 1024
detailed_max_output_tokens = 8192
quotes_max_output_tokens = 2048
request_timeout = 300
context_tokens = 262144
```

`model_name` is sent in the OpenAI-compatible request. llama.cpp normally uses
the model already loaded by the server, so a mismatch with `/v1/models` is
logged as a warning rather than treated as a failure.

`context_tokens` must match the server's `--ctx-size`. The client estimates
prompt size before sending and reports likely overflow, but this is an
approximation rather than model-specific tokenization.

The three output limits apply independently to executive summaries, detailed
summaries, and quote extraction. Increasing a limit does not force the model
to use all available tokens. If Gemini or llama.cpp reports that generation
stopped because a limit was reached, the run records a failed summary and
sends an administrative alert instead of silently emailing truncated output.
The previous `max_output_tokens` setting is still accepted and applies its
value to all three summary types.

With `use_tls = False`, the API key and full video transcripts travel as
unencrypted HTTP. Use this only on a trusted, access-controlled network. For
traffic crossing an untrusted network, terminate HTTPS at llama.cpp or a
reverse proxy and set `use_tls = True`. TLS certificate verification remains
enabled.

At startup the application calls `/v1/models` to verify connectivity and
authentication. Connection failures, timeouts, HTTP errors, context overflows,
and circuit-breaker events flow into the same consolidated administrative
alert used for Gemini failures.

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

### `summary.ini` `[SETTINGS]`

| Setting | Default | Description |
|---------|---------|-------------|
| `window_days` | 7 | How many days back to look for new videos per channel |
| `max_results_per_channel` | 25 | Safety cap on videos considered per channel |
| `subject_prefix` | `Weekly YouTube Digest` | Prefix for the weekly email subject |
| `log_file` | `logs/weekly.log` | Log file path for `weekly_summary.py` |
| `log_level` | `INFO` | Logging level for `weekly_summary.py` |

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
| `openai` | Async client for remote llama.cpp/OpenAI-compatible servers |
| `google-api-python-client` | YouTube Data API client |
| `youtube-transcript-api` | YouTube transcript fetching |
| `aiosmtplib` | Async SMTP for email |
| `tenacity` | Retry logic with exponential backoff |
| `aiofiles` | Async file I/O |
| `markdown` | HTML rendering for email |
