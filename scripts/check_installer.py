"""Disposable all-user installer acceptance. Run elevated on a Windows test VM."""
import argparse
import ctypes
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "{5F1B31C4-08A8-4AE1-9DCD-E62A20280001}_is1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", type=Path, required=True)
    args = parser.parse_args()
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit("Installer acceptance requires an elevated Windows test session.")
    acceptance = (ROOT / ".cache/installer-acceptance").resolve()
    app = acceptance / "app"
    key_name = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + APP_ID
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            location = Path(winreg.QueryValueEx(key, "InstallLocation")[0]).resolve()
            if location != app:
                raise SystemExit("Refusing to replace a non-test installation.")
    except FileNotFoundError:
        pass
    acceptance.mkdir(parents=True, exist_ok=True)
    data, notes = acceptance / "data", acceptance / "Notes"
    data.mkdir(exist_ok=True)
    notes.mkdir(exist_ok=True)
    (data / "settings.json").write_text(json.dumps({"notes_dir": str(notes), "ai_model": "acceptance-fixture"}), encoding="utf-8")
    (data / ".env").write_bytes(b'AI_API_KEY="synthetic-installer-fixture"\r\n')
    with sqlite3.connect(data / "laws.db") as db:
        db.execute("CREATE TABLE IF NOT EXISTS installer_fixture(value TEXT)")
        db.execute("DELETE FROM installer_fixture")
        db.execute("INSERT INTO installer_fixture VALUES ('preserved')")
    (notes / "cp1251.txt").write_bytes("Заметка\r\n".encode("cp1251"))
    (notes / "utf8-bom.txt").write_bytes(b"\xef\xbb\xbf" + "Заметка\r\n".encode())
    snapshots = {p: hashlib.sha256(p.read_bytes()).hexdigest() for folder in (data, notes) for p in folder.iterdir() if not p.name.startswith("laws.db")}
    unmanaged = app / "_internal/user-notes/preserve.txt"
    flags = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/DIR=" + str(app)]
    def setup(name, expected=0):
        result = subprocess.run([str(args.setup.resolve()), *flags, "/LOG=" + str(acceptance / (name + ".log"))], timeout=180)
        assert result.returncode == expected, (name, result.returncode, expected)
    def preserved():
        assert all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in snapshots.items()), "Personal fixtures changed"
        with sqlite3.connect(data / "laws.db") as db:
            assert db.execute("SELECT value FROM installer_fixture").fetchall() == [("preserved",)]
    def uninstall():
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            command = winreg.QueryValueEx(key, "UninstallString")[0]
        executable = Path(command.strip('"')).resolve()
        assert executable.parent == app.resolve() and executable.name.startswith("unins") and executable.suffix == ".exe"
        result = subprocess.run([str(executable), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=120)
        assert result.returncode == 0
        deadline = time.monotonic() + 20
        while executable.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        assert not executable.exists(), "Uninstaller has not finished removing its original executable"
    setup("install")
    assert (app / "AmazingHelper.exe").is_file() and (app / "installed.json").is_file()
    preserved()
    unmanaged.parent.mkdir(parents=True, exist_ok=True)
    unmanaged.write_bytes(b"unmanaged runtime-adjacent note")
    snapshots[unmanaged] = hashlib.sha256(unmanaged.read_bytes()).hexdigest()
    # Keep only our own synthetic backend alive; Setup must decline replacement.
    env = dict(os.environ, AMAZING_OVERLAY_DATA=str(data), AMAZING_OVERLAY_PIPE="amazing-helper-installer-" + str(os.getpid()), QT_QPA_PLATFORM="offscreen")
    backend = subprocess.Popen([str(app / "AmazingHelper.exe"), "backend"], env=env, cwd=app)
    try:
        time.sleep(2)
        assert backend.poll() is None
        setup("busy", expected=7)
        assert backend.poll() is None, "Setup must not kill the backend"
    finally:
        backend.terminate()  # Only this synthetic process, never a user's backend.
        backend.wait(timeout=15)
    (app / "_internal/python312.dll").write_bytes(b"damaged runtime fixture")
    setup("repair")
    manifest = json.loads((app / "manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256((app / "_internal/python312.dll").read_bytes()).hexdigest() == manifest["files"]["_internal/python312.dll"]["sha256"]
    preserved()
    # A user's unmanaged Notes inside the program directory also survives.
    personal = app / "Notes" / "preserve.txt"
    personal.parent.mkdir(exist_ok=True)
    personal.write_bytes(b"unmanaged personal note")
    uninstall()
    preserved()
    assert personal.read_bytes() == b"unmanaged personal note"
    assert not (app / "AmazingHelper.exe").exists()
    setup("reinstall")
    preserved()
    uninstall()
    preserved()
    print("INSTALLER: PASS; install, busy refusal, repair, uninstall, reinstall; personal fixtures preserved")


if __name__ == "__main__":
    main()
