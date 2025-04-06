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
import pathlib

# Third-party libraries (install via requirements.txt)
import google.generativeai as genai
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound, VideoUnavailable

# --- Constants ---
CONFIG_FILE = 'config.ini'
DEFAULT_LOG_LEVEL = logging.INFO

# --- Global Variables ---
# Build services only once if possible (can be done in main or globally)
youtube_service = None
gemini_model = None
processed_video_ids = set()

# --- Logging Setup ---
def setup_logging(log_file, log_level=DEFAULT_LOG_LEVEL):
    """Configures logging to file and console."""
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger()
    logger.setLevel(log_level)

    # File Handler
    # Ensure log directory exists (useful if log_file includes a path)
    log_path = pathlib.Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)

    return logger

# --- Configuration Loading ---
def load_config(config_file=CONFIG_FILE):
    """Loads configuration from the INI file."""
    if not os.path.exists(config_file):
        print(f"ERROR: Configuration file '{config_file}' not found. Please create it.")
        # If logger is not set up yet, print is the only option
        if logging.getLogger().hasHandlers():
             logging.getLogger().critical(f"Configuration file '{config_file}' not found.")
        sys.exit(1)

    config = configparser.ConfigParser()
    try:
        config.read(config_file)

        # Validate essential sections and keys
        required = {
            'API_KEYS': ['youtube_api_key', 'gemini_api_key'],
            'CHANNELS': ['channel_ids'],
            'GEMINI': ['model_name', 'prompt_executive_summary', 'prompt_detailed_summary', 'prompt_key_quotes'],
            'EMAIL': ['smtp_server', 'smtp_port', 'smtp_user', 'smtp_password', 'sender_email', 'recipient_emails'],
            'SETTINGS': ['processed_videos_file', 'log_file', 'output_dir']
        }
        for section, keys in required.items():
            if not config.has_section(section):
                raise ValueError(f"Missing required section in config: [{section}]")
            for key in keys:
                if not config.has_option(section, key) or not config.get(section, key):
                     # Allow smtp_user/password to be empty if intentionally sending unauthenticated
                    if section == 'EMAIL' and key in ['smtp_user', 'smtp_password']:
                        if config.get(section, key, fallback=None) is None: # Explicitly check for None if allowing empty
                           print(f"Warning: Config key '{key}' in section '[{section}]' is empty. Proceeding without SMTP authentication if applicable.")
                           # Set to empty string if missing to avoid errors later
                           if not config.has_option(section, key):
                               config.set(section, key, '')
                    else:
                        raise ValueError(f"Missing or empty required config key: '{key}' in section '[{section}]'")

        # Create output directory if it doesn't exist
        output_dir = config.get('SETTINGS', 'output_dir')
        pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)

        return config

    except configparser.Error as e:
        print(f"ERROR parsing config file '{config_file}': {e}")
        if logging.getLogger().hasHandlers():
            logging.getLogger().critical(f"Error parsing config file '{config_file}': {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR in configuration: {e}")
        if logging.getLogger().hasHandlers():
            logging.getLogger().critical(f"Error in configuration: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR loading configuration: {e}")
        if logging.getLogger().hasHandlers():
            logging.getLogger().critical(f"Unexpected error loading configuration: {e}")
        sys.exit(1)


# --- Processed Videos Handling ---
def load_processed_videos(filepath):
    """Loads the set of processed video IDs from a JSON file."""
    logger = logging.getLogger()
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                data = json.load(f)
                logger.info(f"Loaded {len(data)} processed video IDs from {filepath}")
                return set(data)
        else:
            logger.info(f"Processed videos file '{filepath}' not found. Starting fresh.")
            return set()
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {filepath}. Starting with an empty set.")
        return set()
    except Exception as e:
        logger.error(f"Error loading processed videos file {filepath}: {e}")
        return set()

def save_processed_videos(filepath, video_ids_set):
    """Saves the set of processed video IDs to a JSON file."""
    logger = logging.getLogger()
    try:
        with open(filepath, 'w') as f:
            json.dump(list(video_ids_set), f, indent=4) # Save as a list
        logger.debug(f"Saved {len(video_ids_set)} processed video IDs to {filepath}")
    except IOError as e:
        logger.error(f"Error saving processed videos file {filepath}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error saving processed videos {filepath}: {e}")


# --- YouTube API Functions ---
def get_youtube_service(api_key):
    """Initializes and returns the YouTube Data API service client."""
    global youtube_service
    logger = logging.getLogger()
    if youtube_service is None:
        try:
            logger.debug("Building YouTube API service...")
            youtube_service = build('youtube', 'v3', developerKey=api_key)
            logger.info("YouTube API service built successfully.")
        except Exception as e:
            logger.critical(f"Failed to build YouTube API service: {e}")
            sys.exit(1) # Critical failure
    return youtube_service

def get_channel_name(youtube, channel_id):
    """Gets the display name of a YouTube channel."""
    logger = logging.getLogger()
    try:
        request = youtube.channels().list(
            part="snippet",
            id=channel_id
        )
        response = request.execute()
        if response and response.get('items'):
            return response['items'][0]['snippet']['title']
        else:
            logger.warning(f"Could not find channel name for ID: {channel_id}")
            return f"Channel ID: {channel_id}" # Fallback name
    except HttpError as e:
        logger.error(f"YouTube API error getting channel name for {channel_id}: {e}")
        return f"Channel ID: {channel_id}" # Fallback name
    except Exception as e:
        logger.error(f"Unexpected error getting channel name for {channel_id}: {e}")
        return f"Channel ID: {channel_id}" # Fallback name

def get_latest_videos(youtube, channel_id, max_results):
    """Gets the latest video uploads for a given channel ID."""
    logger = logging.getLogger()
    videos = []
    try:
        # 1. Get the Uploads playlist ID for the channel
        channel_request = youtube.channels().list(
            part="contentDetails",
            id=channel_id
        )
        channel_response = channel_request.execute()

        if not channel_response.get("items"):
            logger.error(f"Channel not found or no content details for ID: {channel_id}")
            return []

        uploads_playlist_id = channel_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # 2. Get the latest videos from the Uploads playlist
        playlist_request = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=max_results # Fetch a few recent ones
        )
        playlist_response = playlist_request.execute()

        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1, minutes=5) # Add buffer

        for item in playlist_response.get("items", []):
            snippet = item.get("snippet", {})
            video_id = snippet.get("resourceId", {}).get("videoId")
            title = snippet.get("title")
            published_at_str = snippet.get("publishedAt")

            if video_id and title and published_at_str:
                 # Parse ISO 8601 timestamp
                try:
                    published_at = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))
                except ValueError:
                     logger.warning(f"Could not parse publish date '{published_at_str}' for video {video_id}. Skipping date check.")
                     published_at = datetime.now(timezone.utc) # Assume recent if unparseable for now


                # Check if video was published roughly within the last hour
                # This helps filter out older videos if max_results > 1
                # if published_at >= one_hour_ago: # No, check only if processed later
                videos.append({
                    "id": video_id,
                    "title": title,
                    "published_at": published_at,
                    "url": f"https://www.youtube.com/watch?v={video_id}"
                })
                # else:
                    # logger.debug(f"Video '{title}' ({video_id}) published at {published_at}, older than check window. Skipping.")

            else:
                 logger.warning(f"Skipping playlist item due to missing data: {item}")

        # Sort by publish date descending (newest first) just in case API order isn't strict
        videos.sort(key=lambda v: v["published_at"], reverse=True)
        logger.info(f"Found {len(videos)} potential recent video(s) for channel {channel_id}.")
        return videos[:max_results] # Return only the requested number of *newest*

    except HttpError as e:
        logger.error(f"YouTube API error getting videos for channel {channel_id}: {e}")
        # Check for quota exceeded specifically
        if e.resp.status == 403 and b'quotaExceeded' in e.content:
            logger.critical("YouTube API Quota Exceeded. Stopping script execution.")
            sys.exit(1) # Stop if quota is hit
        return []
    except Exception as e:
        logger.error(f"Unexpected error getting videos for channel {channel_id}: {e}")
        return []


# --- Transcript Function ---
def get_transcript(video_id):
    """Fetches the transcript for a YouTube video."""
    logger = logging.getLogger()
    try:
        logger.info(f"Fetching transcript for video ID: {video_id}")
        # Fetch available transcripts
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Try to fetch English ('en') first, then any available transcript
        try:
            transcript = transcript_list.find_generated_transcript(['en'])
            logger.info(f"Found generated English transcript for {video_id}.")
        except NoTranscriptFound:
             logger.warning(f"No generated English transcript found for {video_id}. Trying any available...")
             try:
                # Fetch the first available transcript regardless of language
                transcript = transcript_list.find_generated_transcript(transcript_list.available_languages)
                logger.info(f"Found generated transcript in language '{transcript.language}' for {video_id}.")
             except NoTranscriptFound:
                 logger.warning(f"No automatically generated transcript found for {video_id}. Checking manual...")
                 try:
                     # Check for manually created transcripts if no generated one exists
                     transcript = transcript_list.find_manually_created_transcript(transcript_list.available_languages)
                     logger.info(f"Found manual transcript in language '{transcript.language}' for {video_id}.")
                 except NoTranscriptFound:
                     logger.error(f"No suitable transcript (generated or manual) found for video {video_id}.")
                     return None

        # Fetch the actual transcript text segments
        transcript_data = transcript.fetch()
        full_transcript = " ".join([item['text'] for item in transcript_data])
        logger.info(f"Successfully fetched transcript for {video_id} (Length: {len(full_transcript)} chars).")
        return full_transcript

    except TranscriptsDisabled:
        logger.error(f"Transcripts are disabled for video ID: {video_id}")
        return None
    except VideoUnavailable:
         logger.error(f"Video {video_id} is unavailable.")
         return None
    except Exception as e:
        logger.error(f"Error fetching transcript for video ID {video_id}: {e}")
        return None


# --- Gemini API Function ---
def get_gemini_model(api_key, model_name, safety_settings_config):
    """Initializes and returns the Gemini generative model client."""
    global gemini_model
    logger = logging.getLogger()
    if gemini_model is None:
        try:
            logger.debug("Configuring Gemini API...")
            genai.configure(api_key=api_key)

            # Parse safety settings from config
            safety_settings = None
            if safety_settings_config:
                try:
                    # Convert keys from config string to genai constants if needed
                    # Assuming config uses strings like 'HARM_CATEGORY_HARASSMENT'
                    # genai expects enums, but strings often work with the library directly.
                    # If it fails, mapping might be needed:
                    # mapping = {'HARM_CATEGORY_HARASSMENT': HarmCategory.HARM_CATEGORY_HARASSMENT, ...}
                    safety_settings = {
                        item.split(':')[0].strip(): item.split(':')[1].strip()
                        for item in safety_settings_config.split(',')
                    }
                    logger.info(f"Applying Gemini safety settings: {safety_settings}")
                except Exception as e:
                    logger.warning(f"Could not parse or apply safety_settings: {e}. Using Gemini default safety settings.")
                    safety_settings = None # Fallback to default

            logger.debug(f"Initializing Gemini model: {model_name}")
            gemini_model = genai.GenerativeModel(
                model_name,
                safety_settings=safety_settings
                # Add generation_config here if needed (temperature, top_p, etc.)
                # generation_config=genai.types.GenerationConfig(...)
            )
            logger.info("Gemini model initialized successfully.")
        except Exception as e:
            logger.critical(f"Failed to initialize Gemini model: {e}")
            sys.exit(1) # Critical failure
    return gemini_model

def get_gemini_summary(model, prompt_template, transcript, item_name="summary"):
    """Gets a summary/analysis from Gemini based on the transcript and prompt."""
    logger = logging.getLogger()
    if not transcript:
        logger.warning(f"Cannot generate {item_name}, transcript is empty.")
        return f"Could not generate {item_name} (transcript unavailable)."

    # Handle potential large transcripts (check Gemini limits if necessary)
    # Basic check, adjust limit as needed per model
    MAX_CHARS = 1_900_000 # Gemini 1.5 Pro has a large context window, but set a safeguard
    if len(transcript) > MAX_CHARS:
        logger.warning(f"Transcript length ({len(transcript)} chars) exceeds limit ({MAX_CHARS}). Truncating.")
        transcript = transcript[:MAX_CHARS]

    prompt = prompt_template.format(transcript=transcript)
    logger.info(f"Generating {item_name} using Gemini model {model.model_name}...")

    # Add retry logic for potential transient API errors
    max_retries = 3
    retry_delay = 5 # seconds
    for attempt in range(max_retries):
        try:
            # Set safety_settings specifically for the generate_content call if needed
            # response = model.generate_content(prompt, safety_settings=...)
            response = model.generate_content(prompt)

            # Check for safety blocks or empty response
            if not response.candidates:
                finish_reason = response.prompt_feedback.block_reason if response.prompt_feedback else 'Unknown'
                logger.error(f"Gemini response blocked or empty. Reason: {finish_reason}. Prompt Feedback: {response.prompt_feedback}")
                # Get safety ratings details if available
                if response.prompt_feedback and response.prompt_feedback.safety_ratings:
                    for rating in response.prompt_feedback.safety_ratings:
                        logger.error(f"  Safety Rating: {rating.category}, Probability: {rating.probability}")
                return f"Could not generate {item_name} (Safety block or empty response)."


            if response.candidates[0].finish_reason.name != "STOP":
                 logger.warning(f"Gemini generation finished with reason: {response.candidates[0].finish_reason.name}. Output might be incomplete.")


            result_text = response.text.strip()
            logger.info(f"Successfully generated {item_name}.")
            logger.debug(f"{item_name.capitalize()} Result:\n{result_text[:200]}...") # Log snippet
            return result_text

        except Exception as e:
            logger.error(f"Error calling Gemini API (Attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2 # Exponential backoff
            else:
                logger.error(f"Gemini API call failed after {max_retries} attempts.")
                return f"Could not generate {item_name} (API Error after retries)."

    # Should not be reached if loop completes, but as a fallback
    return f"Could not generate {item_name} (Max retries exceeded)."


# --- Email Function ---
def send_email(channel_name, video_title, video_url, exec_summary, detailed_summary, key_quotes, config):
    """Sends a formatted HTML email notification."""
    logger = logging.getLogger()

    # --- Email Configuration ---
    try:
        smtp_server = config.get('EMAIL', 'smtp_server')
        smtp_port = config.getint('EMAIL', 'smtp_port')
        smtp_user = config.get('EMAIL', 'smtp_user', fallback='') # Allow empty user/pass
        smtp_password = config.get('EMAIL', 'smtp_password', fallback='')
        sender_email = config.get('EMAIL', 'sender_email')
        recipient_emails = [email.strip() for email in config.get('EMAIL', 'recipient_emails').split(',')]
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as e:
        logger.error(f"Email configuration error: {e}. Cannot send email.")
        return False
    except Exception as e:
         logger.error(f"Unexpected error reading email config: {e}. Cannot send email.")
         return False


    subject = f"New Video Summary: {channel_name} - {video_title}"

    # --- Create Plain Text Version ---
    # Simple text formatting, replace special chars if needed
    plain_text = f"""
New Video Processed
--------------------
Channel: {channel_name}
Video Title: {video_title}
Video URL: {video_url}

Executive Summary:
------------------
{exec_summary}

Detailed Summary:
-----------------
{detailed_summary}

Key Quotes/Data Points:
-----------------------
{key_quotes}

--------------------
Generated by YouTube Monitor Script
"""

    # --- Create HTML Version ---
    # Basic HTML escaping for content going into the template
    def escape_html(text):
        import html
        return html.escape(text)

    # Format summaries and quotes for HTML display
    # Replace newlines with <br> for paragraphs, handle list items for detailed summary
    html_exec_summary = escape_html(exec_summary).replace('\n', '<br>\n')

    # Assuming detailed summary uses bullet points starting with '*' or '-'
    html_detailed_summary_items = ""
    if detailed_summary:
        items = detailed_summary.strip().split('\n')
        html_detailed_summary_items = "\n".join([f"<li>{escape_html(item.strip('*').strip('-').strip())}</li>" for item in items if item.strip()])
    html_detailed_summary = f"<ul>\n{html_detailed_summary_items}\n</ul>" if html_detailed_summary_items else "<p>No detailed summary provided.</p>"


    # Format quotes, assuming one quote per line
    html_key_quotes_items = ""
    if key_quotes:
        quotes = key_quotes.strip().split('\n')
        html_key_quotes_items = "\n".join([f"<p>{escape_html(quote.strip())}</p>" for quote in quotes if quote.strip()])
    html_key_quotes = html_key_quotes_items if html_key_quotes_items else "<p>No specific key quotes extracted.</p>"


    html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escape_html(subject)}</title>
    <style>
        body {{
            font-family: Arial, Helvetica, sans-serif;
            line-height: 1.6;
            color: #333333;
            background-color: #f8f9fa;
            margin: 0;
            padding: 0;
        }}
        .container {{
            max-width: 650px; /* Slightly wider for better content flow */
            margin: 25px auto;
            background-color: #ffffff;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            overflow: hidden;
        }}
        .header {{
            background-color: #4CAF50; /* Keep the green, maybe a bit softer #5cb85c ? */
            color: #ffffff;
            padding: 20px 25px;
            border-bottom: 1px solid #dee2e6;
        }}
        .header h1 {{
            margin: 0;
            font-size: 22px; /* Slightly larger */
            font-weight: 600;
            word-wrap: break-word;
        }}
         .header p {{ /* Add channel name subtitle */
            margin: 5px 0 0;
            font-size: 14px;
            opacity: 0.9;
         }}
        .content {{
            padding: 25px 30px;
        }}
        .content h2 {{
            color: #343a40; /* Darker grey */
            border-bottom: 2px solid #e9ecef; /* Lighter border */
            padding-bottom: 8px;
            margin-top: 30px;
            margin-bottom: 18px;
            font-size: 19px;
            font-weight: 600;
        }}
        .content h2:first-child {{
             margin-top: 0; /* Remove top margin for the first heading */
        }}
        .content p, .content ul li {{
            font-size: 15px; /* Slightly larger body text */
            margin-bottom: 12px;
            color: #495057; /* Slightly softer black */
        }}
        .content ul {{
            padding-left: 25px;
            margin-top: 5px;
            margin-bottom: 20px;
        }}
        .content ul li {{
            margin-bottom: 8px; /* Smaller gap between list items */
        }}
        .video-info p {{
            margin-bottom: 5px; /* Tighter spacing for video info */
            font-size: 15px;
        }}
        .video-link-container {{
            margin-top: 15px;
            margin-bottom: 25px;
        }}
        .video-link {{
            display: inline-block;
            padding: 10px 18px;
            background-color: #007bff;
            color: #ffffff !important; /* Important to override default link styles */
            text-decoration: none;
            border-radius: 5px;
            font-size: 14px;
            font-weight: 500;
            transition: background-color 0.2s ease;
        }}
        .video-link:hover {{
            background-color: #0056b3;
            text-decoration: none;
        }}
        a {{
            color: #007bff;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        .quotes p {{
            font-style: italic;
            color: #6c757d; /* Muted grey for quotes */
            border-left: 4px solid #e9ecef;
            padding: 8px 12px;
            margin-bottom: 15px;
            background-color: #f8f9fa; /* Subtle background for quote block */
            border-radius: 0 4px 4px 0; /* Rounded corner on right */
        }}
        .quotes p:last-child {{
            margin-bottom: 0;
        }}
        .footer {{
            text-align: center;
            padding: 15px 20px;
            font-size: 12px;
            color: #6c757d;
            background-color: #f1f3f5; /* Slightly different footer background */
            border-top: 1px solid #dee2e6;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>New Video Summary</h1>
            <p>{escape_html(channel_name)}</p>
        </div>
        <div class="content">
            <div class="video-info">
                <p><strong>Video Title:</strong> {escape_html(video_title)}</p>
                <p><strong>Channel:</strong> {escape_html(channel_name)}</p>
            </div>
             <div class="video-link-container">
                <a href="{escape_html(video_url)}" target="_blank" class="video-link">Watch Video on YouTube</a>
            </div>

            <h2>Executive Summary</h2>
            <p>{html_exec_summary}</p>

            <h2>Detailed Summary</h2>
            {html_detailed_summary}

            <h2>Key Quotes & Data Points</h2>
            <div class="quotes">
                {html_key_quotes}
            </div>
        </div>
        <div class="footer">
            Generated by YouTube Monitor Script | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        </div>
    </div>
</body>
</html>
"""

    # --- Create the Email Message ---
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = ", ".join(recipient_emails)

    part1 = MIMEText(plain_text, 'plain', 'utf-8') # Specify encoding
    part2 = MIMEText(html_content, 'html', 'utf-8') # Specify encoding
    msg.attach(part1)
    msg.attach(part2)

    # --- Send the Email ---
    try:
        logger.info(f"Connecting to SMTP server: {smtp_server}:{smtp_port}")
        # Choose SMTP_SSL for port 465, standard SMTP for others (like 587)
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port)
            logger.info("Using SMTP_SSL for connection.")
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=30) # Add timeout
            logger.info("Using standard SMTP connection.")

        # server.set_debuglevel(1) # Uncomment for verbose SMTP debugging

        server.ehlo()
        # If not port 465, attempt STARTTLS (common for port 587)
        if smtp_port != 465:
            try:
                logger.debug("Attempting STARTTLS...")
                server.starttls()
                server.ehlo() # Re-identify after TLS
                logger.info("STARTTLS connection established.")
            except smtplib.SMTPNotSupportedError:
                logger.warning("STARTTLS not supported by the server on this port. Proceeding without encryption (if port is not 465).")
            except Exception as tls_error:
                 logger.error(f"Error during STARTTLS: {tls_error}. Check port and server settings.")
                 server.quit()
                 return False


        # Login only if username is provided
        if smtp_user:
            logger.info(f"Attempting SMTP login as {smtp_user}")
            try:
                server.login(smtp_user, smtp_password)
                logger.info("SMTP login successful.")
            except smtplib.SMTPAuthenticationError as e:
                logger.error(f"SMTP Authentication Error: {e}. Check username/password/app password. Code: {e.smtp_code} - {e.smtp_error}")
                server.quit()
                return False
            except smtplib.SMTPException as e:
                 logger.error(f"SMTP Login Error: {e}")
                 server.quit()
                 return False
        else:
            logger.info("No SMTP username provided, sending email without authentication.")

        logger.info(f"Sending email to: {', '.join(recipient_emails)}")
        server.sendmail(sender_email, recipient_emails, msg.as_string())
        logger.info(f"Email sent successfully for video: '{video_title}'")
        server.quit()
        return True

    except smtplib.SMTPServerDisconnected:
        logger.error("SMTP Server disconnected unexpectedly. Check server/port or network.")
        return False
    except smtplib.SMTPConnectError as e:
         logger.error(f"SMTP Connection Error: Failed to connect to {smtp_server}:{smtp_port}. Error: {e}")
         return False
    except smtplib.SMTPResponseException as e:
         logger.error(f"SMTP Response Error: Code: {e.smtp_code} - Message: {e.smtp_error}")
         # Attempt to quit gracefully even after error
         try: server.quit()
         except: pass
         return False
    except TimeoutError:
         logger.error(f"SMTP connection timed out connecting to {smtp_server}:{smtp_port}.")
         return False
    except Exception as e:
        logger.error(f"An unexpected error occurred during email sending: {e}")
        # Attempt to quit gracefully even after error
        try: server.quit()
        except: pass
        return False


# --- Main Execution Logic ---
def main():
    global processed_video_ids # Allow modification of the global set

    # 1. Load Configuration
    config = load_config(CONFIG_FILE)

    # 2. Setup Logging (after loading config for log file path)
    log_file = config.get('SETTINGS', 'log_file')
    logger = setup_logging(log_file)
    logger.info("--- YouTube Monitor Script Started ---")

    # Retrieve remaining config values needed in main
    youtube_api_key = config.get('API_KEYS', 'youtube_api_key')
    gemini_api_key = config.get('API_KEYS', 'gemini_api_key')
    channel_ids = [cid.strip() for cid in config.get('CHANNELS', 'channel_ids').split(',')]
    gemini_model_name = config.get('GEMINI', 'model_name')
    safety_settings_config = config.get('GEMINI', 'safety_settings', fallback=None)
    prompt_exec = config.get('GEMINI', 'prompt_executive_summary')
    prompt_detail = config.get('GEMINI', 'prompt_detailed_summary')
    prompt_quotes = config.get('GEMINI', 'prompt_key_quotes')
    processed_videos_file = config.get('SETTINGS', 'processed_videos_file')
    output_dir = config.get('SETTINGS', 'output_dir')
    max_results = config.getint('SETTINGS', 'max_results_per_channel', fallback=1)


    # 3. Load Processed Videos History
    processed_video_ids = load_processed_videos(processed_videos_file)
    initial_processed_count = len(processed_video_ids)

    # 4. Initialize API Clients
    try:
        youtube = get_youtube_service(youtube_api_key)
        gemini = get_gemini_model(gemini_api_key, gemini_model_name, safety_settings_config)
    except SystemExit: # Catch exits from API setup failures
        logger.critical("Exiting due to API initialization failure.")
        sys.exit(1)


    # 5. Process Each Channel
    new_videos_processed_count = 0
    for channel_id in channel_ids:
        logger.info(f"--- Checking Channel ID: {channel_id} ---")
        channel_name = get_channel_name(youtube, channel_id) # Get channel name for context

        latest_videos = get_latest_videos(youtube, channel_id, max_results)

        if not latest_videos:
            logger.info(f"No recent videos found for channel: {channel_name} ({channel_id})")
            continue

        # Process videos (usually just the latest one if max_results=1)
        for video in latest_videos:
            video_id = video['id']
            video_title = video['title']
            video_url = video['url']
            logger.info(f"Checking video: '{video_title}' ({video_id})")

            # 6. Check if Already Processed
            if video_id in processed_video_ids:
                logger.info(f"Video '{video_title}' ({video_id}) has already been processed. Skipping.")
                continue # Skip to next video or next channel

            logger.info(f"New video found: '{video_title}' ({video_id}). Processing...")

            # 7. Get Transcript
            transcript = get_transcript(video_id)
            if transcript is None:
                logger.warning(f"Could not retrieve transcript for '{video_title}' ({video_id}). Skipping analysis and notification for this video.")
                # Optionally add to processed_ids even if transcript fails to avoid retrying?
                # processed_video_ids.add(video_id) # Uncomment if you want to mark as processed even without summary
                continue # Skip to next video

            # 8. Get Summaries from Gemini
            logger.info(f"Requesting summaries for '{video_title}' from Gemini...")
            exec_summary = get_gemini_summary(gemini, prompt_exec, transcript, "executive summary")
            time.sleep(1) # Small delay between API calls (optional, adjust as needed)
            detailed_summary = get_gemini_summary(gemini, prompt_detail, transcript, "detailed summary")
            time.sleep(1) # Small delay
            key_quotes = get_gemini_summary(gemini, prompt_quotes, transcript, "key quotes")

            # Check if Gemini calls were successful (basic check)
            if "Could not generate" in exec_summary or "Could not generate" in detailed_summary or "Could not generate" in key_quotes:
                logger.error(f"Failed to generate one or more summaries/quotes for '{video_title}' ({video_id}). Check previous logs. Skipping email notification.")
                # Optionally save partial results?
                # Mark as processed to avoid retrying failed Gemini calls?
                processed_video_ids.add(video_id)
                continue

            # 9. Save Summaries Locally
            summary_filename = f"{video_id}_{channel_name.replace(' ', '_')}_{video_title[:50].replace(' ', '_')}.txt"
            summary_filepath = pathlib.Path(output_dir) / pathlib.Path(summary_filename).name # Sanitize filename
            logger.info(f"Saving summaries to: {summary_filepath}")
            try:
                with open(summary_filepath, 'w', encoding='utf-8') as f:
                    f.write(f"Video Title: {video_title}\n")
                    f.write(f"Video URL: {video_url}\n")
                    f.write(f"Channel: {channel_name}\n")
                    f.write(f"Processed Time: {datetime.now().isoformat()}\n")
                    f.write("\n--- Executive Summary ---\n")
                    f.write(exec_summary + "\n")
                    f.write("\n--- Detailed Summary ---\n")
                    f.write(detailed_summary + "\n")
                    f.write("\n--- Key Quotes/Data Points ---\n")
                    f.write(key_quotes + "\n")
            except IOError as e:
                logger.error(f"Failed to write summary file {summary_filepath}: {e}")
            except Exception as e:
                 logger.error(f"Unexpected error writing summary file {summary_filepath}: {e}")


            # 10. Send Email Notification
            logger.info(f"Sending email notification for '{video_title}'...")
            email_sent = send_email(
                channel_name, video_title, video_url,
                exec_summary, detailed_summary, key_quotes,
                config
            )

            # 11. Update Processed Videos List
            if email_sent: # Only mark as processed if email was sent successfully? Or always mark? Your choice.
                 logger.info(f"Successfully processed and notified for video: '{video_title}' ({video_id})")
                 processed_video_ids.add(video_id)
                 new_videos_processed_count += 1
            else:
                 logger.error(f"Failed to send email for '{video_title}' ({video_id}). It will be retried next run unless manually added to {processed_videos_file}.")
                 # Do NOT add to processed_video_ids if email fails, so it retries

            # Optional delay before processing next video/channel
            time.sleep(2)

        # End of loop for videos within a channel
        logger.info(f"--- Finished checking channel: {channel_name} ({channel_id}) ---")
        time.sleep(5) # Optional delay between channels


    # 12. Save Updated Processed Videos List (only if changes were made)
    if len(processed_video_ids) > initial_processed_count:
        logger.info(f"Saving updated processed videos list ({len(processed_video_ids)} total)...")
        save_processed_videos(processed_videos_file, processed_video_ids)
    else:
        logger.info("No new videos were processed in this run.")


    logger.info(f"--- YouTube Monitor Script Finished ({new_videos_processed_count} new videos processed) ---")

if __name__ == "__main__":
    main()