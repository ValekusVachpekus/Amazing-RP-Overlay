"""Exercise the fixed-payload mapper ONLY against the project's own D3D9 host."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--dep-disabled-only", action="store_true", help="Run only the legacy DEP-off host regression")
    args = parser.parse_args()
    native = args.native_dir.resolve()
    with tempfile.TemporaryDirectory() as directory:
        os.environ.update(AMAZING_OVERLAY_DATA=directory, AMAZING_OVERLAY_PIPE="amazing-helper-map-" + str(os.getpid()), QT_QPA_PLATFORM="offscreen", AMAZING_OVERLAY_NATIVE_SELFTEST="1")
        from PySide6.QtCore import QCoreApplication
        from amazing_overlay.transport import rpc, stop_backend
        from amazing_overlay.game_process import process_info
        from amazing_overlay import __version__
        app = QCoreApplication([])
        processes = []
        try:
            backend = subprocess.Popen([sys.executable, "-m", "amazing_overlay", "backend", "--no-legacy-import"], cwd=ROOT)
            processes.append(backend)
            for _ in range(100):
                try:
                    rpc("health")
                    break
                except ConnectionError:
                    time.sleep(.1)
            rpc("settings_save", {"notes_dir": str(Path(directory) / "Notes")})
            for cycle in (range(4, 8) if args.dep_disabled_only else range(8)):
                tls_fixture = cycle % 4 >= 2
                dep_off = cycle >= 4
                artifacts = native / "tls-fixture" if tls_fixture else native
                host_name = "overlay_native_smoke_dep_off.exe" if dep_off else "overlay_native_smoke.exe"
                host = subprocess.Popen([str(native / host_name), "--manual-map-host"], cwd=native)
                processes.append(host)
                time.sleep(.5)
                if host.poll() is not None:
                    raise RuntimeError("D3D9 host unavailable, exit=" + str(host.returncode))
                game = process_info(host.pid)
                command = [str(artifacts / "overlay_test_injector.exe"), "--pid", str(game.pid), "--created", game.created,
                           "--image", game.path, "--sha256", hashlib.sha256((artifacts / "AmazingLawClient.dll").read_bytes()).hexdigest()]
                result = subprocess.run(command, cwd=native, capture_output=True, timeout=60)
                assert result.returncode == 0, result.stdout.decode("utf-8", errors="replace")
                assert json.loads(result.stdout)["dep_enabled"] == (not dep_off), "Host DEP coverage mismatch"
                identity = {"pid": game.pid, "created": game.created}
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if host.poll() is not None:
                        raise RuntimeError("Manual-mapped host exited: " + str(host.returncode))
                    status = rpc("native_status", identity)
                    if status and status["stage"] == "error":
                        raise RuntimeError("Manual-mapped DLL initialization failed: " + str(status.get("error", "")))
                    if status and status["stage"] == "ready":
                        assert status["version"] == __version__
                        assert status["runtime_check"] == tls_fixture, "TLS probe must run only in the isolated test DLL"
                        break
                    time.sleep(.2)
                else:
                    raise RuntimeError("Manual-mapped DLL did not become ready: " + str(status))
                duplicate = subprocess.run(command, cwd=native, capture_output=True, timeout=15)
                assert duplicate.returncode != 0 and json.loads(duplicate.stdout)["stage"] == "preflight"
                assert rpc("health")["native_sessions"] and host.poll() is None
                host.terminate()  # Only our own disposable host; never the game.
                host.wait(timeout=10)
            stop_backend()
            backend.wait(timeout=10)
            print("MANUAL MAP: PASS; production DLL and isolated TLS fixture, DEP-off regression" +
                  ("" if args.dep_disabled_only else " and DEP-on") + ", ready RPC, duplicate rejected")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
