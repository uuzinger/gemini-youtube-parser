from types import SimpleNamespace

from services import model_validator


def _model(name: str):
    return SimpleNamespace(
        name=name,
        supported_actions=["generateContent", "countTokens"],
    )


def _client_with_models(models):
    return SimpleNamespace(
        models=SimpleNamespace(list=lambda: models),
    )


def test_validate_model_accepts_exact_normalized_name(monkeypatch) -> None:
    models = [
        _model("models/gemini-2.5-flash"),
        _model("models/gemini-2.5-flash-lite"),
    ]
    monkeypatch.setattr(
        model_validator.genai,
        "Client",
        lambda api_key: _client_with_models(models),
    )
    monkeypatch.setattr(model_validator, "clear_model_suggestion", lambda: None)

    result = model_validator.validate_model(
        "example-key",
        "gemini-2.5-flash",
    )

    assert result.configured_available is True


def test_validate_model_returns_current_models_when_unavailable(
    monkeypatch,
) -> None:
    models = [
        _model("models/gemini-2.5-pro"),
        _model("models/gemini-2.5-flash"),
    ]
    monkeypatch.setattr(
        model_validator.genai,
        "Client",
        lambda api_key: _client_with_models(models),
    )
    monkeypatch.setattr(
        model_validator,
        "_write_model_suggestion",
        lambda suggested, old: None,
    )

    result = model_validator.validate_model(
        "example-key",
        "gemini-retired",
    )

    assert result.configured_available is False
    assert result.suggested_model == "models/gemini-2.5-pro"
    assert result.available_models == (
        "models/gemini-2.5-pro",
        "models/gemini-2.5-flash",
    )
