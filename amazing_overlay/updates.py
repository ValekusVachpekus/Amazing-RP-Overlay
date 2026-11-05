"""Signed GitHub release metadata and bounded, verified installer downloads."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__

REPOSITORY = "ValekusVachpekus/Amazing-RP-Overlay"
RELEASES = "https://github.com/" + REPOSITORY + "/releases"
API = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
MAX_INSTALLER = 2 * 1024**3


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value):
        raise ValueError("Некорректная стабильная версия")
    return tuple(map(int, value.split(".")))


def asset_url(value):
    url = urllib.parse.urlsplit(value)
    prefix = "/" + REPOSITORY + "/releases/download/"
    if url.scheme != "https" or url.netloc != "github.com" or not url.path.startswith(prefix) or url.query or url.fragment:
        raise ValueError("Недопустимый адрес файла обновления")
    return value


def open_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AmazingHelper/" + __version__, "Accept": "application/vnd.github+json" if url == API else "application/octet-stream"})
    response = urllib.request.urlopen(request, timeout=25)
    final = urllib.parse.urlsplit(response.geturl())
    if final.scheme != "https" or final.hostname not in {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
        response.close()
        raise ValueError("Недопустимое перенаправление обновления")
    return response


def fetch(url, limit):
    with open_url(url) as response:
        value = response.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Файл обновления превышает допустимый размер")
    return value


def verify_manifest(payload, signature, public_key):
    Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
    manifest = json.loads(payload)
    if manifest.get("format") != 1 or manifest.get("repository") != REPOSITORY:
        raise ValueError("Несовместимый манифест обновления")
    version(manifest["version"])
    packages = manifest.get("packages")
    if not isinstance(packages, list) or len(packages) != 2:
        raise ValueError("Неполный комплект обновления")
    expected = {"installer": "AmazingHelperSetup.exe", "portable": "AmazingHelper-" + manifest["version"] + "-portable-x64.zip"}
    if {p.get("type") for p in packages} != set(expected):
        raise ValueError("Некорректные типы пакетов")
    for package in packages:
        if package.get("name") != expected[package["type"]] or package.get("arch") != "x64":
            raise ValueError("Некорректный пакет обновления")
        if type(package.get("bytes")) is not int or not 0 < package["bytes"] <= MAX_INSTALLER:
            raise ValueError("Некорректный размер обновления")
        if not isinstance(package.get("sha256"), str) or not re.fullmatch("[0-9a-f]{64}", package["sha256"]):
            raise ValueError("Некорректная контрольная сумма")
    return manifest


def public_key():
    return base64.b64decode(Path(__file__).with_name("update_public_key.txt").read_text(encoding="ascii").strip(), validate=True)


def check_update():
    release = json.loads(fetch(API, 2 * 1024**2))
    if release.get("draft") or release.get("prerelease"):
        return None
    tag = release.get("tag_name", "")
    remote = tag.removeprefix("v")
    if version(remote) <= version(__version__):
        return None
    assets = {asset["name"]: asset_url(asset["browser_download_url"]) for asset in release.get("assets", [])}
    manifest = verify_manifest(fetch(assets["updates.json"], 65536), fetch(assets["updates.sig"], 64), public_key())
    if manifest["version"] != remote:
        raise ValueError("Версия релиза не совпадает с подписанным манифестом")
    for package in manifest["packages"]:
        package["url"] = assets[package["name"]]
    return {"version": remote, "notes": str(release.get("body") or "")[:12000], "packages": manifest["packages"], "page": RELEASES + "/tag/" + tag}


def download_installer(update, directory, cancel=None, progress=None):
    package = next(p for p in update["packages"] if p["type"] == "installer")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(directory).free < package["bytes"] + 128 * 1024**2:
        raise OSError("Недостаточно места для скачивания обновления")
    target = directory / ("AmazingHelperSetup-" + update["version"] + ".exe")
    fd, temporary = tempfile.mkstemp(prefix="download-", suffix=".part", dir=directory)
    digest, count = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "wb") as output, open_url(asset_url(package["url"])) as response:
            while True:
                if cancel and cancel.is_set():
                    raise InterruptedError("Скачивание отменено")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > package["bytes"]:
                    raise ValueError("Размер установщика превышает подписанный размер")
                digest.update(chunk)
                output.write(chunk)
                if progress:
                    progress(count, package["bytes"])
            output.flush()
            os.fsync(output.fileno())
        if count != package["bytes"] or digest.hexdigest() != package["sha256"]:
            raise ValueError("Установщик повреждён: размер или SHA-256 не совпадает")
        os.replace(temporary, target)
        return target
    finally:
        Path(temporary).unlink(missing_ok=True)


def start_installer(path, root):
    """runas reports cancellation before the caller closes its launcher."""
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    shell.ShellExecuteW.restype = ctypes.c_void_p
    arguments = subprocess.list2cmdline(["/DIR=" + str(root), "/UPDATE=1"])
    result = shell.ShellExecuteW(None, "runas", str(path), arguments, str(path.parent), 1)
    if not result or result <= 32:
        raise OSError("Установка не запущена: UAC отменён или Windows отклонила запуск.")
