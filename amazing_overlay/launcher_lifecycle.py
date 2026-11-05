"""Local, data-free diagnostics for unexpected desktop launcher termination."""
import atexit
import faulthandler
import logging
import os
from logging.handlers import RotatingFileHandler

from .settings import DATA
from . import __version__

log = logging.getLogger("amazing.launcher.lifecycle")
log.addHandler(logging.NullHandler())
log.propagate = False


def start(app):
    handler = RotatingFileHandler(DATA / "launcher-lifecycle.log", maxBytes=262144,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s pid=%(process)d %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    # Keep the file alive for the whole Qt event loop. No local variables or
    # application data are captured; faulthandler records Python thread stacks.
    app.launcher_fault_file = (DATA / "launcher-fault.log").open("ab")
    faulthandler.enable(file=app.launcher_fault_file)
    log.info("started version=%s", __version__)
    app.aboutToQuit.connect(lambda: log.info("Qt aboutToQuit"))
    app.applicationStateChanged.connect(lambda state: log.info("application state=%s", state.name))
    atexit.register(lambda: log.info("Python normal exit"))


def native_message(message):
    if os.name == "nt":
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        names = {0x0010: "WM_CLOSE", 0x0011: "WM_QUERYENDSESSION",
                 0x0016: "WM_ENDSESSION", 0x007E: "WM_DISPLAYCHANGE"}
        if msg.message in names:
            log.info("window message=%s", names[msg.message])
