from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtCore import QCoreApplication, QLockFile

from .settings import DATA
from .browsers import configure_browser_environment


def main():
    configure_browser_environment()
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["backend", "manager", "overlay", "launcher", "install-browser"], nargs="?", default="launcher")
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
        backend = Backend()
        server = RpcServer(backend)
        if not server.listen():
            backend.close()
            return 1
        app.aboutToQuit.connect(backend.close)
    else:
        from PySide6.QtWidgets import QApplication
        app = QApplication(sys.argv)
        lock = QLockFile(str(DATA / (args.mode + ".lock")))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            return 0
        app.setApplicationName("Amazing Law Overlay 2")
        from .ui import ManagerWindow, OverlayWindow, BrowserInstaller, STYLE
        app.setStyleSheet(STYLE)
        if args.mode == "launcher":
            from .launcher import LauncherWindow
            window = LauncherWindow()
        else:
            window = BrowserInstaller() if args.mode == "install-browser" else ManagerWindow() if args.mode == "manager" else OverlayWindow()
        if args.mode != "overlay":
            window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logging.exception("Application startup failed")
        raise
