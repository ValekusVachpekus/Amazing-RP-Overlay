from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtCore import QCoreApplication, QLockFile

from .settings import DATA
from .browsers import configure_browser_environment


def main():
    from .product import runtime_guard
    guard = runtime_guard()
    configure_browser_environment()
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-legacy-import", action="store_true", help="Не импортировать прежние локальные законы/ссылки (изолированные проверки)")
    parser.add_argument("mode", choices=["backend", "manager", "overlay", "launcher", "install-browser"], nargs="?", default="launcher")
    parser.add_argument("--page", choices=["sources", "update", "ai", "appearance", "diagnostics"], default="sources", help="Начальный раздел менеджера")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA / "diagnostics.log", level=logging.INFO, encoding="utf-8")
    app = None
    if args.mode == "backend":
        app = QCoreApplication(sys.argv)
        lock = QLockFile(str(DATA / "backend.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            return 0
        from .service import Backend
        from .transport import RpcServer
        backend = Backend(import_legacy=not args.no_legacy_import)
        server = RpcServer(backend)
        if not server.listen():
            backend.close()
            return 1
        # Complete in-flight RPC work before destroying its Qt signals or store.
        app.aboutToQuit.connect(lambda: server.pool.waitForDone())
        app.aboutToQuit.connect(backend.close)
    else:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QIcon
        from pathlib import Path
        app = QApplication(sys.argv)
        app.setWindowIcon(QIcon(str(Path(__file__).with_name("assets") / "amazing-helper.ico")))
        desktop = args.mode in ("launcher", "manager")
        lock = QLockFile(str(DATA / (("launcher" if desktop else args.mode) + ".lock")))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            if desktop:
                from .manager_navigation import send
                return 0 if send(args.page if args.mode == "manager" else "overview") else 1
            return 0
        app.setApplicationName("Amazing Law Overlay 2")
        from .ui import OverlayWindow, BrowserInstaller, STYLE
        app.setStyleSheet(STYLE)
        if desktop:
            from .launcher_lifecycle import start
            start(app)
            from .launcher import LauncherWindow
            from .manager_navigation import ManagerNavigation
            window = LauncherWindow()
            if args.mode == "manager":
                window.open_manager_page(args.page)
            window.navigation_server = ManagerNavigation(window)
        else:
            window = BrowserInstaller() if args.mode == "install-browser" else OverlayWindow()
            from .client_control import ClientControl
            window.client_control = ClientControl(window, args.mode)
        if args.mode != "overlay":
            window.show()
    result = app.exec()
    if args.mode in ("launcher", "manager"):
        from .launcher_lifecycle import log
        log.info("Qt event loop returned code=%s", result)
    return result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logging.exception("Application startup failed")
        raise
