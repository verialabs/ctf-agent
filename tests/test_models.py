from pathlib import Path

import pytest
from pydantic_ai.profiles.openai import OpenAIModelProfile

from backend.config import Settings
from backend.cost_tracker import PROVIDER_MAP
from backend.models import (
    DEFAULT_MODELS,
    context_window,
    model_id_from_spec,
    provider_from_spec,
    resolve_model,
    resolve_model_settings,
)


def test_deepseek_settings_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings()

    assert settings.deepseek_api_key == ""
    assert settings.deepseek_base_url == "https://api.deepseek.com"


def test_deepseek_model_spec_and_context() -> None:
    spec = "deepseek/deepseek-v4-pro"

    assert provider_from_spec(spec) == "deepseek"
    assert model_id_from_spec(spec) == "deepseek-v4-pro"
    assert context_window(spec) == 1_000_000
    assert PROVIDER_MAP["deepseek"] == "deepseek"


def test_deepseek_flash_current_name_and_legacy_alias() -> None:
    assert context_window("deepseek/deepseek-flash") == 1_000_000
    assert context_window("deepseek/deepseek-v4-flash") == 1_000_000


def test_deepseek_resolves_openai_compatible_model() -> None:
    settings = Settings(
        deepseek_api_key="test-key",
        deepseek_base_url="https://api.deepseek.com",
    )

    model = resolve_model("deepseek/deepseek-flash", settings)
    model_settings = resolve_model_settings("deepseek/deepseek-flash")

    assert model.model_name == "deepseek-flash"
    profile = OpenAIModelProfile.from_profile(model.profile)
    assert profile.openai_supports_tool_choice_required is False
    assert model_settings["max_tokens"] == 128_000


def test_codex_defaults_use_gpt55() -> None:
    assert "codex/gpt-5.5" in DEFAULT_MODELS
    assert "codex/gpt-5.4" not in DEFAULT_MODELS
    assert context_window("codex/gpt-5.6-sol") == 1_000_000


def test_deepseek_requires_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings()

    try:
        resolve_model("deepseek/deepseek-flash", settings)
    except ValueError as exc:
        assert str(exc) == "DEEPSEEK_API_KEY is required for deepseek/* models"
    else:
        raise AssertionError("missing DeepSeek API key should fail before an API request")
