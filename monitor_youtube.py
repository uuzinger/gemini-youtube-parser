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
import re # For cleaning filenames

# Third-party libraries (install via requirements.txt)
import google.generativeai as genai
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

# --- Configuration Loading ---
CONFIG_FILE = 'config.ini'
config = configparser.ConfigParser()
# *** ADDED: Preserve the case of keys read from the config file ***
config.optionxform = str

if not os.path.exists(CONFIG_FILE):
    print(f"ERROR: Configuration file '{CONFIG_FILE}' not found. Please create it based on the template.")
    sys.exit(1)

# --- Add Debug Print for Config File Path ---
actual_path = os.path.abspath(CONFIG_FILE)
print(f"--- Attempting to read config file: {actual_path} ---", file=sys.stderr)

# --- Global Dictionaries / Lists ---
channel_recipients = {} # To store mapping from Channel ID -> [list, of, emails]
default_recipients = [] # To store the default recipient list

try:
    # *** Use indented multi-line format for prompts in config.ini ***
    config.read(CONFIG_FILE)

    # API Keys
    YOUTUBE_API_KEY = config.get('API_KEYS', 'youtube_api_key', fallback=None)
    GEMINI_API_KEY = config.get('API_KEYS', 'gemini_api_key', fallback=None)

    # Channels to Monitor
    channel_ids_raw = config.get('CHANNELS', 'channel_ids', fallback=None)
    CHANNEL_IDS = [cid.strip() for cid in channel_ids_raw.split(',') if cid.strip()] if channel_ids_raw else []

    # Gemini Settings
    GEMINI_MODEL = config.get('GEMINI', 'model_name', fallback='gemini-1.5-pro-latest')
    # --- Read Prompts (assuming indented format in config.ini) ---
    # configparser handles the indented multi-line values automatically now
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

    # --- Load Channel-Specific and Default Recipients ---
    if config.has_section('CHANNEL_RECIPIENTS'):
        # Because config.optionxform = str, keys will have original case
        for channel_id_key, emails_raw in config.items('CHANNEL_RECIPIENTS'):
            emails = [email.strip() for email in emails_raw.split(',') if email.strip()]
            if emails: # Only add if there are actual emails
                if channel_id_key == 'default_recipients':
                    default_recipients = emails
                    print(f"INFO: Loaded default recipients: {', '.join(default_recipients)}")
                else:
                    # Validation check remains useful
                    if channel_id_key.startswith("UC") and len(channel_id_key) == 24:
                         channel_recipients[channel_id_key] = emails
                         # Use repr(channel_id_key) to clearly show it has original case now
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

    # --- Validate Essential Configuration ---
    errors = []
    if not YOUTUBE_API_KEY or YOUTUBE_API_KEY == 'YOUR_YOUTUBE_DATA_API_V3_KEY':
        errors.append("Missing or placeholder 'youtube_api_key' in [API_KEYS]")
    if not GEMINI_API_KEY or GEMINI_API_KEY == 'YOUR_GEMINI_API_KEY':
        errors.append("Missing or placeholder 'gemini_api_key' in [API_KEYS]")
    if not CHANNEL_IDS:
        errors.append("Missing or empty 'channel_ids' in [CHANNELS]")
    if not SMTP_SERVER:
        errors.append("Missing 'smtp_server' in [EMAIL]")
    if not SMTP_USER:
         errors.append("Missing 'smtp_user' in [EMAIL]")
    if not SMTP_PASSWORD or SMTP_PASSWORD == 'YOUR_EMAIL_PASSWORD_OR_APP_PASSWORD':
         errors.append("Missing or placeholder 'smtp_password' in [EMAIL]")
    if not SENDER_EMAIL:
        errors.append("Missing 'sender_email' in [EMAIL]")
    # Validate recipients: Ensure we have *some* way to send emails
    if not default_recipients and not channel_recipients:
         errors.append("No email recipients configured. Please define 'default_recipients' or specific channel recipients in the '[CHANNEL_RECIPIENTS]' section.")
    elif not default_recipients:
         print("WARNING: No 'default_recipients' configured in [CHANNEL_RECIPIENTS]. Emails will only be sent for channels with specific recipient lists.")


    if errors:
        print("--- CONFIGURATION ERRORS ---")
        for error in errors:
            print(f"- {error}")
        print(f"Please check your '{CONFIG_FILE}' file.")
        sys.exit(1)

except configparser.ParsingError as e:
    # Catch specific INI parsing errors
    print(f"ERROR: Failed to parse '{CONFIG_FILE}'. Check syntax, especially multi-line values (use indentation).")
    print(f"Parser errors:\n{e}")
    sys.exit(1)
except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as e:
    print(f"ERROR reading configuration file '{CONFIG_FILE}': {e}")
    sys.exit(1)
except Exception as e:
    # Catch any other unexpected error during loading
    print(f"An unexpected error occurred during configuration loading: {e}")
    # Optionally add more detail for debugging:
    # import traceback
    # traceback.print_exc()
    sys.exit(1)


# --- Logging Setup ---
# Ensure log directory exists if specified within a path
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler(LOG_FILE, encoding='utf-8'), # Specify encoding
                        logging.StreamHandler() # Also print logs to console
                    ])

# --- Global Variables ---
processed_video_ids = set()

# --- Helper Functions ---

def sanitize_filename(filename):
    """Removes characters that are problematic for filenames."""
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename)
    sanitized = re.sub(r'\s+', '_', sanitized)
    # Limit length
    return sanitized[:150]

def load_processed_videos():
    """Loads the set of processed video IDs from the JSON file."""
    global processed_video_ids
    if not os.path.exists(PROCESSED_VIDEOS_FILE):
        logging.info(f"'{PROCESSED_VIDEOS_FILE}' not found. Starting with an empty set.")
        processed_video_ids = set()
        return # Exit early if file doesn't exist

    try:
        with open(PROCESSED_VIDEOS_FILE, 'r', encoding='utf-8') as f:
            # Handle empty file case
            content = f.read()
            if not content:
                processed_video_ids = set()
                logging.info(f"'{PROCESSED_VIDEOS_FILE}' is empty. Starting with an empty set.")
            else:
                processed_video_ids = set(json.loads(content))
                logging.info(f"Loaded {len(processed_video_ids)} processed video IDs from {PROCESSED_VIDEOS_FILE}")
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {PROCESSED_VIDEOS_FILE}. Starting fresh.", exc_info=True)
        processed_video_ids = set() # Start fresh if file is corrupt
    except Exception as e:
        logging.error(f"Error loading processed videos file: {e}", exc_info=True)
        processed_video_ids = set() # Fallback

def save_processed_videos():
    """Saves the current set of processed video IDs to the JSON file."""
    try:
        # Ensure the directory exists
        proc_dir = os.path.dirname(PROCESSED_VIDEOS_FILE)
        if proc_dir and not os.path.exists(proc_dir):
             os.makedirs(proc_dir, exist_ok=True)

        with open(PROCESSED_VIDEOS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(processed_video_ids), f, indent=4)
        logging.debug(f"Saved {len(processed_video_ids)} processed video IDs to {PROCESSED_VIDEOS_FILE}")
    except Exception as e:
        logging.error(f"Error saving processed videos file: {e}", exc_info=True)

def get_latest_videos(youtube, channel_id, max_results):
    """Fetches the latest videos for a given channel."""
    try:
        # 1. Get the uploads playlist ID for the channel
        channel_response = youtube.channels().list(
            part='contentDetails',
            id=channel_id
        ).execute()

        if not channel_response.get('items'):
            logging.warning(f"Could not find channel details for ID: {channel_id}")
            return []

        uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']

        # 2. Get the latest videos from the uploads playlist
        playlist_items_response = youtube.playlistItems().list(
            part='snippet,contentDetails',
            playlistId=uploads_playlist_id,
            maxResults=max_results # Fetch slightly more just in case? No, rely on time filter.
        ).execute()

        videos = []
        # Check videos published within the last hour + a small buffer (e.g., 10 mins)
        # This avoids reprocessing slightly older videos if the script runs slightly late.
        check_since = datetime.now(timezone.utc) - timedelta(hours=1, minutes=10)

        for item in playlist_items_response.get('items', []):
            video_id = item['contentDetails']['videoId']
            video_title = item['snippet']['title']
            published_at_str = item['snippet']['publishedAt']
            # YouTube API provides ISO 8601 format (e.g., "2024-04-07T10:00:00Z")
            published_at = datetime.fromisoformat(published_at_str.replace('Z', '+00:00')) # Ensure TZ aware

            # Check if video was published recently enough
            if published_at >= check_since:
                 videos.append({
                    'id': video_id,
                    'title': video_title,
                    'published_at': published_at,
                    'channel_id': channel_id # Pass channel_id along for context
                })
                 logging.debug(f"Video '{video_title}' (ID: {video_id}) published at {published_at} IS recent enough.")
            else:
                 logging.debug(f"Video '{video_title}' (ID: {video_id}) published at {published_at} is older than threshold {check_since}, skipping.")


        # Sort by published date just in case API order isn't perfect (newest first)
        videos.sort(key=lambda x: x['published_at'], reverse=True)

        count_found = len(playlist_items_response.get('items', []))
        count_recent = len(videos)
        logging.info(f"Checked {count_found} most recent playlist items for channel {channel_id}. Found {count_recent} published since {check_since}.")
        return videos

    except HttpError as e:
        # Specifically log common quota errors
        if e.resp.status == 403:
             logging.error(f"YouTube API quota error fetching videos for channel {channel_id}: {e}", exc_info=False) # Less verbose log
        else:
             logging.error(f"YouTube API HTTP error fetching videos for channel {channel_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logging.error(f"Unexpected error fetching videos for channel {channel_id}: {e}", exc_info=True)
        return []

def get_transcript(video_id):
    """Retrieves the transcript for a given video ID."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        # Prioritize English, then fallback to any generated transcript
        try:
            # Try common English variants first
            transcript = transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB'])
            logging.debug(f"Found English transcript for {video_id}")
        except NoTranscriptFound:
             # If no English, try fetching the first available generated transcript
             logging.debug(f"No English transcript found for {video_id}. Checking for any generated transcript.")
             available_langs = list(transcript_list._generated_transcripts.keys())
             if not available_langs:
                  # Check for manual transcripts if no generated ones exist at all
                   logging.debug(f"No generated transcripts found for {video_id}. Checking for manual transcripts.")
                   manual_langs = list(transcript_list._manually_created_transcripts.keys())
                   if not manual_langs:
                       logging.warning(f"No generated or manual transcripts found for video ID: {video_id}")
                       return None
                   else:
                       # Use the first available manual transcript
                       transcript = transcript_list.find_manually_created_transcript(manual_langs)
                       logging.info(f"Using first available manual transcript ({manual_langs[0]}) for {video_id}")

             else:
                transcript = transcript_list.find_generated_transcript(available_langs)
                logging.info(f"Using first available generated transcript ({available_langs[0]}) for {video_id}")

        transcript_text = " ".join([item['text'] for item in transcript.fetch()])
        logging.info(f"Successfully fetched transcript (length: {len(transcript_text)} chars) for video ID: {video_id}")
        return transcript_text
    except TranscriptsDisabled:
        logging.warning(f"Transcripts are disabled for video ID: {video_id}")
        return None
    except NoTranscriptFound:
        # This typically means the list_transcripts call itself failed or returned empty
        logging.warning(f"No transcript entries found (manual or generated) via API for video ID: {video_id}")
        return None
    except Exception as e:
        # Catch potential network errors during fetch etc.
        logging.error(f"Error fetching or processing transcript for video ID {video_id}: {e}", exc_info=True)
        return None


def generate_summary_with_gemini(transcript, prompt):
    """Generates content using Gemini based on the transcript and a prompt."""
    if not transcript:
        logging.error("generate_summary_with_gemini called with no transcript.")
        return "Error: No transcript provided."
    try:
        # Configure Gemini API - Ensure API key is handled securely
        genai.configure(api_key=GEMINI_API_KEY)

        generation_config = {
            "temperature": 0.7,
            "top_p": 1,
            "top_k": 1,
            "max_output_tokens": 8192, # Max for Gemini 1.5 Pro
        }

        # Map config strings to SDK enums if necessary (depends on SDK version)
        # For current google-generativeai, strings might work directly
        safety_dict = {}
        if SAFETY_SETTINGS:
             for category, threshold in SAFETY_SETTINGS.items():
                 # Add basic validation if needed
                 safety_dict[category] = threshold

        model = genai.GenerativeModel(model_name=GEMINI_MODEL,
                                      generation_config=generation_config,
                                      safety_settings=safety_dict if safety_dict else None)

        # Format the prompt ONLY if {transcript} is present
        if "{transcript}" in prompt:
             full_prompt = prompt.format(transcript=transcript)
        else:
             logging.warning("Prompt does not contain '{transcript}' placeholder. Sending prompt as-is.")
             full_prompt = prompt # Send the raw prompt if placeholder is missing

        logging.debug(f"Sending prompt to Gemini (first 100 chars): {full_prompt[:100]}...")

        # Add simple retry logic for potential transient API errors
        max_retries = 2
        retry_delay = 5 # seconds
        for attempt in range(max_retries):
            try:
                response = model.generate_content(full_prompt)

                # --- Improved Response Handling ---
                # 1. Check prompt feedback first for immediate blocks
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    block_reason = response.prompt_feedback.block_reason
                    safety_ratings_str = "N/A"
                    if response.prompt_feedback.safety_ratings:
                         safety_ratings_str = ", ".join([f"{rating.category}: {rating.probability}" for rating in response.prompt_feedback.safety_ratings])
                    logging.warning(f"Gemini prompt blocked. Reason: {block_reason}. Ratings: {safety_ratings_str}")
                    return f"[Blocked Prompt - Reason: {block_reason}]"

                # 2. Check if candidates exist
                if not response.candidates:
                    logging.warning("Gemini response has no candidates. Possibly blocked or empty.")
                    # Check finish reason if available in candidate-less response (might not be)
                    try:
                         finish_reason = response.candidates[0].finish_reason # This will fail if no candidates
                    except (IndexError, AttributeError):
                         finish_reason = "Unknown (No Candidates)"
                    return f"[No Content Generated - Finish Reason: {finish_reason}]"

                 # 3. Check the first candidate for content and finish reason
                candidate = response.candidates[0]
                if candidate.finish_reason != 1: # 1 typically means "STOP" (successful completion)
                    logging.warning(f"Gemini generation finished with non-standard reason: {candidate.finish_reason}")
                    # Reasons: 2=MAX_TOKENS, 3=SAFETY, 4=RECITATION, 5=OTHER

                if not candidate.content or not candidate.content.parts:
                    logging.warning("Gemini response candidate has no content parts.")
                    # Try getting text anyway, might reveal partial info or error
                    try:
                        response_text = response.text.strip()
                        if not response_text:
                             return f"[Empty Content - Finish Reason: {candidate.finish_reason}]"
                        else:
                             logging.warning(f"Candidate had no parts but response.text contained data: {response_text[:100]}")
                             return response_text # Return if text exists
                    except ValueError: # response.text can raise ValueError if blocked
                        return f"[Blocked Content or Empty - Finish Reason: {candidate.finish_reason}]"


                # If we got here, content should exist
                logging.info(f"Successfully received Gemini response (length: {len(response.text)} chars).")
                return response.text.strip() # Return the content

            except Exception as e:
                logging.error(f"Gemini API call failed on attempt {attempt + 1}/{max_retries}: {e}", exc_info=False)
                if attempt < max_retries - 1:
                    logging.info(f"Retrying Gemini API call after {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logging.error("Max retries reached for Gemini API call.")
                    return f"Error: Gemini API call failed after {max_retries} attempts - {e}"

        # Should not be reached if retry logic is correct, but as a fallback:
        return "Error: Failed to get response from Gemini after retries."


    except Exception as e:
        logging.error(f"General error during Gemini summary generation: {e}", exc_info=True)
        return f"Error: Failed to generate summary - {e}"


def save_summary_local(video_id, video_title, exec_summary, detailed_summary, key_quotes):
    """Saves the generated summaries to a local text file."""
    try:
        # Ensure the output directory exists
        if OUTPUT_DIR and not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR, exist_ok=True)

        safe_title = sanitize_filename(video_title)
        filename = os.path.join(OUTPUT_DIR, f"{video_id}_{safe_title}.txt")

        # Prepare content
        content = f"Video Title: {video_title}\n"
        content += f"Video ID: {video_id}\n"
        content += f"Video URL: https://www.youtube.com/watch?v={video_id}\n"
        content += f"Processed Date: {datetime.now().isoformat()}\n\n"
        content += "--- Executive Summary ---\n"
        content += f"{exec_summary}\n\n"
        content += "--- Detailed Summary ---\n"
        content += f"{detailed_summary}\n\n"
        content += "--- Key Quotes/Data Points ---\n"
        content += f"{key_quotes}\n"

        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        logging.info(f"Successfully saved summary to {filename}")
        return filename
    except Exception as e:
        logging.error(f"Error saving summary file for video {video_id}: {e}", exc_info=True)
        return None

def send_email_notification(channel_name, video_title, video_id, exec_summary, detailed_summary, key_quotes, recipient_list):
    """Sends an email notification with the video summary TO THE SPECIFIED RECIPIENTS."""
    if not recipient_list:
        logging.warning(f"No recipients provided for video '{video_title}' (ID: {video_id}) from channel '{channel_name}'. Skipping email notification.")
        return

    subject = f"New YouTube Video Summary: [{channel_name}] {video_title}"
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    # Basic HTML formatting for slightly better readability
    body_html = f"""
    <html>
    <head></head>
    <body>
        <p>A new video has been posted on the '{channel_name}' YouTube channel:</p>
        <p>
            <strong>Title:</strong> {video_title}<br>
            <strong>Link:</strong> <a href="{video_url}">{video_url}</a>
        </p>
        <hr>
        <h2>Executive Summary</h2>
        <p>{exec_summary.replace(chr(10), "<br>")}</p>
        <hr>
        <h2>Detailed Summary</h2>
        <p>{detailed_summary.replace(chr(10), "<br>")}</p>
        <hr>
        <h2>Key Quotes/Data Points</h2>
        <p>{key_quotes.replace(chr(10), "<br>")}</p>
    </body>
    </html>
    """

    message = MIMEMultipart('alternative') # Use alternative for HTML/Plain text
    message['From'] = SENDER_EMAIL
    message['To'] = ", ".join(recipient_list)
    message['Subject'] = subject

    # Attach HTML part
    message.attach(MIMEText(body_html, 'html', 'utf-8'))

    try:
        # Use context manager for SMTP connection
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60) as server: # Increased timeout
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            text = message.as_string()
            server.sendmail(SENDER_EMAIL, recipient_list, text)
        logging.info(f"Successfully sent email notification for video ID: {video_id} to {', '.join(recipient_list)}")
    except smtplib.SMTPAuthenticationError:
         logging.error(f"SMTP Authentication Error for user {SMTP_USER}. Check username/password/app password.", exc_info=False)
    except smtplib.SMTPRecipientsRefused as e:
         logging.error(f"SMTP Recipient Error for video ID {video_id}. Server refused recipients: {e.recipients}", exc_info=False)
    except smtplib.SMTPServerDisconnected:
        logging.error("SMTP Server disconnected unexpectedly. Check server/port/network.", exc_info=False)
    except smtplib.SMTPException as e:
         logging.error(f"General SMTP error sending email for video ID {video_id}: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"Unexpected error sending email for video ID {video_id}: {e}", exc_info=True)


def get_channel_name(youtube, channel_id):
    """Fetches the display name of a YouTube channel."""
    try:
        channel_response = youtube.channels().list(
            part='snippet',
            id=channel_id
        ).execute()
        if channel_response.get('items'):
            return channel_response['items'][0]['snippet']['title']
        else:
            logging.warning(f"Could not retrieve channel name for ID: {channel_id}")
            return channel_id # Fallback to ID
    except HttpError as e:
        logging.error(f"YouTube API error fetching channel name for {channel_id}: {e}", exc_info=True)
        return channel_id
    except Exception as e:
        logging.error(f"Unexpected error fetching channel name for {channel_id}: {e}", exc_info=True)
        return channel_id

# --- Main Execution ---
def main():
    start_time = time.time()
    logging.info("--- Starting YouTube Monitor Script ---")

    load_processed_videos() # Load processed IDs at the beginning

    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY, cache_discovery=False) # Disable discovery cache
    except Exception as e:
        logging.error(f"Failed to build YouTube API client: {e}", exc_info=True)
        sys.exit(1)

    new_videos_processed_count = 0
    channel_names = {} # Cache channel names to reduce API calls

    for channel_id in CHANNEL_IDS:
        if not channel_id or not channel_id.startswith("UC"): # Basic validation
            logging.warning(f"Skipping invalid channel ID entry: {repr(channel_id)}")
            continue

        logging.info(f"--- Checking Channel ID: {channel_id} ---")

        # Get channel name (use cache)
        if channel_id not in channel_names:
            channel_names[channel_id] = get_channel_name(youtube, channel_id)
            time.sleep(0.3) # Small delay after channel name lookup

        channel_name = channel_names[channel_id]
        logging.info(f"Processing channel: '{channel_name}' ({channel_id})")

        latest_videos = get_latest_videos(youtube, channel_id, MAX_RESULTS_PER_CHANNEL)

        if not latest_videos:
            # Logged within get_latest_videos if needed
            continue # Move to the next channel

        for video in latest_videos:
            video_id = video['id']
            video_title = video['title']
            # video_channel_id = video['channel_id'] # Redundant, we are in the channel_id loop

            # --- Core Logic: Check if video is new and process ---
            if video_id in processed_video_ids:
                logging.info(f"Video '{video_title}' (ID: {video_id}) already processed. Skipping.")
                continue

            logging.info(f"Found new video: '{video_title}' (ID: {video_id}). Processing...")

            # 1. Get Transcript
            transcript = get_transcript(video_id)
            if not transcript:
                logging.warning(f"Could not get transcript for '{video_title}' ({video_id}). Skipping summarization and notification for this video.")
                # Don't mark as processed, transcript might appear later
                continue # Skip to next video

            time.sleep(1) # Short delay before hitting Gemini

            # 2. Generate Summaries
            logging.info(f"Generating summaries for '{video_title}'...")
            exec_summary = generate_summary_with_gemini(transcript, PROMPT_EXEC_SUMMARY)
            time.sleep(1)
            detailed_summary = generate_summary_with_gemini(transcript, PROMPT_DETAILED_SUMMARY)
            time.sleep(1)
            key_quotes = generate_summary_with_gemini(transcript, PROMPT_KEY_QUOTES)

            # Log first part of results for confirmation
            logging.debug(f"Exec Summary for {video_id}: {exec_summary[:100]}...")
            logging.debug(f"Detailed Summary for {video_id}: {detailed_summary[:100]}...")
            logging.debug(f"Key Quotes for {video_id}: {key_quotes[:100]}...")

            # Check for Gemini generation errors before proceeding
            is_error = False
            for summary in [exec_summary, detailed_summary, key_quotes]:
                 if summary is None or summary.startswith("Error:") or "[Blocked" in summary or "[No Content" in summary:
                     is_error = True
                     break # Found an error, no need to check further

            if is_error:
                 logging.error(f"One or more summaries failed generation or were blocked for video {video_id}. Saving locally but skipping email notification.")
                 # Still save locally what was generated (might contain error messages)
                 save_summary_local(video_id, video_title, exec_summary, detailed_summary, key_quotes)
                 processed_video_ids.add(video_id) # Mark as processed to avoid retrying a failed video
                 new_videos_processed_count += 1
                 continue # Skip email for this video


            # 3. Save Locally
            save_summary_local(video_id, video_title, exec_summary, detailed_summary, key_quotes)

            # 4. Determine Recipients and Send Email
            recipients_for_this_channel = channel_recipients.get(channel_id) # Uses original case key

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
                    channel_name,
                    video_title,
                    video_id,
                    exec_summary,
                    detailed_summary,
                    key_quotes,
                    final_recipient_list
                )

            # 5. Mark as Processed (only if successful through generation)
            processed_video_ids.add(video_id)
            new_videos_processed_count += 1

            time.sleep(2) # Small delay between processing videos from the same channel if multiple are found

        time.sleep(3) # Small delay between checking different channels

    # --- Finalization ---
    save_processed_videos() # Save updated list after processing all channels
    end_time = time.time()
    duration = end_time - start_time
    logging.info(f"--- YouTube Monitor Script Finished ---")
    logging.info(f"Processed {new_videos_processed_count} new videos in this run.")
    logging.info(f"Total execution time: {duration:.2f} seconds.")

if __name__ == "__main__":
    main()