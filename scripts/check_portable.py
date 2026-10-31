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
    executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist" / "AmazingLawOverlay" / "AmazingLawOverlay.exe"
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
            for mode in ("manager", "overlay"):
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
