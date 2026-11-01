"""JSON-lines RPC over QLocalSocket (Windows named pipe), protocol v1."""
from __future__ import annotations

import json
import sys
import time
import uuid

from PySide6.QtCore import QCoreApplication, QObject, QProcess, QRunnable, QThreadPool, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .settings import PIPE_NAME, ROOT

MAX_FRAME = 16 * 1024 * 1024


def rpc(method, params=None, timeout=15000):
    socket = QLocalSocket()
    socket.connectToServer(PIPE_NAME)
    if not socket.waitForConnected(1500):
        raise ConnectionError("Бэкенд не запущен. Запустите scripts/run/run_backend.bat или окно управления.")
    ident = uuid.uuid4().hex
    payload = json.dumps({"v": 1, "id": ident, "method": method, "params": params or {}}, ensure_ascii=False).encode() + b"\n"
    if len(payload) > MAX_FRAME:
        raise ValueError("Запрос слишком большой")
    socket.write(payload)
    socket.waitForBytesWritten(2000)
    data = bytearray()
    deadline = time.monotonic() + timeout / 1000
    try:
        while b"\n" not in data:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0 or (not socket.bytesAvailable() and not socket.waitForReadyRead(remaining)):
                raise TimeoutError("Бэкенд не ответил вовремя")
            data.extend(bytes(socket.readAll()))
            if len(data) > MAX_FRAME:
                raise ValueError("Ответ слишком большой")
        response = json.loads(data.split(b"\n", 1)[0])
        if response.get("v") != 1 or response.get("id") != ident:
            raise ValueError("Несовместимый ответ бэкенда")
        if "error" in response:
            raise RuntimeError(response["error"]["message"])
        return response.get("result")
    finally:
        socket.abort()


def ensure_backend():
    try:
        value = rpc("health")
        if value.get("stopping"):
            raise RuntimeError("Бэкенд ещё останавливается. Дождитесь завершения.")
        return value
    except ConnectionError:
        if getattr(sys, "frozen", False):
            command = ["backend"]
        else:
            command = ["-m", "amazing_overlay", "backend"]
        QProcess.startDetached(sys.executable, command, str(ROOT))
        for _ in range(60):
            time.sleep(0.15)
            try:
                return rpc("health")
            except ConnectionError:
                continue
        raise ConnectionError("Не удалось запустить бэкенд; проверьте зависимости и diagnostics.log")


def stop_backend(timeout=45):
    """Graceful, same-user RPC stop. Never terminate arbitrary Python processes."""
    health = rpc("health", timeout=2500)
    if not health.get("shutdown"):
        raise RuntimeError("Обновите бэкенд: эта версия не поддерживает остановку из лаунчера.")
    rpc("shutdown")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            rpc("health", timeout=2500)
        except ConnectionError:
            return False
        except TimeoutError:
            # A health socket may be accepted just as Qt exits. Confirm that the
            # next connection is gone instead of treating that race as a failure.
            pass
        time.sleep(.15)
    raise TimeoutError("Бэкенд ещё завершает задачи. Он будет остановлен после их завершения; принудительное закрытие не выполнялось.")


class WorkSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Work(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = WorkSignals()

    def run(self):
        try:
            self.signals.done.emit(self.function())
        except Exception as exc:
            self.signals.failed.emit(str(exc))


_active_work = set()


def background(function, done, failed):
    work = Work(function)
    # A QRunnable is auto-deleted by Qt. Keep its Python signal object alive until
    # the queued GUI callback has run, including callers without their own work set.
    _active_work.add(work)
    def finish(callback, value):
        try:
            callback(value)
        finally:
            _active_work.discard(work)
    work.signals.done.connect(lambda value: finish(done, value))
    work.signals.failed.connect(lambda value: finish(failed, value))
    QThreadPool.globalInstance().start(work)
    return work


class RpcServer(QObject):
    completed = Signal(object, bytes, bool)
    stopped = Signal()

    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self.accept)
        self.completed.connect(self.respond)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(8)
        self.buffers = {}
        self.stopping = False
        self.stopped.connect(QCoreApplication.instance().quit)

    def listen(self):
        return self.server.listen(PIPE_NAME)

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self.read(s))
            socket.disconnected.connect(lambda s=socket: self.drop(s))

    def drop(self, socket):
        self.buffers.pop(socket, None)
        socket.deleteLater()

    def read(self, socket):
        data = self.buffers.get(socket)
        if data is None:
            return
        data.extend(bytes(socket.readAll()))
        if len(data) > MAX_FRAME:
            socket.abort()
            return
        if b"\n" not in data:
            return
        raw = bytes(data).split(b"\n", 1)[0]
        # One request per connection; no pipeline or unbounded queued frames.
        socket.readyRead.disconnect()

        def execute():
            ident = None
            stop = False
            try:
                request = json.loads(raw)
                ident = request.get("id")
                if request.get("v") != 1 or not isinstance(ident, str) or not isinstance(request.get("params", {}), dict):
                    raise ValueError("Неверная версия или формат протокола")
                result = self.backend.dispatch(request["method"], request.get("params", {}))
                stop = request["method"] == "shutdown"
                response = {"v": 1, "id": ident, "result": result}
            except Exception as exc:
                response = {"v": 1, "id": ident, "error": {"code": "request_failed", "message": str(exc)[:1000]}}
            encoded = json.dumps(response, ensure_ascii=False).encode() + b"\n"
            if len(encoded) > MAX_FRAME:
                encoded = json.dumps({"v": 1, "id": ident, "error": {"code": "too_large", "message": "Ответ превышает 16 МБ"}}).encode() + b"\n"
            self.completed.emit(socket, encoded, stop)
        self.pool.start(Work(execute))

    def respond(self, socket, data, stop=False):
        if socket in self.buffers:
            socket.write(data)
            socket.disconnectFromServer()
        if stop and not self.stopping:
            self.stopping = True
            # Wait for already accepted requests and cancelled parser/API jobs away
            # from Qt's event loop; keep health reachable until shutdown completes.
            def finish():
                self.pool.waitForDone()
                self.backend.close()
                self.stopped.emit()
            QThreadPool.globalInstance().start(Work(finish))
