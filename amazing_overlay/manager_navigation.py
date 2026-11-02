"""Same-user navigation to the launcher and its embedded management pages."""
import json
import time
from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from .settings import PIPE_NAME

PAGES = ("sources", "update", "ai", "appearance", "diagnostics")
DESTINATIONS = (*PAGES, "overview")
SERVER_NAME = PIPE_NAME + "-manager-navigation"


def send(page="sources"):
    if page not in DESTINATIONS:
        raise ValueError("Неизвестный раздел управления")
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(1500):
        return False
    socket.write((json.dumps({"page": page}) + "\n").encode())
    # On Windows a successful write can stay queued until a read wait pumps the
    # local socket. Keep the connection alive until the manager acknowledges it.
    socket.waitForBytesWritten(200)
    try:
        data = bytearray()
        deadline = time.monotonic() + 2
        while b"\n" not in data:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0 or (not socket.bytesAvailable() and not socket.waitForReadyRead(remaining)):
                return False
            data.extend(bytes(socket.readAll()))
            if len(data) > 16:
                return False
        return data.strip() == b"OK"
    finally:
        socket.abort()


class ManagerNavigation(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self.accept)
        self.listening = self.server.listen(SERVER_NAME)

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            data = bytearray()
            finished = [False]

            def read(socket=socket, data=data, finished=finished):
                if finished[0]:
                    return
                data.extend(bytes(socket.readAll()))
                if len(data) > 1024:
                    finished[0] = True
                    socket.abort()
                    return
                if b"\n" not in data:
                    return
                finished[0] = True
                try:
                    request = json.loads(data.split(b"\n", 1)[0])
                    page = request["page"]
                    if page not in DESTINATIONS:
                        raise ValueError("Unknown page")
                    if page == "overview":
                        self.window.section("overview")
                    elif hasattr(self.window, "open_manager_page"):
                        self.window.open_manager_page(page)
                    else:
                        self.window.tabs.setCurrentIndex(PAGES.index(page))
                    if self.window.isMinimized():
                        self.window.showNormal()
                    else:
                        self.window.show()
                    self.window.raise_()
                    self.window.activateWindow()
                    socket.write(b"OK\n")
                except (KeyError, TypeError, ValueError):
                    pass
                socket.disconnectFromServer()

            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
            read()
