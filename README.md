# YouTube Channel Monitor & AI Summarizer

This set of scripts monitors specified YouTube channels for new video uploads, automatically fetches transcripts, generates AI-powered summaries (executive, detailed bullet points, key quotes) using Google Gemini, saves the results locally, and sends email notifications to configurable recipient lists per channel.

## Features

*   **Multi-Channel Monitoring:** Tracks several YouTube channels simultaneously.
*   **Hourly Checks:** Designed to run periodically (e.g., every hour via cron/Task Scheduler).
*   **Automatic Transcripts:** Fetches available English transcripts (manual or generated).
*   **AI Summarization:** Uses Google Gemini (configurable model, e.g., 1.5 Pro) to create:
    *   Concise Executive Summary
    *   Detailed Bulleted Overview
    *   Extraction of Key Quotes/Data Points
*   **State Persistence:** Keeps track of processed videos in `processed_videos.json` to avoid redundant processing and API calls.
*   **Local Storage:** Saves generated summaries to text files in the `output/` directory.
*   **Customizable Email Notifications:**
    *   Sends cleanly formatted emails upon finding and summarizing a new video.
    *   Supports **different recipient lists for each monitored channel**.
    *   Includes video link and all generated summaries/quotes in the email body.
*   **Configuration Driven:** Uses `config.ini` for API keys, channel IDs, AI prompts, email settings, and recipient lists.
*   **Cross-Platform:** Setup (`setup.py`) and execution (`run.py`) scripts are designed for Windows, macOS, and Linux.
*   **Logging:** Records activities, successes, and errors to `monitor.log`.
*   **Virtual Environment:** Includes a setup script to create a dedicated Python virtual environment (`.venv`) and install dependencies.
*   **Scheduler-Friendly:** Provides a wrapper script (`run.py`) suitable for calling from cron (Linux/macOS) or Task Scheduler (Windows).

## Prerequisites

1.  **Python:** Python 3.7 or higher installed.
2.  **Pip:** Python's package installer (usually comes with Python).
3.  **Google Cloud Account & YouTube Data API Key:**
    *   Go to the [Google Cloud Console](https://console.cloud.google.com/).
    *   Create a project (or use an existing one).
    *   Enable the "YouTube Data API v3".
    *   Create an API Key under "Credentials". Secure this key!
    *   Add this key to `config.ini` (`youtube_api_key`).
4.  **Google AI Studio & Gemini API Key:**
    *   Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
    *   Create an API Key. Secure this key!
    *   Add this key to `config.ini` (`gemini_api_key`).
5.  **YouTube Channel IDs:** Know the IDs of the channels you want to monitor (e.g., `UCxxxxxxxxxxxxxxxxxxxxxx`). You can find these using online tools like [Comment Picker's Channel ID tool](https://commentpicker.com/youtube-channel-id.php).
6.  **Email Account (for Sending):** An email account (like Gmail or a custom domain) that can send emails via SMTP.
    *   You'll need the SMTP server address, port, username, and password.
    *   **Important (Gmail):** If using Gmail with 2-Factor Authentication (2FA), you'll likely need to generate an "App Password" to use in `config.ini` instead of your regular Gmail password.

## File Structure

## Setup & Deployment Instructions

1.  **Download/Extract Files:** Place all the provided files (`config.ini`, `requirements.txt`, `setup.py`, `monitor_youtube.py`, `run.py`, `README.md`) into a dedicated directory on your system.

2.  **Configure `config.ini`:**
    *   Open `config.ini` in a text editor.
    *   **[API_KEYS]**: Fill in your `youtube_api_key` and `gemini_api_key`.
    *   **[CHANNELS]**: List the YouTube Channel IDs you want to monitor, separated by commas.
    *   **[GEMINI]**: Review and customize the `model_name` and prompts if desired. Adjust `safety_settings` if needed.
    *   **[EMAIL]**: Enter your SMTP server details (`smtp_server`, `smtp_port`, `smtp_user`, `smtp_password`, `sender_email`). Remember to use an App Password for Gmail if using 2FA.
    *   **[EMAIL_RECIPIENTS_PER_CHANNEL]**:
        *   For each channel ID from the `[CHANNELS]` section that needs specific recipients, add a line like: `UCxxxxxxxxxxxxxxxxxxxxxx = recipient1@example.com, team-a@example.com`
        *   Optionally, set `default_recipients` for any channels listed in `[CHANNELS]` but *not* given specific recipients here. Leave blank or comment out if no default is needed.
    *   **[SETTINGS]**: Review file paths/names if needed (defaults should be fine). `max_results_per_channel=1` is usually sufficient for hourly checks.

3.  **Run Setup Script:**
    *   Open a terminal or command prompt.
    *   Navigate (`cd`) to the directory where you saved the files.
    *   Execute the setup script:
        ```bash
        python setup.py
        # or potentially: python3 setup.py
        ```
    *   This command will:
        *   Check your Python version.
        *   Create a virtual environment named `.venv`.
        *   Install all required Python packages from `requirements.txt` into `.venv`.
        *   Create the `output` directory if it doesn't exist.

4.  **Manual Test Run:**
    *   In the same terminal (after setup is complete), run the wrapper script:
        ```bash
        python run.py
        # or potentially: python3 run.py
        ```
    *   This activates the virtual environment and executes `monitor_youtube.py`. Check the console output and `monitor.log` for activity. Look in the `output` directory for any generated summaries and check if emails were sent (if new videos were found).

5.  **Schedule Regular Execution:**

    *   **Linux/macOS (using Cron):**
        *   Open your crontab for editing: `crontab -e`
        *   Add a line to run the script every hour (at minute 0). **Use absolute paths!**
            ```cron
            # Run YouTube Monitor every hour
            0 * * * * /usr/bin/python3 /path/to/your/project/run.py >> /path/to/your/project/cron.log 2>&1
            ```
        *   **Replace:**
            *   `/usr/bin/python3` with the result of `which python3` on your system.
            *   `/path/to/your/project/` with the full, absolute path to the directory containing `run.py`.
        *   The `>> ... cron.log 2>&1` part redirects cron's output/errors to a log file, useful for debugging the scheduler itself.

    *   **Windows (using Task Scheduler):**
        *   Open Task Scheduler.
        *   Click "Create Task" (not Basic Task, for more control).
        *   **General Tab:** Give it a name (e.g., "YouTube Monitor"). Choose "Run whether user is logged on or not" and potentially "Run with highest privileges" if needed (usually not).
        *   **Triggers Tab:** Click "New...". Select "Daily". Under "Advanced settings", check "Repeat task every" and choose "1 hour" for a duration of "Indefinitely". Ensure "Enabled" is checked. Click OK.
        *   **Actions Tab:** Click "New...".
            *   Action: `Start a program`
            *   Program/script: `C:\path\to\your\python.exe` (Browse to your *system's* Python executable, e.g., often in `C:\Users\YourUser\AppData\Local\Programs\Python\Python3X\python.exe` or `C:\Program Files\Python3X\python.exe`).
            *   Add arguments (optional): `C:\path\to\your\project\run.py` (Use the absolute path to `run.py`).
            *   **Start in (optional):** `C:\path\to\your\project\` ( **IMPORTANT:** Set this to the absolute path of the directory *containing* `run.py`. This ensures the script can find `config.ini` etc.). Click OK.
        *   **Conditions/Settings Tabs:** Adjust power settings, idle requirements, etc. as needed. Allow task to be run on demand. Stop the task if it runs longer than (e.g.) 1 hour.
        *   Click OK. You may be prompted for your user password.

## Output Files

*   **`monitor.log`:** Contains logs of script execution, checks performed, videos processed, summaries generated, email attempts, and any errors encountered. Check this file first for troubleshooting.
*   **`processed_videos.json`:** A simple JSON file storing a list of YouTube video IDs that have been successfully processed. This prevents reprocessing.
*   **`output/` directory:** Contains individual `.txt` files for each processed video, named like `VIDEO_ID_Channel_Name.txt`. Each file includes the channel/video info, executive summary, detailed summary, and key quotes.

## Troubleshooting

*   **API Key Errors:** Double-check the keys in `config.ini`. Ensure the correct APIs (YouTube Data API v3, Gemini API) are enabled in your Google Cloud/AI Studio projects. Check API quotas.
*   **SMTP Authentication Errors:** Verify your SMTP username and password in `config.ini`. If using Gmail with 2FA, ensure you generated and are using an App Password. Check if your email provider requires specific security settings (like enabling "less secure app access", though STARTTLS used here is preferred). Check firewall rules if connecting to an internal SMTP server.
*   **Module Not Found Errors:** Ensure you ran `python setup.py` successfully and are running the script via `python run.py` (which uses the virtual environment).
*   **Script Fails in Cron/Task Scheduler but Works Manually:** This is often due to path issues or environment variables. Ensure you used **absolute paths** in your scheduler configuration. For Task Scheduler, setting the **"Start in" directory** is crucial. Check the cron/scheduler logs (e.g., `cron.log` specified in the cron example).
*   **Permission Errors:** Ensure the user running the script (or the cron job/scheduled task) has permission to read the script files, write to the `output/` directory, `monitor.log`, and `processed_videos.json`.
*   **Gemini Errors/Blocks:** Check the `monitor.log` for specific error messages from the Gemini API (e.g., quota limits, content safety blocks). You might need to adjust prompts or safety settings in `config.ini`.

## License

Consider adding a license file (e.g., `LICENSE`). The MIT license is a common permissive choice.