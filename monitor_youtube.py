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
import re # For cleaning filenames and parsing duration

# Third-party libraries (install via requirements.txt)
import google.generativeai as genai
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

# --- Configuration Loading ---
CONFIG_FILE = 'config.ini'
config = configparser.ConfigParser()
# Preserve the case of keys read from the config file
config.optionxform = str

if not os.path.exists(CONFIG_FILE):
    print(f"ERROR: Configuration file '{CONFIG_FILE}' not found. Please create it based on the template.")
    sys.exit(1)

# Add Debug Print for Config File Path
actual_path = os.path.abspath(CONFIG_FILE)
print(f"--- Attempting to read config file: {actual_path} ---", file=sys.stderr)

# Global Dictionaries / Lists
channel_recipients = {}
default_recipients = []

# --- Global Setting Variables ---
MIN_DURATION_MINUTES = 0 # Default value

try:
    # Use indented multi-line format for prompts in config.ini
    config.read(CONFIG_FILE)

    # API Keys
    YOUTUBE_API_KEY = config.get('API_KEYS', 'youtube_api_key', fallback=None)
    GEMINI_API_KEY = config.get('API_KEYS', 'gemini_api_key', fallback=None)

    # Channels to Monitor
    channel_ids_raw = config.get('CHANNELS', 'channel_ids', fallback=None)
    CHANNEL_IDS = [cid.strip() for cid in channel_ids_raw.split(',') if cid.strip()] if channel_ids_raw else []

    # Gemini Settings
    GEMINI_MODEL = config.get('GEMINI', 'model_name', fallback='gemini-1.5-pro-latest')
    PROMPT_EXEC_SUMMARY = config.get('GEMINI', 'prompt_executive_summary', fallback="Executive summary prompt missing.")
    PROMPT_DETAILED_SUMMARY = config.get('GEMINI', 'prompt_detailed_summary', fallback="Detailed summary prompt missing.")
    PROMPT_KEY_QUOTES = config.get('GEMINI', 'prompt_key_quotes', fallback="Key quotes prompt missing.")

    SAFETY_SETTINGS_RAW = config.get('GEMINI', 'safety_settings', fallback=None)
    SAFETY_SETTINGS = None
    if SAFETY_SETTINGS_RAW:
        try:
            SAFETY_SETTINGS = {
                item.split(':')[0].strip(): item.split(':')[1].strip()
                for item in SAFETY_SETTINGS_RAW.split(',') if ':' in item
            }
        except Exception as e:
            print(f"Warning: Could not parse safety_settings: {e}. Using default safety settings.")
            SAFETY_SETTINGS = None

    # Email SMTP Settings
    SMTP_SERVER = config.get('EMAIL', 'smtp_server', fallback=None)
    SMTP_PORT = config.getint('EMAIL', 'smtp_port', fallback=587)
    SMTP_USER = config.get('EMAIL', 'smtp_user', fallback=None)
    SMTP_PASSWORD = config.get('EMAIL', 'smtp_password', fallback=None)
    SENDER_EMAIL = config.get('EMAIL', 'sender_email', fallback=None)

    # Load Channel-Specific and Default Recipients
    if config.has_section('CHANNEL_RECIPIENTS'):
        for channel_id_key, emails_raw in config.items('CHANNEL_RECIPIENTS'):
            emails = [email.strip() for email in emails_raw.split(',') if email.strip()]
            if emails:
                if channel_id_key == 'default_recipients':
                    default_recipients = emails
                    print(f"INFO: Loaded default recipients: {', '.join(default_recipients)}")
                else:
                    if channel_id_key.startswith("UC") and len(channel_id_key) == 24:
                         channel_recipients[channel_id_key] = emails
                         print(f"INFO: Loaded recipients for channel {repr(channel_id_key)}: {', '.join(emails)}")
                    else:
                         print(f"WARNING: Ignoring invalid key in [CHANNEL_RECIPIENTS]: {repr(channel_id_key)}. Keys should be YouTube Channel IDs (starting with UC) or 'default_recipients'.")
    else:
        print("WARNING: Configuration section '[CHANNEL_RECIPIENTS]' is missing. Cannot determine email recipients.")

    # Script Settings
    PROCESSED_VIDEOS_FILE = config.get('SETTINGS', 'processed_videos_file', fallback='processed_videos.json')
    LOG_FILE = config.get('SETTINGS', 'log_file', fallback='monitor.log')
    OUTPUT_DIR = config.get('SETTINGS', 'output_dir', fallback='output')
    MAX_RESULTS_PER_CHANNEL = config.getint('SETTINGS', 'max_results_per_channel', fallback=1)
    MIN_DURATION_MINUTES = config.getint('SETTINGS', 'min_video_duration_minutes', fallback=0)
    print(f"INFO: Minimum video duration set to: {MIN_DURATION_MINUTES} minutes (0 means no minimum).")


    # Validate Essential Configuration
    errors = []
    # ... (error checking code remains the same) ...
    if not YOUTUBE_API_KEY or YOUTUBE_API_KEY == 'YOUR_YOUTUBE_DATA_API_V3_KEY': errors.append("Missing or placeholder 'youtube_api_key' in [API_KEYS]")
    if not GEMINI_API_KEY or GEMINI_API_KEY == 'YOUR_GEMINI_API_KEY': errors.append("Missing or placeholder 'gemini_api_key' in [API_KEYS]")
    if not CHANNEL_IDS: errors.append("Missing or empty 'channel_ids' in [CHANNELS]")
    if not SMTP_SERVER: errors.append("Missing 'smtp_server' in [EMAIL]")
    if not SMTP_USER: errors.append("Missing 'smtp_user' in [EMAIL]")
    if not SMTP_PASSWORD or SMTP_PASSWORD == 'YOUR_EMAIL_PASSWORD_OR_APP_PASSWORD': errors.append("Missing or placeholder 'smtp_password' in [EMAIL]")
    if not SENDER_EMAIL: errors.append("Missing 'sender_email' in [EMAIL]")
    if not default_recipients and not channel_recipients: errors.append("No email recipients configured. Please define 'default_recipients' or specific channel recipients in the '[CHANNEL_RECIPIENTS]' section.")
    elif not default_recipients: print("WARNING: No 'default_recipients' configured in [CHANNEL_RECIPIENTS]. Emails will only be sent for channels with specific recipient lists.")

    if errors:
        print("--- CONFIGURATION ERRORS ---")
        for error in errors:
            print(f"- {error}")
        print(f"Please check your '{CONFIG_FILE}' file.")
        sys.exit(1)

except configparser.ParsingError as e:
    print(f"ERROR: Failed to parse '{CONFIG_FILE}'. Check syntax, especially multi-line values (use indentation).")
    print(f"Parser errors:\n{e}")
    sys.exit(1)
except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as e:
    print(f"ERROR reading configuration file '{CONFIG_FILE}': {e}")
    sys.exit(1)
except Exception as e:
    print(f"An unexpected error occurred during configuration loading: {e}")
    sys.exit(1)

# Logging Setup
# ... (logging setup remains the same) ...
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir): os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()])


# Global Variables
processed_video_ids = set()

# Helper Functions
# ... (sanitize_filename, load/save_processed_videos remain the same) ...
# ... (parse_iso8601_duration, format_duration_seconds remain the same) ...
# ... (get_video_details, get_latest_videos remain the same) ...
# ... (get_transcript, generate_summary_with_gemini remain the same) ...
# ... (save_summary_local, send_email_notification remain the same) ...

def sanitize_filename(filename):
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename)
    sanitized = re.sub(r'\s+', '_', sanitized)
    return sanitized[:150]

def load_processed_videos():
    global processed_video_ids
    if not os.path.exists(PROCESSED_VIDEOS_FILE):
        logging.info(f"'{PROCESSED_VIDEOS_FILE}' not found. Starting with an empty set.")
        processed_video_ids = set()
        return
    try:
        with open(PROCESSED_VIDEOS_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content:
                processed_video_ids = set()
                logging.info(f"'{PROCESSED_VIDEOS_FILE}' is empty. Starting with an empty set.")
            else:
                processed_video_ids = set(json.loads(content))
                logging.info(f"Loaded {len(processed_video_ids)} processed video IDs from {PROCESSED_VIDEOS_FILE}")
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {PROCESSED_VIDEOS_FILE}. Starting fresh.", exc_info=True)
        processed_video_ids = set()
    except Exception as e:
        logging.error(f"Error loading processed videos file: {e}", exc_info=True)
        processed_video_ids = set()

def save_processed_videos():
    try:
        proc_dir = os.path.dirname(PROCESSED_VIDEOS_FILE)
        if proc_dir and not os.path.exists(proc_dir): os.makedirs(proc_dir, exist_ok=True)
        with open(PROCESSED_VIDEOS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(processed_video_ids), f, indent=4)
        logging.debug(f"Saved {len(processed_video_ids)} processed video IDs to {PROCESSED_VIDEOS_FILE}")
    except Exception as e:
        logging.error(f"Error saving processed videos file: {e}", exc_info=True)

def parse_iso8601_duration(duration_string):
    if not duration_string or not duration_string.startswith('PT'): return 0
    hours = re.search(r'(\d+)H', duration_string)
    minutes = re.search(r'(\d+)M', duration_string)
    seconds = re.search(r'(\d+)S', duration_string)
    total_seconds = 0
    if hours: total_seconds += int(hours.group(1)) * 3600
    if minutes: total_seconds += int(minutes.group(1)) * 60
    if seconds: total_seconds += int(seconds.group(1))
    return total_seconds

def format_duration_seconds(total_seconds):
    if total_seconds < 0: return "00:00"
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0: return f"{int(hours):01d}:{int(minutes):02d}:{int(seconds):02d}"
    else: return f"{int(minutes):02d}:{int(seconds):02d}"

def get_video_details(youtube, video_id):
    try:
        video_response = youtube.videos().list( part='contentDetails', id=video_id ).execute()
        if not video_response.get('items'):
            logging.warning(f"Could not fetch details for video ID: {video_id}")
            return None
        duration_iso = video_response['items'][0]['contentDetails']['duration']
        return duration_iso
    except HttpError as e:
        logging.error(f"YouTube API error fetching details for video {video_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        logging.error(f"Unexpected error fetching details for video {video_id}: {e}", exc_info=True)
        return None

def get_latest_videos(youtube, channel_id, max_results):
    try:
        channel_response = youtube.channels().list( part='contentDetails', id=channel_id ).execute()
        if not channel_response.get('items'):
            logging.warning(f"Could not find channel details for ID: {channel_id}")
            return []
        uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        playlist_items_response = youtube.playlistItems().list( part='snippet,contentDetails', playlistId=uploads_playlist_id, maxResults=max_results ).execute()
        videos = []
        check_since = datetime.now(timezone.utc) - timedelta(hours=1, minutes=10)
        for item in playlist_items_response.get('items', []):
            video_id = item['contentDetails']['videoId']
            video_title = item['snippet']['title']
            published_at_str = item['snippet']['publishedAt']
            published_at = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))
            if published_at >= check_since:
                 videos.append({ 'id': video_id, 'title': video_title, 'published_at': published_at, 'channel_id': channel_id })
                 logging.debug(f"Video '{video_title}' (ID: {video_id}) published at {published_at} IS recent enough.")
            else:
                 logging.debug(f"Video '{video_title}' (ID: {video_id}) published at {published_at} is older than threshold {check_since}, skipping.")
        videos.sort(key=lambda x: x['published_at'], reverse=True)
        count_found = len(playlist_items_response.get('items', []))
        count_recent = len(videos)
        logging.info(f"Checked {count_found} most recent playlist items for channel {channel_id}. Found {count_recent} published since {check_since}.")
        return videos
    except HttpError as e:
        if e.resp.status == 403: logging.error(f"YouTube API quota error fetching videos for channel {channel_id}: {e}", exc_info=False)
        else: logging.error(f"YouTube API HTTP error fetching videos for channel {channel_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logging.error(f"Unexpected error fetching videos for channel {channel_id}: {e}", exc_info=True)
        return []

def get_transcript(video_id):
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            transcript = transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB'])
            logging.debug(f"Found English transcript for {video_id}")
        except NoTranscriptFound:
             logging.debug(f"No English transcript found for {video_id}. Checking for any generated transcript.")
             available_langs = list(transcript_list._generated_transcripts.keys())
             if not available_langs:
                   logging.debug(f"No generated transcripts found for {video_id}. Checking for manual transcripts.")
                   manual_langs = list(transcript_list._manually_created_transcripts.keys())
                   if not manual_langs:
                       logging.warning(f"No generated or manual transcripts found for video ID: {video_id}")
                       return None
                   else:
                       transcript = transcript_list.find_manually_created_transcript(manual_langs)
                       logging.info(f"Using first available manual transcript ({manual_langs[0]}) for {video_id}")
             else:
                transcript = transcript_list.find_generated_transcript(available_langs)
                logging.info(f"Using first available generated transcript ({available_langs[0]}) for {video_id}")
        transcript_text = " ".join([item.text for item in transcript.fetch()])
        logging.info(f"Successfully fetched transcript (length: {len(transcript_text)} chars) for video ID: {video_id}")
        return transcript_text
    except TranscriptsDisabled:
        logging.warning(f"Transcripts are disabled for video ID: {video_id}")
        return None
    except NoTranscriptFound:
        logging.warning(f"No transcript entries found (manual or generated) via API for video ID: {video_id}")
        return None
    except Exception as e:
        logging.error(f"Error fetching or processing transcript for video ID {video_id}: {e}", exc_info=True)
        return None

def generate_summary_with_gemini(transcript, prompt):
    # ... (generate_summary_with_gemini content remains the same) ...
    if not transcript:
        logging.error("generate_summary_with_gemini called with no transcript.")
        return "Error: No transcript provided."
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        generation_config = { "temperature": 0.7, "top_p": 1, "top_k": 1, "max_output_tokens": 8192, }
        safety_dict = {}
        if SAFETY_SETTINGS:
             for category, threshold in SAFETY_SETTINGS.items(): safety_dict[category] = threshold
        model = genai.GenerativeModel(model_name=GEMINI_MODEL, generation_config=generation_config, safety_settings=safety_dict if safety_dict else None)
        if "{transcript}" in prompt: full_prompt = prompt.format(transcript=transcript)
        else:
             logging.warning("Prompt does not contain '{transcript}' placeholder. Sending prompt as-is.")
             full_prompt = prompt
        logging.debug(f"Sending prompt to Gemini (first 100 chars): {full_prompt[:100]}...")
        max_retries = 2
        retry_delay = 5
        for attempt in range(max_retries):
            try:
                response = model.generate_content(full_prompt)
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    block_reason = response.prompt_feedback.block_reason
                    safety_ratings_str = "N/A"
                    if response.prompt_feedback.safety_ratings: safety_ratings_str = ", ".join([f"{rating.category}: {rating.probability}" for rating in response.prompt_feedback.safety_ratings])
                    logging.warning(f"Gemini prompt blocked. Reason: {block_reason}. Ratings: {safety_ratings_str}")
                    return f"[Blocked Prompt - Reason: {block_reason}]"
                if not response.candidates:
                    logging.warning("Gemini response has no candidates. Possibly blocked or empty.")
                    try: finish_reason = response.candidates[0].finish_reason
                    except (IndexError, AttributeError): finish_reason = "Unknown (No Candidates)"
                    return f"[No Content Generated - Finish Reason: {finish_reason}]"
                candidate = response.candidates[0]
                if candidate.finish_reason != 1: logging.warning(f"Gemini generation finished with non-standard reason: {candidate.finish_reason} (Safety={candidate.finish_reason==3})")
                if not candidate.content or not candidate.content.parts:
                    logging.warning("Gemini response candidate has no content parts.")
                    try:
                        response_text = response.text.strip()
                        if not response_text: return f"[Empty Content - Finish Reason: {candidate.finish_reason}]"
                        else:
                             logging.warning(f"Candidate had no parts but response.text contained data: {response_text[:100]}")
                             return response_text
                    except ValueError: return f"[Blocked Content or Empty - Finish Reason: {candidate.finish_reason}]"
                logging.info(f"Successfully received Gemini response (length: {len(response.text)} chars).")
                return response.text.strip()
            except Exception as e:
                logging.error(f"Gemini API call failed on attempt {attempt + 1}/{max_retries}: {e}", exc_info=False)
                if attempt < max_retries - 1:
                    logging.info(f"Retrying Gemini API call after {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logging.error("Max retries reached for Gemini API call.")
                    return f"Error: Gemini API call failed after {max_retries} attempts - {e}"
        return "Error: Failed to get response from Gemini after retries."
    except Exception as e:
        logging.error(f"General error during Gemini summary generation: {e}", exc_info=True)
        return f"Error: Failed to generate summary - {e}"


def save_summary_local(video_id, video_title, duration_str, exec_summary, detailed_summary, key_quotes):
    # ... (save_summary_local content remains the same) ...
    try:
        if OUTPUT_DIR and not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR, exist_ok=True)
        safe_title = sanitize_filename(video_title)
        filename = os.path.join(OUTPUT_DIR, f"{video_id}_{safe_title}.txt")
        content = f"Video Title: {video_title}\n"
        content += f"Video ID: {video_id}\n"
        content += f"Video URL: https://www.youtube.com/watch?v={video_id}\n"
        content += f"Duration: {duration_str if duration_str else 'N/A'}\n"
        content += f"Processed Date: {datetime.now().isoformat()}\n\n"
        content += "--- Executive Summary ---\n"
        content += f"{exec_summary}\n\n"
        content += "--- Detailed Summary ---\n"
        content += f"{detailed_summary}\n\n"
        content += "--- Key Quotes/Data Points ---\n"
        content += f"{key_quotes}\n"
        with open(filename, 'w', encoding='utf-8') as f: f.write(content)
        logging.info(f"Successfully saved summary to {filename}")
        return filename
    except Exception as e:
        logging.error(f"Error saving summary file for video {video_id}: {e}", exc_info=True)
        return None


def send_email_notification(channel_name, video_title, video_id, duration_str, exec_summary, detailed_summary, key_quotes, recipient_list):
    # ... (send_email_notification content remains the same) ...
    if not recipient_list:
        logging.warning(f"No recipients provided for video '{video_title}' (ID: {video_id}) from channel '{channel_name}'. Skipping email notification.")
        return
    subject = f"New YouTube Video Summary: [{channel_name}] {video_title}"
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    body_html = f"""
    <html><head></head><body>
    <p>A new video has been posted on the '{channel_name}' YouTube channel:</p>
    <p> <strong>Title:</strong> {video_title}<br> {f'<strong>Duration:</strong> {duration_str}<br>' if duration_str else ''} <strong>Link:</strong> <a href="{video_url}">{video_url}</a> </p>
    <hr> <h2>Executive Summary</h2> <p>{exec_summary.replace(chr(10), "<br>")}</p>
    <hr> <h2>Detailed Summary</h2> <p>{detailed_summary.replace(chr(10), "<br>")}</p>
    <hr> <h2>Key Quotes/Data Points</h2> <p>{key_quotes.replace(chr(10), "<br>")}</p>
    </body></html>
    """
    message = MIMEMultipart('alternative')
    message['From'] = SENDER_EMAIL
    message['To'] = ", ".join(recipient_list)
    message['Subject'] = subject
    message.attach(MIMEText(body_html, 'html', 'utf-8'))
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            text = message.as_string()
            server.sendmail(SENDER_EMAIL, recipient_list, text)
        logging.info(f"Successfully sent email notification for video ID: {video_id} to {', '.join(recipient_list)}")
    except smtplib.SMTPAuthenticationError: logging.error(f"SMTP Authentication Error for user {SMTP_USER}. Check username/password/app password.", exc_info=False)
    except smtplib.SMTPRecipientsRefused as e: logging.error(f"SMTP Recipient Error for video ID {video_id}. Server refused recipients: {e.recipients}", exc_info=False)
    except smtplib.SMTPServerDisconnected: logging.error("SMTP Server disconnected unexpectedly. Check server/port/network.", exc_info=False)
    except smtplib.SMTPException as e: logging.error(f"General SMTP error sending email for video ID {video_id}: {e}", exc_info=True)
    except Exception as e: logging.error(f"Unexpected error sending email for video ID {video_id}: {e}", exc_info=True)


def get_channel_name(youtube, channel_id):
    """Fetches the display name of a YouTube channel."""
    # Level 1 (4 spaces)
    try:
        # Level 2 (8 spaces)
        # *** FIX HERE: Added comma between keyword arguments ***
        channel_response = youtube.channels().list( part='snippet', id=channel_id ).execute()
        if channel_response.get('items'):
            return channel_response['items'][0]['snippet']['title']
        else:
            logging.warning(f"Could not retrieve channel name for ID: {channel_id}")
            return channel_id
    # Level 1 (4 spaces)
    except HttpError as e:
        logging.error(f"YouTube API error fetching channel name for {channel_id}: {e}", exc_info=True)
        return channel_id
    # Level 1 (4 spaces)
    except Exception as e:
        logging.error(f"Unexpected error fetching channel name for {channel_id}: {e}", exc_info=True)
        return channel_id


# --- Main Execution ---
def main(): # Level 0
    start_time = time.time()
    new_videos_processed_count = 0
    channel_names = {}

    logging.info("--- Starting YouTube Monitor Script ---")
    logging.info(f"Minimum video duration threshold: {MIN_DURATION_MINUTES} minutes.")

    load_processed_videos()

    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY, cache_discovery=False)
    except Exception as e:
        logging.error(f"Failed to build YouTube API client: {e}", exc_info=True)
        sys.exit(1)

    # Level 1 (4 spaces)
    for channel_id in CHANNEL_IDS:
        if not channel_id or not channel_id.startswith("UC"):
            logging.warning(f"Skipping invalid channel ID entry: {repr(channel_id)}")
            continue

        logging.info(f"--- Checking Channel ID: {channel_id} ---")

        if channel_id not in channel_names:
            channel_names[channel_id] = get_channel_name(youtube, channel_id)
            time.sleep(0.3)

        channel_name = channel_names[channel_id]
        logging.info(f"Processing channel: '{channel_name}' ({channel_id})")

        latest_videos = get_latest_videos(youtube, channel_id, MAX_RESULTS_PER_CHANNEL)

        if not latest_videos:
            continue

        # Level 2 (8 spaces)
        for video in latest_videos:
            # Level 3 (12 spaces)
            video_id = video['id']
            video_title = video['title']

            if video_id in processed_video_ids:
                logging.info(f"Video '{video_title}' (ID: {video_id}) already processed. Skipping.")
                continue

            logging.info(f"Found potential new video: '{video_title}' (ID: {video_id}). Fetching details...")

            duration_iso = get_video_details(youtube, video_id)
            duration_seconds = 0
            formatted_duration_str = None

            if duration_iso:
                duration_seconds = parse_iso8601_duration(duration_iso)
                formatted_duration_str = format_duration_seconds(duration_seconds)
                logging.info(f"DEBUG DURATION for {video_id}: ISO='{duration_iso}', Seconds={duration_seconds}, MinThresholdSec={MIN_DURATION_MINUTES * 60}")
                logging.info(f"Video '{video_title}' duration: {formatted_duration_str} ({duration_seconds} seconds)")
            else:
                logging.warning(f"Could not determine duration for video '{video_title}'. Proceeding without duration check/info.")

            if MIN_DURATION_MINUTES > 0 and duration_seconds > 0:
                min_duration_seconds = MIN_DURATION_MINUTES * 60
                if duration_seconds < min_duration_seconds:
                    logging.info(f"DEBUG SKIP DECISION for {video_id}: duration_seconds ({duration_seconds}) < min_duration_seconds ({min_duration_seconds}) = {duration_seconds < min_duration_seconds}")
                    logging.info(f"Video '{video_title}' ({formatted_duration_str}) is shorter than the minimum {MIN_DURATION_MINUTES} minutes. Skipping processing.")
                    processed_video_ids.add(video_id)
                    new_videos_processed_count += 1
                    save_processed_videos()
                    continue

            logging.info(f"Processing video '{video_title}' (ID: {video_id}).")

            transcript = get_transcript(video_id)
            if not transcript:
                logging.warning(f"Could not get transcript for '{video_title}' ({video_id}). Skipping summarization and notification for this video.")
                continue

            time.sleep(1)

            logging.info(f"Generating summaries for '{video_title}'...")
            exec_summary = generate_summary_with_gemini(transcript, PROMPT_EXEC_SUMMARY)
            time.sleep(1)
            detailed_summary = generate_summary_with_gemini(transcript, PROMPT_DETAILED_SUMMARY)
            time.sleep(1)
            key_quotes = generate_summary_with_gemini(transcript, PROMPT_KEY_QUOTES)

            logging.debug(f"Exec Summary for {video_id}: {exec_summary[:100]}...")
            logging.debug(f"Detailed Summary for {video_id}: {detailed_summary[:100]}...")
            logging.debug(f"Key Quotes for {video_id}: {key_quotes[:100]}...")

            is_error = False
            for summary in [exec_summary, detailed_summary, key_quotes]:
                 if summary is None or summary.startswith("Error:") or "[Blocked" in summary or "[No Content" in summary:
                     is_error = True
                     break

            if is_error:
                 logging.error(f"One or more summaries failed generation or were blocked for video {video_id}. Saving locally but skipping email notification.")
                 save_summary_local(video_id, video_title, formatted_duration_str, exec_summary, detailed_summary, key_quotes)
                 processed_video_ids.add(video_id)
                 new_videos_processed_count += 1
                 continue

            save_summary_local(video_id, video_title, formatted_duration_str, exec_summary, detailed_summary, key_quotes)

            recipients_for_this_channel = channel_recipients.get(channel_id)
            final_recipient_list = []
            if recipients_for_this_channel:
                final_recipient_list = recipients_for_this_channel
                logging.info(f"Using specific recipients for channel {channel_id}: {', '.join(final_recipient_list)}")
            elif default_recipients:
                final_recipient_list = default_recipients
                logging.info(f"Using default recipients for channel {channel_id}: {', '.join(final_recipient_list)}")
            else:
                logging.warning(f"No specific or default recipients found for channel {channel_id}. Cannot send email for video {video_id}.")

            if final_recipient_list:
                send_email_notification(
                    channel_name, video_title, video_id, formatted_duration_str,
                    exec_summary, detailed_summary, key_quotes, final_recipient_list
                )

            processed_video_ids.add(video_id)
            new_videos_processed_count += 1
            time.sleep(2)
        # End 'for video' loop

        time.sleep(3) # Between channels
    # End 'for channel_id' loop

    # Finalization (Level 1 - 4 spaces)
    save_processed_videos()
    end_time = time.time()
    duration = end_time - start_time
    logging.info(f"--- YouTube Monitor Script Finished ---")
    logging.info(f"Processed {new_videos_processed_count} new videos in this run (including skipped due to duration/errors).")
    logging.info(f"Total execution time: {duration:.2f} seconds.")
# End of main function definition (Level 0)

# Script entry point (Level 0)
if __name__ == "__main__":
    # Level 1 (4 spaces)
    main()