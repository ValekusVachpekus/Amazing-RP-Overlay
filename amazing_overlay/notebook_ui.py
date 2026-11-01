"""Plain text notebook client. All disk and RPC work stays off the Qt thread."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
                              QLineEdit, QPlainTextEdit, QPushButton, QLabel, QInputDialog, QMessageBox,
                              QMenu, QTextEdit)

from .settings import DATA, atomic_json
from .transport import rpc, background


def recovery_path(root, path):
    ident = hashlib.sha256((root + "\0" + path).encode()).hexdigest()
    return DATA / "notes-recovery" / ("qt-" + ident + ".json")


class NotebookWidget(QWidget):
    attach = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_path = ""
        self.current = None
        self.dirty = self.loading = self.saving = False
        self.generation = 0
        self.list_generation = 0
        self.after_save = None
        self.matches = []
        self.match_index = -1
        self.pending_state = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        for label, action in (("Файлы", self.file_menu), ("Сохранить", self.save), ("В чат", self.send_to_chat),
                              ("Черновики", self.recover)):
            button = QPushButton(label)
            button.clicked.connect(action)
            top.addWidget(button)
        layout.addLayout(top)
        self.splitter = QSplitter()
        navigation = QWidget()
        left = QVBoxLayout(navigation)
        left.setContentsMargins(0, 0, 0, 0)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Имя или текст заметки…")
        left.addWidget(self.query)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(130)
        left.addWidget(self.tree)
        self.splitter.addWidget(navigation)
        right = QWidget()
        body = QVBoxLayout(right)
        body.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel("Выберите заметку или создайте новую")
        self.name.setWordWrap(True)
        body.addWidget(self.name)
        self.find_bar = QWidget()
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(0, 0, 0, 0)
        self.find_text = QLineEdit()
        self.find_text.setPlaceholderText("Найти в заметке…")
        find_layout.addWidget(self.find_text)
        for label, action in (("↑", lambda: self.step(-1)), ("↓", lambda: self.step(1)), ("×", self.close_find)):
            button = QPushButton(label)
            button.setMaximumWidth(35)
            button.clicked.connect(action)
            find_layout.addWidget(button)
        self.counter = QLabel("0 / 0")
        find_layout.addWidget(self.counter)
        body.addWidget(self.find_bar)
        self.find_bar.hide()
        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.editor.setPlaceholderText("Обычный TXT. Изменения сохраняются автоматически.")
        self.editor.setEnabled(False)
        body.addWidget(self.editor)
        self.splitter.addWidget(right)
        self.splitter.setSizes([190, 500])
        layout.addWidget(self.splitter, 1)
        self.status = QLabel("Блокнот не загружен")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.autosave = QTimer(self)
        self.autosave.setSingleShot(True)
        self.autosave.setInterval(1000)
        self.autosave.timeout.connect(self.save)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(150)
        self.search_timer.timeout.connect(self.refresh)
        self.query.textChanged.connect(lambda _: self.search_timer.start())
        self.tree.itemClicked.connect(self.choose)
        self.editor.textChanged.connect(self.changed)
        self.find_text.textChanged.connect(self.highlight)
        self.find_text.returnPressed.connect(lambda: self.step(1))
        for keys, action in (("Ctrl+S", self.save), ("Ctrl+N", self.new_note), ("Ctrl+F", self.open_find),
                             ("F3", lambda: self.step(1)), ("Shift+F3", lambda: self.step(-1))):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
        self.call(lambda: (rpc("health"), rpc("state_get", {"key": "notebook-qt", "default": {}})), self.initialize)

    def call(self, function, done=lambda value: None, failed=None):
        return background(function, done, failed or self.error)

    def set_text_size(self, size):
        font = QFont("Segoe UI")
        font.setPixelSize(int(size))
        self.setFont(font)
        self.editor.setFont(font)
        self.editor.document().setDefaultFont(font)

    def error(self, message):
        self.status.setText("Ошибка: " + str(message))

    def initialize(self, values):
        health, state = values
        if not health.get("notes"):
            self.error("Обновите и перезапустите бэкенд для работы блокнота")
            return
        self.pending_state = state
        if state.get("sizes"):
            self.splitter.setSizes(state["sizes"])
        self.refresh(initial=True)

    def refresh(self, checked=False, initial=False):
        self.list_generation += 1
        generation = self.list_generation
        query = self.query.text()
        def done(value):
            if generation != self.list_generation:
                return
            previous_root = self.root_path
            if previous_root and previous_root != value["root"] and self.dirty:
                self.error("Папка заметок изменилась. Ваш текст остался в редакторе и черновике")
                return
            self.root_path = value["root"]
            expanded = {self.tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.tree.topLevelItemCount())
                        if self.tree.topLevelItem(i).isExpanded()}
            expanded.update(self.pending_state.get("expanded", []))
            self.tree.clear()
            nodes = {}
            for entry in value["entries"]:
                path = entry["path"]
                parent = nodes.get(path.rpartition("/")[0])
                item = QTreeWidgetItem([("▸ " if entry["folder"] else "") + path.rsplit("/", 1)[-1]])
                item.setData(0, Qt.UserRole, path)
                item.setData(0, Qt.UserRole + 1, entry["folder"])
                item.setToolTip(0, path)
                (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))
                nodes[path] = item
                if path in expanded or query:
                    item.setExpanded(True)
            self.status.setText("Папка: " + self.root_path)
            if initial:
                path = self.pending_state.get("path")
                if path in nodes:
                    self.open_note(path)
            if previous_root and previous_root != self.root_path:
                self.current = None
                self.editor.clear()
                self.editor.setEnabled(False)
        self.call(lambda: rpc("notes_list", {"query": query}), done)

    def choose(self, item, column=0):
        if not item.data(0, Qt.UserRole + 1):
            self.open_note(item.data(0, Qt.UserRole))

    def guard(self, action):
        if self.loading:
            return
        if self.dirty or self.saving:
            self.after_save = action
            self.save()
        else:
            action()

    def open_note(self, path):
        if self.current and self.current["path"] == path:
            return
        self.guard(lambda: self.load(path))

    def load(self, path):
        self.generation += 1
        generation = self.generation
        self.loading = True
        self.editor.setEnabled(False)
        def failed(message):
            self.loading = False
            self.editor.setEnabled(self.current is not None)
            self.error(message)
        def done(value):
            if generation != self.generation:
                return
            self.current = value
            self.editor.setPlainText(value["text"])
            self.editor.setEnabled(True)
            self.editor.setReadOnly(value["readonly"])
            self.loading = self.dirty = False
            self.name.setText(path)
            self.status.setText("Только чтение: файл больше 1 МиБ" if value["readonly"] else "Сохранено")
            if self.pending_state.get("path") == path:
                cursor = self.editor.textCursor()
                cursor.setPosition(min(self.pending_state.get("cursor", 0), self.editor.document().characterCount() - 1))
                self.editor.setTextCursor(cursor)
                scroll = self.pending_state.get("scroll", 0)
                QTimer.singleShot(0, lambda: self.editor.verticalScrollBar().setValue(scroll))
                self.pending_state = {}
            self.highlight()
        self.persist_state()
        self.call(lambda: rpc("notes_read", {"path": path}), done, failed)

    def changed(self):
        if self.loading or not self.current:
            return
        self.dirty = True
        self.status.setText("Изменено")
        self.autosave.start()
        self.highlight()

    def snapshot(self):
        return {**self.current, "text": self.editor.toPlainText(), "root": self.root_path}

    def save(self, checked=False):
        if self.loading or self.saving or not self.current or not self.dirty:
            return
        value = self.snapshot()
        self.saving = True
        self.autosave.stop()
        self.status.setText("Сохраняется…")
        draft_path = recovery_path(value["root"], value["path"])
        def work():
            atomic_json(draft_path, value)
            result = rpc("notes_save", {key: value[key] for key in ("path", "text", "revision", "root")})
            if not result.get("conflict"):
                draft_path.unlink(missing_ok=True)
            return result
        def failed(message):
            self.saving = False
            self.after_save = None
            self.error(str(message) + ". Текст оставлен в редакторе; проверьте «Черновики»")
        def done(result):
            self.saving = False
            if result.get("conflict"):
                self.after_save = None
                self.conflict(result["current"])
                return
            if self.current and self.current["path"] == value["path"]:
                self.current["revision"] = result["revision"]
                self.dirty = self.editor.toPlainText() != value["text"]
                self.status.setText("Изменено" if self.dirty else "Сохранено")
                if self.dirty:
                    self.autosave.start()
                elif self.after_save:
                    action, self.after_save = self.after_save, None
                    action()
            self.persist_state()
        self.call(work, done, failed)

    def conflict(self, external):
        box = QMessageBox(self)
        box.setWindowTitle("Файл изменён другим редактором")
        box.setText("Ваш текст сохранён в черновике. Выберите, какую версию оставить в редакторе.")
        reload_button = box.addButton("Загрузить внешнюю", QMessageBox.AcceptRole)
        copy_button = box.addButton("Сохранить свою копию", QMessageBox.ActionRole)
        box.addButton("Остаться здесь", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == reload_button:
            path = external["path"]
            self.dirty = False
            self.load(path)
        elif box.clickedButton() == copy_button:
            self.create_file(text=self.editor.toPlainText(), suggested=external["path"].removesuffix(".txt") + " — копия.txt")
        else:
            self.error("Конфликт: выберите «Файлы → Сохранить копию» или загрузите заметку заново")

    def selected_path(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item else (self.current["path"] if self.current else "")

    def create_file(self, folder=False, text="", suggested=""):
        selected = self.selected_path()
        item = self.tree.currentItem()
        base = selected if item and item.data(0, Qt.UserRole + 1) else selected.rpartition("/")[0]
        default = suggested or ((base + "/" if base else "") + ("Новая папка" if folder else "Новая заметка.txt"))
        name, ok = QInputDialog.getText(self, "Новая папка" if folder else "Новая заметка", "Путь относительно Notes:", text=default)
        if not ok:
            return
        self.loading = True
        self.editor.setEnabled(False)
        def failed(message):
            self.loading = False
            self.editor.setEnabled(self.current is not None)
            self.error(message)
        def done(value):
            self.loading = False
            self.editor.setEnabled(self.current is not None)
            self.refresh()
            if not folder:
                self.dirty = False
                self.load(value["path"])
        root = self.root_path
        self.call(lambda: rpc("notes_create", {"path": name, "folder": folder, "text": text, "root": root}), done, failed)

    def new_note(self):
        self.guard(self.create_file)

    def move(self):
        source = self.selected_path()
        if not source:
            return
        name, ok = QInputDialog.getText(self, "Переименовать / переместить", "Новый путь относительно Notes:", text=source)
        if not ok or name == source:
            return
        def action():
            self.loading = True
            self.editor.setEnabled(False)
            root = self.root_path
            def failed(message):
                self.loading = False
                self.editor.setEnabled(self.current is not None)
                self.error(message)
            def done(value):
                self.loading = False
                self.editor.setEnabled(self.current is not None)
                if self.current and (self.current["path"] == source or self.current["path"].startswith(source + "/")):
                    self.current["path"] = name + self.current["path"][len(source):]
                    self.name.setText(self.current["path"])
                    self.persist_state()
                self.refresh()
            self.call(lambda: rpc("notes_move", {"path": source, "destination": name, "root": root}), done, failed)
        self.guard(action)

    def delete(self):
        path = self.selected_path()
        if not path or QMessageBox.question(self, "Корзина", "Переместить в корзину: " + path + "?") != QMessageBox.Yes:
            return
        def action():
            self.loading = True
            self.editor.setEnabled(False)
            root = self.root_path
            def failed(message):
                self.loading = False
                self.editor.setEnabled(self.current is not None)
                self.error(message)
            def done(value):
                self.loading = False
                self.editor.setEnabled(self.current is not None)
                if self.current and (self.current["path"] == path or self.current["path"].startswith(path + "/")):
                    self.current = None
                    self.loading = True
                    self.editor.clear()
                    self.loading = False
                    self.editor.setEnabled(False)
                    self.name.setText("Заметка в корзине")
                self.refresh()
            self.call(lambda: rpc("notes_delete", {"path": path, "root": root}), done, failed)
        self.guard(action)

    def trash(self):
        def done(items):
            labels = [i["path"] + " [" + i["id"][:6] + "]" for i in items]
            choice, ok = QInputDialog.getItem(self, "Восстановить из корзины", "Удалённые записи:", labels, editable=False)
            if ok:
                item = items[labels.index(choice)]
                destination, yes = QInputDialog.getText(self, "Восстановить", "Путь восстановления:", text=item["path"])
                if yes:
                    self.call(lambda: rpc("notes_restore", {"id": item["id"], "destination": destination}), lambda _: self.refresh())
        self.call(lambda: rpc("notes_trash"), done)

    def file_menu(self):
        menu = QMenu(self)
        for label, action in (("Новая заметка", self.new_note), ("Новая папка", lambda: self.create_file(folder=True)),
                              ("Переименовать / переместить", self.move), ("Сохранить копию", lambda: self.create_file(text=self.editor.toPlainText())),
                              ("Перечитать с диска", lambda: self.guard(lambda: self.load(self.current["path"])) if self.current else None),
                              ("В корзину", self.delete), ("Восстановить из корзины", self.trash), ("Обновить список", self.refresh)):
            menu.addAction(label).triggered.connect(action)
        menu.exec(self.mapToGlobal(self.rect().topLeft()))

    def open_find(self):
        self.find_bar.show()
        self.find_text.setFocus()
        self.find_text.selectAll()

    def close_find(self):
        self.find_bar.hide()
        self.find_text.clear()
        self.editor.setFocus()

    def escape(self):
        if not self.find_bar.isHidden():
            self.close_find()
            return True
        return False

    def highlight(self):
        self.matches = []
        query = self.find_text.text()
        cursor = QTextCursor(self.editor.document())
        if query:
            while len(self.matches) < 5000:
                cursor = self.editor.document().find(query, cursor)
                if cursor.isNull():
                    break
                self.matches.append(QTextCursor(cursor))
        self.match_index = min(max(self.match_index, 0), len(self.matches) - 1)
        self.paint_matches()

    def paint_matches(self):
        selections = []
        for index, cursor in enumerate(self.matches):
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(QColor("#f6b93b" if index == self.match_index else "#76632e"))
            selection.format.setForeground(QColor("#111111" if index == self.match_index else "#ffffff"))
            selections.append(selection)
        self.editor.setExtraSelections(selections)
        self.counter.setText(f"{self.match_index + 1} / {len(self.matches)}" + ("+" if len(self.matches) == 5000 else ""))

    def step(self, direction):
        if not self.matches:
            return
        self.match_index = (self.match_index + direction) % len(self.matches)
        self.editor.setTextCursor(self.matches[self.match_index])
        self.editor.ensureCursorVisible()
        self.paint_matches()

    def send_to_chat(self):
        if not self.current:
            return
        selected = self.editor.textCursor().selectedText().replace("\u2029", "\n")
        text = "Заметка «" + self.current["path"] + "»:\n" + (selected or self.editor.toPlainText())
        if len(text) > 12000:
            self.error("Для чата выделите фрагмент: предел сообщения 12000 символов. Текст не отправлен")
            return
        self.attach.emit(text)

    def recover(self):
        def work():
            entries = []
            for path in (DATA / "notes-recovery").glob("*.json"):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if value.get("root") == self.root_path:
                        entries.append(value)
                except (ValueError, OSError):
                    pass
            return entries
        def done(entries):
            if not entries:
                self.status.setText("Нет восстановительных черновиков")
                return
            labels = [e["path"] + f" [{i + 1}]" for i, e in enumerate(entries)]
            choice, ok = QInputDialog.getItem(self, "Черновики", "Восстановить отдельной копией:", labels, editable=False)
            if ok:
                value = entries[labels.index(choice)]
                self.guard(lambda: self.create_file(text=value["text"], suggested=value["path"].removesuffix(".txt") + " — восстановлено.txt"))
        self.call(work, done)

    def persist_state(self):
        if not self.current:
            return
        expanded = []
        def walk(item):
            if item.isExpanded():
                expanded.append(item.data(0, Qt.UserRole))
            for index in range(item.childCount()):
                walk(item.child(index))
        for index in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(index))
        value = {"path": self.current["path"], "cursor": self.editor.textCursor().position(),
                 "scroll": self.editor.verticalScrollBar().value(), "sizes": self.splitter.sizes(), "expanded": expanded}
        self.call(lambda: rpc("state_set", {"key": "notebook-qt", "value": value}))

    def flush(self):
        self.save()
        self.persist_state()
