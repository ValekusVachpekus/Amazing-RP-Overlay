"""Shared source/frozen launcher; never injects into another process."""
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog

from .settings import ROOT
from .transport import rpc, background, ensure_backend

AUTHOR = "Автор: Vladislav_Shetkov"


class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Amazing • Лаунчер • " + AUTHOR)
        self.resize(590, 560)
        self.pending = False
        self.notes_root = None
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("Amazing RP Overlay")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(QLabel(AUTHOR))
        self.status = QLabel("Проверяем бэкенд…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.folder = QLabel()
        self.folder.setWordWrap(True)
        layout.addWidget(self.folder)
        for label, action in (("Управление и парсер", lambda: self.launch("manager")),
                              ("Обычный оверлей", lambda: self.launch("overlay")),
                              ("Запустить бэкенд", self.start_backend), ("Открыть заметки", self.open_notes),
                              ("Выбрать папку заметок…", self.choose_notes),
                              ("Открыть папку ASI/DLL", self.open_native)):
            button = QPushButton(label)
            button.setMinimumHeight(37)
            button.clicked.connect(action)
            layout.addWidget(button)
        self.build = ROOT / "scripts" / "build"
        if self.build.is_dir() and not getattr(sys, "frozen", False):
            for name, label in (("build_native.bat", "Собрать ASI/DLL"), ("build_portable.bat", "Собрать и обновить Release")):
                button = QPushButton(label)
                button.clicked.connect(lambda checked=False, name=name: self.build_run(name))
                layout.addWidget(button)
        layout.addStretch()
        layout.addWidget(QLabel("ASI/DLL загружается вручную. Закрытие лаунчера не останавливает бэкенд."))
        self.setCentralWidget(panel)
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.check)
        self.timer.start()
        self.check()

    def launch(self, mode):
        command = [mode] if getattr(sys, "frozen", False) else ["-m", "amazing_overlay", mode]
        success, _ = QProcess.startDetached(sys.executable, command, str(ROOT))
        if not success:
            self.status.setText("Не удалось запустить " + mode)

    def check(self):
        if self.pending:
            return
        self.pending = True
        def work():
            health = rpc("health", timeout=2500)
            notes = rpc("notes_info") if health.get("notes") else {}
            return health, notes
        def done(value):
            self.pending = False
            health, notes = value
            self.status.setText("Бэкенд работает • версия " + health["app_version"] + " • законов: " + str(health["documents"]))
            self.notes_root = notes.get("root")
            self.folder.setText("Заметки: " + (self.notes_root or "обновите бэкенд для поддержки блокнота"))
        def failed(message):
            self.pending = False
            self.status.setText("Бэкенд недоступен. " + str(message))
        background(work, done, failed)

    def start_backend(self):
        self.status.setText("Запускаем бэкенд…")
        background(ensure_backend, lambda _: self.check(), lambda message: self.status.setText(str(message)))

    def open_notes(self):
        def done(value):
            QDesktopServices.openUrl(QUrl.fromLocalFile(value["root"]))
            self.check()
        background(lambda: (ensure_backend(), rpc("notes_info"))[1], done, lambda message: self.status.setText(str(message)))

    def choose_notes(self):
        path = QFileDialog.getExistingDirectory(self, "Папка заметок (файлы автоматически не перемещаются)", self.notes_root or str(ROOT))
        if path:
            background(lambda: (ensure_backend(), rpc("settings_save", {"notes_dir": path})), lambda _: self.check(), lambda message: self.status.setText(str(message)))

    def open_native(self):
        folder = ROOT / "Release" if (ROOT / "Release").is_dir() else ROOT
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def build_run(self, name):
        process = QProcess(self)
        # A visible console is intentional: builds are interactive developer commands.
        process.setWorkingDirectory(str(ROOT))
        process.startDetached("cmd.exe", ["/c", "start", "Amazing build", "cmd.exe", "/k", str(self.build / name)])
