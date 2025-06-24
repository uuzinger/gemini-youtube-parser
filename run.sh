#!/bin/bash

# --- Wrapper Script to Run YouTube Monitor ---

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Define the virtual environment directory relative to the script directory
VENV_DIR="$SCRIPT_DIR/.venv"

# Log file for the wrapper script itself (optional, but helpful for cron debugging)
WRAPPER_LOG="$SCRIPT_DIR/run_wrapper.log"

echo "--- Wrapper script started at $(date) ---" >> "$WRAPPER_LOG"

# Check if virtual environment exists
if [ ! -d "$VENV_DIR" ] || [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "$(date): ERROR: Virtual environment not found at $VENV_DIR. Did you run setup.sh?" | tee -a "$WRAPPER_LOG"
    exit 1
fi

# Activate virtual environment
echo "$(date): Activating virtual environment: $VENV_DIR" >> "$WRAPPER_LOG"
source "$VENV_DIR/bin/activate"
if [ $? -ne 0 ]; then
    echo "$(date): ERROR: Failed to activate virtual environment." | tee -a "$WRAPPER_LOG"
    exit 1
fi

# Navigate to the script directory (important for relative paths like config.ini)
cd "$SCRIPT_DIR" || exit 1

# --- FIX: Set the default Python encoding to UTF-8 ---
# This forces Python to use UTF-8 for its standard streams and default file I/O,
# preventing UnicodeEncodeError in minimal cron environments.
export PYTHONIOENCODING=utf-8

# Run the Python script
echo "$(date): Running monitor_youtube.py..." >> "$WRAPPER_LOG"
python monitor_youtube.py >> "$WRAPPER_LOG" 2>&1 # Redirect stdout and stderr to the log
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "$(date): Python script finished successfully." >> "$WRAPPER_LOG"
else
    echo "$(date): ERROR: Python script exited with code $EXIT_CODE." | tee -a "$WRAPPER_LOG"
fi

# Deactivate virtual environment
deactivate
echo "$(date): Deactivated virtual environment." >> "$WRAPPER_LOG"

echo "--- Wrapper script finished at $(date) ---" >> "$WRAPPER_LOG"

exit $EXIT_CODE
