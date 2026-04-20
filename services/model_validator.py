from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from google import genai
from google.genai import types

from .exceptions import ModelNotFoundError

logger = logging.getLogger(__name__)

MODEL_SUGGESTION_FILE = ".model_suggestion"

# Priority order for model selection
_MODEL_PRIORITY = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-pro",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-pro",
    "gemini-flash",
]


def _model_supports_text_generation(model: types.Model) -> bool:
    if not model.supported_generating_methods:
        return False
    return any(
        "generateContent" in method for method in model.supported_generating_methods
    )


def _find_best_model_from_list(models) -> str | None:
    text_models = []
    for model in models:
        if _model_supports_text_generation(model):
            text_models.append(model.name)
    if not text_models:
        return None
    for priority_name in _MODEL_PRIORITY:
        for name in text_models:
            if priority_name in name:
                return name
    return text_models[0]


def validate_model(api_key: str, model_name: str) -> str | None:
    """Validate that the configured model is available.
    
    Returns the suggested model name if the configured one is not found,
    or None if everything is fine.
    """
    client = genai.Client(api_key=api_key)
    
    try:
        available_models = list(client.models.list())
    except Exception as e:
        logger.error("Failed to list available models: %s", e)
        return None

    # Check if configured model exists
    configured_found = False
    for model in available_models:
        if model_name in model.name:
            if _model_supports_text_generation(model):
                configured_found = True
                break

    if configured_found:
        logger.info("Configured model '%s' is available.", model_name)
        return None

    # Model not found - find the best alternative
    suggested = _find_best_model_from_list(available_models)
    if suggested:
        logger.warning(
            "Configured model '%s' is not available. "
            "Suggested model: '%s'.",
            model_name,
            suggested,
        )
        _write_model_suggestion(suggested, model_name)
        return suggested

    logger.warning(
        "Configured model '%s' is not available and no suitable replacement found.",
        model_name,
    )
    return None


def _write_model_suggestion(suggested_model: str, old_model: str) -> None:
    suggestion = {
        "suggested_model": suggested_model,
        "old_model": old_model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": (
            f"Your configured model '{old_model}' is no longer available. "
            f"Update your config.ini [GEMINI] model_name to '{suggested_model}' "
            f"or the newest best model."
        ),
    }
    try:
        with open(MODEL_SUGGESTION_FILE, "w", encoding="utf-8") as f:
            json.dump(suggestion, f, indent=2)
        logger.info("Model suggestion written to %s", MODEL_SUGGESTION_FILE)
    except OSError as e:
        logger.error("Failed to write model suggestion file: %s", e)


def read_model_suggestion() -> dict | None:
    """Read the model suggestion file if it exists."""
    if not os.path.exists(MODEL_SUGGESTION_FILE):
        return None
    try:
        with open(MODEL_SUGGESTION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read model suggestion file: %s", e)
        return None


def clear_model_suggestion() -> None:
    """Remove the model suggestion file."""
    if os.path.exists(MODEL_SUGGESTION_FILE):
        try:
            os.remove(MODEL_SUGGESTION_FILE)
            logger.info("Cleared model suggestion file.")
        except OSError as e:
            logger.error("Failed to clear model suggestion file: %s", e)
