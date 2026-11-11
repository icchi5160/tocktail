import pytest

from tocktail.config.settings import Settings


def test_settings_roundtrip_normalizes_exclusions():
    settings = Settings(10, True, (" Chrome.exe ", "chrome.exe", "CODE.EXE", ""), True, False)
    assert settings.excluded_apps == ("chrome.exe", "code.exe")
    assert Settings.from_dict(settings.to_dict()) == settings


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), 86401])
def test_invalid_idle_preferences(seconds):
    with pytest.raises(ValueError):
        Settings(idle_seconds=seconds)


@pytest.mark.parametrize("name", ["c:/app.exe", "c:\\app.exe", "app", "app.exe\nsecret.exe"])
def test_invalid_exclusions(name):
    with pytest.raises(ValueError):
        Settings(excluded_apps=(name,))


def test_invalid_saved_preferences_are_reported():
    with pytest.raises(ValueError, match="Saved desktop settings"):
        Settings.from_dict({"save_titles": "false"})
    with pytest.raises(ValueError, match="Saved desktop settings"):
        Settings.from_dict({"excluded_apps": "chrome.exe"})


def test_shared_idle_setting_has_precedence():
    assert Settings.from_dict({"idle_seconds": 20}, idle_seconds=10).idle_seconds == 10
