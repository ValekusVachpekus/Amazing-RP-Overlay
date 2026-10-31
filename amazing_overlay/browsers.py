"""One browser cache for the installer, source runs and frozen backends."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def browser_environment(environ=None):
    env = dict(os.environ if environ is None else environ)
    if not env.get("PLAYWRIGHT_BROWSERS_PATH"):
        home = Path(env.get("USERPROFILE") or env.get("HOME") or str(Path.home()))
        if sys.platform == "win32":
            cache = Path(env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")) / "ms-playwright"
        elif sys.platform == "darwin":
            cache = home / "Library" / "Caches" / "ms-playwright"
        else:
            cache = Path(env.get("XDG_CACHE_HOME") or str(home / ".cache")) / "ms-playwright"
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache)
    return env


def configure_browser_environment():
    env = browser_environment()
    # Explicitly setting this prevents Playwright's frozen-mode default of "0".
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = env["PLAYWRIGHT_BROWSERS_PATH"]
    return env["PLAYWRIGHT_BROWSERS_PATH"]
