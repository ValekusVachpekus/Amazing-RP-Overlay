"""Source/frozen native Qt launcher. Backend and client actions stay asynchronous."""
import sys
from datetime import datetime
from PySide6.QtCore import QProcess, QTimer, Qt, QSize
from PySide6.QtWidgets import (QButtonGroup, QFrame, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QFileDialog, QScrollArea, QSizePolicy, QStackedWidget)
from . import __version__
from .desktop import AUTHOR, action, label, open_folder
from .launcher_presentation import CardGrid, PathLabel, icon, separator, setup, state
from .settings import ROOT, DATA
from .transport import rpc, background, ensure_backend


class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        setup(self)
        self.setWindowTitle("Amazing • Лаунчер • " + AUTHOR)
        area = self.screen().availableGeometry()
        self.resize(min(1440, max(760, area.width() - 48)), min(900, max(540, area.height() - 48)))
        self.setMinimumSize(760, 540)
        self.pending = self.operation = self.closed = self.online = False
        self.can_stop = self.stopping = self.checked_once = self.notes_pending = False
        self.operation_kind = ""
        self.generation = 0
        self.poll_revision = 0
        self.notes_root = None
        self.manager = None
        self.group_pages = {"manager": "sources", "settings": "appearance"}
        self.laws_root = str(DATA)
        self.build = ROOT / "scripts/build"
        self.developer_mode = self.build.is_dir() and not getattr(sys, "frozen", False)
        self.root = QWidget()
        self.root.setObjectName("launcherRoot")
        outer = QVBoxLayout(self.root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(self.root)
        self.make_brand(outer)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)
        self.make_sidebar(body)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.overview = QWidget()
        self.overview.setObjectName("overview")
        self.content = QVBoxLayout(self.overview)
        self.content.setContentsMargins(40, 38, 40, 20)
        self.content.setSpacing(20)
        self.scroll.setWidget(self.overview)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.scroll)
        body.addWidget(self.pages, 1)
        title = QHBoxLayout()
        title.setSpacing(20)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        texts.addWidget(label("Центр управления", "pageTitle"))
        texts.addWidget(label("Запускайте игровой оверлей, управляйте источниками и контролируйте бэкенд.", "subtitle"))
        title.addLayout(texts, 1)
        self.badge = label("●  Проверка…", "badge", False)
        title.addWidget(self.badge, 0, Qt.AlignTop)
        self.content.addLayout(title)
        self.content.addSpacing(10)
        self.make_backend()
        self.notice = label("", "error")
        self.notice.hide()
        self.content.addWidget(self.notice)
        self.content.addSpacing(4)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(label("Рабочее пространство", "sectionTitle"))
        heading.addWidget(label("Выберите режим работы или перейдите к настройке базы.", "small"))
        self.content.addLayout(heading)
        self.manager_card, manager = self.make_card("Управление и парсер", "database", "Законодательство XenForo, импорт ссылок, обновления и экспорт.")
        self.manager_button = action("Открыть управление  →", lambda: self.launch("manager"), manager, "primary")
        self.overlay_card, overlay = self.make_card("Обычный оверлей", "overlay", "Быстрый поиск и чтение поверх игры. Режим оконного наложения.")
        self.overlay_button = action("Открыть оверлей  →", lambda: self.launch("overlay"), overlay)
        self.native_card, native = self.make_card("Внутриигровой ASI / DLL", "plugin", "Папка плагина и файлы для ручной установки в игру.")
        self.native_button = action("Открыть папку ASI/DLL", self.open_native, native)
        self.apps_grid = CardGrid([self.manager_card, self.overlay_card, self.native_card], parent=self.overview)
        self.content.addWidget(self.apps_grid)
        self.notes_card, notes = self.make_card("Заметки", "note", "Управление TXT-файлами, выбор рабочей папки и автосохранение.")
        self.folder = PathLabel("Папка будет показана после подключения к бэкенду.")
        notes.addWidget(self.folder)
        note_actions = QHBoxLayout()
        note_actions.setSpacing(10)
        self.notes_button = action("Открыть заметки", self.open_notes, note_actions)
        self.choose_button = action("Выбрать папку", self.choose_notes, note_actions, "quiet")
        notes.addLayout(note_actions)
        secondary = [self.notes_card]
        self.developer_card = None
        if self.developer_mode:
            self.developer_card, development = self.make_card("Разработка", "code", "Сборка ASI/DLL и переносимого Release-пакета.")
            row = QHBoxLayout()
            row.setSpacing(10)
            for name, text in (("build_native.bat", "Собрать ASI/DLL"), ("build_portable.bat", "Собрать Release")):
                action(text, lambda checked=False, name=name: self.build_run(name), row, "quiet")
            development.addLayout(row)
            secondary.append(self.developer_card)
        self.secondary_grid = CardGrid(secondary, 2, parent=self.overview)
        self.content.addWidget(self.secondary_grid)
        self.laws_folder = PathLabel("Закрытие окна не останавливает бэкенд · Локальные данные: " + self.laws_root)
        self.laws_folder.setObjectName("footnote")
        self.content.addWidget(self.laws_folder)
        self.content.addStretch(1)
        self.update_buttons()
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.check)
        self.timer.start()
        self.check()

    def make_brand(self, outer):
        bar = QFrame()
        bar.setObjectName("brandBar")
        bar.setFixedHeight(80)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(30, 20, 32, 20)
        layout.setSpacing(14)
        mark = label("R", "brandMark", False)
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(28, 28)
        layout.addWidget(mark)
        layout.addWidget(label("AMAZING  /  OVERLAY", "brandTitle", False))
        layout.addStretch(1)
        layout.addWidget(label(AUTHOR, "author", False))
        outer.addWidget(bar)

    def make_sidebar(self, body):
        self.sidebar = QWidget()
        self.sidebar.setObjectName("launcherSidebar")
        self.sidebar.setFixedWidth(250)
        nav = QVBoxLayout(self.sidebar)
        nav.setContentsMargins(18, 64, 18, 24)
        nav.setSpacing(10)
        self.nav_buttons = {}
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        items = [("overview", "Обзор", "home", lambda: self.section("overview")),
                 ("manager", "Управление базой", "database", lambda: self.open_manager_page(self.group_pages["manager"])),
                 ("overlay", "Оверлей", "overlay", lambda: self.launch("overlay")),
                 ("settings", "Настройки", "settings", self.open_settings)]
        for name, text, glyph, callback in items:
            button = action(text, callback, nav, "navigation", icon(glyph))
            button.setIconSize(QSize(18, 18))
            button.setMinimumHeight(22)
            button.setAccessibleName(text)
            if name in {"overview", "manager", "settings"}:
                button.setCheckable(True)
                self.nav_group.addButton(button)
            self.nav_buttons[name] = button
        self.nav_buttons["overview"].setChecked(True)
        nav.addStretch(1)
        nav.addWidget(label("RP Companion  ·  v" + __version__, "small", False))
        nav.addWidget(label("Made by Vladislav_Shetkov", "footnote", False))
        body.addWidget(self.sidebar)

    def make_card(self, title, glyph, description):
        frame = QFrame(self.overview)
        frame.setObjectName("launcherCard")
        frame.setMinimumWidth(0)
        frame.setMinimumHeight(168)
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(10)
        picture = label("", wrap=False)
        picture.setPixmap(icon(glyph).pixmap(18, 18))
        picture.setFixedSize(18, 22)
        row.addWidget(picture, 0, Qt.AlignTop)
        row.addWidget(label(title, "cardTitle"), 1)
        layout.addLayout(row)
        layout.addWidget(label(description))
        layout.addStretch(1)
        return frame, layout

    def make_backend(self):
        self.backend_card = QFrame(self.overview)
        self.backend_card.setObjectName("launcherCard")
        layout = QVBoxLayout(self.backend_card)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        row = QHBoxLayout()
        row.setSpacing(20)
        identity = QHBoxLayout()
        identity.setSpacing(20)
        self.backend_icon = label("", "backendIcon", False)
        self.backend_icon.setFixedSize(60, 60)
        self.backend_icon.setAlignment(Qt.AlignCenter)
        identity.addWidget(self.backend_icon, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        self.backend_title = label("Проверяем подключение…", "backendTitle")
        self.backend_description = label("Поиск, парсер и ИИ используют локальный бэкенд.")
        texts.addWidget(self.backend_title)
        texts.addWidget(self.backend_description)
        identity.addLayout(texts, 1)
        row.addLayout(identity, 1)
        self.backend_controls = QStackedWidget(self.backend_card)
        self.backend_controls.setStyleSheet(self.styleSheet())
        self.backend_controls.setFixedWidth(230)
        for name in ("start", "stop"):
            panel = QWidget(self.backend_controls)
            controls = QVBoxLayout(panel)
            controls.setContentsMargins(0, 0, 0, 0)
            if name == "start":
                self.start_button = action("Запустить бэкенд", self.start_backend, controls, "primary", icon("play", "#f3f1f7"))
            else:
                self.stop_button = action("Остановить бэкенд", self.stop_backend, controls, icon=icon("stop"))
            self.backend_controls.addWidget(panel)
        self.hero_row = row
        row.addWidget(self.backend_controls, 0, Qt.AlignTop)
        layout.addLayout(row)
        layout.addWidget(separator())
        details = QHBoxLayout()
        details.setSpacing(16)
        self.status = label("Ожидаем результат проверки…", "backendDetails")
        details.addWidget(self.status, 1)
        self.laws_button = action("Папка законов", self.open_laws, details, "quiet", icon("folder"))
        layout.addLayout(details)
        self.content.addWidget(self.backend_card)

    def section(self, name):
        if name != "overview":
            raise ValueError("Неизвестный раздел лаунчера")
        self.nav_buttons["overview"].setChecked(True)
        self.pages.setCurrentWidget(self.scroll)

    def open_manager_page(self, page="sources"):
        from .manager_navigation import PAGES
        if page not in PAGES:
            raise ValueError("Неизвестный раздел управления")
        if self.manager is None:
            from .ui import ManagerWindow
            self.manager_host = QWidget(self.pages)
            self.manager_layout = QVBoxLayout(self.manager_host)
            self.manager_layout.setContentsMargins(32, 28, 32, 16)
            self.manager_layout.setSpacing(20)
            self.subnav = QHBoxLayout()
            self.subnav.setSpacing(8)
            self.subnav_buttons = {}
            self.subnav_group = QButtonGroup(self)
            for index, title in ((0, "Источники"), (1, "Обновление"), (4, "Диагностика"), (3, "Оформление и клавиши"), (2, "ИИ / API")):
                item = action(title, lambda checked=False, index=index: self.open_manager_page(PAGES[index]), self.subnav, "subnav")
                item.setCheckable(True)
                self.subnav_group.addButton(item)
                self.subnav_buttons[index] = item
            self.subnav.addStretch(1)
            self.manager_layout.addLayout(self.subnav)
            self.manager = ManagerWindow(parent=self.manager_host, embedded=True)
            self.manager.settings_saved.connect(lambda _: self.check())
            self.manager_layout.addWidget(self.manager, 1)
            self.pages.addWidget(self.manager_host)
            self.manager.tabs.currentChanged.connect(self.manager_page_changed)
        self.manager.tabs.setCurrentIndex(PAGES.index(page))
        self.manager_page_changed(PAGES.index(page))
        self.pages.setCurrentWidget(self.manager_host)

    def manager_page_changed(self, index):
        from .manager_navigation import PAGES
        group = "settings" if index in (2, 3) else "manager"
        self.group_pages[group] = PAGES[index]
        self.nav_buttons[group].setChecked(True)
        for number, item in self.subnav_buttons.items():
            item.setVisible((number in (2, 3)) == (group == "settings"))
            item.setChecked(number == index)

    def update_buttons(self):
        ready = self.checked_once and not self.operation and not self.stopping
        self.start_button.setEnabled(ready and not self.online)
        self.stop_button.setEnabled(ready and self.online and self.can_stop)
        self.stop_button.setToolTip("Штатно завершить бэкенд; клиенты останутся открытыми." if self.can_stop else "Для остановки нужен обновлённый бэкенд.")
        self.start_button.setText("Запускаем…" if self.operation_kind == "start" else "Запустить бэкенд")
        self.stop_button.setText("Останавливаем…" if self.operation_kind == "stop" else "Остановить бэкенд")
        self.backend_controls.setCurrentIndex(1 if self.online else 0)
        busy = self.operation or self.stopping
        if busy:
            badge, title = ("Останавливается…", "Завершаем работу бэкенда…") if self.operation_kind == "stop" or self.stopping else ("Запускается…", "Запускаем бэкенд…")
            appearance = "pending"
        elif not self.checked_once:
            badge, title, appearance = "Проверка…", "Проверяем подключение…", "pending"
        elif self.online:
            badge, title, appearance = "Бэкенд работает", "Все системы готовы", "online"
        else:
            badge, title, appearance = "Отключён", "Бэкенд ожидает запуска", "offline"
        self.badge.setText("●  " + badge)
        self.backend_title.setText(title)
        self.backend_description.setText("База законов подключена. Можно открыть управление или запустить игровой оверлей." if self.online and not busy else "Для поиска, парсера и ИИ необходимо подключить локальный бэкенд." if not busy else "Завершаем текущие операции без принудительного закрытия процессов.")
        self.backend_icon.setPixmap(icon("check" if appearance == "online" else "power", "#82d2af" if appearance == "online" else "#bda0ff").pixmap(30, 30))
        state(self.badge, appearance)
        state(self.backend_icon, appearance)
        state(self.status, appearance)
        self.notes_button.setEnabled(not self.notes_pending)
        self.choose_button.setEnabled(not self.notes_pending)

    def launch(self, mode, page=None):
        if mode == "manager":
            self.open_manager_page(page or "sources")
            return
        command = [mode] if getattr(sys, "frozen", False) else ["-m", "amazing_overlay", mode]
        if page:
            command.extend(["--page", page])
        success, _ = QProcess.startDetached(sys.executable, command, str(ROOT))
        if not success:
            self.show_error("Не удалось запустить " + mode)

    def open_settings(self):
        self.open_manager_page(self.group_pages["settings"])

    def check(self):
        if self.pending or self.operation or self.closed:
            return
        self.pending = True
        generation = self.generation
        self.poll_revision += 1
        poll_revision = self.poll_revision
        def work():
            health = rpc("health", timeout=2500)
            notes, notes_error = {}, ""
            if health.get("notes") and not health.get("stopping"):
                try:
                    notes = rpc("notes_info")
                except Exception as exc:
                    notes_error = str(exc)
            return health, notes, notes_error
        def done(value):
            if self.closed or poll_revision != self.poll_revision:
                return
            self.pending = False
            if self.closed or self.operation or generation != self.generation:
                return
            health, notes, notes_error = value
            self.checked_once = self.online = True
            self.stopping = health.get("stopping", False)
            self.can_stop = health.get("shutdown", False)
            self.status.setText("Бэкенд работает · v" + health["app_version"] + " · SQLite · законов: " + str(health["documents"]) + " · проверено " + datetime.now().strftime("%d.%m.%Y %H:%M"))
            if self.stopping:
                self.status.setText("Бэкенд останавливается: завершаются текущие задачи…")
            self.notes_root = notes.get("root", self.notes_root)
            self.laws_root = health["data_dir"]
            self.laws_folder.set_path("Закрытие окна не останавливает бэкенд · Локальные данные: " + self.laws_root)
            self.laws_button.setToolTip(self.laws_root)
            self.folder.set_path("Заметки недоступны: " + notes_error if notes_error else self.notes_root or "Обновите бэкенд для поддержки блокнота.")
            self.update_buttons()
        def failed(message):
            if self.closed or poll_revision != self.poll_revision:
                return
            self.pending = False
            if self.closed or self.operation or generation != self.generation:
                return
            self.checked_once = True
            self.online = self.stopping = False
            self.status.setText("Нет подключения. Источники и заметки станут доступны после запуска." if "не запущен" in str(message).lower() else str(message))
            self.update_buttons()
        background(work, done, failed)

    def backend_action(self, function, message, success):
        if self.operation:
            return
        self.operation = True
        self.generation += 1
        self.poll_revision += 1
        self.pending = False
        self.notice.hide()
        self.status.setText(message)
        self.update_buttons()
        def finish(value=None, error=None):
            self.operation = False
            self.operation_kind = ""
            if self.closed:
                return
            self.checked_once = True
            self.status.setText(str(error) if error else success)
            if error:
                self.show_error(error)
            else:
                self.online = bool(value)
                self.stopping = False
            self.update_buttons()
            QTimer.singleShot(500, self.check)
        background(function, lambda value: finish(value), lambda error: finish(error=error))

    def start_backend(self):
        if self.operation:
            return
        self.operation_kind = "start"
        self.backend_action(ensure_backend, "Запускаем бэкенд…", "Бэкенд запущен.")

    def stop_backend(self):
        if self.operation:
            return
        from .transport import stop_backend
        self.operation_kind = "stop"
        self.backend_action(stop_backend, "Останавливаем: ждём завершения записи и отмены задач…", "Бэкенд остановлен.")

    def open_laws(self):
        try:
            open_folder(self.laws_root)
        except OSError as exc:
            self.show_error(exc)

    def open_notes(self):
        if self.notes_pending:
            return
        self.notes_pending = True
        self.update_buttons()
        def done(value):
            self.notes_pending = False
            if self.closed:
                return
            try:
                open_folder(value["root"])
            except OSError as exc:
                self.show_error(exc)
            self.update_buttons()
            self.check()
        def failed(error):
            self.notes_pending = False
            if not self.closed:
                self.update_buttons()
                self.show_error(error)
        background(lambda: (ensure_backend(), rpc("notes_info"))[1], done, failed)

    def show_error(self, message):
        if not self.closed:
            self.notice.setText(str(message))
            self.notice.show()

    def choose_notes(self):
        if self.notes_pending:
            return
        path = QFileDialog.getExistingDirectory(self, "Папка заметок (файлы автоматически не перемещаются)", self.notes_root or str(ROOT))
        if path:
            self.notes_pending = True
            self.update_buttons()
            def finish(error=None):
                self.notes_pending = False
                if self.closed:
                    return
                self.update_buttons()
                if error:
                    self.show_error(error)
                elif self.manager is not None:
                    self.manager.inputs["notes_dir"].setText(path)
                self.check()
            background(lambda: (ensure_backend(), rpc("settings_save", {"notes_dir": path})), lambda _: finish(), lambda error: finish(error))

    def open_native(self):
        folder = ROOT / "Release" if (ROOT / "Release").is_dir() else ROOT
        try:
            open_folder(folder)
        except OSError as exc:
            self.show_error(exc)

    def build_run(self, name):
        if not self.developer_mode:
            self.show_error("Команды сборки доступны только в исходном проекте.")
            return
        process = QProcess(self)
        process.setWorkingDirectory(str(ROOT))
        success, _ = process.startDetached("cmd.exe", ["/c", "start", "Amazing build", "cmd.exe", "/k", str(self.build / name)])
        if not success:
            self.show_error("Не удалось запустить сборку.")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            compact = event.size().width() < 1100
            self.sidebar.setFixedWidth(210 if compact else 250)
            if hasattr(self, "content"):
                self.content.setContentsMargins(24 if compact else 40, 28 if compact else 38, 24 if compact else 40, 20)
            if hasattr(self, "hero_row"):
                self.hero_row.setDirection(QHBoxLayout.TopToBottom if event.size().width() < 1150 else QHBoxLayout.LeftToRight)
            if hasattr(self, "manager_layout"):
                margin = 20 if compact else 32
                self.manager_layout.setContentsMargins(margin, 28, margin, 16)

    def closeEvent(self, event):
        self.closed = True
        self.timer.stop()
        if self.manager is not None:
            self.manager.close()
        super().closeEvent(event)
