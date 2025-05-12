# -*- coding: utf-8 -*-
# vim: set fileencoding=utf-8 :
import os
import sys
import configparser
import json
import logging # Ensure logging is imported early
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
import time
import re # For cleaning filenames and parsing duration
import html # Added for escaping in HTML fallback

# Third-party libraries (install via requirements.txt)
import google.generativeai as genai
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import markdown # Added for Markdown to HTML conversion

# --- Configuration Loading ---
CONFIG_FILE = 'config.ini'
config = configparser.ConfigParser()
# Preserve the case of keys read from the config file
config.optionxform = str

if not os.path.exists(CONFIG_FILE):
    print(f"ERROR: Configuration file '{CONFIG_FILE}' not found. Please create it based on the template.", file=sys.stderr)
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
            # Parse comma-separated key:value pairs into a dictionary
            settings_dict = {}
            for item in SAFETY_SETTINGS_RAW.split(','):
                if ':' in item:
                    key, value = item.strip().split(':', 1) # Split only on first colon
                    key = key.strip()
                    value = value.strip()
                    settings_dict[key] = value # Store as string, Gemini library might handle conversion

            # Basic validation/mapping (optional but good practice)
            valid_categories = ['HARM_CATEGORY_HARASSMENT', 'HARM_CATEGORY_HATE_SPEECH', 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'HARM_CATEGORY_DANGEROUS_CONTENT']

            SAFETY_SETTINGS = {k: v for k, v in settings_dict.items() if k in valid_categories or k.startswith('HARM_CATEGORY_')} # Basic check
            if len(SAFETY_SETTINGS) != len(settings_dict):
                 ignored_keys = set(settings_dict.keys()) - set(SAFETY_SETTINGS.keys())
                 print(f"Warning: Ignoring potentially invalid safety settings keys: {', '.join(ignored_keys)}. Valid keys start with HARM_CATEGORY_.", file=sys.stderr)

            if not SAFETY_SETTINGS:
                 print(f"Warning: safety_settings were provided but none matched expected HARM_CATEGORY_ format or were parsed. Using default safety settings.", file=sys.stderr)
                 SAFETY_SETTINGS = None # Ensure it's None if parsing failed or keys were invalid
            else:
                 print(f"INFO: Parsed safety_settings: {SAFETY_SETTINGS}", file=sys.stderr)


        except Exception as e:
            print(f"Warning: Could not parse safety_settings: {e}. Using default safety settings.", file=sys.stderr)
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
                if channel_id_key.lower() == 'default_recipients': # Use .lower() for case-insensitivity
                    default_recipients = emails
                    print(f"INFO: Loaded default recipients: {', '.join(default_recipients)}", file=sys.stderr)
                else:
                    # Basic validation for channel ID format
                    if channel_id_key.startswith("UC") and len(channel_id_key) == 24:
                         channel_recipients[channel_id_key] = emails
                         print(f"INFO: Loaded recipients for channel {repr(channel_id_key)}: {', '.join(emails)}", file=sys.stderr)
                    else:
                         print(f"WARNING: Ignoring potentially invalid key in [CHANNEL_RECIPIENTS]: {repr(channel_id_key)}. Keys should be YouTube Channel IDs (starting with UC) or 'default_recipients'.", file=sys.stderr)
    else:
        print("WARNING: Configuration section '[CHANNEL_RECIPIENTS]' is missing. Cannot determine email recipients.", file=sys.stderr)

    # Script Settings
    PROCESSED_VIDEOS_FILE = config.get('SETTINGS', 'processed_videos_file', fallback='processed_videos.json')
    LOG_FILE = config.get('SETTINGS', 'log_file', fallback='monitor.log')
    OUTPUT_DIR = config.get('SETTINGS', 'output_dir', fallback='output')
    MAX_RESULTS_PER_CHANNEL = config.getint('SETTINGS', 'max_results_per_channel', fallback=1)
    MIN_DURATION_MINUTES = config.getint('SETTINGS', 'min_video_duration_minutes', fallback=0)
    print(f"INFO: Minimum video duration set to: {MIN_DURATION_MINUTES} minutes (0 means no minimum).", file=sys.stderr)


    # Validate Essential Configuration
    errors = []
    if not YOUTUBE_API_KEY or YOUTUBE_API_KEY == 'YOUR_YOUTUBE_DATA_API_V3_KEY': errors.append("Missing or placeholder 'youtube_api_key' in [API_KEYS]")
    if not GEMINI_API_KEY or GEMINI_API_KEY == 'YOUR_GEMINI_API_KEY': errors.append("Missing or placeholder 'gemini_api_key' in [API_KEYS]")
    if not CHANNEL_IDS: errors.append("Missing or empty 'channel_ids' in [CHANNELS]")
    if not SMTP_SERVER: errors.append("Missing 'smtp_server' in [EMAIL]")
    if not SMTP_USER: errors.append("Missing 'smtp_user' in [EMAIL]")
    if not SMTP_PASSWORD or SMTP_PASSWORD == 'YOUR_EMAIL_PASSWORD_OR_APP_PASSWORD': errors.append("Missing or placeholder 'smtp_password' in [EMAIL]")
    if not SENDER_EMAIL: errors.append("Missing 'sender_email' in [EMAIL]")
    if not default_recipients and not channel_recipients: errors.append("No email recipients configured. Please define 'default_recipients' or specific channel recipients in the '[CHANNEL_RECIPIENTS]' section.")
    elif not default_recipients: print("WARNING: No 'default_recipients' configured in [CHANNEL_RECIPIENTS]. Emails will only be sent for channels with specific recipient lists.", file=sys.stderr)

    if errors:
        print("--- CONFIGURATION ERRORS ---", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        print(f"Please check your '{CONFIG_FILE}' file.", file=sys.stderr)
        sys.exit(1)

except configparser.ParsingError as e:
    print(f"ERROR: Failed to parse '{CONFIG_FILE}'. Check syntax, especially multi-line values (use indentation).", file=sys.stderr)
    print(f"Parser errors:\n{e}", file=sys.stderr)
    sys.exit(1)
except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as e:
    print(f"ERROR reading configuration file '{CONFIG_FILE}': {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"An unexpected error occurred during configuration loading: {e}", file=sys.stderr)
    # import traceback
    # traceback.print_exc() # Uncomment for more detail if needed
    sys.exit(1)


# --- Logging Setup ---
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError as e:
        print(f"ERROR: Could not create log directory '{log_dir}': {e}", file=sys.stderr)
        LOG_FILE = os.path.basename(LOG_FILE) # Fallback to current dir
        print(f"WARNING: Falling back to log file in current directory: '{LOG_FILE}'", file=sys.stderr)

# Configure logging now that LOG_FILE is potentially adjusted
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()])


# Global Variables
processed_video_ids = set()

# Helper Functions

def sanitize_filename(filename):
    """Sanitizes a string to be safe for use as a filename."""
    # Decode any potential non-UTF-8 bytes first if necessary (though the original issue seems to be encoding *to* latin-1)
    try:
        filename = filename.encode('utf-8').decode('utf-8') # Explicitly encode to utf-8 and decode back
    except UnicodeDecodeError:
        logging.warning(f"Could not decode filename string before sanitization: {filename}. Proceeding with raw string.")
        pass # Proceed with potentially problematic string

    filename = filename.replace(u'\ufffd', '_') # Replace replacement character
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename) # Remove characters illegal in Windows/Linux filenames
    sanitized = re.sub(r'\s+', '_', sanitized) # Replace spaces with underscores
    sanitized = re.sub(r'_+', '_', sanitized) # Replace multiple underscores with a single one
    sanitized = sanitized.strip('_') # Remove leading/trailing underscores

    # Replace specific problematic characters like \u2019 (right single quotation mark)
    sanitized = sanitized.replace('\u2019', "'").replace('\u2018', "'") # Replace curly quotes with straight ones
    sanitized = sanitized.replace('\u201c', '"').replace('\u201d', '"') # Replace curly double quotes with straight ones
    sanitized = sanitized.replace('\u2013', '-').replace('\u2014', '--') # Replace various dashes

    # Ensure the final filename is safe and within a reasonable length
    return sanitized[:150] # Truncate to a reasonable length


def load_processed_videos():
    """Loads the set of already processed video IDs from a JSON file."""
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
                # Convert list back to set upon loading
                processed_video_ids = set(json.loads(content))
                logging.info(f"Loaded {len(processed_video_ids)} processed video IDs from {PROCESSED_VIDEOS_FILE}")
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {PROCESSED_VIDEOS_FILE}. Starting fresh.", exc_info=True)
        processed_video_ids = set()
    except Exception as e:
        logging.error(f"Error loading processed videos file: {e}", exc_info=True)
        processed_video_ids = set() # Start fresh on any load error

def save_processed_videos():
    """Saves the current set of processed video IDs to a JSON file."""
    try:
        proc_dir = os.path.dirname(PROCESSED_VIDEOS_FILE)
        if proc_dir and not os.path.exists(proc_dir):
            os.makedirs(proc_dir, exist_ok=True)
        # Save set as a list (JSON can't serialize sets)
        with open(PROCESSED_VIDEOS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(processed_video_ids), f, indent=4)
        logging.debug(f"Saved {len(processed_video_ids)} processed video IDs to {PROCESSED_VIDEOS_FILE}")
    except Exception as e:
        logging.error(f"Error saving processed videos file: {e}", exc_info=True)


def parse_iso8601_duration(duration_string):
    """Parses an ISO 8601 duration string (like PT1H2M3S) into total seconds."""
    if not duration_string or not duration_string.startswith('PT'):
        return 0
    # Remove the 'PT' prefix
    duration_string = duration_string[2:]

    total_seconds = 0
    # Use regex to find hours, minutes, and seconds
    hours_match = re.search(r'(\d+)H', duration_string)
    minutes_match = re.search(r'(\d+)M', duration_string)
    seconds_match = re.search(r'(\d+)S', duration_string)

    if hours_match:
        total_seconds += int(hours_match.group(1)) * 3600
    if minutes_match:
        total_seconds += int(minutes_match.group(1)) * 60
    if seconds_match:
        total_seconds += int(seconds_match.group(1))

    return total_seconds

def format_duration_seconds(total_seconds):
    """Formats total seconds into HH:MM:SS or MM:SS string."""
    if total_seconds is None or total_seconds < 0:
        return "N/A"
    total_seconds = int(total_seconds) # Ensure it's an integer
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        return f"{hours:01d}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes:02d}:{seconds:02d}"

def get_video_details(youtube, video_id):
    """Fetches content details (like duration) for a given video ID."""
    try:
        video_response = youtube.videos().list(
            part='contentDetails',
            id=video_id
        ).execute()

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
    """Fetches the latest videos for a channel, focusing on recent uploads."""
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
        logging.debug(f"Found uploads playlist ID: {uploads_playlist_id} for channel {channel_id}")

        # 2. Get the most recent videos from the uploads playlist
        playlist_items_response = youtube.playlistItems().list(
            part='snippet,contentDetails',
            playlistId=uploads_playlist_id,
            maxResults=max_results # Get more results than we might need to find recent ones
        ).execute()

        videos = []
        # Define 'recent' threshold - e.g., anything posted in the last 24 hours plus a buffer
        # This helps catch videos even if the script runs infrequently
        # Using timezone.utc is important for comparing against the API's UTC timestamps
        recent_threshold = datetime.now(timezone.utc) - timedelta(hours=25) # Adjust buffer as needed

        for item in playlist_items_response.get('items', []):
            video_id = item['contentDetails']['videoId']
            video_title = item['snippet']['title']
            published_at_str = item['snippet']['publishedAt']
            # Parse the published date string, assuming it's in ISO format with Z (UTC)
            published_at = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))

            # Check if the video is recent enough
            if published_at >= recent_threshold:
                 videos.append({
                     'id': video_id,
                     'title': video_title,
                     'published_at': published_at,
                     'channel_id': channel_id
                 })
                 logging.debug(f"Video '{video_title}' (ID: {video_id}) published at {published_at} IS recent enough (>{recent_threshold.isoformat()}).")
            else:
                 logging.debug(f"Video '{video_title}' (ID: {video_id}) published at {published_at} is older than threshold ({recent_threshold.isoformat()}), skipping.")

        # Sort videos by publish date, newest first
        videos.sort(key=lambda x: x['published_at'], reverse=True)

        count_found_in_playlist = len(playlist_items_response.get('items', []))
        count_recent = len(videos)
        logging.info(f"Checked {count_found_in_playlist} most recent playlist items for channel {channel_id}. Found {count_recent} published since {recent_threshold.strftime('%Y-%m-%d %H:%M:%S %Z')}.")

        # Return only up to MAX_RESULTS_PER_CHANNEL *recent* videos
        return videos[:max_results]

    except HttpError as e:
        if e.resp.status == 403:
             logging.error(f"YouTube API quota error fetching videos for channel {channel_id}: {e}", exc_info=False) # Don't print full traceback for quota
        else:
             logging.error(f"YouTube API HTTP error fetching videos for channel {channel_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logging.error(f"Unexpected error fetching videos for channel {channel_id}: {e}", exc_info=True)
        return []


def get_transcript(video_id):
    """Fetches the transcript for a video, prioritizing English or first available."""
    if not video_id:
         logging.warning("get_transcript called with empty video_id.")
         return None

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Try to find an English transcript first
        try:
            # Prefer generated English if available, then manual
            transcript = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
            logging.debug(f"Found English transcript for {video_id}")
        except NoTranscriptFound:
             logging.debug(f"No direct English transcript found for {video_id}. Checking for any generated transcript.")
             available_generated_langs = list(transcript_list._generated_transcripts.keys())
             if available_generated_langs:
                   # Find the first available generated transcript
                   transcript = transcript_list.find_generated_transcript(available_generated_langs)
                   logging.info(f"Using first available generated transcript ({available_generated_langs[0]}) for {video_id}")
             else:
                   logging.debug(f"No generated transcripts found for {video_id}. Checking for manual transcripts.")
                   available_manual_langs = list(transcript_list._manually_created_transcripts.keys())
                   if available_manual_langs:
                       # Find the first available manual transcript
                       transcript = transcript_list.find_manually_created_transcript(available_manual_langs)
                       logging.info(f"Using first available manual transcript ({available_manual_langs[0]}) for {video_id}")
                   else:
                       logging.warning(f"No generated or manual transcripts found for video ID: {video_id}")
                       return None # No transcript available at all

        # Fetch the transcript text
        transcript_text = " ".join([item.text for item in transcript.fetch()])
        logging.info(f"Successfully fetched transcript (length: {len(transcript_text)} chars) for video ID: {video_id}")
        return transcript_text

    except TranscriptsDisabled:
        logging.warning(f"Transcripts are disabled for video ID: {video_id}")
        return None
    except NoTranscriptFound:
        # This catch is redundant with the internal logic but kept for safety
        logging.warning(f"No transcript entries found (manual or generated) via API for video ID: {video_id}")
        return None
    except Exception as e:
        logging.error(f"Error fetching or processing transcript for video ID {video_id}: {e}", exc_info=True)
        return None


def generate_summary_with_gemini(transcript, prompt):
    """Sends transcript and prompt to Gemini AI to get a summary."""
    if not transcript:
        logging.error("generate_summary_with_gemini called with no transcript.")
        return "Error: No transcript provided."
    if not prompt:
         logging.error("generate_summary_with_gemini called with no prompt.")
         return "Error: No prompt provided."

    try:
        genai.configure(api_key=GEMINI_API_KEY)

        generation_config = {
            "temperature": 0.7,
            "top_p": 1,
            "top_k": 1,
            "max_output_tokens": 8192,
        }

        safety_settings_mapped = None # Initialize as None
        try: # INNER try for initial safety settings parsing
            if SAFETY_SETTINGS:
                 # Attempt simple string-based mapping. Consult docs if errors occur.
                 # The API expects a list of tuples: [(category, threshold), ...]
                 safety_settings_mapped = list(SAFETY_SETTINGS.items())
        except Exception as e: # INNER except for safety settings parsing
             logging.error(f"Failed to map safety settings values: {e}. Attempting with original strings/defaults.", exc_info=False)
             # On failure, keep safety_settings_mapped as None or try original dict items
             # For now, let's ensure it's explicitly set to None if parsing the list of tuples fails
             safety_settings_mapped = None
             logging.warning("Proceeding without specific safety settings.")


        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            generation_config=generation_config,
            safety_settings=safety_settings_mapped # Pass mapped settings or None
        )

        # Prepare the full prompt
        if "{transcript}" in prompt:
            full_prompt = prompt.format(transcript=transcript)
        else:
             logging.warning("Prompt does not contain '{transcript}' placeholder. Appending transcript to prompt.")
             full_prompt = prompt + "\n\n" + transcript

        logging.debug(f"Sending prompt to Gemini (first 200 chars): {full_prompt[:200]}...")
        logging.debug(f"Prompt length: {len(full_prompt)} characters.")


        max_retries = 3
        retry_delay = 10

        for attempt in range(max_retries):
            try: # OUTER try of the retry loop (for generate_content call)
                response = model.generate_content(full_prompt)

                # Check for prompt feedback (e.g., blocking before generation)
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    block_reason = response.prompt_feedback.block_reason
                    safety_ratings_str = "N/A"
                    if response.prompt_feedback.safety_ratings:
                         safety_ratings_str = ", ".join([f"{rating.category}: {rating.probability}" for rating in response.prompt_feedback.safety_ratings])
                    logging.warning(f"Gemini prompt blocked. Reason: {block_reason}. Ratings: {safety_ratings_str}")
                    return f"[Blocked Prompt - Reason: {block_reason}]" # Return exits the function

                # Check if any candidates were generated
                if not response.candidates:
                    logging.warning("Gemini response has no candidates. Possibly blocked, empty, or API issue.")
                    finish_reason = "Unknown (No Candidates)"
                    # Attempt to get finish reason if candidates list is just empty, not missing
                    try:
                        if response.candidates is not None and len(response.andidates) > 0 and hasattr(response.candidates[0], 'finish_reason') and response.candidates[0].finish_reason is not None:
                             finish_reason = response.candidates[0].finish_reason
                        elif hasattr(response, 'usage_metadata') and hasattr(response.usage_metadata, 'finish_reason') and response.usage_metadata.finish_reason is not None:
                             finish_reason = response.usage_metadata.finish_reason
                    except Exception: # Ignore errors trying to get reason details
                        pass

                    return f"[No Content Generated - Finish Reason: {finish_reason}]" # Return exits the function


                # Check if the *first* candidate has content parts
                candidate = response.candidates[0]
                if not candidate.content or not candidate.content.parts:
                    logging.warning("Gemini response candidate has no content parts.")
                    # Re-aligning the try block and its contents
                    try:
                        response_text = response.text.strip()
                        if not response_text:
                            return f"[Empty Content Parts - Finish Reason: {candidate.finish_reason}]" # Return exits the function
                        # Corrected indentation for the 'else' block
                        else:
                             logging.warning(f"Candidate had no parts but response.text contained data (first 100 chars): {response_text[:100]}...")
                             return response_text # Return exits the function
                    # Corrected indentation for the 'except ValueError' block
                    except ValueError:
                         logging.warning(f"Gemini response.text blocked or not available.")
                         return f"[Blocked Content or Empty Text]" # Return exits the function

                # Execution continues here if the inner try/except block above completed without returning.

                # Check if the generation finished due to safety or other non-STOP reasons
                if candidate.finish_reason != 1: # 1 typically means STOP. Check if not STOP.
                     logging.warning(f"Gemini generation finished with non-standard reason: {candidate.finish_reason}")
                     if candidate.finish_reason == 3: # Reason 3 often indicates SAFETY stop.
                          safety_ratings_str = "N/A"
                          if candidate.safety_ratings:
                              safety_ratings_str = ", ".join([f"{rating.category}: {rating.probability}" for rating in candidate.safety_ratings])
                          logging.warning(f"Generation stopped due to safety reasons. Ratings: {safety_ratings_str}")
                          # Return partial content if available, plus a warning message
                          return f"[Generation Stopped by Safety - Reason: {candidate.finish_reason}] {response.text.strip()}" # Return exits the function
                     else: # Handle other non-safety finish reasons if needed
                          return f"[Generation Finished with Reason {candidate.finish_reason}] {response.text.strip()}" # Return exits the function


                # If all checks pass and finish_reason is STOP, return the generated text
                logging.info(f"Successfully received Gemini response (length: {len(response.text)} chars).")
                return response.text.strip() # Return exits the function

            except Exception as e: # OUTER except for the retry loop
                logging.error(f"Gemini API call failed on attempt {attempt + 1}/{max_retries}: {e}", exc_info=False)
                if attempt < max_retries - 1:
                    logging.info(f"Retrying Gemini API call after {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else: # Max retries reached
                    logging.error("Max retries reached for Gemini API call.")
                    return f"Error: Gemini API call fled after {max_retries} attempts - {e}" # Return exits the function

        # This code is after the for loop.
        # It's only reached if the for loop somehow completes without hitting a return *inside* any of the try/except blocks.
        # This shouldn't happen with the current logic, but add a final fallback return just in case.
        logging.error("Gemini generation loop finished without returning. This indicates a logic error.")
        return "Error: Gemini generation logic error - loop finished unexpectedly."

    except Exception as e: # OUTERMOST except
        logging.error(f"General error during Gemini summary generation: {e}", exc_info=True)
        return f"Error: Failed to generate summary - {e}"


# This is Level 0, starting a new function definition.
def save_summary_local(video_id, video_title, duration_str, exec_summary, detailed_summary, key_quotes):
    """Saves the generated summaries to a local text file."""
    try:
        if OUTPUT_DIR and not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR, exist_ok=True)

        # Sanitize the video title for use in the filename
        safe_title = sanitize_filename(video_title)

        filename = os.path.join(OUTPUT_DIR, f"{video_id}_{safe_title}.txt")

        # Initialize the content variable
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

        # Open the file with utf-8 encoding, explicitly
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)

        logging.info(f"Successfully saved summary to {filename}")
        return filename

    except Exception as e:
        logging.error(f"Error saving summary file for video {video_id}: {e}", exc_info=True)
        return None


def send_email_notification(channel_name, video_title, video_id, duration_str, exec_summary, detailed_summary, key_quotes, recipient_list):
    """Sends an email notification with the video summaries."""
    if not recipient_list:
        logging.warning(f"No recipients provided for video '{video_title}' (ID: {video_id}) from channel '{channel_name}'. Skipping email notification.")
        return

    subject = f"New YouTube Video Summary: [{channel_name}] {video_title}"
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    # --- Convert Markdown Summaries to HTML ---
    try:
        exec_summary_html = markdown.markdown(exec_summary)
        detailed_summary_html = markdown.markdown(detailed_summary)
        key_quotes_html = markdown.markdown(key_quotes)
        logging.debug(f"Successfully converted Markdown to HTML for video {video_id}.")
    except Exception as e:
        logging.error(f"Error converting Markdown to HTML for video {video_id}: {e}", exc_info=True)
        # Fallback: Use plain text summaries with basic newline replacement, wrapped in <pre>
        logging.warning("Falling back to plain text for email body due to Markdown conversion error.")
        # Escape HTML entities in fallback to prevent rendering issues
        exec_summary_escaped = html.escape(exec_summary)
        detailed_summary_escaped = html.escape(detailed_summary)
        key_quotes_escaped = html.escape(key_quotes)

        exec_summary_html = f"<pre>{exec_summary_escaped}</pre>"# Use <pre> to preserve formatting a bit
        detailed_summary_html = f"<pre>{detailed_summary_escaped}</pre>"
        key_quotes_html = f"<pre>{key_quotes_escaped}</pre>"


    # Construct the HTML email body using the converted summaries
    body_html = f"""
    <html>
    <head></head>
    <body>
        <p>A nw video has been posted on the '{channel_name}' YouTube channel:</p>
        <p>
            <strong>Title:</strong> {video_title}<br>
            {f'<strong>Duration:</strong> {duration_str}<br>' if duration_str else ''}
            <strong>Link:</strong> <a href="{video_url}">{video_url}</a>
        </p>
        <hr>
        <h2>Executive Summary</h2>
        <div>{exec_summary_html}</div> <!-- Insert the HTML converted summary -->
        <hr>
        <h2>Detailed Summary</h2>
        <div>{detailed_summary_html}</div> <!-- Insert the HTML converted summary -->
        <hr>
        <h2>Key Quotes/Data Points</h2>
        <div>{key_quotes_html}</div> <!-- Insert the HTML converted summary -->
        <hr>
        <p><i>Summaries generated by Gemini AI.</i></p>
    </body>
    </html>
    """ # End of multi-line string literal

    # Create the email message
    message = MIMEMultipart('alternative')
    message['From'] = SENDER_EMAIL
    message['To'] = ", ".join(recipient_list) # Join list for 'To' header
    message['Subject'] = subject

    # Attach the HTML body
    message.attach(MIMEText(body_html, 'html', 'utf-8'))

    # Send the email
    try:
        # Use context manager for SMTP connection
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60) as server:
            server.ehlo()  # Can be omitted
            server.starttls() # Secure the connection
            server.ehlo()  # Can be omitted
            server.login(SMTP_USER, SMTP_PASSWORD)
            # sendmail expects a list of recipients
            server.sendmail(SENDER_EMAIL, recipient_list, message.as_string())

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
    """Fetches the channel name for a given channel ID."""
    try:
        channel_response = youtube.channels().list(
            part='snippet',
            id=channel_id
        ).execute()

        if channel_response.get('items'):
            return channel_response['items'][0]['snippet']['title']
        else:
            logging.warning(f"Could not retrieve channel name for ID: {channel_id}")
            return channel_id # Return the ID if name not found

    except HttpError as e:
        logging.error(f"YouTube API error fetching channel name for {channel_id}: {e}", exc_info=True)
        return channel_id # Return ID on error
    except Exception as e:
        logging.error(f"Unexpected error fetching channel name for {channel_id}: {e}", exc_info=True)
        return channel_id # Return ID on error


# --- Main Execution ---
def main():
    start_time = time.time()
    new_videos_processed_count = 0
    channel_names = {} # Cache channel names to avoid repeated API calls

    # Log the script start messge now that logging is configured
    logging.info("--- Starting YouTube Monitor Script ---")
    logging.info(f"Minimum video duration threshold: {MIN_DURATION_MINUTES} minutes.")
    if default_recipients:
         logging.info(f"Default recipients: {', '.join(default_recipients)}")
    if channel_recipients:
         logging.info(f"Channel-specific recipients configured for {len(channel_recipients)} channels.")


    load_processed_videos()

    try:
        # Build YouTube API client
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY, cache_discovery=False)
    except Exception as e:
        logging.error(f"Failed to build YouTube API client: {e}", exc_info=True)
        sys.exit(1) # Exit if API client cannot be built

    # Iterate through each channel to monitor
    for channel_id in CHANNEL_IDS:
        # Basic validation for channel ID format
        if not channel_id or not channel_id.startswith("UC") or len(channel_id) != 24:
            logging.warning(f"Skipping invalid channel ID entry: {repr(channel_id)}")
            continue

        logging.info(f"--- Checking Channel ID: {channel_id} ---")

        # Get channel name (cached)
        # Use get with fallback in case name fetching failed
        channel_name = channel_names.get(channel_id)
        if channel_name is None: # Check if name is not yet cached or failed to fetch
            channel_names[channel_id] = get_channel_name(youtube, channel_id)
            channel_name = channel_names[channel_id] # Use the fetched name (or ID if failed)
            time.sleep(0.3) # Small delay between API calls

        logging.info(f"Processing channel: '{channel_name}' ({channel_id})")

        # Get the latest videos for this channel
        latest_videos = get_latest_videos(youtube, channel_id, MAX_RESULTS_PER_CHANNEL + 5) # Fetch slightly more than needed to ensure we find recent ones

        if not latest_videos:
            logging.info(f"No new recent videos found for channel '{channel_name}'.")
            continue

        # Process each potential new video (sorted by publish date)
        for video in latest_videos:
            video_id = video['id']
            video_title = video['title']
            video_published_at = video['published_at']

            # Check if video has already been processed
            if video_id in processed_video_ids:
                logging.info(f"Video '{video_title}' (ID: {video_id}, Published: {video_published_at}) already processed. Skipping.")
                continue

            logging.info(f"Found potential new video: '{video_title}' (ID: {video_id}, Published: {video_published_at.strftime('%Y-%m-%d %H:%M:%S %Z')}). Fetching details...")

            # Get video duration
            duration_iso = get_video_details(youtube, video_id)
            duration_seconds = 0
            formatted_duration_str = None

            if duration_iso:
                duration_seconds = parse_iso8601_duration(duration_iso)
                formatted_duration_str = format_duration_seconds(duration_seconds)
                logging.debug(f"Video '{video_title}' duration: {formatted_duration_str} ({duration_seconds} seconds), ISO: {duration_iso}")
            else:
                logging.warning(f"Could not determine duration for video '{video_title}' (ID: {video_id}). Proceeding without duration check/info.")

            # Apply Minimum Duration Filter
            if MIN_DURATION_MINUTES > 0 and duration_seconds > 0:
                min_duration_seconds = MIN_DURATION_MINUTES * 60
                if duration_seconds < min_duration_seconds:
                    logging.info(f"Video '{video_title}' ({formatted_duration_str}) is shorter than the minimum {MIN_DURATION_MINUTES} minutes. Skipping processing.")
                    # Mark as processed so it's not checked again
                    processed_video_ids.add(video_id)
                    new_videos_processed_count += 1
                    save_processed_videos() # Save immediately after adding
                    continue

            # --- Video Processing ---
            logging.info(f"Processing video '{video_title}' (ID: {video_id}). Duration: {formatted_duration_str if formatted_duration_str else 'N/A'}")

            # Get transcript
            transcript = get_transcript(video_id)
            if not transcript:
                logging.warning(f"Could not get transcript for '{video_title}' ({video_id}). Skipping summarization and notification for this video.")
                # Decide whether to mark as processed or retry later.
                # Marking as processed prevents repeated attempts on videos with disabled transcripts.
                processed_video_ids.add(video_id)
                new_videos_processed_count += 1
                save_processed_videos()
                continue

            time.sleep(1) # Small delay before hitting Gemini API

            # Generate summaries using Gemini
            logging.info(f"Generating summaries for '{video_title}' using Gemini...")
            exec_summary = generate_summary_with_gemini(transcript, PROMPT_EXEC_SUMMARY)
            time.sleep(1) # Delay between Gemini calls
            detailed_summary = generate_summary_with_gemini(transcript, PROMPT_DETAILED_SUMMARY)
            time.sleep(1) # Delay between Gemini calls
            key_quotes = generate_summary_with_gemini(transcript, PROMPT_KEY_QUOTES)

            # Check if summaries indicate an error or blocking
            is_error_in_summary = False
            error_messages = []
            for summary in [exec_summary, detailed_summary, key_quotes]:
                 # Check if it's a string AND starts with Error/Blocked/No Content
                 if isinstance(summary, str) and (summary.startswith("Error:") or "[Blocked" in summary or "[No Content" in summary):
                     is_error_in_summary = True
                     error_messages.append(summary)
                     # No break here, collect all error messages if needed, or add break if you only care about the first error
                     # break # Add break here if you want to stop checking after the first error is found
                 # Also consider None as an error case
                 elif summary is None:
                      is_error_in_summary = True
                      error_messages.append("None/Empty Summary")
                      # No break here either
                      # break # Add break here if you want to stop checking after None is found


            logging.debug(f"Summary generation check for {video_id}. is_error_in_summary = {is_error_in_summary}")

            # Save summaries locally regardless of email success/failure
            logging.debug(f"Calling save_summary_local for video ID: {video_id}")
            saved_file_path = save_summary_local(video_id, video_title, formatted_duration_str, exec_summary, detailed_summary, key_quotes)

            if not saved_file_path:
                 logging.error(f"FAILED to save summary file locally for video {video_id}.")
                 # Decide if you want to mark as processed even if saving failed.
                 # For now, we will, assuming the main goal is not re-processing.
                 processed_video_ids.add(video_id)
                 new_videos_processed_count += 1
                 save_processed_videos()
                 continue # Skip email if local save failed

            # If there was an error during Gemini processing
            if is_error_in_summary:
                 logging.error(f"One or more summaries failed generation or were blocked for video {video_id}. Summaries saved locally,but skipping email notification. Errors: {', '.join(error_messages)}")
                 # Mark as processed since we saved the error locally and likely can't get a better summary without intervention
                 processed_video_ids.add(video_id)
                 new_videos_processed_count += 1
                 save_processed_videos()
                 continue # Skip email

            # Determine recipients
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
                final_recipient_list = [] # Ensure it's empty if no recipients found

            # Send email notification
            if final_recipient_list:
                logging.info(f"Attempting to send email for video ID: {video_id} to {', '.join(final_recipient_list)}")
                send_email_notification(
                    channel_name, video_title, video_id, formatted_duration_str,
                    exec_summary, detailed_summary, key_quotes, final_recipient_list
                )
            else:
                logging.warning(f"Skipping email for {video_id} due to no recipients found.")

            # Mark video as processed after successful (or attempted) processing and notification
            logging.debug(f"Adding video {video_id} to processed set.")
            processed_video_ids.add(video_id)
            new_videos_processed_count += 1

            # This save happens for *each* processed video
            save_processed_videos()

            # Add a small delay between processing videos within a channel
            time.sleep(2)

        # End 'for video' loop

        # Add a delay between checking different channels
        time.sleep(3)

    # End 'for channel_id' loop

    # Final save of processed videos list (redundant if saved per video, but harmless)
    save_processed_videos()

    end_time = time.time()
    duration = end_time - start_time

    logging.info(f"--- YouTube Monitor Script Finished ---")
    logging.info(f"Processed {new_videos_processed_count} new videos in this run (including those skipped due to duration, transcript errors, or Gemini failures).")
    logging.info(f"Total execution time: {duration:.2f} seconds.")

# Script entry point
if __name__ == "__main__":
    main()
