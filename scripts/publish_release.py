"""Copy a portable build to the tracked Release directory, with an integrity manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "Release"
MANIFEST = "manifest.json"
REQUIRED = (
    "start.bat",
    "AmazingHelper.exe", "AmazingLawClient.dll", "AmazingLawClient.asi", "AmazingHelperInjector.exe",
    "start_manager.bat", "start_backend.bat", "start_overlay.bat",
    "start_install-browser.bat",
)
PERSONAL = {"Notes", "local", "laws", "browser-profile", ".browser-profile", ".env", "config.json", "urls.txt"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _digest(stream)


def _digest(stream):
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(chunk)
    return result.hexdigest()


def safe_path(directory, name):
    path = directory / name
    if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()) or name == MANIFEST or name.split('/')[0] in PERSONAL:
        raise ValueError(f"Unsafe release path: {name}")
    return path


def files_in(directory):
    files = {}
    for path in directory.rglob("*"):
        relative = path.relative_to(directory)
        if relative.parts[0] in PERSONAL or (len(relative.parts) == 1 and path.suffix == '.log'):
            continue
        if path.is_symlink():
            raise ValueError(f"Links are not allowed in the release: {path}")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = path
    return files


def verify():
    manifest = json.loads((RELEASE / MANIFEST).read_text(encoding="utf-8"))
    expected = manifest["files"]
    actual = files_in(RELEASE)
    if set(actual) != set(expected) | {MANIFEST}:
        raise ValueError("Release contains missing or extra files; regenerate it before committing")
    for name, entry in expected.items():
        path = safe_path(RELEASE, name)
        if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError(f"Release checksum mismatch: {name}")
    for name in REQUIRED:
        if name not in expected:
            raise ValueError(f"Required release file missing: {name}")
    if expected["AmazingLawClient.dll"]["sha256"] != expected["AmazingLawClient.asi"]["sha256"]:
        raise ValueError("DLL and ASI must be identical")
    print(f"RELEASE: PASS; {len(expected)} files, {sum(e['bytes'] for e in expected.values()) / 1024**2:.1f} MiB")


def publish(bundle):
    bundle = bundle.resolve()
    if bundle == RELEASE.resolve() or RELEASE.resolve().is_relative_to(bundle):
        raise ValueError("Choose a separate portable build directory")
    sources = {}
    for name in REQUIRED:
        path = bundle / name
        if not path.is_file():
            raise ValueError(f"Build the complete portable package first: missing {path}")
        sources[name] = path
    for name in ("_internal", "licenses"):
        directory = bundle / name
        if not directory.is_dir() or not any(directory.iterdir()):
            raise ValueError(f"Required runtime directory missing or empty: {directory}")
        for relative, path in files_in(directory).items():
            sources[f"{name}/{relative}"] = path
    sources["README.md"] = ROOT / "docs" / "RELEASE.md"
    for name in ("V2.md", "PROTOCOL.md", "GAME_ACCEPTANCE.md", "NOTEBOOK.md", "DISTRIBUTION.md"):
        sources[f"docs/{name}"] = ROOT / "docs" / name
    entries = {}
    for name, source in sorted(sources.items()):
        safe_path(RELEASE, name)
        if source.is_symlink() or any(part in {".env", ".browser-profile", "browser-profile", "__pycache__"} for part in source.parts):
            raise ValueError(f"Private or generated data cannot be published: {name}")
        size = source.stat().st_size
        if size >= 100 * 1024**2:
            raise ValueError(f"File exceeds GitHub's ordinary Git file limit: {name}")
        entries[name] = {"bytes": size, "sha256": digest(source)}
    old = {}
    if RELEASE.exists():
        old = json.loads((RELEASE / MANIFEST).read_text(encoding="utf-8"))["files"]
        if set(files_in(RELEASE)) - (set(old) | {MANIFEST}):
            raise ValueError("Release has unmanaged files; move them out before updating")
    # Validate every old path before deleting stale manifest-owned files.
    stale = [safe_path(RELEASE, name) for name in set(old) - set(entries)]
    # Fail before changing any payload if Windows has a running EXE/DLL locked.
    # Opening for write checks access; no bytes are written by this preflight.
    for name, entry in entries.items():
        target = safe_path(RELEASE, name)
        if target.exists() and digest(target) != entry["sha256"]:
            try:
                with target.open("r+b"):
                    pass
            except PermissionError:
                raise ValueError("Закройте программы из Release перед обновлением: " + name) from None
    RELEASE.mkdir(exist_ok=True)
    for name, source in sources.items():
        target = safe_path(RELEASE, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or digest(target) != entries[name]["sha256"]:
            shutil.copyfile(source, target)
    for path in stale:
        path.unlink(missing_ok=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    version = json.loads((ROOT / "amazing_overlay/version.json").read_text(encoding="utf-8"))["version"]
    manifest = {"format": 1, "version": version, "source_revision": revision, "files": entries}
    (RELEASE / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verify()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / ".cache/dist" / "portable-build" / "AmazingLawOverlay")
    parser.add_argument("--check", action="store_true", help="Only verify the tracked release")
    args = parser.parse_args()
    if args.check:
        verify()
    else:
        publish(args.bundle)
