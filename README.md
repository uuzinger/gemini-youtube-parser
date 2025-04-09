# YouTube Channel Monitor & Gemini Summarizer

This project provides a Python automation script designed to monitor specified YouTube channels for new video uploads. When a new video meeting certain criteria (like minimum duration) is detected, it fetches the transcript, generates summaries using the Google Gemini API, saves the summaries locally, and sends email notifications to configured recipients.

## Overview

The script performs the following actions:

1.  **Monitors Multiple Channels:** Checks a list of specified YouTube channel IDs every hour (when run via cron).
2.  **Detects New Videos:** Identifies videos published within the last hour (approximately).
3.  **Fetches Video Details:** Retrieves video duration.
4.  **Filters by Duration:** Optionally ignores videos shorter than a configurable minimum length (in minutes).
5.  **Retrieves Transcripts:** Fetches the auto-generated or manual transcript for eligible new videos.
6.  **Generates Summaries via Gemini:** Uses the Google Gemini API (e.g., Gemini 1.5 Pro) to create:
    *   A short executive summary.
    *   A detailed bulleted summary.
    *   A list of key quotes or data points.
7.  **Saves Locally:** Stores the generated summaries and video metadata in text files within a local `output/` directory. It also maintains a `processed_videos.json` file to track which videos have already been handled.
8.  **Sends Email Notifications:** Dispatches cleanly formatted emails containing the video link, duration, and generated summaries.
9.  **Channel-Specific Recipients:** Allows configuring different email recipient lists for different YouTube channels, with a fallback default list.
10. **Cron Job Ready:** Includes a wrapper script (`run.sh`) designed to be called by a cron job, ensuring the correct virtual environment is activated.
11. **Configurable:** Uses an INI configuration file (`config.ini`) for API keys, channel lists, email settings, Gemini prompts, and other parameters.
12. **Logging:** Records script activities, successes, and errors to `monitor.log`.

## Features

*   Automated hourly monitoring of multiple YouTube channels.
*   Leverages Google Gemini for AI-powered summarization.
*   Customizable summary prompts via configuration.
*   Configurable minimum video duration filter.
*   Channel-specific email recipient routing.
*   Local archival of summaries and processed video status.
*   Designed for unattended execution via cron.
*   Uses a Python virtual environment for dependency management.
*   Detailed logging for diagnostics.

## Prerequisites

Before you begin, ensure you have the following:

1.  **Python 3:** Python 3.8 or higher recommended.
2.  **pip:** Python package installer (usually comes with Python 3).
3.  **Google Cloud Account & YouTube Data API v3 Key:**
    *   Go to the [Google Cloud Console](https://console.cloud.google.com/).
    *   Create a project (or use an existing one).
    *   Enable the "YouTube Data API v3".
    *   Create an API key under "Credentials".
    *   **Important:** Restrict your API key (e.g., to specific IP addresses or HTTP referrers) for security if possible, although for server-side scripts IP restrictions might be most relevant. Note that this API has quotas.
4.  **Google AI Studio Account & Gemini API Key:**
    *   Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
    *   Sign in with your Google account.
    *   Create an API key. Note that Gemini API usage may incur costs depending on the model and usage volume.
5.  **Email Account for Sending Notifications:**
    *   An email account (e.g., Gmail, Outlook) capable of sending via SMTP.
    *   **Crucially:** If using Gmail/Google Workspace with 2-Factor Authentication (2FA), you **must** generate an "App Password" specifically for this script. Do *not* use your regular account password in the config file. See [Google's App Password documentation](https://support.google.com/accounts/answer/185833).
    *   Know your SMTP server details (hostname, port).

## Setup Instructions

1.  **Clone or Download:** Get the script files (`monitor_youtube.py`, `config.ini`, `requirements.txt`, `setup.sh`, `run.sh`) and place them in a dedicated directory on your server or machine where you intend to run the monitor.

2.  **Navigate to Directory:** Open a terminal or command prompt and change to the directory containing the downloaded files.
    ```bash
    cd /path/to/your/script/directory
    ```

3.  **Make Setup Script Executable:**
    ```bash
    chmod +x setup.sh
    ```

4.  **Run Setup Script:** Execute the setup script. This will create a Python virtual environment named `.venv` in the current directory and install all required dependencies from `requirements.txt`.
    ```bash
    ./setup.sh
    ```
    Follow any prompts or address any errors reported by the script (e.g., missing Python 3).

5.  **Configure `config.ini`:** This is the most crucial step. Open the `config.ini` file in a text editor **after** running `setup.sh`. Carefully fill in the following details:

    *   **`[API_KEYS]`**:
        *   `youtube_api_key`: Your YouTube Data API v3 key.
        *   `gemini_api_key`: Your Google Gemini API key.
    *   **`[CHANNELS]`**:
        *   `channel_ids`: A comma-separated list of the YouTube Channel IDs you want to monitor (e.g., `UCxxxxxxxxxxxxxx,UCyyyyyyyyyyyyyy`). Ensure the case is correct (starts with `UC`).
    *   **`[GEMINI]`**:
        *   `model_name`: The Gemini model to use (e.g., `gemini-1.5-pro-latest`).
        *   `prompt_...`: Review and customize the prompts if desired. **IMPORTANT:** Ensure these multi-line prompts use the **indented format** as shown in the template (no triple quotes).
        *   `safety_settings` (Optional): Configure content safety thresholds if needed.
    *   **`[EMAIL]`**:
        *   `smtp_server`: Your email provider's SMTP server address (e.g., `smtp.gmail.com`).
        *   `smtp_port`: The SMTP port (usually `587` for TLS).
        *   `smtp_user`: Your full email address for sending.
        *   `smtp_password`: Your email password or, preferably, an **App Password** (see Prerequisites).
        *   `sender_email`: The email address that should appear in the "From" field.
    *   **`[CHANNEL_RECIPIENTS]`**:
        *   Use the exact YouTube Channel ID (case-sensitive) as the key (e.g., `UCxxxxxxxxxxxxxx = person1@example.com,team@example.com`).
        *   Provide a comma-separated list of recipient emails for each specific channel.
        *   `default_recipients`: A comma-separated list of emails to use for any channel in `[CHANNELS]` that doesn't have its own specific entry here. It's highly recommended to set a default.
    *   **`[SETTINGS]`**:
        *   Review file paths (`processed_videos_file`, `log_file`, `output_dir`). Relative paths are based on the script's directory.
        *   `max_results_per_channel`: Usually `1` is sufficient for hourly checks.
        *   `min_video_duration_minutes`: Set the minimum video length in minutes to process (e.g., `5`). Set to `0` to disable this filter and process all videos.

6.  **Make Run Script Executable:**
    ```bash
    chmod +x run.sh
    ```

## Usage

### Manual Execution

You can run the script manually to test it or process videos immediately:

```bash
./run.sh
