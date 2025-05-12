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

# Third-party libraries (install via requirements.txt)
import google.generativeai as genai
# from google.generativeai.types import SafetySetting # Removed - assume strings work for config
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

# --- Configuration ---
CONFIG_FILE = 'config.ini'
config = configparser.ConfigParser()

if not os.path.exists(CONFIG_FILE):
    print(f"ERROR: Configuration file '{CONFIG_FILE}' not found. Please create it.")
    sys.exit(1)

try:
    config.read(CONFIG_FILE)
    LOG_FILE = config.get('SETTINGS', 'log_file', fallback='monitor.log')
    OUTPUT_DIR = config.get('SETTINGS', 'output_dir', fallback='output')
except (configparser.Error, KeyError) as e:
    print(f"WARNING: Error reading basic settings from config file '{CONFIG_FILE}': {e}. Using defaults.")
    LOG_FILE = 'monitor.log'
    OUTPUT_DIR = 'output'

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()
logger.setLevel(logging.INFO)
for handler in logger.handlers[:]: logger.removeHandler(handler)

try: # File Handler
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
except IOError as e: print(f"CRITICAL: Could not open log file {LOG_FILE} for writing: {e}")

class SafeStreamHandler(logging.StreamHandler): # Console Handler
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            encoding = getattr(stream, 'encoding', 'utf-8') or 'utf-8'
            encoded_msg = msg.encode(encoding, 'replace').decode(encoding)
            stream.write(encoded_msg + self.terminator)
            self.flush()
        except Exception: self.handleError(record)
try:
    stream_handler = SafeStreamHandler(sys.stdout)
    stream_handler.setFormatter(log_formatter)
    logger.addHandler(stream_handler)
except Exception as e: print(f"WARNING: Could not initialize console logging stream handler: {e}")

logging.info(f"Logging initialized. Log file: {LOG_FILE}")

# --- Continue Configuration Parsing ---
try:
    YOUTUBE_API_KEY = config.get('API_KEYS', 'youtube_api_key')
    GEMINI_API_KEY = config.get('API_KEYS', 'gemini_api_key')
    CHANNEL_IDS = [cid.strip() for cid in config.get('CHANNELS', 'channel_ids').split(',')]
    GEMINI_MODEL = config.get('GEMINI', 'model_name')
    PROMPT_EXEC_SUMMARY = config.get('GEMINI', 'prompt_executive_summary')
    PROMPT_DETAILED_SUMMARY = config.get('GEMINI', 'prompt_detailed_summary')
    PROMPT_KEY_QUOTES = config.get('GEMINI', 'prompt_key_quotes')
    SMTP_SERVER = config.get('EMAIL', 'smtp_server')
    SMTP_PORT = config.getint('EMAIL', 'smtp_port')
    SMTP_USER = config.get('EMAIL', 'smtp_user')
    SMTP_PASSWORD = config.get('EMAIL', 'smtp_password')
    SENDER_EMAIL = config.get('EMAIL', 'sender_email')
    RECIPIENT_EMAILS = [email.strip() for email in config.get('EMAIL', 'recipient_emails').split(',')]
    PROCESSED_VIDEOS_FILE = config.get('SETTINGS', 'processed_videos_file')
    OUTPUT_DIR = config.get('SETTINGS', 'output_dir')
    MAX_RESULTS_PER_CHANNEL = config.getint('SETTINGS', 'max_results_per_channel', fallback=1)

    # Parse safety settings later during model init

    if not all([YOUTUBE_API_KEY, GEMINI_API_KEY, CHANNEL_IDS, SMTP_SERVER, SMTP_USER, SMTP_PASSWORD, SENDER_EMAIL, RECIPIENT_EMAILS]):
        raise ValueError("One or more essential configuration values are missing in config.ini")
    if 'YOUR_' in YOUTUBE_API_KEY or 'YOUR_' in GEMINI_API_KEY or 'YOUR_' in SMTP_PASSWORD:
         logging.warning(f"Placeholder API key or password detected in '{CONFIG_FILE}'. Please replace them.")

except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as e:
    logging.error(f"ERROR reading configuration file '{CONFIG_FILE}': {e}")
    sys.exit(1)


# --- State Management ---
def load_processed_videos():
    try:
        if os.path.exists(PROCESSED_VIDEOS_FILE):
            with open(PROCESSED_VIDEOS_FILE, 'r', encoding='utf-8') as f: return set(json.load(f))
        else: logging.info(f"Processed videos file '{PROCESSED_VIDEOS_FILE}' not found. Starting fresh."); return set()
    except json.JSONDecodeError: logging.error(f"Error decoding JSON from {PROCESSED_VIDEOS_FILE}. Starting with empty set."); return set()
    except IOError as e: logging.error(f"Could not read state file {PROCESSED_VIDEOS_FILE}: {e}. Starting with empty set."); return set()

def save_processed_videos(processed_ids):
    try:
        with open(PROCESSED_VIDEOS_FILE, 'w', encoding='utf-8') as f: json.dump(list(processed_ids), f, indent=4)
    except IOError as e: logging.error(f"Could not write state file {PROCESSED_VIDEOS_FILE}: {e}")

# --- YouTube API Functions ---
youtube = None
try:
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    logging.info("YouTube API client initialized successfully.")
except Exception as e: logging.error(f"Failed to initialize YouTube API client: {e}"); sys.exit(1)

def get_latest_videos(channel_id, max_results=1):
    if not youtube: logging.error("YouTube client not initialized."); return []
    try:
        channel_response = youtube.channels().list(part='contentDetails', id=channel_id).execute()
        if not channel_response.get('items'): logging.warning(f"No channel found for ID: {channel_id}"); return []
        uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        playlist_response = youtube.playlistItems().list(part='snippet,contentDetails', playlistId=uploads_playlist_id, maxResults=max_results).execute()
        videos = []
        for item in playlist_response.get('items', []):
            video_id = item['contentDetails']['videoId']
            video_title = item['snippet']['title']
            published_at_str = item['snippet']['publishedAt']
            published_at = datetime.strptime(published_at_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            videos.append({'id': video_id, 'title': video_title, 'published_at': published_at, 'channel_id': channel_id})
        return videos
    except HttpError as e:
        logging.error(f"YouTube API HTTP error for channel {channel_id}: {e}")
        if hasattr(e, 'resp') and e.resp.status == 403: logging.error("Potential Quota Exceeded or Invalid API Key for YouTube.")
        return []
    except Exception as e: logging.error(f"An unexpected error occurred fetching videos for channel {channel_id}: {e}"); return []

# --- Transcript Fetching ---
def get_transcript(video_id):
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None; preferred_langs = ['en', 'en-US', 'en-GB']
        try: transcript = transcript_list.find_manually_created_transcript(preferred_langs); logging.info(f"Found manual transcript ({transcript.language}) for {video_id}")
        except NoTranscriptFound:
            try: transcript = transcript_list.find_generated_transcript(preferred_langs); logging.info(f"Found generated transcript ({transcript.language}) for {video_id}")
            except NoTranscriptFound:
                 try:
                     transcript = next(iter(transcript_list), None)
                     if transcript: logging.info(f"Found transcript in fallback language ({transcript.language}) for {video_id}")
                     else: logging.warning(f"No transcripts listed at all for video {video_id}"); return None
                 except Exception as iter_ex: logging.error(f"Error iterating transcript list for {video_id}: {iter_ex}"); return None
        if not transcript: logging.warning(f"Could not find any suitable transcript object for {video_id}"); return None
        fetched_transcript_iterable = transcript.fetch()
        if fetched_transcript_iterable is None: logging.error(f"Transcript fetch for {video_id} returned None."); return None
        try:
            segment_texts = [segment.text for segment in fetched_transcript_iterable if hasattr(segment, 'text')]
            if not segment_texts: logging.warning(f"Transcript fetch for {video_id} resulted in no text segments."); return ""
            full_text = " ".join(segment_texts)
        except TypeError as e:
            logging.error(f"TypeError during transcript processing for {video_id}. Not iterable?: {e}", exc_info=True)
            logging.error(f"Type of fetched data: {type(fetched_transcript_iterable)}"); return None
        logging.info(f"Successfully processed transcript for video {video_id} (Language: {transcript.language})")
        return full_text
    except TranscriptsDisabled: logging.warning(f"Transcripts are disabled for video {video_id}"); return None
    except NoTranscriptFound: logging.warning(f"No transcript could be found for video {video_id} (API list/find check)"); return None
    except TypeError as e: logging.error(f"TypeError accessing transcript segment property for {video_id}: {e}", exc_info=True); return None
    except Exception as e: logging.error(f"General error fetching/processing transcript for video {video_id}: {e}", exc_info=True); return None

# --- Gemini AI Interaction ---
# *** THIS FUNCTION IS UPDATED WITH INTEGER COMPARISON FOR finish_reason ***
genai_model = None
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model_init_args = {"model_name": GEMINI_MODEL}
    # Safety Settings Parsing (Revised - assume strings work directly)
    if config.has_option('GEMINI', 'safety_settings'):
        SAFETY_SETTINGS_RAW = config.get('GEMINI', 'safety_settings', fallback=None)
        if SAFETY_SETTINGS_RAW:
            try:
                # Pass as simple dict[str, str] - library might handle mapping
                safety_dict = {}
                for item in SAFETY_SETTINGS_RAW.split(','):
                    key_val = item.split(':')
                    if len(key_val) == 2: # Basic validation
                        safety_dict[key_val[0].strip()] = key_val[1].strip()
                if safety_dict: # Only add if not empty
                     model_init_args["safety_settings"] = safety_dict
                logging.info(f"Applying safety settings: {safety_dict}")
            except Exception as e:
                logging.warning(f"Could not parse safety_settings string '{SAFETY_SETTINGS_RAW}': {e}. Using default safety settings.")

    genai_model = genai.GenerativeModel(**model_init_args)
    logging.info(f"Google Gemini AI client initialized successfully with model '{GEMINI_MODEL}'.")
except Exception as e:
     logging.error(f"Failed to initialize Google Gemini AI client: {e}", exc_info=True)
     sys.exit(1)

def generate_summary_with_gemini(transcript):
    """Generates summaries and quotes using the Gemini API."""
    if not genai_model:
         logging.error("Gemini client not initialized."); return "Error:...", "Error:...", "Error:..." # Shortened for brevity
    if not transcript or not transcript.strip():
        logging.warning("Empty/missing transcript."); return "Error:...", "Error:...", "Error:..."

    results = {}
    prompts = {"executive": PROMPT_EXEC_SUMMARY, "detailed": PROMPT_DETAILED_SUMMARY, "quotes": PROMPT_KEY_QUOTES}

    # Finish Reason Integer Constants
    FINISH_REASON_STOP = 1; FINISH_REASON_MAX_TOKENS = 2; FINISH_REASON_SAFETY = 3
    FINISH_REASON_RECITATION = 4; FINISH_REASON_OTHER = 5; FINISH_REASON_UNKNOWN = 0
    WARNING_FINISH_REASONS = {FINISH_REASON_MAX_TOKENS, FINISH_REASON_SAFETY, FINISH_REASON_RECITATION}

    for key, base_prompt in prompts.items():
        try:
            prompt_text = base_prompt.format(transcript=transcript)
            max_retries = 3; last_exception = None
            for attempt in range(max_retries):
                response = None
                try:
                    response = genai_model.generate_content(prompt_text)
                    # 1. Check blocks
                    if response.prompt_feedback and response.prompt_feedback.block_reason:
                         block_reason_val = response.prompt_feedback.block_reason
                         logging.warning(f"Gemini prompt for {key} blocked. Reason value: {block_reason_val}")
                         results[key] = f"Error: Content generation blocked (Reason: {block_reason_val})."; break
                    # 2. Check candidates
                    finish_reason = FINISH_REASON_UNKNOWN; safety_ratings = "N/A"; response_text = None
                    if hasattr(response, 'candidates') and response.candidates:
                        candidate = response.candidates[0]
                        finish_reason_val = getattr(candidate, 'finish_reason', FINISH_REASON_UNKNOWN)
                        try: finish_reason = int(finish_reason_val)
                        except (ValueError, TypeError): logging.warning(f"Non-int finish_reason '{finish_reason_val}', using UNKNOWN."); finish_reason = FINISH_REASON_UNKNOWN
                        safety_ratings_obj = getattr(candidate, 'safety_ratings', None); safety_ratings = str(safety_ratings_obj) if safety_ratings_obj else "N/A"
                        if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts') and candidate.content.parts:
                             response_text = getattr(candidate.content.parts[0], 'text', None)
                    # 3. Fallback text
                    elif hasattr(response, 'text'): response_text = response.text; finish_reason = FINISH_REASON_STOP
                    # --- Evaluate Finish Reason ---
                    finish_reason_str = str(finish_reason)
                    # Case A: Success
                    if finish_reason == FINISH_REASON_STOP:
                        if response_text is not None:
                            results[key] = response_text.strip(); logging.info(f"Success Gemini {key} (Attempt {attempt + 1}). Reason: {finish_reason_str}"); last_exception = None; break
                        else: logging.warning(f"Gemini {key} STOP but no text."); results[key] = f"Error: No text despite reason {finish_reason_str}."; break
                    # Case B: Warning/Limit
                    elif finish_reason in WARNING_FINISH_REASONS:
                        logging.warning(f"Gemini {key} limited. Reason: {finish_reason_str}. Safety: {safety_ratings}")
                        if response_text is not None: results[key] = response_text.strip() + f"\n(Warning: Generation limited due to reason {finish_reason_str})"
                        else: results[key] = f"Warning: Limited by reason {finish_reason_str}, no text returned."
                        break
                    # Case C: Failure
                    else: logging.error(f"Gemini {key} failed. Reason: {finish_reason_str}. Safety: {safety_ratings}"); results[key] = f"Error: Generation failed with reason {finish_reason_str}."; break
                except Exception as e:
                    logging.warning(f"Gemini API attempt {attempt + 1}/{max_retries} failed for {key}: {e}")
                    last_exception = e
                    if response and hasattr(response, 'prompt_feedback') and response.prompt_feedback: logging.warning(f"Gemini feedback during exception: {response.prompt_feedback}")
                    if attempt == max_retries - 1: logging.error(f"Gemini API failed permanently for {key} after {max_retries} attempts."); results[key] = f"Error generating {key} after retries ({type(last_exception).__name__})."
                    else: time.sleep(2 ** attempt)
            # Fallback if key not set
            if key not in results:
                err_msg = f"Error generating {key} after retries" + (f" (Last Error: {type(last_exception).__name__})." if last_exception else " (Unknown error).")
                results[key] = err_msg; logging.error(err_msg)
            time.sleep(1)
        except Exception as e: logging.exception(f"Unexpected error in Gemini outer loop for {key}: {e}"); results[key] = f"Error generating {key} (Setup error)."
    return results.get("executive", "Error: Gen failed."), results.get("detailed", "Error: Gen failed."), results.get("quotes", "Error: Gen failed.")

# --- Email Sending ---
def send_email_notification(video_info, executive_summary, detailed_summary, key_quotes):
    video_id=video_info['id']; video_title=video_info['title']; channel_id=video_info['channel_id']
    video_url=f"https://www.youtube.com/watch?v={video_id}"; channel_title = channel_id
    try: # Get Channel Title
        if youtube:
             channel_response = youtube.channels().list(part='snippet', id=channel_id).execute()
             if channel_response.get('items'): channel_title = channel_response['items'][0]['snippet']['title']
    except Exception as e: logging.warning(f"Could not fetch channel title for {channel_id}: {e}")
    subject = f"New Video Summary: [{channel_title}] {video_title}"
    def escape_html(text): return text.replace('&', '&').replace('<', '<').replace('>', '>') if isinstance(text, str) else ""
    exec_summary_html = escape_html(executive_summary).replace('\n', '<br>')
    detailed_summary_lines = escape_html(detailed_summary).split('\n'); detailed_summary_html = ""
    for line in detailed_summary_lines:
        stripped_line = line.strip()
        if stripped_line.startswith("(Warning:"): detailed_summary_html += f"<br><i>{stripped_line}</i>"
        elif stripped_line.startswith('* ') or stripped_line.startswith('- '): detailed_summary_html += f"<br>• {stripped_line[2:]}"
        elif stripped_line: detailed_summary_html += f"<br>{line}"
    if detailed_summary_html.startswith("<br>"): detailed_summary_html = detailed_summary_html[4:]
    key_quotes_html = f"<pre>{escape_html(key_quotes)}</pre>"
    body = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{escape_html(subject)}</title></head><body><h2>New Video Posted: <a href="{video_url}">{escape_html(video_title)}</a></h2><p><b>Channel:</b> {escape_html(channel_title)}</p><p><b>Video Link:</b> <a href="{video_url}">{video_url}</a></p><hr><h3>Executive Summary:</h3><p>{exec_summary_html}</p><hr><h3>Detailed Bulleted Summary:</h3><p>{detailed_summary_html}</p><hr><h3>Key Quotes / Data Points:</h3>{key_quotes_html}<hr><p><small><i>Processed by YouTube Monitor Script at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i></small></p></body></html>"""
    msg = MIMEMultipart('alternative'); msg['Subject'] = subject; msg['From'] = SENDER_EMAIL; msg['To'] = ", ".join(RECIPIENT_EMAILS)
    html_part = MIMEText(body, 'html', 'utf-8'); msg.attach(html_part)
    server = None
    try:
        if SMTP_PORT == 587: server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30); server.starttls(); server.login(SMTP_USER, SMTP_PASSWORD)
        elif SMTP_PORT == 465: server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30); server.login(SMTP_USER, SMTP_PASSWORD)
        else: server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30); server.login(SMTP_USER, SMTP_PASSWORD) # Assuming login needed
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAILS, msg.as_string())
        logging.info(f"Successfully sent email for video {video_id} to {', '.join(RECIPIENT_EMAILS)}")
        server.quit(); return True
    except smtplib.SMTPAuthenticationError as e: logging.error(f"SMTP Auth Error for {video_id}: {e}"); return False
    except smtplib.SMTPException as e: logging.error(f"SMTP Error for {video_id}: {e}"); return False
    except TimeoutError: logging.error(f"Timeout sending email for {video_id}."); return False
    except Exception as e: logging.error(f"Failed sending email for {video_id}: {e}", exc_info=True); return False
    finally:
        if server and hasattr(server, 'sock') and server.sock:
             try: server.quit()
             except Exception: pass

# --- Main Execution Logic ---
def main():
    logging.info("--- Starting YouTube Monitor Script ---")
    processed_video_ids = load_processed_videos()
    new_videos_processed_count = 0
    if not CHANNEL_IDS: logging.warning("No channel IDs configured. Exiting."); return
    if not youtube or not genai_model: logging.critical("API clients not initialized. Exiting."); return

    for channel_id in CHANNEL_IDS:
        logging.info(f"Checking channel: {channel_id}")
        try:
            latest_videos = get_latest_videos(channel_id, max_results=MAX_RESULTS_PER_CHANNEL)
            if not latest_videos: logging.info(f"No new videos found for channel {channel_id}."); continue

            for video in latest_videos:
                video_id = video['id']; video_title = video['title']
                logging.info(f"Found video: '{video_title}' (ID: {video_id})")
                if video_id in processed_video_ids: logging.info(f"Video {video_id} already processed. Skipping."); continue
                logging.info(f"Processing new video: {video_id} - '{video_title}'")

                # 1. Get Transcript
                transcript = get_transcript(video_id)
                if transcript is None: logging.warning(f"Transcript fetch failed for {video_id}. Skipping."); continue
                elif not transcript.strip():
                    logging.warning(f"Transcript for {video_id} is empty. Skipping summary.");
                    processed_video_ids.add(video_id); save_processed_videos(processed_video_ids)
                    logging.info(f"Added {video_id} to processed list (empty transcript)."); continue

                # 2. Generate Summaries
                logging.info(f"Generating summaries for video {video_id}...")
                exec_summary, detailed_summary, key_quotes = generate_summary_with_gemini(transcript)
                generation_failed = any(s.startswith("Error:") for s in [exec_summary, detailed_summary, key_quotes])
                if generation_failed:
                    logging.error(f"Summary generation failed for {video_id}. Skipping notification/save.")
                    # Optionally mark as processed here if errors are persistent
                    continue

                # 3. Save Summary
                safe_video_id = "".join(c for c in video_id if c.isalnum() or c in ('-', '_')).rstrip()
                summary_filename = os.path.join(OUTPUT_DIR, f"{safe_video_id}.txt")
                try:
                    with open(summary_filename, 'w', encoding='utf-8') as f:
                        f.write(f"Video Title: {video_title}\nURL: https://www.youtube.com/watch?v={video_id}\nProcessed: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n--- Executive Summary ---\n{exec_summary}\n\n--- Detailed Summary ---\n{detailed_summary}\n\n--- Key Quotes/Data Points ---\n{key_quotes}\n")
                    logging.info(f"Successfully saved summary to {summary_filename}")
                except IOError as e: logging.error(f"Failed to save summary file {summary_filename}: {e}") # Decide whether to continue

                # 4. Send Email
                email_sent = send_email_notification(video, exec_summary, detailed_summary, key_quotes)

                # 5. Update Processed List
                if email_sent:
                    processed_video_ids.add(video_id); save_processed_videos(processed_video_ids)
                    logging.info(f"Successfully processed and notified for video {video_id}.")
                    new_videos_processed_count += 1
                else: logging.error(f"Email failed for {video_id}. Will retry next run.")

        except Exception as e: logging.exception(f"Unexpected error processing channel {channel_id}: {e}")

    logging.info(f"--- YouTube Monitor Script Finished ---")
    logging.info(f"Processed {new_videos_processed_count} new videos in this run.")

if __name__ == "__main__":
    main()
