from __future__ import annotations

import html
import json
import sys
from datetime import datetime

from PySide6.QtCore import QByteArray, QEvent, QPoint, QProcess, QProcessEnvironment, QTimer, Qt, QPropertyAnimation, QEasingCurve, QAbstractAnimation, Signal
from PySide6.QtGui import QAction, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                              QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                              QPushButton, QScrollArea, QSizeGrip, QSpinBox, QSplitter, QStyle, QSystemTrayIcon,
                              QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit,
                              QVBoxLayout, QWidget)

from .documents import chunks
from .desktop import AUTHOR, action, heading, label, open_folder, setup_desktop
from .settings import ROOT, DEFAULTS
from .browsers import configure_browser_environment
from .transport import background, ensure_backend, rpc
from .win32 import Hotkeys, activate, belongs_to_game, foreground, native_owns_input, open_native_notebook, parse_hotkey, set_input

STYLE = """
QWidget {background:#111827;color:#e2e8f0;font-family:'Segoe UI';font-size:14px;}
QMainWindow,QDialog {background:#111827;}
QLineEdit,QTextEdit,QPlainTextEdit,QTextBrowser,QTreeWidget,QListWidget,QTableWidget,QComboBox,QSpinBox,QDoubleSpinBox {
 background:#172235;border:1px solid #334155;border-radius:6px;padding:6px;selection-background-color:#2563eb;}
QPushButton {background:#253348;border:1px solid #40516b;border-radius:6px;padding:7px 12px;}
QPushButton:hover {background:#354967;} QPushButton:disabled {color:#64748b;}
QLabel#status {color:#93c5fd;} QTabBar::tab {padding:10px;background:#1e293b;}
QTabBar::tab:selected {background:#334155;} QHeaderView::section {background:#253348;padding:7px;}
QSplitter::handle {background:#334155;width:3px;} QScrollBar:vertical {width:12px;}
"""


def button(text, action, layout):
    item = QPushButton(text)
    item.clicked.connect(action)
    layout.addWidget(item)
    return item


def browser():
    value = QTextBrowser()
    value.setOpenExternalLinks(False)
    value.anchorClicked.connect(lambda url: open_url(url.toString()))
    return value


def open_url(url):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    if url.startswith(("https://", "http://")):
        QDesktopServices.openUrl(QUrl(url))


class BrowserInstaller(QMainWindow):
    """Install the parser browser without requiring Python on portable machines."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Amazing • Установка браузера парсера")
        self.resize(800, 450)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.setCentralWidget(self.output)
        from playwright._impl._driver import compute_driver_executable
        executable, script = compute_driver_executable()
        self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PLAYWRIGHT_BROWSERS_PATH", configure_browser_environment())
        self.process.setProcessEnvironment(environment)
        self.output.append("Каталог браузеров: " + configure_browser_environment())
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(lambda: self.output.append(bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")))
        self.process.errorOccurred.connect(lambda error: self.output.append("Не удалось запустить установку: " + self.process.errorString()))
        self.process.finished.connect(lambda code, status: self.output.append("Готово. Можно закрыть окно." if code == 0 else f"Установка завершилась с кодом {code}. Проверьте сеть и повторите."))
        self.process.start(str(executable), [str(script), "install", "chromium"])

    def closeEvent(self, event):
        if self.process.state() != QProcess.NotRunning:
            self.process.terminate()
            self.process.waitForFinished(1000)
        super().closeEvent(event)


class RemoteWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.work = set()
        self.jobs = {}
        self.polling = False
        self.closed = False
        self.status = QLabel("Подключение к бэкенду…")
        self.status.setObjectName("status")
        self.timer = QTimer(self)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def error(self, message):
        if not self.closed:
            self.status.setText(str(message))

    def task(self, function, done=lambda result: None):
        holder = []

        def finish(value, success):
            if holder:
                self.work.discard(holder[0])
            if not self.closed:
                done(value) if success else self.error(value)
        item = background(function, lambda v: finish(v, True), lambda e: finish(e, False))
        holder.append(item)
        self.work.add(item)

    def call(self, method, params=None, done=lambda result: None):
        self.task(lambda: rpc(method, params), done)

    def start_job(self, method, params=None, done=lambda result: None, progress=lambda value: None, finished=lambda: None):
        def started(value):
            self.jobs[value["job_id"]] = (done, progress, finished)
            self.status.setText("Задача запущена")
        self.call(method, params, started)

    def poll(self):
        if self.polling or not self.jobs or self.closed:
            return
        self.polling = True
        ids = list(self.jobs)

        def receive(values):
            self.polling = False
            for ident, state in values:
                if ident not in self.jobs:
                    continue
                done, progress, finished = self.jobs[ident]
                progress(state.get("progress", {}))
                if state["state"] != "running":
                    del self.jobs[ident]
                    if state["state"] == "done":
                        done(state.get("result"))
                    else:
                        self.error(state.get("error", "Ошибка задачи"))
                    finished()

        def fetch():
            values = []
            for ident in ids:
                try:
                    values.append((ident, rpc("job_status", {"id": ident})))
                except Exception as exc:
                    values.append((ident, {"state": "error", "error": str(exc)}))
            return values
        self.task(fetch, receive)

    def cancel_jobs(self):
        for ident in list(self.jobs):
            self.call("job_cancel", {"id": ident})


class ManagerWindow(RemoteWindow):
    settings_saved = Signal(dict)

    def __init__(self, parent=None, embedded=False):
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            from .launcher_presentation import STYLE as manager_style
            self.setWindowFlags(Qt.Widget)
            self.setStyleSheet(manager_style)
        else:
            setup_desktop(self)
        self.setWindowTitle("Amazing • Управление базой и настройками • " + AUTHOR)
        if not embedded:
            self.resize(1320, 840)
            self.setMinimumSize(1040, 620)
        self.data_dir = None
        self.header_ready = False
        root = QWidget()
        root.setObjectName("desktopRoot")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(214)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(16, 24, 16, 20)
        nav.setSpacing(8)
        nav.addWidget(label("AMAZING", "sectionTitle"))
        nav.addWidget(label("Управление базой", "subtitle"))
        nav.addSpacing(24)
        self.nav_buttons = []
        for index, (title, icon) in enumerate((("Источники", QStyle.SP_FileDialogDetailedView),
                                               ("Обновление", QStyle.SP_BrowserReload),
                                               ("ИИ / API", QStyle.SP_MessageBoxInformation),
                                               ("Оформление и клавиши", QStyle.SP_ComputerIcon),
                                               ("Диагностика", QStyle.SP_FileDialogInfoView))):
            item = action(title, lambda checked=False, i=index: self.tabs.setCurrentIndex(i), nav,
                          "nav", self.style().standardIcon(icon))
            item.setCheckable(True)
            item.setAutoExclusive(True)
            self.nav_buttons.append(item)
        nav.addStretch()
        action("Папка законов", self.open_laws, nav, icon=self.style().standardIcon(QStyle.SP_DirOpenIcon))
        nav.addWidget(label(AUTHOR))
        outer.addWidget(sidebar)
        sidebar.setVisible(not embedded)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(*((0, 0, 0, 0) if embedded else (22, 22, 22, 14)))
        layout.setSpacing(14)
        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()
        self.tabs.currentChanged.connect(self.select_section)
        layout.addWidget(self.tabs)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        outer.addWidget(content, 1)
        self.setCentralWidget(root)
        self.preview_id = None
        self.source_values = []
        self.inputs = {}
        self.make_sources()
        self.make_updates()
        self.make_ai()
        self.make_appearance()
        self.make_diagnostics()
        self.header_timer = QTimer(self)
        self.header_timer.setSingleShot(True)
        self.header_timer.setInterval(500)
        self.header_timer.timeout.connect(self.save_header)
        self.sources.horizontalHeader().sectionResized.connect(self.header_changed)
        self.select_section(0)
        self.retry_button = action("Повторить подключение", self.connect_backend, layout)
        self.retry_button.hide()
        self.connect_backend()

    def connect_backend(self):
        self.retry_button.hide()
        self.tabs.setEnabled(False)
        self.status.setText("Подключение к бэкенду…")
        self.task(lambda: (ensure_backend(), rpc("settings"), rpc("sources"),
                           rpc("state_get", {"key": "desktop-sources-header", "default": ""})), self.ready)

    def error(self, message):
        super().error(message)
        if not self.header_ready:
            self.retry_button.show()

    def row(self):
        if self.embedded:
            from .launcher_presentation import FlowLayout
            return FlowLayout()
        return QHBoxLayout()

    def select_section(self, index):
        for i, item in enumerate(self.nav_buttons):
            item.setChecked(i == index)

    def tab(self, title):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        self.tabs.addTab(scroll, title)
        return layout

    def make_sources(self):
        layout = self.tab("Источники")
        heading(layout, "Источники законов", "Добавляйте темы форума, проверяйте документы и управляйте обновлениями.")
        controls = self.row()
        action("Добавить источник", self.add_source, controls, "primary")
        action("Импорт ссылок…", self.import_urls, controls)
        action("Экспорт ссылок…", self.export_urls, controls)
        controls.addStretch()
        action("Обновить список", self.refresh_sources, controls)
        layout.addLayout(controls)
        self.source_filter = QLineEdit()
        self.source_filter.setPlaceholderText("Найти источник по названию или ссылке…")
        self.source_filter.setClearButtonEnabled(True)
        self.source_filter.textChanged.connect(self.filter_sources)
        layout.addWidget(self.source_filter)
        self.sources = QTableWidget(0, 5)
        self.sources.setHorizontalHeaderLabels(["Вкл.", "Название закона", "Ссылка на тему", "Обновлено", "Состояние"])
        self.sources.setSelectionBehavior(QTableWidget.SelectRows)
        self.sources.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sources.setAlternatingRowColors(True)
        self.sources.setShowGrid(False)
        self.sources.setWordWrap(False)
        self.sources.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        self.sources.setVerticalScrollMode(QTableWidget.ScrollPerPixel)
        self.sources.verticalHeader().hide()
        self.sources.verticalHeader().setDefaultSectionSize(46)
        header = self.sources.horizontalHeader()
        # Every divider controls its own left column. Stretch modes make a drag
        # redistribute widths in surprising ways, especially the last column.
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setMinimumSectionSize(64)
        for index, width in enumerate((64, 320, 290, 166, 170)):
            header.resizeSection(index, width)
        header.setToolTip("Потяните правую границу заголовка, чтобы изменить ширину этой колонки. Внизу есть горизонтальная прокрутка.")
        self.sources.itemDoubleClicked.connect(lambda _: self.preview_selected())
        self.sources.itemSelectionChanged.connect(self.update_source_actions)
        layout.addWidget(self.sources, 1)
        line = self.row()
        self.source_actions = [action("Предпросмотр", self.preview_selected, line),
                               action("Изменить…", self.edit_source, line),
                               action("Вкл. / выкл.", self.enable_source, line),
                               action("Версии", self.show_versions, line)]
        line.addStretch()
        self.source_actions.append(action("Удалить", self.delete_source, line, "danger"))
        layout.addLayout(line)
        self.source_count = label("Нет источников. Добавьте ссылку или импортируйте urls.txt.")
        layout.addWidget(self.source_count)
        self.update_source_actions()

    def update_source_actions(self):
        enabled = bool(self.selected_ids())
        for item in getattr(self, "source_actions", []):
            item.setEnabled(enabled)

    def filter_sources(self):
        query = self.source_filter.text().strip().casefold()
        count = 0
        for row, source in enumerate(self.source_values):
            hidden = query not in ((source.get("title") or "") + " " + source["url"] + " " + source["id"]).casefold()
            self.sources.setRowHidden(row, hidden)
            if hidden:
                for col in range(self.sources.columnCount()):
                    self.sources.item(row, col).setSelected(False)
            count += not hidden
        self.source_count.setText(f"Показано {count} из {len(self.source_values)} • Двойной клик — предпросмотр документа")

    def header_changed(self, *_):
        if self.header_ready:
            self.header_timer.start()

    def save_header(self):
        state = bytes(self.sources.horizontalHeader().saveState().toBase64()).decode("ascii")
        self.call("state_set", {"key": "desktop-sources-header", "value": state})

    def open_laws(self):
        if not self.data_dir:
            return self.error("Подождите подключения к бэкенду.")
        try:
            open_folder(self.data_dir)
        except OSError as exc:
            self.error(str(exc))

    def make_updates(self):
        layout = self.tab("Обновление")
        heading(layout, "Обновление базы", "Загрузка идёт в фоне. При ошибке сохраняется последняя успешная версия закона.")
        self.visible_browser = QCheckBox("Открыть браузер для ручной проверки сайта (может переключить фокус игры)")
        layout.addWidget(self.visible_browser)
        controls = self.row()
        action("Обновить выбранные", lambda: self.update(False), controls, "primary")
        action("Все включённые", lambda: self.update(True), controls)
        action("Отменить задачи", self.cancel_jobs, controls, "danger")
        controls.addStretch()
        layout.addLayout(controls)
        tools = self.row()
        action("Экспорт законов в TXT/JSON…", self.export_documents, tools)
        action("Установить Chromium", self.install_browser, tools)
        tools.addStretch()
        layout.addLayout(tools)
        self.update_log = QTextEdit()
        self.update_log.setReadOnly(True)
        self.update_log.setPlaceholderText("Здесь появятся ход загрузки, номера страниц и результаты обновления.")
        layout.addWidget(self.update_log)
        self.last_progress = ""

    def make_ai(self):
        layout = self.tab("ИИ")
        heading(layout, "ИИ и подключение к API", "Настройте провайдера и модель. Ключ хранится отдельно от программы.")
        form = QFormLayout()
        form.setSpacing(16)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.provider = QComboBox()
        self.provider.addItem("OpenRouter / OpenAI-compatible", "openai")
        self.provider.addItem("Gemini", "gemini")
        form.addRow("Провайдер", self.provider)
        for key, title in (("ai_base_url", "Base URL"), ("ai_model", "Модель"), ("ai_api_key", "Новый API-ключ")):
            field = QLineEdit()
            if key == "ai_api_key":
                field.setEchoMode(QLineEdit.Password)
                field.setPlaceholderText("Оставьте пустым, чтобы сохранить существующий ключ")
            self.inputs[key] = field
            form.addRow(title, field)
        for key, title, limits in (("max_tokens", "Максимум токенов ответа", (128, 16000)), ("context_chars", "Бюджет источников (символы)", (4000, 120000))):
            field = QSpinBox()
            field.setRange(*limits)
            self.inputs[key] = field
            form.addRow(title, field)
        layout.addLayout(form)
        self.env_label = QLabel()
        self.env_label.setWordWrap(True)
        layout.addWidget(self.env_label)
        controls = self.row()
        action("Сохранить настройки", self.save_settings, controls, "primary")
        action("Проверить сохранённое соединение", self.test_ai, controls)
        controls.addStretch()
        layout.addLayout(controls)
        self.ai_result = QTextEdit()
        self.ai_result.setReadOnly(True)
        self.ai_result.setPlaceholderText("Результат проверки: провайдер, фактически выбранная модель, ответ или понятная ошибка API.")
        layout.addWidget(self.ai_result)

    def make_appearance(self):
        layout = self.tab("Оформление и клавиши")
        heading(layout, "Оформление и клавиши", "Общие настройки ридера и блокнота для внутриигрового и обычного оверлея.")
        form = QFormLayout()
        form.setSpacing(14)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        for key, title in (("toggle_hotkey", "Показать / скрыть"), ("input_hotkey", "Переключить ввод"),
                           ("notes_hotkey", "Блокнот / новая заметка"),
                           ("prev_law_hotkey", "Предыдущий закон"), ("next_law_hotkey", "Следующий закон"),
                           ("scroll_up_hotkey", "Прокрутка вверх"), ("scroll_down_hotkey", "Прокрутка вниз")):
            self.inputs[key] = QLineEdit()
            form.addRow(title, self.inputs[key])
        font = QSpinBox()
        font.setRange(14, 26)
        self.inputs["font_size"] = font
        form.addRow("Размер текста", font)
        opacity = QDoubleSpinBox()
        opacity.setRange(0.3, 1)
        opacity.setSingleStep(0.05)
        self.inputs["opacity"] = opacity
        form.addRow("Прозрачность", opacity)
        self.theme = QComboBox()
        self.theme.addItem("Тёмная", "dark")
        self.theme.addItem("Светлая", "light")
        form.addRow("Тема оверлея", self.theme)
        self.animations = QCheckBox("Плавное появление и скрытие (0,2 с)")
        form.addRow("Анимация", self.animations)
        self.inputs["notes_dir"] = QLineEdit()
        self.inputs["notes_dir"].setPlaceholderText("По умолчанию: Notes рядом с проектом / программой")
        form.addRow("Папка заметок (полный путь)", self.inputs["notes_dir"])
        layout.addLayout(form)
        layout.addWidget(label("Esc: ввод → просмотр → скрыть. Для обычного оверлея изменение горячих клавиш требует перезапуска клиента."))
        controls = self.row()
        action("Сохранить настройки", self.save_settings, controls, "primary")
        action("Запустить обычный оверлей", self.launch_overlay, controls)
        controls.addStretch()
        layout.addLayout(controls)
        layout.addStretch()

    def make_diagnostics(self):
        layout = self.tab("Диагностика")
        heading(layout, "Диагностика", "Версия сервиса, состояние базы и проверка браузера парсера.")
        controls = self.row()
        action("Обновить состояние", lambda: self.call("health", done=self.show_health), controls, "primary")
        action("Проверить Chromium", lambda: self.start_job("browser_check", done=self.show_health), controls)
        controls.addStretch()
        layout.addLayout(controls)
        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        layout.addWidget(self.diagnostics)

    def show_health(self, value):
        if value.get("data_dir"):
            self.data_dir = value["data_dir"]
        self.diagnostics.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))

    def ready(self, values):
        health, settings, sources, header_state = values
        self.show_health(health)
        self.apply_settings(settings)
        self.show_sources(sources)
        if isinstance(header_state, str) and len(header_state) < 10000:
            header = self.sources.horizontalHeader()
            header.restoreState(QByteArray.fromBase64(header_state.encode("ascii", errors="ignore")))
            # Enforce independent resizing even if a prior state used Stretch.
            header.setStretchLastSection(False)
            header.setSectionResizeMode(QHeaderView.Interactive)
        self.header_ready = True
        self.tabs.setEnabled(True)
        self.retry_button.hide()
        self.status.setText(f"Бэкенд подключён • версия {health['app_version']} • законов: {health['documents']}")

    def apply_settings(self, values):
        for key, field in self.inputs.items():
            if key == "ai_api_key":
                field.clear()
            elif isinstance(field, QLineEdit):
                field.setText(str(values.get(key, DEFAULTS.get(key, ""))))
            else:
                field.setValue(values.get(key, 15))
        self.provider.setCurrentIndex(max(0, self.provider.findData(values.get("ai_provider"))))
        self.theme.setCurrentIndex(max(0, self.theme.findData(values.get("theme", "dark"))))
        self.animations.setChecked(values.get("animations", True))
        self.env_label.setText("Ключ: " + ("настроен" if values.get("has_api_key") else "не задан") +
                               ". Переопределения из окружения/.env: " + (", ".join(values.get("env_overrides", [])) or "нет"))

    def save_settings(self):
        values = {key: field.text() if isinstance(field, QLineEdit) else field.value() for key, field in self.inputs.items()}
        values["ai_provider"] = self.provider.currentData()
        values["theme"] = self.theme.currentData()
        values["animations"] = self.animations.isChecked()
        try:
            bindings = [parse_hotkey(value) for key, value in values.items() if key.endswith("_hotkey")]
            if len(set(bindings)) != len(bindings) or any(value[1] == 27 for value in bindings):
                raise ValueError("Клавиши должны отличаться друг от друга и от Esc")
        except ValueError as exc:
            self.error(str(exc))
            return
        def saved(result):
            self.apply_settings(result)
            self.status.setText("Настройки сохранены")
            self.settings_saved.emit(result)
        self.call("settings_save", values, saved)

    def show_sources(self, values):
        selected = set(self.selected_ids())
        self.source_values = values
        self.sources.blockSignals(True)
        self.sources.clearSelection()
        self.sources.setRowCount(len(values))
        for row, source in enumerate(values):
            title = " ".join((source["title"] or source["id"]).replace("\ufeff", "").replace("\u200b", "").replace("\u2800", " ").split())
            for prefix in ("ЗАКРЕПЛЕНО ", "ИНФОРМАЦИЯ "):
                title = title.removeprefix(prefix)
            updated = source.get("updated") or "—"
            try:
                updated = datetime.fromisoformat(updated.replace("Z", "+00:00")).astimezone().strftime("%d.%m.%Y %H:%M")
            except ValueError:
                pass
            status = source["status"] or ("Локальная копия" if source.get("updated") else "Не загружен")
            for col, text in enumerate(("Да" if source["enabled"] else "Нет", title, source["url"], updated, status)):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.UserRole, source["id"])
                item.setToolTip(str(source.get("updated") or "Не обновлялся") if col == 3 else str(text))
                if col == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.sources.setItem(row, col, item)
                if source["id"] in selected:
                    item.setSelected(True)
        self.sources.blockSignals(False)
        self.filter_sources()
        self.update_source_actions()

    def selected_ids(self):
        return sorted({item.data(Qt.UserRole) for item in self.sources.selectedItems() if item.data(Qt.UserRole)})

    def refresh_sources(self):
        self.call("sources", done=self.show_sources)

    def add_source(self):
        url, ok = QInputDialog.getText(self, "Новый источник", "Ссылка на тему (сначала будет предпросмотр):")
        if ok and url.strip():
            self.preview(url.strip())

    def preview_selected(self):
        ids = self.selected_ids()
        if ids:
            source = next(item for item in self.source_values if item["id"] == ids[0])
            self.preview(source["url"])

    def edit_source(self):
        ids = self.selected_ids()
        if ids:
            source = next(item for item in self.source_values if item["id"] == ids[0])
            url, ok = QInputDialog.getText(self, "Изменить источник", "Новая ссылка (после предпросмотра заменит прежнюю):", text=source["url"])
            if ok and url.strip():
                self.preview(url.strip(), replaces=source["id"])

    def preview(self, url, replaces=None):
        def ready(result, job_id):
            dialog = self.preview_dialog(result)
            if dialog.exec() == QDialog.Accepted:
                self.call("preview_save", {"job_id": job_id, "replaces": replaces}, lambda _: self.refresh_sources())

        def started(value):
            self.preview_id = value["job_id"]
            ident = self.preview_id
            self.jobs[ident] = (lambda result: ready(result, ident), self.progress, lambda: None)
            self.status.setText("Загрузка предпросмотра…")
        self.call("preview", {"url": url, "visible": self.visible_browser.isChecked()}, started)

    def preview_dialog(self, result):
        dialog = QDialog(self)
        dialog.setWindowTitle("Предпросмотр • " + result["document"]["title"])
        dialog.resize(900, 650)
        layout = QVBoxLayout(dialog)
        reader = browser()
        if self.embedded:
            from .documents import document_html
            from .launcher_presentation import DOCUMENT_STYLE
            reader.setHtml(document_html(result["document"], stylesheet=DOCUMENT_STYLE))
        else:
            reader.setHtml(result["html"])
        layout.addWidget(reader)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить источник")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog

    def import_urls(self):
        path, _ = QFileDialog.getOpenFileName(self, "Импорт ссылок", str(ROOT / "local" / "urls.txt"), "Текст (*.txt)")
        if path:
            from pathlib import Path
            self.task(lambda: rpc("sources_import", {"text": Path(path).read_text(encoding="utf-8-sig")}),
                      lambda values: (self.refresh_sources(), self.status.setText(f"Ссылок: {len(values)}, ошибок: {sum('error' in v for v in values)}")))

    def export_urls(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт ссылок", "urls.txt", "Текст (*.txt)")
        if path:
            from .parser import atomic_text
            text = "\n".join(s["url"] for s in self.source_values) + "\n"
            self.task(lambda: atomic_text(path, text), lambda _: self.status.setText("Ссылки экспортированы"))

    def enable_source(self):
        ids = self.selected_ids()
        source_values = list(self.source_values)
        def change():
            for ident in ids:
                source = next(s for s in source_values if s["id"] == ident)
                rpc("source_update", {"id": ident, "enabled": not source["enabled"]})
        self.task(change, lambda _: self.refresh_sources())

    def delete_source(self):
        ids = self.selected_ids()
        if ids and QMessageBox.question(self, "Удалить ссылки?", "Скачанные документы останутся в базе. Удалить выбранные ссылки?") == QMessageBox.Yes:
            self.task(lambda: [rpc("source_update", {"id": ident, "delete": True}) for ident in ids], lambda _: self.refresh_sources())

    def show_versions(self):
        ids = self.selected_ids()
        if ids:
            self.call("versions", {"id": ids[0]}, lambda values: QMessageBox.information(self, "Версии документа", "\n".join(v["updated"] + "  " + v["digest"][:12] for v in values) or "Нет сохранённых версий"))

    def progress(self, value):
        text = value.get("message", "")
        if "page" in value:
            text = f"{value['page']}/{value['pages']}: {text}"
        if text and text != self.last_progress:
            self.last_progress = text
            self.update_log.append(html.escape(text))
            self.status.setText(text)

    def update(self, all_sources):
        ids = None if all_sources else self.selected_ids()
        if ids == []:
            return self.error("Выберите источники на первой вкладке")
        self.tabs.setCurrentIndex(1)
        self.start_job("update", {"ids": ids, "visible": self.visible_browser.isChecked()},
                       lambda results: (self.refresh_sources(), self.status.setText(f"Завершено. Успешно: {sum(r['ok'] for r in results)}, ошибок: {sum(not r['ok'] for r in results)}")), self.progress)

    def export_documents(self):
        path = QFileDialog.getExistingDirectory(self, "Экспорт TXT/JSON и index.csv")
        if path:
            self.start_job("export", {"directory": path}, lambda count: self.status.setText(f"Экспортировано документов: {count}"))

    def test_ai(self):
        self.start_job("ai_test", done=lambda result: self.ai_result.setPlainText(json.dumps(result, ensure_ascii=False, indent=2)),
                       progress=lambda value: self.ai_result.setPlainText(value.get("answer", value.get("message", "Проверка…"))))

    def launch_overlay(self):
        args = ["overlay"] if getattr(sys, "frozen", False) else ["-m", "amazing_overlay", "overlay"]
        QProcess.startDetached(sys.executable, args, str(ROOT))

    def install_browser(self):
        args = ["install-browser"] if getattr(sys, "frozen", False) else ["-m", "amazing_overlay", "install-browser"]
        QProcess.startDetached(sys.executable, args, str(ROOT))

    def closeEvent(self, event):
        if self.header_timer.isActive():
            self.header_timer.stop()
            self.save_header()
        self.cancel_jobs()
        self.timer.stop()
        self.closed = True
        super().closeEvent(event)


class OverlayWindow(RemoteWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Amazing • Законы")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(660, 540)
        self.setMinimumSize(430, 280)
        self.mode = "view"
        self.game_hwnd = 0
        self.current_id = None
        self.current_doc = None
        self.search_generation = 0
        self.load_generation = 0
        self.saving = False
        self.restoring = True
        self.state = {"favorites": [], "recent": []}
        self.messages = []
        self.notebook = None
        self.notes_open = False
        self.notes_beside = False
        self.ai_busy = False
        self.drag_offset = None
        self.root = QWidget()
        layout = QVBoxLayout(self.root)
        self.top = QWidget()
        top = QHBoxLayout(self.top)
        top.setContentsMargins(0, 0, 0, 0)
        title = QLabel("AMAZING  /  ЗАКОНЫ")
        top.addWidget(title)
        self.top.installEventFilter(self)
        title.installEventFilter(self)
        self.list_button = button("Список", lambda: self.toggle_panel(self.left), top)
        button("Блокнот", self.toggle_notebook, top)
        self.beside_button = QCheckBox("Рядом")
        self.beside_button.toggled.connect(self.set_notes_beside)
        top.addWidget(self.beside_button)
        button("Чат", lambda: self.toggle_panel(self.chat), top)
        button("↗", self.expand, top)
        button("Настройки", self.manager, top)
        button("×", self.hide_overlay, top)
        layout.addWidget(self.top)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по всем законам…")
        layout.addWidget(self.search)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(150)
        self.debounce.timeout.connect(self.run_search)
        self.search.textChanged.connect(self.query_changed)
        self.splitter = QSplitter()
        self.left = QWidget()
        self.left.setMinimumWidth(240)
        left = QVBoxLayout(self.left)
        self.only_favorites = QCheckBox("Избранное")
        self.only_favorites.toggled.connect(self.fill_documents)
        left.addWidget(self.only_favorites)
        self.law_list = QListWidget()
        self.law_list.setWordWrap(True)
        self.law_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.law_list.currentItemChanged.connect(self.select_item)
        left.addWidget(self.law_list)
        self.splitter.addWidget(self.left)
        self.middle = QWidget()
        self.middle.setMinimumWidth(280)
        middle = QVBoxLayout(self.middle)
        middle.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("База загружается…")
        self.title.setWordWrap(True)
        middle.addWidget(self.title)
        tools = QHBoxLayout()
        self.favorite = button("☆", self.toggle_favorite, tools)
        self.outline = QComboBox()
        self.outline.setMinimumWidth(120)
        self.outline.activated.connect(self.jump_outline)
        tools.addWidget(self.outline, 1)
        button("↑", lambda: self.find(False), tools)
        button("↓", lambda: self.find(True), tools)
        button("Источник", self.open_original, tools)
        middle.addLayout(tools)
        self.reader = browser()
        middle.addWidget(self.reader)
        self.splitter.addWidget(self.middle)
        self.chat = QWidget()
        self.chat.setMinimumWidth(330)
        chat = QVBoxLayout(self.chat)
        self.scope = QComboBox()
        self.scope.addItems(["Вся база", "Текущий закон", "Выбранные документы…"])
        chat.addWidget(self.scope)
        self.history = QTextBrowser()
        self.history.setOpenLinks(False)
        self.history.anchorClicked.connect(self.source_clicked)
        chat.addWidget(self.history)
        self.chat_input = QTextEdit()
        self.chat_input.setPlaceholderText("Вопрос по игровым законам…")
        self.chat_input.setMaximumHeight(100)
        chat.addWidget(self.chat_input)
        chat_buttons = QHBoxLayout()
        self.send = button("Отправить", self.ask_ai, chat_buttons)
        button("Стоп", self.cancel_jobs, chat_buttons)
        button("Повтор", self.retry_ai, chat_buttons)
        button("Копировать", self.copy_ai, chat_buttons)
        chat.addLayout(chat_buttons)
        self.splitter.addWidget(self.chat)
        self.left.hide()
        self.chat.hide()
        layout.addWidget(self.splitter, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(QSizeGrip(self))
        layout.addLayout(bottom)
        self.setCentralWidget(self.root)
        self.hotkeys = Hotkeys(QApplication.instance(), self.hotkey)
        QApplication.instance().aboutToQuit.connect(self.hotkeys.clear)
        QApplication.instance().installEventFilter(self)
        self.scope_timer = QTimer(self)
        self.scope_timer.setInterval(100)
        self.scope_timer.timeout.connect(self.update_hotkey_scope)
        self.scope_timer.start()
        self.state_timer = QTimer(self)
        self.state_timer.setSingleShot(True)
        self.state_timer.setInterval(500)
        self.state_timer.timeout.connect(self.save_state)
        self.settings_poll_pending = False
        self.settings_timer = QTimer(self)
        self.settings_timer.setInterval(1500)
        self.settings_timer.timeout.connect(self.refresh_settings)
        self.settings_timer.start()
        self.reader.verticalScrollBar().valueChanged.connect(self.schedule_save)
        self.setup_tray()
        self.documents = []
        self.task(lambda: (ensure_backend(), rpc("settings"), rpc("documents"), rpc("state_get", {"key": "overlay", "default": {}})), self.ready)

    def toggle_notebook(self, checked=False, force=False):
        if not self.notebook:
            from .notebook_ui import NotebookWidget
            self.notebook = NotebookWidget()
            QApplication.instance().aboutToQuit.connect(self.notebook.flush)
            self.notebook.attach.connect(self.attach_note)
            self.splitter.addWidget(self.notebook)
            self.notebook.set_text_size(getattr(self, "appearance_settings", DEFAULTS)["font_size"])
        opening = not self.notes_open
        if opening:
            self.notes_previous_panels = (not self.left.isHidden(), not self.chat.isHidden())
        self.notes_open = True if force else not self.notes_open
        if not self.notes_open:
            self.notebook.flush()
        self.notebook.setVisible(self.notes_open)
        self.middle.setVisible(not self.notes_open or self.notes_beside)
        if self.notes_open:
            self.left.hide()
            self.chat.hide()
            self.resize(max(self.width(), 1100 if self.notes_beside else 850), self.height())
        else:
            left, chat = getattr(self, "notes_previous_panels", (False, False))
            self.left.setVisible(left)
            self.chat.setVisible(chat)
        self.search.setVisible(not self.notes_open or self.notes_beside)
        self.schedule_save()

    def set_notes_beside(self, value):
        self.notes_beside = value
        if self.notes_open:
            self.middle.setVisible(value)
            self.search.setVisible(value)
            if value:
                self.resize(max(1100, self.width()), self.height())
        self.schedule_save()

    def attach_note(self, text):
        if self.chat_input.toPlainText().strip():
            if QMessageBox.question(self, "Черновик чата", "Заменить существующий черновик текстом заметки?") != QMessageBox.Yes:
                return
        self.chat_input.setPlainText(text)
        self.chat.show()
        self.resize(max(1200, self.width()), self.height())
        self.chat_input.setFocus()

    def setup_tray(self):
        icon = QApplication.style().standardIcon(QStyle.SP_FileDialogInfoView)
        self.tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        for text, action in (("Показать / скрыть", self.toggle), ("Ввод", self.enter_input), ("Обновить базу", self.refresh), ("Настройки и парсер", self.manager), ("Выход", self.quit)):
            item = menu.addAction(text)
            item.triggered.connect(action)
        self.tray.setContextMenu(menu)
        self.tray.show()
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def ready(self, values):
        _, settings, self.documents, state = values
        self.state.update(state or {})
        self.notes_beside = self.state.get("notes_beside", False)
        self.beside_button.setChecked(self.notes_beside)
        try:
            self.hotkeys.configure(settings)
        except ValueError as exc:
            self.error(str(exc))
            self.tray.showMessage("Горячие клавиши", str(exc))
        self.apply_appearance(settings)
        geometry = self.state.get("geometry")
        if geometry and len(geometry) == 4:
            self.setGeometry(*map(int, geometry))
            if not any(screen.availableGeometry().intersects(self.frameGeometry()) for screen in QApplication.screens()):
                self.move(QApplication.primaryScreen().availableGeometry().topLeft() + QPoint(60, 60))
        self.left.setVisible(self.state.get("left", False))
        self.chat.setVisible(self.state.get("chat", False))
        self.fill_documents()
        ident = self.state.get("document")
        if not any(d["id"] == ident for d in self.documents):
            ident = self.documents[0]["id"] if self.documents else None
        if ident:
            self.load_document(ident, restore_scroll=self.state.get("scroll", 0))
        self.restoring = False
        self.set_mode("view")

    def apply_appearance(self, settings):
        self.appearance_settings = dict(settings)
        animation = getattr(self, "visibility_animation", None)
        if not animation or animation.state() != QAbstractAnimation.Running:
            self.setWindowOpacity(settings["opacity"])
        style = STYLE.replace("font-size:14px", f"font-size:{settings['font_size']}px")
        if settings.get("theme") == "light":
            for dark, light in (("#111827", "#f0f4f8"), ("#e2e8f0", "#182b40"), ("#172235", "#ffffff"),
                                ("#253348", "#d5e3f0"), ("#334155", "#a5b8ca"), ("#354967", "#b9d4ed"),
                                ("#1e293b", "#e2eaf3"), ("#93c5fd", "#185e94")):
                style = style.replace(dark, light)
        self.setStyleSheet(style)
        font = QFont("Segoe UI")
        font.setPixelSize(settings["font_size"])
        self.reader.setFont(font)
        if self.notebook:
            self.notebook.set_text_size(settings["font_size"])
        self.update_mode_hint()

    def refresh_settings(self):
        if self.restoring or self.closed or self.settings_poll_pending:
            return
        self.settings_poll_pending = True
        def fetch():
            try:
                return rpc("settings")
            except Exception:
                return None
        def receive(settings):
            self.settings_poll_pending = False
            if settings and settings != getattr(self, "appearance_settings", {}):
                self.apply_appearance(settings)
                try:
                    self.hotkeys.configure(settings)
                except ValueError as exc:
                    self.error(str(exc))
        self.task(fetch, receive)

    def refresh(self):
        self.call("documents", done=lambda values: (setattr(self, "documents", values), self.fill_documents()))

    def fill_documents(self):
        if not hasattr(self, "documents"):
            return
        self.law_list.blockSignals(True)
        self.law_list.clear()
        favorites, recent = self.state.get("favorites", []), self.state.get("recent", [])
        ordered = sorted(self.documents, key=lambda d: (0 if d["id"] in recent else 1, recent.index(d["id"]) if d["id"] in recent else 0, d["title"]))
        for doc in ordered:
            if self.only_favorites.isChecked() and doc["id"] not in favorites:
                continue
            item = QListWidgetItem(("★ " if doc["id"] in favorites else "") + doc["title"])
            item.setData(Qt.UserRole, {"id": doc["id"]})
            self.law_list.addItem(item)
        self.law_list.blockSignals(False)

    def query_changed(self):
        self.search_generation += 1
        self.debounce.start()

    def run_search(self):
        query, generation = self.search.text().strip(), self.search_generation
        if not query:
            self.fill_documents()
            return

        def show(values):
            if generation != self.search_generation:
                return
            self.left.show()
            self.law_list.blockSignals(True)
            self.law_list.clear()
            for hit in values:
                item = QListWidgetItem(hit["title"] + "\n" + hit["heading"] + "\n" + hit["snippet"])
                item.setData(Qt.UserRole, {"id": hit["doc_id"], "heading": hit["heading"], "post_id": hit["post_id"]})
                self.law_list.addItem(item)
            self.law_list.blockSignals(False)
            self.status.setText(f"Совпадений: {len(values)} • Ввод • Esc → просмотр")
        self.call("search", {"query": query}, show)

    def select_item(self, current, previous):
        if current:
            value = current.data(Qt.UserRole)
            self.load_document(value["id"], value.get("post_id"), value.get("heading"))

    def load_document(self, ident, post_id=None, heading=None, restore_scroll=None):
        self.requested_id = ident
        self.load_generation += 1
        generation = self.load_generation

        def show(result):
            if generation != self.load_generation:
                return
            self.current_id, self.current_doc = ident, result["document"]
            self.title.setText(self.current_doc["title"] + (" • без исходной структуры" if not self.current_doc.get("structured") else ""))
            self.reader.setHtml(result["html"])
            self.outline.clear()
            for section in chunks(self.current_doc):
                self.outline.addItem(section["heading"], {"post_id": section["post_id"], "heading": section["heading"]})
            if post_id:
                self.reader.scrollToAnchor("post-" + post_id)
            if heading:
                self.reader.find(heading)
            if restore_scroll is not None:
                QTimer.singleShot(0, lambda: self.reader.verticalScrollBar().setValue(int(restore_scroll)))
            recent = self.state.get("recent", [])
            self.state["recent"] = [ident] + [value for value in recent if value != ident][:19]
            self.favorite.setText("★" if ident in self.state.get("favorites", []) else "☆")
            self.schedule_save()
        self.call("document", {"id": ident}, show)

    def jump_outline(self, index):
        value = self.outline.itemData(index)
        if value:
            self.reader.moveCursor(QTextCursor.Start)
            self.reader.scrollToAnchor("post-" + value["post_id"])
            self.reader.find(value["heading"])

    def find(self, forward):
        text = self.search.text().strip()
        if text:
            from PySide6.QtGui import QTextDocument
            flag = QTextDocument.FindFlags() if forward else QTextDocument.FindBackward
            if not self.reader.find(text, flag):
                self.reader.moveCursor(QTextCursor.Start if forward else QTextCursor.End)
                self.reader.find(text, flag)

    def toggle_favorite(self):
        if self.current_id:
            favorites = self.state.setdefault("favorites", [])
            if self.current_id in favorites:
                favorites.remove(self.current_id)
            else:
                favorites.append(self.current_id)
            self.favorite.setText("★" if self.current_id in favorites else "☆")
            self.fill_documents()
            self.schedule_save()

    def open_original(self):
        if self.current_doc and self.current_doc.get("url"):
            open_url(self.current_doc["url"])

    def toggle_panel(self, panel):
        panel.setVisible(not panel.isVisible())
        self.schedule_save()

    def expand(self):
        self.resize(1100, 760) if self.width() < 900 else self.resize(660, 540)

    def manager(self):
        args = ["manager"] if getattr(sys, "frozen", False) else ["-m", "amazing_overlay", "manager"]
        QProcess.startDetached(sys.executable, args, str(ROOT))

    def set_mode(self, mode):
        self.mode = mode
        interactive = mode == "input"
        set_input(int(self.winId()), interactive)
        self.drag_offset = None
        self.update_mode_hint()

    def update_mode_hint(self):
        settings = getattr(self, "appearance_settings", DEFAULTS)
        input_key = settings.get("input_hotkey", DEFAULTS["input_hotkey"])
        toggle_key = settings.get("toggle_hotkey", DEFAULTS["toggle_hotkey"])
        self.status.setText(f"Ввод • {input_key} / Esc → просмотр" if self.mode == "input" else
                            f"Просмотр • {input_key} → ввод • {toggle_key} → скрыть")

    def enter_input(self):
        current = foreground()
        if belongs_to_game(current):
            self.game_hwnd = current
        if not self.isVisible():
            self.show()
        self.set_mode("input")
        self.activateWindow()
        activate(int(self.winId()))
        self.search.setFocus()

    def fade_visibility(self, visible):
        settings = getattr(self, "appearance_settings", {})
        target = settings.get("opacity", .96) if visible else 0.0
        animation = getattr(self, "visibility_animation", None)
        if animation:
            animation.stop()
            animation.deleteLater()
        if not settings.get("animations", True):
            self.setWindowOpacity(target)
            if not visible:
                self.hide()
            return
        animation = QPropertyAnimation(self, b"windowOpacity", self)
        self.visibility_animation = animation
        animation.setDuration(200)
        animation.setEasingCurve(QEasingCurve.InOutCubic)
        animation.setStartValue(self.windowOpacity())
        animation.setEndValue(target)
        animation.finished.connect(lambda: self.hide() if self.mode == "hidden" else None)
        animation.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.setWindowOpacity(0)
        self.fade_visibility(True)

    def leave_input(self, return_game=True):
        self.set_mode("view")
        if return_game and self.game_hwnd and foreground() == int(self.winId()):
            activate(self.game_hwnd)

    def hide_overlay(self):
        if self.notebook:
            self.notebook.flush()
        self.save_state()
        self.leave_input()
        self.mode = "hidden"
        self.fade_visibility(False)

    def toggle(self):
        if self.isVisible() and self.mode != "hidden":
            self.hide_overlay()
        else:
            self.set_mode("view")
            if self.isVisible():
                self.fade_visibility(True)
            else:
                self.show()

    def hotkey(self, action):
        current = foreground()
        if native_owns_input(current) or (current == int(self.winId()) and native_owns_input(self.game_hwnd)):
            self.hotkeys.set_active(False)
            if self.isVisible():
                self.hide_overlay()
            if action == "notes":
                open_native_notebook(self.game_hwnd if current == int(self.winId()) else current)
            return
        if current != int(self.winId()) and not belongs_to_game(current):
            return
        if belongs_to_game(current):
            self.game_hwnd = current
        if action == "notes":
            if self.notebook and self.notes_open and self.notebook.editor.hasFocus():
                self.notebook.new_note()
            else:
                self.toggle_notebook(force=True)
                self.enter_input()
                self.notebook.editor.setFocus()
            return
        if action == "toggle":
            self.toggle()
        elif action == "input":
            self.leave_input() if self.mode == "input" else self.enter_input()
        elif action == "escape" and self.isVisible():
            if self.notebook and self.notes_open and self.notebook.escape():
                return
            self.leave_input() if self.mode == "input" else self.hide_overlay()
        elif self.isVisible() and action in {"prev_law", "next_law"}:
            if self.notebook and self.notebook.editor.hasFocus():
                return
            self.switch_law(1 if action == "next_law" else -1)
        elif self.isVisible() and action.startswith("scroll_"):
            if self.notebook and self.notebook.editor.hasFocus():
                return
            bar = self.reader.verticalScrollBar()
            bar.setValue(bar.value() + (100 if action == "scroll_down" else -100))

    def switch_law(self, direction):
        ids = []
        for index in range(self.law_list.count()):
            ident = self.law_list.item(index).data(Qt.UserRole)["id"]
            if ident not in ids:
                ids.append(ident)
        if not ids:
            return
        current = getattr(self, "requested_id", self.current_id)
        index = ids.index(current) if current in ids else -1
        target = ids[max(0, min(len(ids) - 1, index + direction))] if index >= 0 else ids[0]
        if target != current:
            self.load_document(target)

    def update_hotkey_scope(self):
        current = foreground()
        native = native_owns_input(current) or (current == int(self.winId()) and native_owns_input(self.game_hwnd))
        active = not native and (current == int(self.winId()) or belongs_to_game(current)) and QApplication.activeModalWidget() is None
        if native and self.isVisible() and self.mode != "hidden":
            self.hide_overlay()
        try:
            self.hotkeys.set_active(active, self.isVisible())
        except ValueError as exc:
            self.error(str(exc))
            self.tray.showMessage("Горячие клавиши", str(exc))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ApplicationDeactivate and self.mode == "input":
            if self.notebook:
                self.notebook.flush()
            self.leave_input(return_game=False)
            self.mode = "hidden"
            self.hide()
        if watched in {self.top} or (isinstance(watched, QLabel) and watched.parent() is self.top):
            if event.type() == QEvent.MouseButtonPress and self.mode == "input" and event.button() == Qt.LeftButton:
                self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.MouseMove and self.drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self.drag_offset)
                return True
            if event.type() == QEvent.MouseButtonRelease:
                self.drag_offset = None
        return super().eventFilter(watched, event)

    def schedule_save(self, *args):
        if not self.restoring:
            self.state_timer.start()

    def save_state(self):
        if self.restoring or self.closed:
            return
        rect = self.geometry()
        self.state.update(geometry=[rect.x(), rect.y(), rect.width(), rect.height()], document=self.current_id,
                          scroll=self.reader.verticalScrollBar().value(), left=not self.left.isHidden(), chat=not self.chat.isHidden(),
                          notes_beside=self.notes_beside)
        self.call("state_set", {"key": "overlay", "value": dict(self.state)})

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "state_timer"):
            self.schedule_save()

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "state_timer"):
            self.schedule_save()

    def ask_ai(self):
        if self.ai_busy:
            return
        question = self.chat_input.toPlainText().strip()
        if not question:
            return
        if len(question) > 12000:
            return self.error("Предел сообщения — 12000 символов. Выделите меньший фрагмент заметки")
        ids = None
        if self.scope.currentIndex() == 1:
            ids = [self.current_id] if self.current_id else []
        elif self.scope.currentIndex() == 2:
            dialog = QDialog(self)
            dialog.setWindowTitle("Документы для ИИ")
            layout = QVBoxLayout(dialog)
            listing = QListWidget()
            for doc in self.documents:
                item = QListWidgetItem(doc["title"])
                item.setData(Qt.UserRole, doc["id"])
                item.setCheckState(Qt.Unchecked)
                listing.addItem(item)
            layout.addWidget(listing)
            actions = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            actions.accepted.connect(dialog.accept)
            actions.rejected.connect(dialog.reject)
            layout.addWidget(actions)
            if dialog.exec() != QDialog.Accepted:
                return
            ids = [listing.item(i).data(Qt.UserRole) for i in range(listing.count()) if listing.item(i).checkState() == Qt.Checked]
        if ids == []:
            return self.error("Выберите хотя бы один документ")
        history = [{"role": m["role"], "content": m["text"]} for m in self.messages if m["text"] and not m.get("pending") and not m.get("failed")]
        self.messages.append({"role": "user", "text": question})
        response = {"role": "assistant", "text": "", "sources": [], "pending": True}
        self.messages.append(response)
        self.last_question = question
        self.chat_input.clear()
        self.ai_busy = True
        self.send.setEnabled(False)
        self.render_chat()

        def progress(value):
            response["text"] = value.get("answer", response["text"])
            response["sources"] = value.get("sources", response["sources"])
            if value.get("message"):
                self.status.setText(value["message"])
            self.render_chat()

        def done(value):
            response.update(text=value["answer"], sources=value.get("sources", []), usage=value.get("usage", {}))

        def finished():
            response["pending"] = False
            if not response["text"]:
                response["text"] = "Ответ не получен. " + self.status.text()
                response["failed"] = True
            self.ai_busy = False
            self.send.setEnabled(True)
            self.render_chat()
        # Handle a failure to even create a job as well as a failed running job.
        def start():
            return rpc("ai", {"question": question, "history": history, "doc_ids": ids})

        def started(value):
            self.work.discard(work)
            self.jobs[value["job_id"]] = (done, progress, finished)
        def failed(error):
            self.work.discard(work)
            self.error(error)
            finished()
        work = background(start, started, failed)
        self.work.add(work)

    def render_chat(self):
        parts = []
        for index, message in enumerate(self.messages):
            text = html.escape(message["text"]).replace("\n", "<br>")
            valid = {source["label"]: source for source in message.get("sources", [])}
            import re
            text = re.sub(r"\[(S\d+)\]", lambda match: f'<a href="law-source:{index}:{match[1]}">[{match[1]}]</a>' if match[1] in valid else match[0], text)
            parts.append(f"<p><b>{'Вы' if message['role'] == 'user' else 'ИИ'}</b><br>{text or ('Поиск источников…' if message.get('pending') else '')}</p>")
            for source in message.get("sources", []):
                parts.append(f'<p><a href="law-source:{index}:{source["label"]}">[{source["label"]}] {html.escape(source["title"])} — {html.escape(source["heading"])}</a></p>')
            if message.get("usage"):
                parts.append("<p>Использование: " + html.escape(json.dumps(message["usage"], ensure_ascii=False)) + "</p>")
        bar = self.history.verticalScrollBar()
        old_scroll = bar.value()
        at_end = bar.maximum() - bar.value() < 30
        self.history.setHtml("".join(parts))
        if at_end:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(old_scroll)

    def source_clicked(self, url):
        value = url.toString().split(":")
        if len(value) == 3 and value[0] == "law-source":
            try:
                source = next(s for s in self.messages[int(value[1])].get("sources", []) if s["label"] == value[2])
            except (ValueError, IndexError, StopIteration):
                return
            self.load_document(source["doc_id"], source["post_id"], source["heading"])

    def retry_ai(self):
        if not self.ai_busy and getattr(self, "last_question", ""):
            self.chat_input.setPlainText(self.last_question)
            self.ask_ai()

    def copy_ai(self):
        message = next((m for m in reversed(self.messages) if m["role"] == "assistant"), None)
        if message:
            QApplication.clipboard().setText(message["text"])

    def closeEvent(self, event):
        self.hide_overlay()
        event.ignore()

    def quit(self):
        if self.notebook:
            self.notebook.flush()
        self.save_state()
        self.cancel_jobs()
        self.hotkeys.clear()
        self.tray.hide()
        self.closed = True
        QApplication.quit()
