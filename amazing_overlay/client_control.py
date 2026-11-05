"""Cooperative close endpoint for same-user external clients; never force-kills."""
import json
import time

from PySide6.QtCore import QObject, QTimer, QProcess
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from shiboken6 import isValid

from .settings import PIPE_NAME


def request_close(mode):
    socket = QLocalSocket()
    socket.connectToServer(PIPE_NAME + "-control-" + mode)
    if not socket.waitForConnected(1000):
        return
    socket.write(b'{"action":"prepare_update"}\n')
    socket.waitForBytesWritten(1000)
    data = bytearray()
    deadline = time.monotonic() + 45
    try:
        while b"\n" not in data:
            left = int((deadline - time.monotonic()) * 1000)
            if left <= 0 or (not socket.bytesAvailable() and not socket.waitForReadyRead(left)):
                raise RuntimeError("Клиент не завершился. Закройте его вручную перед обновлением.")
            data.extend(bytes(socket.readAll()))
            if len(data) > 1024:
                raise ValueError("Некорректный ответ клиента")
        result = json.loads(data.split(b"\n", 1)[0])
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Клиент занят"))
    finally:
        socket.abort()


class ClientControl(QObject):
    def __init__(self, window, mode):
        super().__init__(window)
        self.window, self.mode = window, mode
        self.preparing = False
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self.accept)
        if not self.server.listen(PIPE_NAME + "-control-" + mode):
            raise RuntimeError("Не удалось открыть канал завершения клиента")

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            data = bytearray()
            handled = [False]
            def read(socket=socket, data=data, handled=handled):
                if handled[0]:
                    return
                data.extend(bytes(socket.readAll()))
                if len(data) > 1024:
                    socket.abort()
                    return
                if b"\n" not in data:
                    return
                handled[0] = True
                try:
                    if json.loads(data.split(b"\n", 1)[0]) != {"action": "prepare_update"}:
                        raise ValueError("Unknown action")
                    self.prepare(socket)
                except (ValueError, TypeError):
                    socket.disconnectFromServer()
            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
            read()

    def prepare(self, socket):
        window = self.window
        if self.preparing:
            socket.write(b'{"ok":false,"error":"Client is already preparing"}\n')
            socket.disconnectFromServer()
            return
        if self.mode == "install-browser" and window.process.state() != QProcess.NotRunning:
            socket.write(b'{"ok":false,"error":"Chromium installation is running"}\n')
            socket.disconnectFromServer()
            return
        self.preparing = True
        window.setEnabled(False)
        paused = []
        for name in ("timer", "state_timer"):
            timer = getattr(window, name, None)
            if isinstance(timer, QTimer) and timer.isActive():
                paused.append(timer)
                timer.stop()
        if hasattr(window, "cancel_jobs"):
            window.cancel_jobs()
        if hasattr(window, "save_state"):
            window.save_state()
        notebook = getattr(window, "notebook", None)
        started = time.monotonic()
        if notebook:
            notebook.save()
            notebook.persist_state()
        def fail():
            self.preparing = False
            window.setEnabled(True)
            for timer in paused:
                timer.start()
            if not isinstance(socket, QObject) or isValid(socket):
                socket.write(b'{"ok":false,"error":"Save notes and finish operations before updating"}\n')
                socket.disconnectFromServer()
        def finish():
            if isinstance(socket, QObject) and not isValid(socket):
                fail()
                return
            if (notebook and not notebook.saving and notebook.dirty) or time.monotonic() - started > 40:
                fail()
                return
            from .transport import _active_work
            if (notebook and notebook.saving) or _active_work:
                QTimer.singleShot(100, finish)
                return
            socket.write(b'{"ok":true}\n')
            socket.flush()
            socket.disconnectFromServer()
            if self.mode == "overlay":
                QTimer.singleShot(100, window.quit)
            else:
                QTimer.singleShot(100, window.close)
        finish()
