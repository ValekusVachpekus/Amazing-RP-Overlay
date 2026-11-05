"""User-owned plain text files. No note contents enter the laws index or logs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import uuid

EDIT_LIMIT = 1024 * 1024
VIEW_LIMIT = 4 * EDIT_LIMIT
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def workspace(root: Path) -> Path:
    # A frozen Release inside a source checkout shares the checkout's Notes.
    parent = root.parent
    return parent if root.name == "Release" and (parent / "amazing_overlay").is_dir() and (parent / "start.bat").is_file() else root


def no_links(path: Path):
    for part in [path, *path.parents]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Ссылки и junction в папке заметок не поддерживаются")


def atomic_bytes(path: Path, content: bytes):
    descriptor, temporary = tempfile.mkstemp(prefix=".note-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Notes:
    def __init__(self, root: Path, data: Path, settings):
        self.app_root, self.data, self.settings = root, data, settings
        self.lock = threading.RLock()
        self._active_root = None

    @property
    def root(self):
        if self._active_root is not None:
            return self._active_root
        configured = self.settings().get("notes_dir", "")
        from .product import default_notes
        value = Path(configured).expanduser() if configured else default_notes(workspace(self.app_root))
        if not value.is_absolute():
            value = workspace(self.app_root) / value
        value = Path(os.path.abspath(value))
        no_links(value)
        value.mkdir(parents=True, exist_ok=True)
        return value

    def path(self, name, *, internal=False):
        if not isinstance(name, str) or "\\" in name or ":" in name:
            raise ValueError("Некорректный путь заметки")
        parts = name.split("/")
        if not name or any(not p or p in {".", ".."} or p.endswith((" ", ".")) or
                           re.search(r'[<>:"|?*\x00-\x1f]', p) or RESERVED.match(p) or
                           (p.startswith(".") and not internal) for p in parts):
            raise ValueError("Недопустимое имя файла или папки")
        target = self.root.joinpath(*parts)
        no_links(target)
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Путь вне папки заметок")
        return target

    def read(self, name):
        target = self.path(name)
        if target.suffix.lower() != ".txt" or not target.is_file():
            raise ValueError("Выберите существующий TXT-файл")
        if target.stat().st_size > VIEW_LIMIT:
            raise ValueError("Файл больше 4 МиБ. Откройте его внешним редактором; содержимое не изменено")
        raw = target.read_bytes()
        if b"\x00" in raw:
            raise ValueError("Файл содержит двоичные данные")
        try:
            encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            encoding = "cp1251"
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                raise ValueError("Не удалось прочитать кодировку TXT-файла") from None
        newline = "\r\n" if b"\r\n" in raw else "\n"
        return {"path": name, "text": text.replace("\r\n", "\n"), "revision": hashlib.sha256(raw).hexdigest(),
                "encoding": encoding, "newline": newline, "readonly": len(raw) > EDIT_LIMIT,
                "bytes": len(raw)}

    def listing(self, query=""):
        base = self.root
        result = []
        def walk(directory):
            for entry in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
                if entry.name.startswith("."):
                    continue
                try:
                    no_links(entry)
                    name = entry.relative_to(base).as_posix()
                    self.path(name)
                    if entry.is_dir():
                        if not query:
                            result.append({"path": name, "folder": True})
                        walk(entry)
                    elif entry.suffix.lower() == ".txt":
                        matched = not query or query.casefold() in name.casefold()
                        if query and not matched and entry.stat().st_size <= EDIT_LIMIT:
                            matched = query.casefold() in self.read(name)["text"].casefold()
                        if matched:
                            result.append({"path": name, "folder": False, "bytes": entry.stat().st_size})
                except (ValueError, OSError):
                    continue
        walk(base)
        return {"root": str(base), "entries": result}

    def create(self, name, folder=False, text=""):
        target = self.path(name)
        if not target.parent.is_dir():
            raise ValueError("Родительская папка не существует")
        if folder:
            target.mkdir()  # Exclusive; never replace an existing object.
            return {"path": name, "folder": True}
        if target.suffix.lower() != ".txt":
            raise ValueError("Заметки должны иметь расширение .txt")
        content = str(text).encode("utf-8")
        if len(content) > EDIT_LIMIT:
            raise ValueError("Предел редактирования — 1 МиБ")
        with target.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return self.read(name)

    def save(self, name, text, revision):
        current = self.read(name)
        if current["revision"] != revision:
            return {"conflict": True, "current": current}
        if current["readonly"]:
            raise ValueError("Файл больше 1 МиБ доступен только для чтения")
        target = self.path(name)
        try:
            raw = str(text).replace("\r\n", "\n").replace("\n", current["newline"]).encode(current["encoding"])
        except UnicodeEncodeError:
            raise ValueError("Символы не помещаются в CP1251. Сохраните отдельную UTF-8 копию") from None
        if len(raw) > EDIT_LIMIT:
            raise ValueError("Предел редактирования — 1 МиБ")
        atomic_bytes(target, raw)
        return {"conflict": False, **self.read(name)}

    def move(self, name, destination):
        source, target = self.path(name), self.path(destination)
        if target.exists() or not target.parent.is_dir() or target.is_relative_to(source):
            raise ValueError("Место занято или перемещение недопустимо")
        if source.is_file() and target.suffix.lower() != ".txt":
            raise ValueError("Сохраните расширение .txt")
        source.rename(target)
        return {"path": destination, "previous": name}

    def delete(self, name):
        source = self.path(name)
        if not source.exists():
            raise ValueError("Файл уже отсутствует")
        ident = uuid.uuid4().hex
        trash = self.root / ".trash"
        no_links(trash)
        trash.mkdir(exist_ok=True)
        metadata = trash / (ident + ".json")
        atomic_bytes(metadata, json.dumps({"path": name}, ensure_ascii=False).encode())
        source.rename(trash / ident)
        return {"trash_id": ident}

    def trash(self):
        trash = self.root / ".trash"
        no_links(trash)
        result = []
        if trash.exists():
            for entry in trash.glob("*.json"):
                try:
                    no_links(entry)
                    value = json.loads(entry.read_text(encoding="utf-8"))
                    self.path(value["path"])
                    result.append({"id": entry.stem, "path": value["path"]})
                except (ValueError, OSError, KeyError):
                    pass
        return result

    def restore(self, ident, destination=None):
        if not re.fullmatch("[a-f0-9]{32}", ident):
            raise ValueError("Некорректный идентификатор корзины")
        trash = self.root / ".trash"
        no_links(trash)
        meta, source = trash / (ident + ".json"), trash / ident
        no_links(meta)
        no_links(source)
        original = json.loads(meta.read_text(encoding="utf-8"))["path"]
        name = destination or original
        target = self.path(name)
        if target.exists() or not target.parent.is_dir():
            raise ValueError("Место занято или родительская папка отсутствует; выберите другой путь")
        source.rename(target)
        meta.unlink()
        return {"path": name}

    def dispatch(self, method, params):
        with self.lock:
            base = self.root
            if params.get("root") and params["root"] != str(base):
                raise ValueError("Папка заметок изменилась. Обновите список; ваш текст сохранён в черновике")
            self._active_root = base
            try:
                return self._dispatch(method, params)
            finally:
                self._active_root = None

    def _dispatch(self, method, params):
        if method == "notes_info":
            return {"root": str(self.root), "edit_limit": EDIT_LIMIT}
        if method in {"notes_list", "notes_search"}:
            return self.listing(params.get("query", ""))
        if method == "notes_read":
            return self.read(params["path"])
        if method == "notes_create":
            return self.create(params["path"], params.get("folder", False), params.get("text", ""))
        if method == "notes_save":
            return self.save(params["path"], params["text"], params["revision"])
        if method == "notes_move":
            return self.move(params["path"], params["destination"])
        if method == "notes_delete":
            return self.delete(params["path"])
        if method == "notes_trash":
            return self.trash()
        if method == "notes_restore":
            return self.restore(params["id"], params.get("destination"))
        raise ValueError("Неизвестная операция блокнота")
