# -*- coding: utf-8 -*-
# vim: set fileencoding=utf-8 :
import os
import sys
import configparser
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
import time
import re
import html
from xml.etree import ElementTree

import google.generativeai as genai
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import (
    YouTubeTranscriptApi,
    TranscriptsDisabled,
    NoTranscriptFound,
)
import markdown

# --- Configuration Loading ---
CONFIG_FILE = "config.ini"
config = configparser.ConfigParser()
config.optionxform = str

if not os.path.exists(CONFIG_FILE):
    print(
        f"ERROR: Configuration file '{CONFIG_FILE}' not found. Please create it based on the template.",
        file=sys.stderr,
    )
    sys.exit(1)

actual_path = os.path.abspath(CONFIG_FILE)
print(f"--- Attempting to read config file: {actual_path} ---", file=sys.stderr)

channel_recipients = {}
default_recipients = []
MIN_DURATION_MINUTES = 0

try:
    config.read(CONFIG_FILE)
    YOUTUBE_API_KEY = config.get("API_KEYS", "youtube_api_key", fallback=None)
    GEMINI_API_KEY = config.get("API_KEYS", "gemini_api_key", fallback=None)
    channel_ids_raw = config.get("CHANNELS", "channel_ids", fallback=None)
    CHANNEL_IDS = (
        [cid.strip() for cid in channel_ids_raw.split(",") if cid.strip()]
        if channel_ids_raw
        else []
    )
    GEMINI_MODEL = config.get("GEMINI", "model_name", fallback="gemini-1.5-pro-latest")
    PROMPT_EXEC_SUMMARY = config.get(
        "GEMINI", "prompt_executive_summary", fallback="Executive summary prompt missing."
    )
    PROMPT_DETAILED_SUMMARY = config.get(
        "GEMINI", "prompt_detailed_summary", fallback="Detailed summary prompt missing."
    )
    PROMPT_KEY_QUOTES = config.get(
        "GEMINI", "prompt_key_quotes", fallback="Key quotes prompt missing."
    )
    SAFETY_SETTINGS_RAW = config.get("GEMINI", "safety_settings", fallback=None)
    SAFETY_SETTINGS = None
    if SAFETY_SETTINGS_RAW:
        try:
            settings_dict = {}
            for item in SAFETY_SETTINGS_RAW.split(","):
                if ":" in item:
                    key, value = item.strip().split(":", 1)
                    settings_dict[key.strip()] = value.strip()
            valid_categories = [
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            ]
            SAFETY_SETTINGS = {
                k: v
                for k, v in settings_dict.items()
                if k in valid_categories or k.startswith("HARM_CATEGORY_")
            }
            if len(SAFETY_SETTINGS) != len(settings_dict):
                ignored_keys = set(settings_dict.keys()) - set(SAFETY_SETTINGS.keys())
                print(
                    f"Warning: Ignoring invalid safety settings: {', '.join(ignored_keys)}.",
                    file=sys.stderr,
                )
        except Exception as e:
            print(
                f"Warning: Could not parse safety_settings: {e}. Using defaults.",
                file=sys.stderr,
            )
            SAFETY_SETTINGS = None

    SMTP_SERVER = config.get("EMAIL", "smtp_server", fallback=None)
    SMTP_PORT = config.getint("EMAIL", "smtp_port", fallback=587)
    SMTP_USER = config.get("EMAIL", "smtp_user", fallback=None)
    SMTP_PASSWORD = config.get("EMAIL", "smtp_password", fallback=None)
    SENDER_EMAIL = config.get("EMAIL", "sender_email", fallback=None)

    if config.has_section("CHANNEL_RECIPIENTS"):
        for key, value in config.items("CHANNEL_RECIPIENTS"):
            emails = [email.strip() for email in value.split(",") if email.strip()]
            if emails:
                if key.lower() == "default_recipients":
                    default_recipients = emails
                elif key.startswith("UC") and len(key) == 24:
                    channel_recipients[key] = emails
                else:
                    print(
                        f"WARNING: Invalid key in [CHANNEL_RECIPIENTS]: {key}",
                        file=sys.stderr,
                    )

    PROCESSED_VIDEOS_FILE = config.get(
        "SETTINGS", "processed_videos_file", fallback="processed_videos.json"
    )
    LOG_FILE = config.get("SETTINGS", "log_file", fallback="monitor.log")
    OUTPUT_DIR = config.get("SETTINGS", "output_dir", fallback="output")
    MAX_RESULTS_PER_CHANNEL = config.getint(
        "SETTINGS", "max_results_per_channel", fallback=1
    )
    MIN_DURATION_MINUTES = config.getint(
        "SETTINGS", "min_video_duration_minutes", fallback=0
    )

    errors = []
    if not YOUTUBE_API_KEY or YOUTUBE_API_KEY == "YOUR_YOUTUBE_DATA_API_V3_KEY":
        errors.append("youtube_api_key")
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY":
        errors.append("gemini_api_key")
    if not CHANNEL_IDS:
        errors.append("channel_ids")
    if not SMTP_SERVER or not SMTP_USER or not SMTP_PASSWORD or not SENDER_EMAIL:
        errors.append("Email settings")
    if not default_recipients and not channel_recipients:
        errors.append("Email recipients")

    if errors:
        print(f"--- CONFIGURATION ERRORS: Missing {', '.join(errors)} ---", file=sys.stderr)
        sys.exit(1)

except Exception as e:
    print(f"FATAL: Error loading configuration: {e}", file=sys.stderr)
    sys.exit(1)

# --- Logging Setup ---
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir, exist_ok=True)

# CRITICAL FIX for UnicodeEncodeError on some systems
# Use 'errors="replace"' to prevent crashes when logging characters the console can't display
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout), # StreamHandler to console
    ],
    encoding="utf-8",
    errors="replace", # Tell logger to replace problematic characters
)

processed_video_ids = set()


def sanitize_filename(filename):
    filename = filename.replace("\ufffd", "_")
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename)
    sanitized = re.sub(r"\s+", "_", sanitized)
    return sanitized[:150].strip("_")


def load_processed_videos():
    global processed_video_ids
    if not os.path.exists(PROCESSED_VIDEOS_FILE):
        return
    try:
        with open(PROCESSED_VIDEOS_FILE, "r", encoding="utf-8") as f:
            processed_video_ids = set(json.load(f))
        logging.info(f"Loaded {len(processed_video_ids)} processed video IDs.")
    except (json.JSONDecodeError, FileNotFoundError):
        logging.error("Could not load or parse processed_videos.json. Starting fresh.")
        processed_video_ids = set()


def save_processed_videos():
    with open(PROCESSED_VIDEOS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(processed_video_ids), f, indent=4)


def parse_iso8601_duration(duration_string):
    if not duration_string or not duration_string.startswith("PT"):
        return 0
    duration_string = duration_string[2:]
    total_seconds = 0
    parts = re.findall(r"(\d+)([HMS])", duration_string)
    for value, unit in parts:
        value = int(value)
        if unit == "H":
            total_seconds += value * 3600
        elif unit == "M":
            total_seconds += value * 60
        elif unit == "S":
            total_seconds += value
    return total_seconds


def format_duration_seconds(seconds):
    if seconds is None:
        return "N/A"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"


def get_video_details(youtube, video_id):
    try:
        response = youtube.videos().list(part="contentDetails", id=video_id).execute()
        return response["items"][0]["contentDetails"]["duration"]
    except (HttpError, IndexError, KeyError) as e:
        logging.error(f"Could not get details for video {video_id}: {e}")
        return None


def get_latest_videos(youtube, channel_id, max_results):
    try:
        response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
        uploads_id = response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        response = youtube.playlistItems().list(
            part="snippet,contentDetails", playlistId=uploads_id, maxResults=max_results
        ).execute()
        videos = []
        recent_threshold = datetime.now(timezone.utc) - timedelta(hours=25)
        for item in response.get("items", []):
            published_at = datetime.fromisoformat(
                item["snippet"]["publishedAt"].replace("Z", "+00:00")
            )
            if published_at >= recent_threshold:
                videos.append(
                    {
                        "id": item["contentDetails"]["videoId"],
                        "title": item["snippet"]["title"],
                        "channel_id": channel_id,
                    }
                )
        videos.sort(key=lambda x: x.get("published_at", datetime.min), reverse=True)
        return videos
    except (HttpError, IndexError, KeyError) as e:
        logging.error(f"Could not get latest videos for {channel_id}: {e}")
        return []


def get_transcript(video_id):
    """
    Fetches the transcript for a video, with retries for parsing errors.
    """
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript_to_fetch = transcript_list.find_transcript(["en", "en-US", "en-GB"])

        max_retries = 3
        retry_delay_seconds = 30
        for attempt in range(max_retries):
            try:
                transcript_text = " ".join(
                    [item["text"] for item in transcript_to_fetch.fetch()]
                )
                logging.info(f"Successfully fetched transcript for video ID: {video_id}")
                return transcript_text
            except ElementTree.ParseError:
                logging.warning(
                    f"Attempt {attempt + 1}/{max_retries} to parse transcript for {video_id} failed (likely not ready yet)."
                )
                if attempt < max_retries - 1:
                    logging.info(f"Retrying in {retry_delay_seconds} seconds...")
                    time.sleep(retry_delay_seconds)
                else:
                    logging.error(
                        f"All {max_retries} attempts failed for video {video_id}."
                    )
                    return None
    except (TranscriptsDisabled, NoTranscriptFound):
        logging.warning(f"No English transcript found or disabled for {video_id}.")
        return None
    except Exception as e:
        logging.error(f"Error fetching transcript for {video_id}: {e}")
        return None


def generate_summary_with_gemini(transcript, prompt):
    if not transcript or not prompt:
        return "Error: Missing transcript or prompt."
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        full_prompt = prompt.format(transcript=transcript)

        for attempt in range(3):
            try:
                response = model.generate_content(
                    full_prompt,
                    safety_settings=SAFETY_SETTINGS,
                    generation_config={"temperature": 0.7},
                )
                return response.text.strip()
            except Exception as e:
                logging.error(f"Gemini API call attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    time.sleep(10)
        return "Error: Gemini API call failed after 3 attempts."
    except Exception as e:
        logging.error(f"General error in generate_summary: {e}", exc_info=True)
        return "Error: Failed to generate summary due to a system error."


def save_summary_local(video_id, title, duration, exec_summary, detailed, quotes):
    try:
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)
        filename = os.path.join(OUTPUT_DIR, f"{video_id}_{sanitize_filename(title)}.txt")
        content = (
            f"Video Title: {title}\nVideo ID: {video_id}\n"
            f"URL: https://www.youtube.com/watch?v={video_id}\nDuration: {duration}\n\n"
            f"--- Executive Summary ---\n{exec_summary}\n\n"
            f"--- Detailed Summary ---\n{detailed}\n\n"
            f"--- Key Quotes ---\n{quotes}\n"
        )
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        logging.info(f"Saved summary to {filename}")
    except Exception as e:
        logging.error(f"Failed to save summary for {video_id}: {e}")


def send_email_notification(
    channel_name, video, duration, exec_summary, detailed, quotes
):
    recipients = channel_recipients.get(video["channel_id"], default_recipients)
    if not recipients:
        logging.warning(f"No recipients for channel {video['channel_id']}. Skipping email.")
        return

    subject = f"New YouTube Summary: [{channel_name}] {video['title']}"
    exec_html = markdown.markdown(exec_summary)
    detailed_html = markdown.markdown(detailed)
    quotes_html = markdown.markdown(quotes)

    body_html = f"""
    <html><body>
        <p>A new video has been posted on the '{channel_name}' channel:</p>
        <p>
            <b>Title:</b> {html.escape(video['title'])}<br>
            <b>Duration:</b> {duration}<br>
            <b>Link:</b> <a href="https://www.youtube.com/watch?v={video['id']}">https://www.youtube.com/watch?v={video['id']}</a>
        </p><hr>
        <h2>Executive Summary</h2><div>{exec_html}</div><hr>
        <h2>Detailed Summary</h2><div>{detailed_html}</div><hr>
        <h2>Key Quotes</h2><div>{quotes_html}</div>
    </body></html>
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SENDER_EMAIL, recipients, msg.as_string())
        logging.info(f"Successfully sent email for {video['id']}")
    except Exception as e:
        logging.error(f"Failed to send email for {video['id']}: {e}")


def get_channel_name(youtube, channel_id):
    try:
        response = youtube.channels().list(part="snippet", id=channel_id).execute()
        return response["items"][0]["snippet"]["title"]
    except (HttpError, IndexError, KeyError) as e:
        logging.error(f"Error fetching channel name for {channel_id}: {e}")
        return channel_id


def main():
    """Main execution function."""
    start_time = time.time()
    processed_count = 0
    load_processed_videos()

    try:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY, cache_discovery=False)
    except Exception as e:
        logging.fatal(f"Failed to build YouTube API client: {e}")
        sys.exit(1)

    all_channel_names = {cid: get_channel_name(youtube, cid) for cid in CHANNEL_IDS}

    for channel_id, channel_name in all_channel_names.items():
        logging.info(f"--- Checking Channel: {channel_name} ({channel_id}) ---")
        latest_videos = get_latest_videos(youtube, channel_id, MAX_RESULTS_PER_CHANNEL + 5)

        for video in latest_videos:
            video_id = video["id"]
            if video_id in processed_video_ids:
                continue

            logging.info(f"Processing new video: '{video['title']}' (ID: {video_id})")
            processed_video_ids.add(video_id) # Mark as processed immediately
            processed_count += 1
            
            duration_iso = get_video_details(youtube, video_id)
            duration_s = parse_iso8601_duration(duration_iso)

            if MIN_DURATION_MINUTES > 0 and duration_s < (MIN_DURATION_MINUTES * 60):
                logging.info(f"Skipping short video: {video_id}")
                save_processed_videos()
                continue
            
            transcript = get_transcript(video_id)
            if transcript:
                exec_summary = generate_summary_with_gemini(transcript, PROMPT_EXEC_SUMMARY)
                time.sleep(1)
                detailed_summary = generate_summary_with_gemini(transcript, PROMPT_DETAILED_SUMMARY)
                time.sleep(1)
                key_quotes = generate_summary_with_gemini(transcript, PROMPT_KEY_QUOTES)

                duration_str = format_duration_seconds(duration_s)
                save_summary_local(video_id, video['title'], duration_str, exec_summary, detailed_summary, key_quotes)

                is_error = any(s.startswith("Error:") for s in [exec_summary, detailed_summary, key_quotes] if s)
                if not is_error:
                    send_email_notification(channel_name, video, duration_str, exec_summary, detailed_summary, key_quotes)
            else:
                logging.warning(f"No transcript for {video_id}, cannot process further.")

            save_processed_videos()
            time.sleep(5)

    logging.info(
        f"--- Script Finished. Processed {processed_count} new videos in {time.time() - start_time:.2f} seconds. ---"
    )


if __name__ == "__main__":
    main()
