"""Run the frozen backend against isolated data and named pipe."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["AMAZING_OVERLAY_PIPE"] = "amazing-law-overlay-portable-smoke"
from PySide6.QtCore import QCoreApplication
from amazing_overlay.settings import ROOT
from amazing_overlay.transport import rpc


def main():
    app = QCoreApplication([])
    paths = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    executable = Path(paths[0]).resolve() if paths else ROOT / ".cache/dist" / "AmazingLawOverlay" / "AmazingLawOverlay.exe"
    with tempfile.TemporaryDirectory() as directory:
        env = {key: value for key, value in os.environ.items() if not key.startswith("AI_")}
        env.update(AMAZING_OVERLAY_DATA=directory, QT_QPA_PLATFORM="offscreen")
        process = subprocess.Popen([str(executable), "backend"], cwd=executable.parent, env=env)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(f"Frozen backend exited: {process.returncode}")
                try:
                    health = rpc("health")
                    break
                except ConnectionError:
                    time.sleep(0.1)
            else:
                raise AssertionError("Frozen backend did not start")
            assert health["version"] == 1
            assert health["documents"] == 0, "Release must not contain local laws"
            assert not rpc("settings")["has_api_key"], "Release must not contain a local credential"
            assert "ai_api_key" not in rpc("settings")
            rpc("state_set", {"key": "portable-test", "value": "работает"})
            assert rpc("state_get", {"key": "portable-test"}) == "работает"
            print("PORTABLE BACKEND: PASS; no local laws or credentials bundled")
            assert health.get("notes"), "Update the backend for notebook support"
            notes_root = Path(directory) / "Личные заметки"
            rpc("settings_save", {"notes_dir": str(notes_root)})
            note = rpc("notes_create", {"path": "Проверка.txt", "text": "Привет\nСтатья 12"})
            saved = rpc("notes_save", {"path": note["path"], "text": "Обновлено", "revision": note["revision"]})
            assert not saved["conflict"] and notes_root.joinpath(note["path"]).read_text(encoding="utf-8") == "Обновлено"
            assert rpc("notes_save", {"path": note["path"], "text": "stale", "revision": note["revision"]})["conflict"]
            print("PORTABLE NOTEBOOK: PASS; UTF-8, atomic save and revision conflicts")
            if "--settings-check" in sys.argv:
                assert health.get("native_settings"), "Update the backend for in-game settings"
                saved = rpc("settings_save", {"opacity": .3, "font_size": 26, "theme": "light", "animations": False})
                assert (saved["opacity"], saved["font_size"], saved["theme"], saved["animations"]) == (.3, 26, "light", False)
                job = rpc("ai_test", {"ai_model": "unsaved-portable-test"})["job_id"]
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    status = rpc("job_status", {"id": job})
                    if status["state"] != "running":
                        break
                    time.sleep(.1)
                assert status["state"] == "error" and "API" in status["error"], status
                assert rpc("settings")["ai_model"] != "unsaved-portable-test"
                print("PORTABLE ASI SETTINGS: PASS; unsaved API check does not change saved fields")
            if "--browser-check" in sys.argv:
                job = rpc("browser_check")["job_id"]
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    status = rpc("job_status", {"id": job})
                    if status["state"] != "running":
                        break
                    time.sleep(0.1)
                assert status["state"] == "done", status.get("error", "Browser check timed out")
                assert status["result"]["ok"]
                assert "_internal" not in status["result"]["browsers_dir"], "Frozen driver reverted to bundle-local browsers"
                print("PORTABLE CHROMIUM LAUNCH: PASS")
            for mode in ("manager", "overlay", "launcher"):
                client = subprocess.Popen([str(executable), mode], cwd=executable.parent, env=env)
                try:
                    time.sleep(3)
                    assert client.poll() is None, f"Frozen {mode} failed to start: {client.returncode}"
                    print(f"PORTABLE {mode.upper()}: PASS")
                finally:
                    if client.poll() is None:
                        client.terminate()
                    client.wait(timeout=10)
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
