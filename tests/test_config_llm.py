from config import _is_valid_llm_host, load_config, validate_config


def test_loads_remote_llama_cpp_configuration(tmp_path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[API_KEYS]
youtube_api_key = youtube-example-key

[CHANNELS]
Example = UCaaaaaaaaaaaaaaaaaaaaaa

[EMAIL]
smtp_server = smtp.example.com
smtp_user = monitor@example.com
smtp_password = example-password
sender_email = monitor@example.com

[CHANNEL_RECIPIENTS]
default_recipients = admin@example.com

[LLM]
provider = llama_cpp
host = llm.internal.example
port = 8081
use_tls = true
api_key = local-example-key
model_name = qwen3.6-a35b
temperature = 0.6
executive_max_output_tokens = 1024
detailed_max_output_tokens = 8192
quotes_max_output_tokens = 2048
request_timeout = 600
context_tokens = 262144
""",
        encoding="utf-8",
    )

    config = load_config(str(config_file))

    assert config.llm_provider == "llama_cpp"
    assert config.llm_host == "llm.internal.example"
    assert config.llm_port == 8081
    assert config.llm_use_tls is True
    assert config.llm_model == "qwen3.6-a35b"
    assert config.llm_temperature == 0.6
    assert config.llm_executive_max_output_tokens == 1024
    assert config.llm_detailed_max_output_tokens == 8192
    assert config.llm_quotes_max_output_tokens == 2048
    assert config.llm_request_timeout == 600
    assert config.llm_context_tokens == 262144
    assert config.gemini_api_key == ""
    assert validate_config(config) == []


def test_rejects_url_in_llm_host(tmp_path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[API_KEYS]
youtube_api_key = youtube-example-key

[CHANNELS]
Example = UCaaaaaaaaaaaaaaaaaaaaaa

[EMAIL]
smtp_server = smtp.example.com
smtp_user = monitor@example.com
smtp_password = example-password
sender_email = monitor@example.com

[CHANNEL_RECIPIENTS]
default_recipients = admin@example.com

[LLM]
provider = llama_cpp
host = http://llm.internal.example/v1
api_key = local-example-key
model_name = qwen3.6-a35b
""",
        encoding="utf-8",
    )

    try:
        load_config(str(config_file))
    except ValueError as error:
        assert "hostname or IP address only" in str(error)
    else:
        raise AssertionError("Expected invalid LLM host to fail validation")


def test_llm_host_validation_accepts_hosts_but_not_embedded_ports() -> None:
    assert _is_valid_llm_host("llm.internal.example") is True
    assert _is_valid_llm_host("192.168.1.50") is True
    assert _is_valid_llm_host("2001:db8::1") is True
    assert _is_valid_llm_host("llm.internal.example:8080") is False


def test_legacy_max_output_tokens_applies_to_all_summary_types(
    tmp_path,
) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[API_KEYS]
youtube_api_key = youtube-example-key

[CHANNELS]
Example = UCaaaaaaaaaaaaaaaaaaaaaa

[EMAIL]
smtp_server = smtp.example.com
smtp_user = monitor@example.com
smtp_password = example-password
sender_email = monitor@example.com

[CHANNEL_RECIPIENTS]
default_recipients = admin@example.com

[LLM]
provider = llama_cpp
host = llm.internal.example
api_key = local-example-key
model_name = qwen3.6-a35b
max_output_tokens = 4096
""",
        encoding="utf-8",
    )

    config = load_config(str(config_file))

    assert config.llm_executive_max_output_tokens == 4096
    assert config.llm_detailed_max_output_tokens == 4096
    assert config.llm_quotes_max_output_tokens == 4096
