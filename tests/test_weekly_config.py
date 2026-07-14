import pytest

from config.summary import load_weekly_config, validate_weekly_config
from config.models import WeeklyConfig


def test_loads_channels_and_recipients(tmp_path) -> None:
    config_file = tmp_path / "summary.ini"
    config_file.write_text(
        """
[CHANNELS]
Example = UCaaaaaaaaaaaaaaaaaaaaaa
Other = UCbbbbbbbbbbbbbbbbbbbbbb

[CHANNEL_RECIPIENTS]
default_recipients = fallback@example.com
UCaaaaaaaaaaaaaaaaaaaaaa = alice@example.com, bob@example.com

[SETTINGS]
window_days = 10
max_results_per_channel = 15
subject_prefix = My Weekly Digest
log_file = logs/custom_weekly.log
log_level = DEBUG
""",
        encoding="utf-8",
    )

    config = load_weekly_config(str(config_file))

    assert config.channel_ids == [
        "UCaaaaaaaaaaaaaaaaaaaaaa",
        "UCbbbbbbbbbbbbbbbbbbbbbb",
    ]
    assert config.channel_recipients == {
        "UCaaaaaaaaaaaaaaaaaaaaaa": ["alice@example.com", "bob@example.com"]
    }
    assert config.default_recipients == ["fallback@example.com"]
    assert config.window_days == 10
    assert config.max_results_per_channel == 15
    assert config.subject_prefix == "My Weekly Digest"
    assert config.log_file == "logs/custom_weekly.log"
    assert config.log_level == "DEBUG"


def test_channel_without_recipients_or_default_fails(tmp_path) -> None:
    config_file = tmp_path / "summary.ini"
    config_file.write_text(
        """
[CHANNELS]
Example = UCaaaaaaaaaaaaaaaaaaaaaa

[CHANNEL_RECIPIENTS]
UCbbbbbbbbbbbbbbbbbbbbbb = someone@example.com
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="recipients"):
        load_weekly_config(str(config_file))


def test_channel_without_specific_entry_falls_back_to_default() -> None:
    config = WeeklyConfig(
        channel_ids=["UCaaaaaaaaaaaaaaaaaaaaaa"],
        channel_recipients={},
        default_recipients=["fallback@example.com"],
    )

    assert validate_weekly_config(config) == []


def test_no_channels_configured_fails() -> None:
    config = WeeklyConfig(channel_ids=[], channel_recipients={})

    errors = validate_weekly_config(config)

    assert any("channels" in e for e in errors)
