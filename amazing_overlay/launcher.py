"""Shared source/frozen launcher; never injects into another process."""
import sys
from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QScrollArea, QStyle
from .desktop import AUTHOR, action, card, label, open_folder, setup_desktop
from .settings import ROOT, DATA
from .transport import rpc, background, ensure_backend


class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        setup_desktop(self)
        self.setWindowTitle("Amazing • Лаунчер • " + AUTHOR)
        self.resize(740, 820)
        self.setMinimumSize(640, 580)
        self.pending = False
        self.operation = False
        self.closed = False
        self.online = False
        self.can_stop = False
        self.stopping = False
        self.generation = 0
        self.notes_root = None
        self.laws_root = str(DATA)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        panel.setObjectName("desktopRoot")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)
        top = QHBoxLayout()
        top.addWidget(label("Amazing RP Overlay", "title"), 1)
        self.badge = label("Проверка…", "badge", False)
        top.addWidget(self.badge)
        layout.addLayout(top)
        layout.addWidget(label(AUTHOR))
        apps = card(layout)
        apps.addWidget(label("Рабочее пространство", "sectionTitle"))
        apps.addWidget(label("Законы, источники и настройки — в управлении. Для игры выберите ASI/DLL или обычный оверлей."))
        row = QHBoxLayout()
        action("Управление и парсер", lambda: self.launch("manager"), row, "primary", self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        action("Обычный оверлей", lambda: self.launch("overlay"), row, icon=self.style().standardIcon(QStyle.SP_DesktopIcon))
        apps.addLayout(row)
        action("Открыть папку ASI/DLL", self.open_native, apps)
        backend = card(layout)
        backend.addWidget(label("Бэкенд", "sectionTitle"))
        self.status = label("Проверяем бэкенд…", "status")
        backend.addWidget(self.status)
        self.notice = label("", "error")
        self.notice.hide()
        backend.addWidget(self.notice)
        row = QHBoxLayout()
        self.start_button = action("Запустить", self.start_backend, row, "primary", self.style().standardIcon(QStyle.SP_MediaPlay))
        self.stop_button = action("Остановить", self.stop_backend, row, "danger", self.style().standardIcon(QStyle.SP_MediaStop))
        backend.addLayout(row)
        self.laws_folder = label("База законов: " + self.laws_root)
        self.laws_folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        backend.addWidget(self.laws_folder)
        action("Открыть папку законов", self.open_laws, backend, icon=self.style().standardIcon(QStyle.SP_DirOpenIcon))
        backend.addWidget(label("Законы хранятся в laws.db (SQLite). Экспорт в TXT/JSON доступен в управлении базой."))
        notes = card(layout)
        notes.addWidget(label("Заметки", "sectionTitle"))
        self.folder = label("Папка будет показана после подключения к бэкенду.")
        self.folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        notes.addWidget(self.folder)
        row = QHBoxLayout()
        action("Открыть заметки", self.open_notes, row)
        action("Выбрать папку…", self.choose_notes, row)
        notes.addLayout(row)
        self.build = ROOT / "scripts" / "build"
        if self.build.is_dir() and not getattr(sys, "frozen", False):
            developer = card(layout)
            developer.addWidget(label("Разработка", "sectionTitle"))
            row = QHBoxLayout()
            for name, text in (("build_native.bat", "Собрать ASI/DLL"), ("build_portable.bat", "Собрать Release")):
                action(text, lambda checked=False, name=name: self.build_run(name), row)
            developer.addLayout(row)
        layout.addWidget(label("ASI/DLL загружается вручную. Закрытие этого окна оставляет бэкенд работающим."))
        layout.addStretch()
        scroll.setWidget(panel)
        self.setCentralWidget(scroll)
        self.update_buttons()
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.check)
        self.timer.start()
        self.check()

    def update_buttons(self):
        self.start_button.setEnabled(not self.operation and not self.online and not self.stopping)
        self.stop_button.setEnabled(not self.operation and self.online and self.can_stop and not self.stopping)
        self.stop_button.setToolTip("Штатно завершить бэкенд; клиенты останутся открытыми." if self.can_stop else "Для остановки из лаунчера нужен обновлённый бэкенд.")
        self.badge.setText("Работает" if self.online else "Отключён")
        self.badge.setProperty("state", "online" if self.online else "offline")
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)

    def launch(self, mode):
        command = [mode] if getattr(sys, "frozen", False) else ["-m", "amazing_overlay", mode]
        success, _ = QProcess.startDetached(sys.executable, command, str(ROOT))
        if not success:
            self.show_error("Не удалось запустить " + mode)

    def check(self):
        if self.pending or self.operation or self.closed:
            return
        self.pending = True
        generation = self.generation
        def work():
            health = rpc("health", timeout=2500)
            notes = rpc("notes_info") if health.get("notes") and not health.get("stopping") else {}
            return health, notes
        def done(value):
            self.pending = False
            if self.closed or self.operation or generation != self.generation:
                return
            health, notes = value
            self.online = True
            self.stopping = health.get("stopping", False)
            self.can_stop = health.get("shutdown", False)
            self.status.setText("Бэкенд работает • версия " + health["app_version"] + " • законов: " + str(health["documents"]))
            if self.stopping:
                self.status.setText("Бэкенд останавливается: завершаются текущие задачи…")
            self.notes_root = notes.get("root", self.notes_root)
            self.laws_root = health["data_dir"]
            self.laws_folder.setText("База законов: " + self.laws_root)
            self.folder.setText(self.notes_root or "Обновите бэкенд для поддержки блокнота.")
            self.update_buttons()
        def failed(message):
            self.pending = False
            if self.closed or self.operation or generation != self.generation:
                return
            self.online = False
            self.stopping = False
            self.status.setText("Бэкенд отключён. Нажмите «Запустить» для подключения." if "не запущен" in str(message).lower() else str(message))
            self.update_buttons()
        background(work, done, failed)

    def backend_action(self, function, message, success):
        if self.operation:
            return
        self.operation = True
        self.generation += 1
        self.notice.hide()
        self.status.setText(message)
        self.update_buttons()
        def finish(value=None, error=None):
            self.operation = False
            if self.closed:
                return
            self.status.setText(str(error) if error else success)
            if error:
                self.show_error(error)
            if not error:
                self.online = bool(value)
                self.stopping = False
            self.update_buttons()
            QTimer.singleShot(500, self.check)
        background(function, lambda v: finish(v), lambda e: finish(error=e))

    def start_backend(self):
        self.backend_action(ensure_backend, "Запускаем бэкенд…", "Бэкенд запущен.")

    def stop_backend(self):
        from .transport import stop_backend
        self.backend_action(stop_backend, "Останавливаем: ждём завершения записи и отмены задач…", "Бэкенд остановлен.")

    def open_laws(self):
        # Cached health path also works while stopped; do not auto-start here.
        try:
            open_folder(self.laws_root)
        except OSError as exc:
            self.show_error(exc)

    def open_notes(self):
        def done(value):
            if self.closed:
                return
            try:
                open_folder(value["root"])
            except OSError as exc:
                self.show_error(exc)
            self.check()
        background(lambda: (ensure_backend(), rpc("notes_info"))[1], done, self.show_error)

    def show_error(self, message):
        if not self.closed:
            self.notice.setText(str(message))
            self.notice.show()

    def choose_notes(self):
        path = QFileDialog.getExistingDirectory(self, "Папка заметок (файлы автоматически не перемещаются)", self.notes_root or str(ROOT))
        if path:
            background(lambda: (ensure_backend(), rpc("settings_save", {"notes_dir": path})), lambda _: self.check(), self.show_error)

    def open_native(self):
        folder = ROOT / "Release" if (ROOT / "Release").is_dir() else ROOT
        try:
            open_folder(folder)
        except OSError as exc:
            self.show_error(exc)

    def build_run(self, name):
        process = QProcess(self)
        process.setWorkingDirectory(str(ROOT))
        success, _ = process.startDetached("cmd.exe", ["/c", "start", "Amazing build", "cmd.exe", "/k", str(self.build / name)])
        if not success:
            self.show_error("Не удалось запустить сборку.")

    def closeEvent(self, event):
        self.closed = True
        self.timer.stop()
        super().closeEvent(event)
