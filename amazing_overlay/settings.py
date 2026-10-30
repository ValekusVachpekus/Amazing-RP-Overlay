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
            "max_tokens": 1500, "context_chars": 32000, "font_size": 15, "opacity": 0.96,
            "toggle_hotkey": "Ctrl+L", "input_hotkey": "Alt+F"}


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
                for old, new in (("toggle", "toggle_hotkey"), ("search", "input_hotkey")):
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
    value["has_api_key"] = bool(value.get("ai_api_key"))
    if not private:
        value.pop("ai_api_key", None)
    return value


def save_settings(values: dict):
    current = load_settings()
    for key in DEFAULTS:
        if key in values:
            current[key] = values[key]
    for key, low, high in (("max_tokens", 128, 16000), ("context_chars", 4000, 120000), ("font_size", 10, 30)):
        current[key] = max(low, min(high, int(current[key])))
    current["opacity"] = max(0.4, min(1.0, float(current["opacity"])))
    from .win32 import parse_hotkey
    first, second = parse_hotkey(current["toggle_hotkey"]), parse_hotkey(current["input_hotkey"])
    if first == second or first[1] == 27 or second[1] == 27:
        raise ValueError("Горячие клавиши должны различаться и не занимать Esc")
    for key in ("has_api_key", "env_overrides", "ai_api_key"):
        current.pop(key, None)
    atomic_json(DATA / "settings.json", current)
    if values.get("ai_api_key"):
        set_key(str(DATA / ".env"), "AI_API_KEY", values["ai_api_key"])
    return load_settings()
