from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("AMAZING_OVERLAY_DATA", str(Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "AmazingLawOverlay" / "v2")))
PIPE_NAME = os.getenv("AMAZING_OVERLAY_PIPE", "amazing-law-overlay-v2")
DEFAULTS = {"ai_provider": "openai", "ai_base_url": "https://openrouter.ai/api/v1", "ai_model": "openrouter/free",
            "max_tokens": 1500, "context_chars": 32000, "font_size": 17, "opacity": 0.96,
            "theme": "dark", "animations": True,
            "toggle_hotkey": "Ctrl+L", "input_hotkey": "Alt+F", "prev_law_hotkey": "Alt+Left",
            "next_law_hotkey": "Alt+Right", "scroll_up_hotkey": "Alt+Up", "scroll_down_hotkey": "Alt+Down"}


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_settings(private=False) -> dict:
    value = dict(DEFAULTS)
    for path in (ROOT / "config.json", DATA / "settings.json"):
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if path == ROOT / "config.json":
                for old, new in (("toggle", "toggle_hotkey"), ("search", "input_hotkey"), ("prev_law", "prev_law_hotkey"),
                                 ("next_law", "next_law_hotkey"), ("scroll_up", "scroll_up_hotkey"), ("scroll_down", "scroll_down_hotkey")):
                    hotkey = loaded.get("hotkeys", {}).get(old)
                    if hotkey:
                        value[new] = "+".join([*hotkey.get("modifiers", []), hotkey["key"]])
                if "opacity" in loaded.get("window", {}):
                    value["opacity"] = loaded["window"]["opacity"]
            value.update(loaded)
    env = {**dotenv_values(ROOT / ".env"), **dotenv_values(DATA / ".env"), **os.environ}
    overrides = []
    for name in ("provider", "base_url", "model", "api_key"):
        key = "AI_" + name.upper()
        if env.get(key):
            value["ai_" + name] = env[key]
            if name != "api_key":
                overrides.append(key)
    value["env_overrides"] = overrides
    value["api_key_from_environment"] = bool(os.environ.get("AI_API_KEY"))
    value["has_api_key"] = bool(value.get("ai_api_key"))
    if not private:
        value.pop("ai_api_key", None)
    return value


def validated_settings(values: dict):
    current = load_settings()
    for key in DEFAULTS:
        if key in values:
            current[key] = values[key]
    for key, low, high in (("max_tokens", 128, 16000), ("context_chars", 4000, 120000), ("font_size", 14, 26)):
        current[key] = max(low, min(high, int(current[key])))
    current["opacity"] = max(0.3, min(1.0, float(current["opacity"])))
    if current["theme"] not in {"dark", "light"} or not isinstance(current["animations"], bool):
        raise ValueError("Некорректная тема или настройка анимации")
    if current["ai_provider"] not in {"openai", "gemini"}:
        raise ValueError("Неизвестный провайдер ИИ")
    from urllib.parse import urlsplit
    address = urlsplit(current["ai_base_url"])
    if address.scheme not in {"http", "https"} or not address.netloc or address.username or address.password:
        raise ValueError("Укажите адрес API http(s) без ключа и пароля в адресе")
    if not str(current["ai_model"]).strip():
        raise ValueError("Укажите модель ИИ")
    from .win32 import parse_hotkey
    bindings = [parse_hotkey(current[key]) for key in DEFAULTS if key.endswith("_hotkey")]
    if len(set(bindings)) != len(bindings) or any(value[1] == 27 for value in bindings):
        raise ValueError("Горячие клавиши должны различаться и не занимать Esc")
    for key in ("has_api_key", "env_overrides", "api_key_from_environment", "ai_api_key"):
        current.pop(key, None)
    return current


def preview_settings(values: dict):
    """Evaluate unsaved API fields with the same env precedence as saving."""
    current = validated_settings(values)
    private = load_settings(True)
    current["ai_api_key"] = values.get("ai_api_key") or private.get("ai_api_key", "")
    env = {**dotenv_values(ROOT / ".env"), **dotenv_values(DATA / ".env"), **os.environ}
    for name in ("provider", "base_url", "model"):
        if env.get("AI_" + name.upper()):
            current["ai_" + name] = env["AI_" + name.upper()]
    if os.environ.get("AI_API_KEY"):
        current["ai_api_key"] = os.environ["AI_API_KEY"]
    return current


def save_settings(values: dict):
    current = validated_settings(values)
    atomic_json(DATA / "settings.json", current)
    if values.get("ai_api_key"):
        set_key(str(DATA / ".env"), "AI_API_KEY", values["ai_api_key"])
    return load_settings()
