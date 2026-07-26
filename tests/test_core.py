# -*- coding: utf-8 -*-
"""Headless tests for the non-GUI core (no tkinter, no network)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdf2zh_core as core  # noqa: E402


# ------------------------------------------------------------ page ranges --
@pytest.mark.parametrize("text,expected", [
    ("", None),
    ("   ", None),
    ("全部", None),
    ("all", None),
    ("1", [0]),
    ("1,3,5-8", [0, 2, 4, 5, 6, 7]),
    ("3-3", [2]),
    ("2, 2 , 1", [0, 1]),
])
def test_parse_pages(text, expected):
    assert core.parse_pages(text) == expected


@pytest.mark.parametrize("text", ["0", "abc", "5-2", "1-", "-3", "1,x"])
def test_parse_pages_rejects_bad_input(text):
    with pytest.raises(ValueError):
        core.parse_pages(text)


# -------------------------------------------------------------- base URLs --
@pytest.mark.parametrize("raw,expected", [
    ("https://api.anthropic.com", "https://api.anthropic.com/v1"),
    ("https://api.anthropic.com/", "https://api.anthropic.com/v1"),
    ("https://api.anthropic.com/v1", "https://api.anthropic.com/v1"),
    ("http://localhost:8000", "http://localhost:8000/v1"),
    ("https://api.longcat.chat/anthropic", "https://api.longcat.chat/anthropic/v1"),
    ("https://example.com/v1beta/openai/", "https://example.com/v1beta/openai"),
])
def test_normalize_base_url_openai_like(raw, expected):
    assert core._normalize_base_url(raw, "openailiked") == expected


def test_normalize_base_url_uses_fixed_provider_base():
    assert core._normalize_base_url("https://api.deepseek.com", "deepseek") == (
        "https://api.deepseek.com/v1"
    )


def test_normalize_base_url_keeps_empty():
    assert core._normalize_base_url("", "openailiked") == ""


# ---------------------------------------------------------------- prompts --
def test_build_prompt_template_returns_none_without_notes():
    assert core.build_prompt_template("") is None
    assert core.build_prompt_template("   \n  ") is None


def test_build_prompt_template_injects_bulleted_notes():
    tpl = core.build_prompt_template("不翻译公式\n保留人名")
    rendered = tpl.safe_substitute(lang_out="zh", text="Hello")
    assert "- 不翻译公式" in rendered
    assert "- 保留人名" in rendered
    assert "Hello" in rendered
    # pdf2zh substitutes these later, so they must survive our pass
    assert "$notes" not in rendered


# ------------------------------------------------------------ output modes --
@pytest.mark.parametrize("mode,expected", [
    ("both", ("dual", "mono")),
    ("dual", ("dual",)),
    ("mono", ("mono",)),
    ("unknown", ("dual", "mono")),
])
def test_select_outputs(mode, expected):
    assert core.select_outputs(mode) == expected


# ------------------------------------------------------------------ proxy --
def test_proxy_modes_round_trip():
    for label, key in core.PROXY_MODES.items():
        assert core.PROXY_MODE_LABELS[key] == label


def test_proxy_direct_disables_env_and_trust(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://example:1")
    core.set_proxy_settings("direct")
    try:
        assert core.resolve_proxy() == (None, False)
        assert "HTTP_PROXY" not in os.environ
    finally:
        core.set_proxy_settings("system")


def test_proxy_custom_sets_env(monkeypatch):
    core.set_proxy_settings("custom", "http://127.0.0.1:7890")
    try:
        assert core.resolve_proxy() == ("http://127.0.0.1:7890", False)
        assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    finally:
        core.set_proxy_settings("system")


def test_proxy_custom_without_url_falls_back_to_system():
    core.set_proxy_settings("custom", "")
    try:
        assert core.get_proxy_settings()["mode"] == "system"
        assert core.resolve_proxy() == (None, True)
    finally:
        core.set_proxy_settings("system")


# --------------------------------------------------------- response shapes --
def test_extract_message_openai():
    data = {"choices": [{"message": {"content": " hi "}}]}
    assert core.extract_message(data) == "hi"


def test_extract_message_anthropic_blocks():
    data = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert core.extract_message(data) == "ab"


def test_extract_message_ollama():
    assert core.extract_message({"message": {"content": "ok"}}) == "ok"


def test_extract_message_unknown_shape():
    assert core.extract_message({"foo": 1}) == ""
    assert core.extract_message(None) == ""


# ----------------------------------------------------------- api targeting --
def test_api_target_anthropic_headers():
    base, headers = core._api_target("anthropic", {
        "OPENAILIKED_BASE_URL": "https://api.anthropic.com",
        "OPENAILIKED_API_KEY": "sk-test",
    })
    assert base == "https://api.anthropic.com/v1"
    assert headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"] == "2023-06-01"


def test_api_target_requires_key():
    with pytest.raises(ValueError):
        core._api_target("deepseek", {})


def test_is_anthropic_style():
    assert core.is_anthropic_style("claudeliked", "https://x.test/v1")
    assert core.is_anthropic_style("openailiked", "https://x.test/anthropic/v1")
    assert not core.is_anthropic_style("openailiked", "https://x.test/v1")


# ------------------------------------------------------------- max tokens --
def test_translate_max_tokens_is_clamped():
    core.set_translate_max_tokens(10)
    assert core.get_translate_max_tokens() == 256
    core.set_translate_max_tokens(999999)
    assert core.get_translate_max_tokens() == 32000
    core.set_translate_max_tokens("nonsense")
    assert core.get_translate_max_tokens() == core.DEFAULT_MAX_TOKENS


# ---------------------------------------------------------------- secrets --
def test_secret_round_trip():
    token = core.encrypt_secret("sk-secret-value")
    assert core.decrypt_secret(token) == "sk-secret-value"
    if os.name == "nt":
        assert token.startswith("enc:v1:")


def test_secret_helpers_are_noops_on_empty():
    assert core.encrypt_secret("") == ""
    assert core.decrypt_secret("") == ""
    assert core.decrypt_secret("plaintext-key") == "plaintext-key"


def test_profiles_round_trip_encrypts_api_key(tmp_path, monkeypatch):
    path = tmp_path / "gui_services.json"
    monkeypatch.setattr(core, "GUI_SERVICES_PATH", path)
    profile = {
        "display": "My Claude",
        "type": "anthropic",
        "envs": {
            "OPENAILIKED_BASE_URL": "https://api.anthropic.com",
            "OPENAILIKED_API_KEY": "sk-plain",
            "OPENAILIKED_MODEL": "claude-sonnet-5",
        },
    }
    core.save_profiles([profile])
    on_disk = path.read_text(encoding="utf-8")
    if os.name == "nt":  # DPAPI only exists on Windows
        assert "sk-plain" not in on_disk
    loaded = core.load_profiles()
    assert len(loaded) == 1
    assert loaded[0]["envs"]["OPENAILIKED_API_KEY"] == "sk-plain"
    # stored base URL is normalized on read
    assert loaded[0]["envs"]["OPENAILIKED_BASE_URL"] == "https://api.anthropic.com/v1"


def test_load_profiles_skips_unknown_types(tmp_path, monkeypatch):
    path = tmp_path / "gui_services.json"
    path.write_text('[{"display": "x", "type": "nope", "envs": {}}]', encoding="utf-8")
    monkeypatch.setattr(core, "GUI_SERVICES_PATH", path)
    assert core.load_profiles() == []


def test_load_profiles_tolerates_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "GUI_SERVICES_PATH", tmp_path / "absent.json")
    assert core.load_profiles() == []


# ------------------------------------------------------------------ prefs --
def test_prefs_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "GUI_PREFS_PATH", tmp_path / "gui_prefs.json")
    core.save_prefs({"ui_scale": 1.2, "proxy_mode": "direct"})
    prefs = core.load_prefs()
    assert prefs["ui_scale"] == 1.2
    assert prefs["proxy_mode"] == "direct"
    # repo defaults still fill in the keys the user never touched
    assert "lang_out" in prefs


# ---------------------------------------------------------------- schemas --
def test_every_service_schema_has_a_label():
    assert set(core.SERVICE_SCHEMAS) == set(core.SERVICE_TYPE_LABELS)


def test_groq_default_model_is_a_valid_id():
    default = dict(
        (k, d) for k, _l, _r, _s, d in core.SERVICE_SCHEMAS["groq"]
    )["GROQ_MODEL"]
    assert default == "llama-3.3-70b-versatile"


def test_service_backend_maps_to_known_pdf2zh_service():
    for backend in core.SERVICE_BACKEND.values():
        assert backend in core.SERVICE_SCHEMAS
