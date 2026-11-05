"""Explicit native launch orchestration, called only by the launch button."""
import json
import os
import subprocess
import time

from . import __version__
from .game_process import GAME_NAMES, process_info
from .product import verify_bundle

_uncertain = set()


def launch_native(game, root, ensure_backend, rpc):
    if game.error:
        raise RuntimeError("Нет доступа к игре: " + game.error)
    current = process_info(game.pid)
    if current != game or os.path.basename(current.path).casefold() not in GAME_NAMES:
        raise RuntimeError("Игровой процесс изменился. Повторите выбор.")
    if not game.x86:
        raise RuntimeError("Поддерживается только игровой процесс x86.")
    health = ensure_backend()
    if health.get("app_version") != __version__ or not health.get("native_sessions"):
        raise RuntimeError("Остановите старый бэкенд и запустите версию " + __version__)
    identity = {"pid": game.pid, "created": game.created}
    status = rpc("native_status", identity)
    if status:
        if status["version"] != __version__:
            raise RuntimeError("Загружена другая версия DLL. Полностью перезапустите игру.")
        if status["stage"] == "ready":
            return "Внутриигровой оверлей уже запущен. Ctrl+L — открыть."
        raise RuntimeError("DLL уже загружена: " + (status.get("error") or status["stage"]) + ". При необходимости перезапустите игру.")
    key = (game.pid, game.created)
    if key in _uncertain:
        raise RuntimeError("Предыдущая загрузка не подтверждена. Полностью перезапустите игру.")
    verify_bundle(root, ["AmazingLawClient.dll", "AmazingHelperInjector.exe"])
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    digest = manifest["files"]["AmazingLawClient.dll"]["sha256"]
    command = [str(root / "AmazingHelperInjector.exe"), "--pid", str(game.pid), "--created", game.created,
               "--image", game.path, "--sha256", digest]
    _uncertain.add(key)  # Remains set after any ambiguous failure; never remap blindly.
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, timeout=60,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError("Загрузчик не ответил. Перезапустите игру перед новой попыткой.") from None
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise RuntimeError("Некорректный ответ загрузчика. Перезапустите игру перед новой попыткой.") from None
    if result.returncode or not payload.get("ok"):
        if payload.get("stage") == "preflight":
            _uncertain.discard(key)
        raise RuntimeError(payload.get("error", "Не удалось загрузить DLL"))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = process_info(game.pid)
        if current.created != game.created:
            raise RuntimeError("Игра завершилась во время запуска.")
        status = rpc("native_status", identity)
        if status:
            if status["version"] != __version__:
                raise RuntimeError("Версия загруженной DLL отличается. Перезапустите игру.")
            if status["stage"] == "error":
                raise RuntimeError("Ошибка DLL: " + status["error"])
            if status["stage"] == "ready":
                return "Внутриигровой оверлей запущен. Ctrl+L — открыть."
        time.sleep(.3)
    raise RuntimeError("DLL загружена, но готовность рендера не подтверждена. Перезапустите игру перед новой попыткой.")
