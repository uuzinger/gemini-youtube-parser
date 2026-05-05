#!/bin/bash

# --- Grab a single YouTube video: transcript + Gemini executive summary ---

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_DIR" ] || [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "ERROR: Virtual environment not found at $VENV_DIR. Run 'python setup.py' first."
    exit 1
fi

source "$VENV_DIR/bin/activate"
cd "$SCRIPT_DIR" || exit 1

export PYTHONIOENCODING=utf-8

if [ $# -lt 1 ]; then
    echo "Usage: $0 <youtube-url>"
    echo "  e.g. $0 https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    exit 1
fi

python "$SCRIPT_DIR/grab_video.py" "$1"
EXIT_CODE=$?

deactivate
exit $EXIT_CODE
