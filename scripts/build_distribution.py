"""One staged build feeds portable, tracked Release and the all-user installer."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import pefile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from amazing_overlay import __version__
from publish_release import digest, files_in, REQUIRED, publish


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--skip-freeze", action="store_true")
    args = parser.parse_args()
    native = args.native_dir.resolve()
    native_image = pefile.PE(str(native / "AmazingLawClient.dll"))
    exports = getattr(native_image, "DIRECTORY_ENTRY_EXPORT", None)
    if exports and any(symbol.name == b"AmazingOverlayTlsFixtureTag" for symbol in exports.symbols):
        raise ValueError("TLS self-test fixture cannot be shipped as the production DLL")
    tls = getattr(native_image, "DIRECTORY_ENTRY_TLS", None)
    if tls and tls.struct.EndAddressOfRawData - tls.struct.StartAddressOfRawData > 8:
        raise ValueError("Production DLL exceeds the established 8-byte static TLS baseline; test TLS belongs in tls-fixture")
    native_image.close()
    probe = subprocess.run([str(native / "AmazingHelperInjector.exe"), "--version"], capture_output=True, check=True)
    if json.loads(probe.stdout)["version"] != __version__:
        raise ValueError("Native version does not match the Python version")
    output = ROOT / ".cache/dist"
    bundle = output / "portable-build/AmazingHelper"
    if not args.skip_freeze:
        run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", "AmazingHelper", "--specpath", ".cache/build/spec", "--distpath", ".cache/dist/portable-build",
            "--workpath", ".cache/build/portable-build", "--collect-all", "playwright", "--collect-all", "bs4",
            "--collect-all", "openai", "--collect-all", "dotenv", "--exclude-module", "PySide6.QtWebEngineCore",
            "--add-data", str(ROOT / "amazing_overlay/assets") + ";amazing_overlay/assets",
            "--add-data", str(ROOT / "amazing_overlay/version.json") + ";amazing_overlay",
            "--add-data", str(ROOT / "amazing_overlay/update_public_key.txt") + ";amazing_overlay", "scripts/portable_entry.py")
    run("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/package_portable.ps1",
        "-BundlePath", str(bundle.relative_to(ROOT)), "-NativeDirectory", str(native))
    run(sys.executable, "scripts/collect_licenses.py", "--bundle-path", bundle, "--native-dir", native)
    docs = bundle / "docs"
    docs.mkdir(exist_ok=True)
    for name in ("V2.md", "PROTOCOL.md", "GAME_ACCEPTANCE.md", "NOTEBOOK.md", "DISTRIBUTION.md"):
        shutil.copyfile(ROOT / "docs" / name, docs / name)
    entries = {name: {"bytes": path.stat().st_size, "sha256": digest(path)} for name, path in files_in(bundle).items() if name != "manifest.json"}
    if not set(REQUIRED) <= entries.keys():
        raise ValueError("Incomplete runtime")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (bundle / "manifest.json").write_text(json.dumps({"format": 1, "version": __version__, "source_revision": revision, "files": entries}, indent=2) + "\n", encoding="utf-8")
    run(sys.executable, "scripts/check_portable.py", bundle / "AmazingHelper.exe", "--settings-check")
    archive = output / ("AmazingHelper-" + __version__ + "-portable-x64")
    shutil.make_archive(str(archive), "zip", bundle)
    if args.iscc:
        run(args.iscc.resolve(), "/DAppVersion=" + __version__, "/DBundleDir=" + str(bundle),
            "/DOutputDir=" + str(output), ROOT / "installer/AmazingHelper.iss")
    if args.publish:
        publish(bundle)
    print("Distribution ready:", str(archive) + ".zip")


if __name__ == "__main__":
    main()
