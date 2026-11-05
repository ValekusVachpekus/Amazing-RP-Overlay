"""Small, expiring native status registry. No process memory operations."""
import time
import threading


class NativeRegistry:
    def __init__(self):
        self.entries = {}
        self.lock = threading.Lock()

    def register(self, value):
        pid = value.get("pid")
        created = value.get("created")
        stage = value.get("stage")
        version = value.get("version")
        if type(pid) is not int or not 0 < pid <= 0xffffffff or not isinstance(created, str) or not created.isdecimal() or len(created) > 20:
            raise ValueError("Некорректная идентичность нативного клиента")
        if stage not in {"initializing", "ready", "error"} or not isinstance(version, str) or len(version) > 32:
            raise ValueError("Некорректное состояние нативного клиента")
        error = value.get("error", "")
        if not isinstance(error, str) or len(error) > 1000:
            raise ValueError("Некорректная ошибка нативного клиента")
        with self.lock:
            now = time.monotonic()
            self.entries = {key: entry for key, entry in self.entries.items() if now - entry[0] < 15}
            if len(self.entries) >= 128 and pid not in self.entries:
                raise ValueError("Слишком много нативных клиентов")
            self.entries[pid] = (now, {"pid": pid, "created": created, "version": version, "stage": stage, "error": error,
                                       "runtime_check": value.get("runtime_check") is True})
        return True

    def status(self, pid, created):
        with self.lock:
            entry = self.entries.get(pid)
            if entry and entry[1]["created"] == created and time.monotonic() - entry[0] < 15:
                return dict(entry[1])
        return None
