import os
import sys
import configparser
import json
import logging
import smtplib
import platform # Import platform module
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
import time

# Third-party libraries (install via requirements.txt)
import google.generativeai as genai
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound, VideoUnavailable

# --- Constants ---
CONFIG_FILE = 'config.ini'
# Use os.path.join for platform-independent paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) # Directory where the script resides

# --- Configuration Loading ---
config = configparser.ConfigParser()
config_path = os.path.join(SCRIPT_DIR, CONFIG_FILE)

channel_recipient_map = {} # Dictionary to store {channel_id: [list_of_emails]}
default_recipients = [] # List for default emails

if not os.path.exists(config_path):
    print(f"ERROR: Configuration file '{CONFIG_FILE}' not found in script directory '{SCRIPT_DIR}'.")
    sys.exit(1)

try:
    config.read(config_path)

    # API Keys
    YOUTUBE_API_KEY = config.get('API_KEYS', 'youtube_api_key', fallback=None)
    GEMINI_API_KEY = config.get('API_KEYS', 'gemini_api_key', fallback=None)

    # Channels
    channel_ids_raw = config.get('CHANNELS', 'channel_ids', fallback='')
    CHANNEL_IDS = [cid.strip() for cid in channel_ids_raw.split(',') if cid.strip()]

    # Gemini Settings
    GEMINI_MODEL = config.get('GEMINI', 'model_name', fallback='gemini-1.5-pro-latest')
    PROMPT_EXEC_SUMMARY = config.get('GEMINI', 'prompt_executive_summary')
    PROMPT_DETAILED_SUMMARY = config.get('GEMINI', 'prompt_detailed_summary')
    PROMPT_KEY_QUOTES = config.get('GEMINI', 'prompt_key_quotes')
    SAFETY_SETTINGS_RAW = config.get('GEMINI', 'safety_settings', fallback=None)
    SAFETY_SETTINGS = None
    if SAFETY_SETTINGS_RAW:
        try:
            SAFETY_SETTINGS = {}
            for item in SAFETY_SETTINGS_RAW.split(','):
                key, value = item.split(':')
                SAFETY_SETTINGS[key.strip()] = value.strip()
        except Exception as e:
            print(f"Warning: Could not parse safety_settings: {e}. Using default safety settings.")

    # Email Settings (General)
    SMTP_SERVER = config.get('EMAIL', 'smtp_server', fallback=None)
    SMTP_PORT = config.getint('EMAIL', 'smtp_port', fallback=587)
    SMTP_USER = config.get('EMAIL', 'smtp_user', fallback=None)
    SMTP_PASSWORD = config.get('EMAIL', 'smtp_password', fallback=None)
    SENDER_EMAIL = config.get('EMAIL', 'sender_email', fallback=None)

    # --- Load Per-Channel and Default Recipients ---
    if config.has_section('EMAIL_RECIPIENTS_PER_CHANNEL'):
        for channel_id_key in config['EMAIL_RECIPIENTS_PER_CHANNEL']:
            if channel_id_key.lower() != 'default_recipients':
                 emails_raw = config.get('EMAIL_RECIPIENTS_PER_CHANNEL', channel_id_key, fallback='')
                 emails_list = [email.strip() for email in emails_raw.split(',') if email.strip()]
                 if emails_list:
                     channel_recipient_map[channel_id_key] = emails_list
                 else:
                      print(f"Warning: Empty email list configured for channel ID {channel_id_key} in config.")
        default_emails_raw = config.get('EMAIL_RECIPIENTS_PER_CHANNEL', 'default_recipients', fallback='')
        default_recipients = [email.strip() for email in default_emails_raw.split(',') if email.strip()]
        if not default_recipients:
             print("Info: No default_recipients configured in [EMAIL_RECIPIENTS_PER_CHANNEL].")
    else:
        print("Warning: Section [EMAIL_RECIPIENTS_PER_CHANNEL] not found in config. No emails will be sent.")

    # Script Settings
    PROCESSED_VIDEOS_FILE_NAME = config.get('SETTINGS', 'processed_videos_file', fallback='processed_videos.json')
    LOG_FILE_NAME = config.get('SETTINGS', 'log_file', fallback='monitor.log')
    OUTPUT_DIR_NAME = config.get('SETTINGS', 'output_dir', fallback='output')
    MAX_RESULTS_PER_CHANNEL = config.getint('SETTINGS', 'max_results_per_channel', fallback=1)

    # Construct Full Paths
    PROCESSED_VIDEOS_FILE = os.path.join(SCRIPT_DIR, PROCESSED_VIDEOS_FILE_NAME)
    LOG_FILE = os.path.join(SCRIPT_DIR, LOG_FILE_NAME)
    OUTPUT_DIR = os.path.join(SCRIPT_DIR, OUTPUT_DIR_NAME)

    # --- Updated Validation ---
    essential_configs = {
        "YouTube API Key": YOUTUBE_API_KEY,
        "Gemini API Key": GEMINI_API_KEY,
        "Channel IDs": CHANNEL_IDS,
        "SMTP Server": SMTP_SERVER,
        "SMTP User": SMTP_USER,
        "SMTP Password": SMTP_PASSWORD,
        "Sender Email": SENDER_EMAIL,
        "Email Recipient Configured": bool(channel_recipient_map or default_recipients),
        "Exec Summary Prompt": PROMPT_EXEC_SUMMARY,
        "Detailed Summary Prompt": PROMPT_DETAILED_SUMMARY,
        "Key Quotes Prompt": PROMPT_KEY_QUOTES,
    }
    missing_configs = [name for name, value in essential_configs.items() if not value]
    if missing_configs:
        if "Email Recipient Configured" in missing_configs:
             raise ValueError(f"Missing essential configuration values: {', '.join(missing_configs)}. " \
                              f"Ensure at least one channel has recipients defined or default_recipients is set in [EMAIL_RECIPIENTS_PER_CHANNEL] in {config_path}")
        else:
             raise ValueError(f"Missing essential configuration values: {', '.join(missing_configs)} in {config_path}")

except (configparser.Error, ValueError) as e:
    print(f"ERROR loading configuration from {config_path}: {e}")
    sys.exit(1)
except Exception as e:
    print(f"An unexpected error occurred during configuration loading: {e}")
    sys.exit(1)

# --- Logging Setup ---
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# --- Ensure Output Directory Exists ---
try:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
except OSError as e:
    logging.error(f"Could not create output directory '{OUTPUT_DIR}': {e}")
    sys.exit(1)

# --- Global Variables ---
processed_video_ids = set()

# --- Helper Functions ---
def load_processed_videos():
    """Loads the set of processed video IDs from the JSON file."""
    global processed_video_ids
    try:
        if os.path.exists(PROCESSED_VIDEOS_FILE):
            with open(PROCESSED_VIDEOS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    processed_video_ids = set(data)
                elif isinstance(data, dict) and 'processed_ids' in data:
                    processed_video_ids = set(data.get('processed_ids', []))
                else:
                    logging.warning(f"Processed videos file '{PROCESSED_VIDEOS_FILE}' has unexpected format. Starting fresh.")
                    processed_video_ids = set()
            logging.info(f"Loaded {len(processed_video_ids)} processed video IDs from {PROCESSED_VIDEOS_FILE}")
        else:
            logging.info(f"Processed videos file '{PROCESSED_VIDEOS_FILE}' not found. Starting with empty set.")
            processed_video_ids = set()
    except (json.JSONDecodeError, IOError) as e:
        logging.error(f"Error loading processed videos file {PROCESSED_VIDEOS_FILE}: {e}. Starting with empty set.")
        processed_video_ids = set()

def save_processed_videos():
    """Saves the current set of processed video IDs to the JSON file."""
    try:
        data_to_save = {'processed_ids': sorted(list(processed_video_ids))}
        with open(PROCESSED_VIDEOS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, indent=4)
    except IOError as e:
        logging.error(f"Error saving processed videos file {PROCESSED_VIDEOS_FILE}: {e}")

def get_channel_name(youtube, channel_id):
    """Fetches the channel name using the YouTube API."""
    try:
        request = youtube.channels().list(
            part="snippet",
            id=channel_id
        )
        response = request.execute()
        if response and 'items' in response and len(response['items']) > 0:
            return response['items'][0]['snippet']['title']
        else:
            logging.warning(f"Could not find channel name for ID: {channel_id}")
            return f"Unknown Channel ({channel_id})"
    except HttpError as e:
        logging.error(f"HTTP Error fetching channel name for {channel_id}: {e}")
        return f"Unknown Channel ({channel_id})"
    except Exception as e:
        logging.error(f"Error fetching channel name for {channel_id}: {e}")
        return f"Unknown Channel ({channel_id})"

def get_latest_videos(youtube, channel_id, max_results):
    """Fetches the latest videos for a given channel."""
    try:
        request = youtube.channels().list(
            part="contentDetails",
            id=channel_id
        )
        response = request.execute()
        if not response or 'items' not in response or not response['items']:
             logging.error(f"Could not get content details for channel {channel_id}. Skipping.")
             return []
        uploads_playlist_id = response['items'][0]['contentDetails']['relatedPlaylists']['uploads']

        request = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=max_results
        )
        response = request.execute()
        videos = []
        if 'items' in response:
            for item in response['items']:
                if item.get('contentDetails') and item['contentDetails'].get('videoId') and \
                   item.get('snippet') and item['snippet'].get('title'):
                    videos.append({
                        'id': item['contentDetails']['videoId'],
                        'title': item['snippet']['title'],
                    })
                else:
                     logging.warning(f"Skipping playlist item due to missing data for channel {channel_id}: {item.get('id', 'N/A')}")
        return videos
    except HttpError as e:
        logging.error(f"YouTube API error fetching videos for channel {channel_id}: {e}")
        return []
    except Exception as e:
        logging.error(f"Unexpected error fetching videos for {channel_id}: {e}")
        return []

def get_transcript(video_id):
    """Retrieves the transcript for a video ID."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None
        preferred_langs = ['en', 'en-US', 'en-GB']
        try:
            transcript = transcript_list.find_manually_created_transcript(preferred_langs)
            logging.info(f"Found manually created English transcript for {video_id}")
        except NoTranscriptFound:
            try:
                transcript = transcript_list.find_generated_transcript(preferred_langs)
                logging.info(f"Found generated English transcript for {video_id}")
            except NoTranscriptFound:
                logging.warning(f"No English transcript (manual or generated) found for {video_id}. Trying first available.")
                available_transcripts = list(transcript_list)
                if available_transcripts:
                     transcript = available_transcripts[0]
                     logging.info(f"Using first available transcript (lang: {transcript.language}) for {video_id}")
                else:
                    logging.error(f"No transcripts available at all for video {video_id}.")
                    return None

        transcript_pieces = transcript.fetch()
        transcript_text = " ".join([item['text'] for item in transcript_pieces if isinstance(item, dict) and 'text' in item])
        if not transcript_text.strip():
             logging.warning(f"Fetched transcript for {video_id} appears to be empty.")
             return None
        logging.info(f"Successfully fetched transcript for video {video_id} (Language: {transcript.language})")
        return transcript_text
    except TranscriptsDisabled:
        logging.warning(f"Transcripts are disabled for video {video_id}.")
        return None
    except VideoUnavailable:
        logging.warning(f"Video {video_id} is unavailable.")
        return None
    except NoTranscriptFound:
        logging.error(f"Could not find any transcript for video {video_id}.")
        return None
    except Exception as e:
        logging.error(f"Error fetching transcript for video {video_id}: {e} ({type(e).__name__})")
        return None

def generate_summary_with_gemini(transcript, prompt):
    """Generates content using Gemini based on the transcript and a prompt."""
    if not GEMINI_API_KEY:
        logging.error("Gemini API Key is not configured.")
        return "Error: Gemini API Key not configured."
    if not transcript:
        logging.warning("Cannot generate summary: Transcript is empty.")
        return "Error: No transcript provided."

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        generation_config = genai.types.GenerationConfig(temperature=0.7)
        model = genai.GenerativeModel(
            GEMINI_MODEL,
            generation_config=generation_config,
            safety_settings=SAFETY_SETTINGS
        )

        full_prompt = prompt.format(transcript=transcript)
        response = model.generate_content(full_prompt)

        if not response.parts:
             feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else None
             block_reason = feedback.block_reason if feedback and hasattr(feedback, 'block_reason') else "Unknown"
             logging.warning(f"Gemini request potentially blocked or yielded no parts. Reason: {block_reason}. Full response: {response}")
             return f"Error: Content generation failed or blocked (Reason: {block_reason})."

        if hasattr(response.parts[0], 'text'):
            return response.parts[0].text.strip()
        else:
             logging.warning(f"Gemini response part does not contain text. Part: {response.parts[0]}")
             return "Error: Gemini response format unexpected."

    except Exception as e:
        logging.error(f"Error during Gemini API call: {e} ({type(e).__name__})")
        return f"Error: Failed to generate content using Gemini ({type(e).__name__})."

def save_summary_to_file(video_id, channel_name, video_title, exec_summary, detailed_summary, key_quotes):
    """Saves the generated summaries and quotes to a local text file."""
    safe_channel_name = "".join(c if c.isalnum() or c in (' ', '_') else '_' for c in channel_name).rstrip()
    safe_channel_name = safe_channel_name.replace(' ', '_')
    filename = os.path.join(OUTPUT_DIR, f"{video_id}_{safe_channel_name}.txt")
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"Channel: {channel_name}\n")
            f.write(f"Video Title: {video_title}\n")
            f.write(f"Video ID: {video_id}\n")
            f.write(f"Video URL: https://www.youtube.com/watch?v={video_id}\n")
            f.write("="*30 + "\n")
            f.write("Executive Summary:\n")
            f.write(exec_summary + "\n\n")
            f.write("="*30 + "\n")
            f.write("Detailed Summary:\n")
            f.write(detailed_summary + "\n\n")
            f.write("="*30 + "\n")
            f.write("Key Quotes/Data Points:\n")
            f.write(key_quotes + "\n")
        logging.info(f"Saved summary to file: {filename}")
        return filename
    except IOError as e:
        logging.error(f"Error saving summary file {filename}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error saving summary file {filename}: {e}")
        return None

def send_email_notification(recipient_list, channel_name, video_title, video_id, exec_summary, detailed_summary, key_quotes):
    """Sends an email notification with the video summary to a SPECIFIC list of recipients."""
    if not recipient_list:
         logging.error("Email recipient list is empty. Cannot send notification.")
         return False
    if not all([SMTP_SERVER, SMTP_USER, SMTP_PASSWORD, SENDER_EMAIL]):
        logging.error("SMTP configuration is incomplete. Cannot send notification.")
        return False

    subject = f"New Video Summary: [{channel_name}] {video_title}"
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    body_lines = [
        "A new video has been posted and summarized:", "",
        f"Channel: {channel_name}", f"Title: {video_title}", f"Link: {video_url}", "",
        "------------------------------", "Executive Summary:", "------------------------------", exec_summary, "",
        "------------------------------", "Detailed Summary:", "------------------------------", detailed_summary, "",
        "------------------------------", "Key Quotes/Data Points:", "------------------------------", key_quotes, ""
    ]
    body = "\n".join(body_lines)

    message = MIMEMultipart()
    message['From'] = SENDER_EMAIL
    message['To'] = ", ".join(recipient_list)
    message['Subject'] = subject
    message.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        logging.info(f"Connecting to SMTP server {SMTP_SERVER}:{SMTP_PORT}...")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
        server.ehlo()
        if server.has_extn('STARTTLS'):
            logging.info("Starting TLS...")
            server.starttls()
            server.ehlo()
        else:
             logging.warning("SMTP Server does not support STARTTLS. Proceeding without encryption (if port is not 465).")

        logging.info("Attempting SMTP login...")
        server.login(SMTP_USER, SMTP_PASSWORD)
        logging.info("SMTP login successful. Sending email...")
        server.sendmail(SENDER_EMAIL, recipient_list, message.as_string())
        server.quit()
        logging.info(f"Email notification sent successfully to {', '.join(recipient_list)}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        logging.error(f"SMTP Authentication Error: {e}. Check username/password/app password.")
        return False
    except smtplib.SMTPConnectError as e:
         logging.error(f"SMTP Connection Error: Could not connect to {SMTP_SERVER}:{SMTP_PORT}. {e}")
         return False
    except smtplib.SMTPServerDisconnected as e:
         logging.error(f"SMTP Server Disconnected unexpectedly: {e}")
         return False
    except smtplib.SMTPException as e:
        logging.error(f"SMTP Error occurred: {e} ({type(e).__name__})")
        return False
    except TimeoutError:
         logging.error(f"SMTP connection timed out connecting to {SMTP_SERVER}:{SMTP_PORT}.")
         return False
    except Exception as e:
        logging.error(f"Failed to send email: {e} ({type(e).__name__})")
        return False

# --- Main Execution Logic ---
def main():
    logging.info("--- Starting YouTube Monitor Script ---")
    start_time = time.time()

    load_processed_videos()
    initial_processed_count = len(processed_video_ids)

    if not YOUTUBE_API_KEY:
        logging.error("YouTube API Key is missing in config. Cannot proceed.")
        sys.exit(1)

    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
        logging.info("YouTube API client initialized.")
    except Exception as e:
        logging.error(f"Failed to initialize YouTube API client: {e}")
        sys.exit(1)

    new_videos_processed_count = 0
    for channel_id in CHANNEL_IDS:
        logging.info(f"--- Checking Channel ID: {channel_id} ---")
        channel_name = get_channel_name(youtube, channel_id)
        logging.info(f"Processing channel: '{channel_name}'")

        latest_videos = get_latest_videos(youtube, channel_id, MAX_RESULTS_PER_CHANNEL)

        if not latest_videos:
            logging.info(f"No recent videos found or error fetching for channel {channel_id}. Moving to next.")
            continue

        for video in latest_videos:
            video_id = video['id']
            video_title = video['title']

            logging.info(f"Checking video: '{video_title}' (ID: {video_id})")

            if video_id in processed_video_ids:
                logging.info(f"Video {video_id} has already been processed. Skipping.")
                continue

            logging.info(f"New video found: {video_id}. Processing...")

            transcript = get_transcript(video_id)
            if not transcript:
                logging.warning(f"Could not get transcript for {video_id}. Cannot process summaries. Marking as processed to avoid retries.")
                processed_video_ids.add(video_id)
                save_processed_videos()
                continue

            logging.info(f"Generating executive summary for {video_id}...")
            exec_summary = generate_summary_with_gemini(transcript, PROMPT_EXEC_SUMMARY)

            logging.info(f"Generating detailed summary for {video_id}...")
            detailed_summary = generate_summary_with_gemini(transcript, PROMPT_DETAILED_SUMMARY)

            logging.info(f"Generating key quotes for {video_id}...")
            key_quotes = generate_summary_with_gemini(transcript, PROMPT_KEY_QUOTES)

            generation_failed = False
            if "Error:" in exec_summary:
                logging.error(f"Executive summary generation failed for {video_id}: {exec_summary}")
                generation_failed = True
            if "Error:" in detailed_summary:
                logging.error(f"Detailed summary generation failed for {video_id}: {detailed_summary}")
                generation_failed = True
            if "Error:" in key_quotes:
                logging.error(f"Key quotes generation failed for {video_id}: {key_quotes}")
                generation_failed = True

            if generation_failed:
                 logging.error(f"One or more Gemini generations failed for video {video_id}. Skipping save/email. Will retry next run.")
                 continue

            saved_filepath = save_summary_to_file(video_id, channel_name, video_title, exec_summary, detailed_summary, key_quotes)
            if not saved_filepath:
                 logging.error(f"Failed to save summary file for {video_id}. Skipping email notification. Will retry next run.")
                 continue

            recipients_for_this_channel = []
            if channel_id in channel_recipient_map:
                recipients_for_this_channel = channel_recipient_map[channel_id]
                logging.info(f"Using specific recipients for channel {channel_id}: {', '.join(recipients_for_this_channel)}")
            elif default_recipients:
                recipients_for_this_channel = default_recipients
                logging.info(f"Using default recipients for channel {channel_id}: {', '.join(recipients_for_this_channel)}")
            else:
                logging.warning(f"No specific or default email recipients configured for channel {channel_id}. Cannot send email notification.")
                processed_video_ids.add(video_id)
                new_videos_processed_count += 1
                save_processed_videos()
                continue

            email_sent = False
            if recipients_for_this_channel:
                email_sent = send_email_notification(
                    recipient_list=recipients_for_this_channel,
                    channel_name=channel_name, video_title=video_title, video_id=video_id,
                    exec_summary=exec_summary, detailed_summary=detailed_summary, key_quotes=key_quotes
                )
            else:
                 logging.warning(f"Internal logic error or empty recipient list for {channel_id}. Skipping email.")

            if email_sent:
                logging.info(f"Successfully processed and notified for video {video_id}.")
                processed_video_ids.add(video_id)
                new_videos_processed_count += 1
                save_processed_videos()
            elif not recipients_for_this_channel:
                 logging.info(f"Video {video_id} processed (summary saved), but no recipients configured for email.")
                 # Already marked processed above when recipients were determined to be missing
            else: # Email sending failed
                logging.warning(f"Processing steps completed for {video_id}, but email notification failed. Marked as processed to avoid duplicate summaries if Gemini works next time, but check email logs.")
                processed_video_ids.add(video_id)
                new_videos_processed_count += 1
                save_processed_videos()

    end_time = time.time()
    duration = end_time - start_time
    logging.info(f"--- YouTube Monitor Script Finished ---")
    logging.info(f"Duration: {duration:.2f} seconds")
    logging.info(f"Total videos processed in this run: {new_videos_processed_count}")
    logging.info(f"Total unique processed video IDs now: {len(processed_video_ids)}")

if __name__ == "__main__":
    main()