"""Explicit, small real API check; never prints the credential."""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from amazing_overlay.ai import completion, friendly_error
from amazing_overlay.settings import load_settings


def main():
    settings = load_settings(True)
    result = {"provider": settings["ai_provider"], "model": settings["ai_model"], "has_api_key": bool(settings.get("ai_api_key"))}
    try:
        answer, usage = completion(settings, [{"role": "user", "content": "Reply with OK"}], threading.Event(), lambda _: None, 32)
        result.update(ok=True, answer=answer, usage=usage)
    except Exception as exc:
        result.update(ok=False, error=friendly_error(exc, settings))
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
