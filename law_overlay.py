from __future__ import annotations

import urllib.request
import urllib.error
import csv
import ctypes
from ctypes import wintypes
import json
import re
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut, QTextDocument, QColor, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QStyle,
    QMenu,
    QSplitter,
)

BASE_DIR = Path(__file__).resolve().parent
LAWS_DIR = BASE_DIR / "laws"
INDEX_FILE = LAWS_DIR / "index.csv"
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "hotkeys": {
        "toggle": {"modifiers": ["ctrl", "shift"], "key": "l"},
        "scroll_down": {"modifiers": ["alt"], "key": "down"},
        "scroll_up": {"modifiers": ["alt"], "key": "up"},
        "next_law": {"modifiers": ["alt"], "key": "right"},
        "prev_law": {"modifiers": ["alt"], "key": "left"},
        "search": {"modifiers": ["alt"], "key": "f"}
    },
    "window": {"width": 1180, "height": 760, "opacity": 0.97},
    "close_on_original_click": False,
    "ai_api_key": "",
}


@dataclass
class Law:
    law_id: str
    title: str
    filename: str
    url: str
    pages: str = ""
    posts: str = ""


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return json.loads(json.dumps(DEFAULT_CONFIG))

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for section, values in data.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section].update(values)
        else:
            merged[section] = values
    return merged


def load_laws() -> list[Law]:
    LAWS_DIR.mkdir(exist_ok=True)
    result: list[Law] = []

    if INDEX_FILE.exists():
        try:
            with INDEX_FILE.open("r", newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    filename = (row.get("file") or "").strip()
                    if not filename:
                        continue

                    path = LAWS_DIR / Path(filename).name
                    if not path.exists():
                        continue

                    title = (row.get("title") or "").strip() or path.stem
                    result.append(
                        Law(
                            law_id=path.stem.split("__", 1)[0],
                            title=title,
                            filename=path.name,
                            url=(row.get("url") or "").strip(),
                            pages=(row.get("pages") or "").strip(),
                            posts=(row.get("posts") or "").strip(),
                        )
                    )
        except Exception:
            result = []

    known = {x.filename for x in result}
    for path in sorted(LAWS_DIR.glob("*.txt")):
        if path.name in known:
            continue
        stem = path.stem
        if "__" in stem:
            law_id, title = stem.split("__", 1)
        else:
            law_id, title = "", stem
        result.append(Law(law_id, title, path.name, ""))

    result.sort(key=lambda x: x.title.casefold())
    return result


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp1251", errors="replace")
    except Exception:
        return ""


def text_to_html(text: str, search_terms: list[str] = None) -> str:
    import html
    out = []
    
    hl_pattern = None
    if search_terms:
        # Сортируем слова по длине (сначала длинные), чтобы корректно выделять корни
        sorted_terms = sorted(search_terms, key=len, reverse=True)
        escaped_terms = [re.escape(t) for t in sorted_terms]
        hl_pattern = re.compile(r'(' + '|'.join(escaped_terms) + r')', re.IGNORECASE)

    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append("<div class='spacer'></div>")
            continue

        # Безопасное выделение искомых слов цветом
        if hl_pattern:
            parts = hl_pattern.split(line)
            escaped_parts = []
            for i, part in enumerate(parts):
                if i % 2 == 1:  # Это найденное слово
                    escaped_parts.append(f"<span style='background-color:#fbbf24;color:#111316;border-radius:2px;'>{html.escape(part)}</span>")
                else:
                    escaped_parts.append(html.escape(part))
            esc = "".join(escaped_parts)
        else:
            esc = html.escape(line)

        if re.match(r"^(Глава|РАЗДЕЛ|Раздел|Статья|СТАТЬЯ)\b", s):
            out.append(f"<h3>{esc}</h3>")
        elif re.match(r"^\[Сообщение \d+", s):
            out.append(f"<div class='meta'>{esc}</div>")
        elif s.startswith("Источник: "):
            url = s[len("Источник: "):].strip()
            safe = html.escape(url, quote=True)
            out.append(f"<div class='source'>Источник: <a href='{safe}'>{safe}</a></div>")
        elif set(s) <= {"="} and len(s) >= 10:
            out.append("<hr>")
        else:
            out.append(f"<div class='law-line'>{esc}</div>")

    return "\n".join(out)


class IndexWorker(QThread):
    progress = Signal(int, int)
    ready = Signal()
    error = Signal(str)

    def __init__(self, laws: list[Law]):
        super().__init__()
        self.laws = laws

    def run(self):
        try:
            # Warm-up/cache: читаем каждый файл один раз, чтобы после запуска
            # поиск работал быстрее на диске с большим числом файлов.
            for i, law in enumerate(self.laws, 1):
                read_text_file(LAWS_DIR / law.filename)
                self.progress.emit(i, len(self.laws))
            self.ready.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class GlobalHotkey(QObject):
    activated = Signal()
    registration_failed = Signal(str)

    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008

    def __init__(self, modifiers: list[str], key: str, hotkey_id: int):
        super().__init__()
        self.modifiers = modifiers
        self.key = key
        self._thread = None
        self._stop = threading.Event()
        self._thread_id = None
        self._hotkey_id = hotkey_id

    @staticmethod
    def key_code(key: str) -> int:
        key = key.strip().lower()
        named = {
            "space": 0x20, "enter": 0x0D, "escape": 0x1B, "esc": 0x1B,
            "tab": 0x09, "backspace": 0x08,
            "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
            "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
            "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B, 
            "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27
        }
        if key in named:
            return named[key]
        if len(key) == 1:
            return ord(key.upper())
        raise ValueError(
            f"Неизвестная клавиша '{key}'. Используй L, F8, Space и т.п."
        )

    @classmethod
    def modifier_mask(cls, modifiers: list[str]) -> int:
        mask = 0
        for modifier in modifiers:
            m = modifier.lower()
            if m in {"ctrl", "control"}:
                mask |= cls.MOD_CONTROL
            elif m == "shift":
                mask |= cls.MOD_SHIFT
            elif m == "alt":
                mask |= cls.MOD_ALT
            elif m in {"win", "windows"}:
                mask |= cls.MOD_WIN
            else:
                raise ValueError(f"Неизвестный модификатор: {modifier}")
        return mask

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="GlobalHotkey", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, self.WM_QUIT, 0, 0
            )

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        try:
            mods = self.modifier_mask(self.modifiers)
            vk = self.key_code(self.key)

            if not user32.RegisterHotKey(None, self._hotkey_id, mods, vk):
                self.registration_failed.emit(
                    "Горячая клавиша уже занята другой программой."
                )
                return

            msg = wintypes.MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(
                    ctypes.byref(msg), None, 0, 0
                )
                if result <= 0:
                    break

                if msg.message == self.WM_HOTKEY and msg.wParam == self._hotkey_id:
                    self.activated.emit()

                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            user32.UnregisterHotKey(None, self._hotkey_id)
        except Exception as exc:
            self.registration_failed.emit(str(exc))
        finally:
            self._thread_id = None


class SettingsDialog(QDialog):
  saved = Signal(dict)

  def __init__(self, config: dict, parent=None):
      super().__init__(parent)
      self.config = json.loads(json.dumps(config))
      self.setWindowTitle("Настройки оверлея")
      self.resize(500, 480)

      self.setStyleSheet("""
        QDialog { background: #17191d; }
        QLabel, QGroupBox { color: #e5e7eb; }
        QComboBox {
            background: #22252b; border: 1px solid #3a3d44;
            border-radius: 4px; padding: 4px 8px; color: #f3f4f6;
        }
        QComboBox QAbstractItemView {
            background: #22252b; color: #f3f4f6;
            selection-background-color: #32363f;
        }
        """)


      layout = QVBoxLayout(self)
      
      from PySide6.QtWidgets import QGridLayout, QFormLayout, QGroupBox
      grid = QGridLayout()
      
      self.actions_info = {
          "toggle": "Скрыть/Показать окно",
          "search": "Активировать поиск",
          "scroll_up": "Прокрутка вверх",
          "scroll_down": "Прокрутка вниз",
          "prev_law": "Предыдущий закон",
          "next_law": "Следующий закон",
      }
      
      self.inputs = {}
      row_idx = 0
      grid.addWidget(QLabel("Действие"), row_idx, 0)
      grid.addWidget(QLabel("Модификаторы"), row_idx, 1)
      grid.addWidget(QLabel("Клавиша"), row_idx, 2)
      row_idx += 1
      
      mod_options = [
          "Нет", "Alt", "Ctrl", "Shift", "Ctrl + Shift", 
          "Ctrl + Alt", "Alt + Shift", "Ctrl + Shift + Alt"
      ]
      self.mod_map = [
          [], ["alt"], ["ctrl"], ["shift"], ["ctrl", "shift"], 
          ["ctrl", "alt"], ["alt", "shift"], ["ctrl", "shift", "alt"]
      ]

      for action_key, label_text in self.actions_info.items():
          grid.addWidget(QLabel(label_text), row_idx, 0)
          mods_combo = QComboBox()
          mods_combo.addItems(mod_options)
          key_input = QLineEdit()
          
          current_cfg = self.config.get("hotkeys", {}).get(action_key, {})
          current_mods = tuple(sorted(current_cfg.get("modifiers", [])))
          
          idx = next((i for i, m in enumerate(self.mod_map) if set(m) == set(current_mods)), 0)
          mods_combo.setCurrentIndex(idx)
          key_input.setText(str(current_cfg.get("key", "")).upper())
          
          self.inputs[action_key] = (mods_combo, key_input)
          grid.addWidget(mods_combo, row_idx, 1)
          grid.addWidget(key_input, row_idx, 2)
          row_idx += 1

      layout.addLayout(grid)

      # --- Новые настройки ---
      extra_group = QGroupBox("Параметры интерфейса")
      form = QFormLayout(extra_group)
      
      self.width_input = QLineEdit(str(self.config.get("window", {}).get("width", 1180)))
      self.height_input = QLineEdit(str(self.config.get("window", {}).get("height", 760)))
      self.scroll_input = QLineEdit(str(self.config.get("scroll_speed", 100)))
      
      form.addRow("Ширина окна (px):", self.width_input)
      form.addRow("Высота окна (px):", self.height_input)
      form.addRow("Скорость прокрутки (px):", self.scroll_input)
      layout.addWidget(extra_group)

      # ИИшка
      self.api_key_input = QLineEdit(self.config.get("ai_api_key", ""))
      self.api_key_input.setPlaceholderText("AI Studio / Gemini API Key")
      self.api_key_input.setEchoMode(QLineEdit.PasswordEchoOnEdit)
      form.addRow("API Ключ ИИ:", self.api_key_input)

      buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
      buttons.accepted.connect(self.save_config)
      buttons.rejected.connect(self.reject)
      layout.addWidget(buttons)

  def save_config(self):
      new_hotkeys = {}
      for action_key, (mods_combo, key_input) in self.inputs.items():
          key_str = key_input.text().strip().lower()
          mods = self.mod_map[mods_combo.currentIndex()]
          if not key_str:
              QMessageBox.warning(self, "Ошибка", f"Не указана клавиша для: {self.actions_info[action_key]}")
              return
          try:
              GlobalHotkey.modifier_mask(mods)
              GlobalHotkey.key_code(key_str)
          except ValueError as exc:
              QMessageBox.warning(self, "Ошибка", str(exc))
              return
          new_hotkeys[action_key] = {"modifiers": mods, "key": key_str}

      try:
          w = int(self.width_input.text())
          h = int(self.height_input.text())
          spd = int(self.scroll_input.text())
      except ValueError:
          QMessageBox.warning(self, "Ошибка", "Размеры и скорость должны быть числами!")
          return

      self.config["hotkeys"] = new_hotkeys
      self.config.setdefault("window", {})["width"] = w
      self.config.setdefault("window", {})["height"] = h
      self.config["scroll_speed"] = spd
      self.config["ai_api_key"] = self.api_key_input.text().strip()

      CONFIG_FILE.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
      self.saved.emit(self.config)
      self.accept()


class OverlayWindow(QMainWindow):
    hotkey_changed = Signal(dict)
    want_reindex = Signal()

    def __init__(self, config: dict, laws: list[Law]):
        super().__init__()
        self.config = config
        self.laws = laws
        # Теперь храним кортежи: (Объект Law, количество совпадений)
        self.filtered = [(law, 0) for law in laws] 
        self.current_law = None

        self.setWindowTitle("Законодательство")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowOpacity(
            float(config["window"].get("opacity", 0.97))
        )
        self.resize(
            int(config["window"].get("width", 1180)),
            int(config["window"].get("height", 760)),
        )

        self.build_ui()

    def build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("ЗАКОНОДАТЕЛЬСТВО")
        title.setObjectName("windowTitle")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        top.addWidget(title)
        top.addStretch()

        self.status = QLabel("Готово")
        self.status.setObjectName("status")
        top.addWidget(self.status)

        self.btn_chat = QPushButton("AI")
        self.btn_chat.setFixedSize(50, 32)
        self.btn_chat.clicked.connect(self.toggle_chat)
        top.addWidget(self.btn_chat)

        settings = QPushButton("⚙")
        settings.setFixedSize(36, 32)
        settings.setToolTip("Горячая клавиша")
        settings.clicked.connect(self.open_settings)
        top.addWidget(settings)

        close = QPushButton("✕")
        close.setFixedSize(36, 32)
        close.clicked.connect(self.hide)
        top.addWidget(close)
        layout.addLayout(top)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Поиск по названиям и тексту законов..."
        )
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_laws)
        search_row.addWidget(self.search, 1)

        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.reload_laws)
        search_row.addWidget(refresh)
        layout.addLayout(search_row)

        splitter = QSplitter(Qt.Horizontal)

        left = QFrame()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.currentItemChanged.connect(self.select_law)
        left_l.addWidget(self.list)
        self.count = QLabel()
        self.count.setObjectName("count")
        left_l.addWidget(self.count)
        splitter.addWidget(left)

        right = QFrame()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.law_title = QLabel("Выберите закон")
        self.law_title.setObjectName("lawTitle") # Добавь эту строку
        self.law_title.setWordWrap(True)
        self.law_title.setFont(QFont("Segoe UI", 13, QFont.Bold))
        header.addWidget(self.law_title, 1)

        self.original = QPushButton("Открыть оригинал ↗")
        self.original.setEnabled(False)
        self.original.clicked.connect(self.open_original)

        # --- БЛОК НАВИГАЦИИ ПО СЛОВАМ ---
        self.nav_widget = QWidget()
        nav_layout = QHBoxLayout(self.nav_widget)
        nav_layout.setContentsMargins(10, 0, 10, 0)
        nav_layout.setSpacing(5)
        
        self.btn_prev = QPushButton("▲")
        self.btn_prev.setFixedSize(28, 28)
        self.btn_prev.clicked.connect(self.find_prev)
        
        self.btn_next = QPushButton("▼")
        self.btn_next.setFixedSize(28, 28)
        self.btn_next.clicked.connect(self.find_next)
        
        nav_layout.addWidget(QLabel("Поиск:"))
        nav_layout.addWidget(self.btn_prev)
        nav_layout.addWidget(self.btn_next)
        self.nav_widget.hide() # Скрываем, пока нет активного поиска
        
        header.addWidget(self.nav_widget)
        header.addWidget(self.original)
        right_l.addLayout(header)

        header.addWidget(self.original)
        right_l.addLayout(header)

        self.reader = QTextBrowser()
        self.reader.setOpenLinks(False)
        self.reader.anchorClicked.connect(
            lambda url: webbrowser.open(url.toString())
        )
        right_l.addWidget(self.reader, 1)
        splitter.addWidget(right)
        splitter.setSizes([390, 790])

        # Обертка для сплиттера и чата
        main_h_layout = QHBoxLayout()
        main_h_layout.addWidget(splitter, 1)

        # --- ПАНЕЛЬ ИИ ЧАТА ---
        self.chat_panel = QFrame()
        self.chat_panel.setFixedWidth(400)
        chat_l = QVBoxLayout(self.chat_panel)
        chat_l.setContentsMargins(0, 0, 0, 0)
        
        chat_title = QLabel("ИИ Помощник")
        chat_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        chat_l.addWidget(chat_title)

        self.chat_history = QTextBrowser()
        self.chat_history.setHtml("<div style='color:#9ca3af'>Чат готов. ИИ видит открытый сейчас закон.</div>")
        chat_l.addWidget(self.chat_history, 1)

        from PySide6.QtWidgets import QCheckBox
        self.chk_all_laws = QCheckBox("Анализировать все законы базы")
        self.chk_all_laws.setStyleSheet("color: #e5e7eb; font-size: 12px; margin-bottom: 4px;")
        chat_l.addWidget(self.chk_all_laws)
        # ---------------------------

        chat_input_row = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Спроси что-нибудь по закону...")
        self.chat_input.returnPressed.connect(self.send_chat_message)
        chat_input_row.addWidget(self.chat_input, 1)

        self.btn_send = QPushButton("➤")
        self.btn_send.setFixedSize(36, 36)
        self.btn_send.clicked.connect(self.send_chat_message)
        chat_input_row.addWidget(self.btn_send)

        chat_l.addLayout(chat_input_row)
        self.chat_panel.hide() # Скрыт по умолчанию

        main_h_layout.addWidget(self.chat_panel)
        layout.addLayout(main_h_layout, 1)

        self.setStyleSheet("""
        QWidget#root { background:#17191d; border:1px solid #3a3d44; }
        
        /* Исправление невидимых текстов */
        QLabel { color: #e5e7eb; } 
        QLabel#windowTitle { color:#f3f4f6; }
        QLabel#status, QLabel#count { color:#9ca3af; }
        QLabel#lawTitle { color:#ffffff; font-size:16px; font-weight:bold; } 

        QLineEdit {
            background:#22252b; border:1px solid #3a3d44;
            border-radius:8px; padding:8px 10px; color:#f3f4f6;
            font-size:14px;
        }
        QLineEdit:focus { border:1px solid #6b7280; }
        
        QPushButton {
            background:#24272e; border:1px solid #3a3d44;
            border-radius:7px; padding:7px 11px; color:#e5e7eb; 
            font-weight:bold; /* Делает стрелки поиска четче */
        }
        QPushButton:hover { background:#2d3139; }
        QPushButton:disabled { color:#737780; }
        
        QListWidget {
            background:#1f2227; alternate-background-color:#262931;
            border:1px solid #34373f;
            border-radius:8px; color:#e5e7eb;
            outline:none; font-size:13px;
        }
        QListWidget::item {
            padding:9px 10px; border-bottom:1px solid #2b2e34;
        }
        QListWidget::item:selected { 
            background:#32363f; 
            border-left: 3px solid #fbbf24; /* Желтая полоска у выбранного закона */
        } 
        
        QTextBrowser {
            background:#111316; border:1px solid #34373f;
            border-radius:8px; color:#e5e7eb;
            padding:12px; font-family:"Segoe UI"; font-size:14px;
        }
        
        /* Заставляет активное слово в поиске выделяться желтым, а не системным белым */
        QTextBrowser::selection {
            background-color: #fbbf24; color: #111316; 
        }
        """)

        self.populate()

        self.esc = QShortcut(QKeySequence("Esc"), self)
        self.esc.activated.connect(self.hide)

    def populate(self):
      self.list.clear()
      for law, count in self.filtered:
          # Оставляем только приписку с количеством, без перекраски всей строки
          if count > 0 and self.search.text().strip():
              item = QListWidgetItem(f"[{count}] {law.title}")
          else:
              item = QListWidgetItem(law.title)
              
          item.setData(Qt.UserRole, law)
          self.list.addItem(item)

      self.count.setText(f"Показано: {len(self.filtered)} из {len(self.laws)}")
      if self.filtered:
          self.list.setCurrentRow(0)
      else:
          self.law_title.setText("Ничего не найдено")
          self.reader.setHtml("<div style='color:#9ca3af;padding:20px'>По запросу ничего не найдено.</div>")
          self.original.setEnabled(False)
          self.nav_widget.hide()

    def filter_laws(self, query: str):
            q = query.strip().casefold()
            if not q:
                self.filtered = [(law, 0) for law in self.laws]
                self.populate()
                return

            terms = [x for x in re.split(r"\s+", q) if x]
            found = []

            for law in self.laws:
                content = read_text_file(LAWS_DIR / law.filename).casefold()
                title_cf = law.title.casefold()

                if all(t in title_cf or t in content for t in terms):
                    # Считаем общее количество совпадений для всех слов запроса
                    count = sum(content.count(t) + title_cf.count(t) for t in terms)
                    found.append((law, count))

            # Сортируем: сначала те, где больше всего совпадений
            found.sort(key=lambda x: x[1], reverse=True)
            self.filtered = found
            self.populate()

    def select_law(self, current, previous):
            if current is None:
                return
            law = current.data(Qt.UserRole)
            if not isinstance(law, Law):
                return

            self.current_law = law
            self.law_title.setText(law.title)
            self.original.setEnabled(bool(law.url))

            text = read_text_file(LAWS_DIR / law.filename)
            if not text:
                self.reader.setHtml("<div style='color:#9ca3af;padding:20px'>Файл пуст.</div>")
                return

            query = self.search.text().strip()
            terms = [x for x in re.split(r"\s+", query) if x] if query else None

            # Передаем искомые слова для выделения желтым
            html_content = f"""
            <style>
            body {{ color:#e5e7eb; line-height:1.45; }}
            h3 {{ color:#f3f4f6; margin-top:18px; }}
            .law-line {{ margin-bottom:3px; }}
            .meta, .source {{ color:#9ca3af; }}
            a {{ color:#93c5fd; }}
            .spacer {{ height:6px; }}
            </style>
            {text_to_html(text, terms)}
            """
            self.reader.setHtml(html_content)
            self.reader.verticalScrollBar().setValue(0)
            
            # Показываем кнопки навигации и прыгаем к первому совпадению
            if terms:
                self.nav_widget.show()
                self.reader.find(terms[0])
            else:
                self.nav_widget.hide()

    def find_next(self):
        query = self.search.text().strip()
        if not query: return
        terms = [x for x in re.split(r"\s+", query) if x]
        if not terms: return
        
        found = self.reader.find(terms[0])
        if not found:
            cursor = self.reader.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.reader.setTextCursor(cursor)
            self.reader.find(terms[0])

    def find_prev(self):
        query = self.search.text().strip()
        if not query: return
        terms = [x for x in re.split(r"\s+", query) if x]
        if not terms: return
        
        found = self.reader.find(terms[0], QTextDocument.FindBackward)
        if not found:
            cursor = self.reader.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.reader.setTextCursor(cursor)
            self.reader.find(terms[0], QTextDocument.FindBackward)


    def open_original(self):
        if self.current_law and self.current_law.url:
            webbrowser.open(self.current_law.url)
            if self.config.get("close_on_original_click", False):
                self.hide()

    def open_settings(self):
        dlg = SettingsDialog(self.config, self)
        dlg.saved.connect(self.hotkey_changed.emit)
        dlg.exec()

    def reload_laws(self):
        self.laws = load_laws()
        self.filtered = list(self.laws)
        self.populate()
        self.want_reindex.emit()

    def show_centered(self):
        screen = QApplication.primaryScreen()
        if screen:
            g = screen.availableGeometry()
            self.move(
                g.center().x() - self.width() // 2,
                g.center().y() - self.height() // 2,
            )
        self.show()
        self.raise_()
        # self.activateWindow()
        # self.search.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def scroll_text(self, delta: int):
        scrollbar = self.reader.verticalScrollBar()
        scrollbar.setValue(scrollbar.value() + delta)

    def switch_law(self, step: int):
        if not self.filtered:
            return
        current_idx = self.list.currentRow()
        new_idx = max(0, min(len(self.filtered) - 1, current_idx + step))
        self.list.setCurrentRow(new_idx)

    def focus_search(self):
        self.activateWindow() # Принудительный перехват фокуса
        self.search.setFocus()
        self.search.selectAll()

    def toggle_chat(self):
      is_hidden = self.chat_panel.isHidden()
      self.chat_panel.setVisible(is_hidden)
      
      # Плавно увеличиваем или уменьшаем ширину окна на 410 пикселей
      w = self.width()
      new_w = w + 410 if is_hidden else w - 410
      self.resize(new_w, self.height())
      
      if is_hidden:
            self.chat_input.setFocus()
            
    def send_chat_message(self):
      text = self.chat_input.text().strip()
      if not text: return

      # Читаем контекст в зависимости от состояния чекбокса
      context_text = ""
      if self.chk_all_laws.isChecked():
          # Собираем текст абсолютно со всех файлов законов
          all_texts = []
          for law in self.laws:
              law_content = read_text_file(LAWS_DIR / law.filename)
              if law_content:
                  all_texts.append(f"=== {law.title} ===\n{law_content}")
          context_text = "\n\n".join(all_texts)
          mode_msg = "<i>(ИИ анализирует всю базу законов)</i>"
      else:
          # Только текущий закон
          if self.current_law:
              context_text = read_text_file(LAWS_DIR / self.current_law.filename)
          mode_msg = "<i>(ИИ видит только текущий закон)</i>"

      # Выводим сообщение пользователя
      self.chat_history.append(f"<div style='color:#60a5fa; margin-top:8px;'><b>Вы:</b> {text} <br><span style='font-size:10px; color:#9ca3af;'>{mode_msg}</span></div>")
      self.chat_input.clear()
      self.chat_input.setEnabled(False)
      self.btn_send.setEnabled(False)
      self.chat_history.append("<div style='color:#9ca3af;'><i>ИИ думает...</i></div>")

      # Запускаем поток
      api_key = self.config.get("ai_api_key", "")
      self.ai_worker = AIWorker(api_key, text, context_text)
      self.ai_worker.response_ready.connect(self.receive_chat_message)
      self.ai_worker.error_occurred.connect(self.receive_chat_error)
      self.ai_worker.start()


    def receive_chat_message(self, response):
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.removeSelectedText()
        
        html_resp = response.replace('\n', '<br>')
        self.chat_history.append(f"<div style='color:#fbbf24; margin-bottom:10px;'><b>ИИ:</b> {html_resp}</div>")
        self.chat_input.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.chat_input.setFocus()

    def receive_chat_error(self, error_text):
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.removeSelectedText()
        
        self.chat_history.append(f"<div style='color:#f87171; margin-bottom:10px;'><b>Ошибка:</b> {error_text}</div>")
        self.chat_input.setEnabled(True)
        self.btn_send.setEnabled(True)


class AppController(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.config = load_config()
        self.laws = load_laws()

        self.window = OverlayWindow(self.config, self.laws)
        self.window.hide()
        self.window.hotkey_changed.connect(self.apply_hotkey_config)
        self.window.want_reindex.connect(self.reindex)

        self.setup_tray()
        self.start_hotkey()
        self.reindex()

    def start_hotkey(self):
      self.active_hotkeys = []
      cfg = self.config.get("hotkeys", {})
      
      base_id = 1000
      for action, keys in cfg.items():
          hk = GlobalHotkey(keys["modifiers"], keys["key"], base_id)
          
          # Привязываем сигналы в зависимости от действия
          if action == "toggle":
              hk.activated.connect(self.toggle)
          elif action == "scroll_down":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.scroll_text(100))
          elif action == "scroll_up":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.scroll_text(-100))
          elif action == "next_law":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.switch_law(1))
          elif action == "prev_law":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.switch_law(-1))
          elif action == "search":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.focus_search())

          hk.registration_failed.connect(self.hotkey_failed)
          hk.start()
          self.active_hotkeys.append(hk)
          base_id += 1

    def restart_hotkey(self):
      for hk in getattr(self, "active_hotkeys", []):
          try:
              hk.stop()
          except Exception:
              pass
      time.sleep(0.1)
      self.start_hotkey()

    def quit(self):
      for hk in getattr(self, "active_hotkeys", []):
          try:
              hk.stop()
          except Exception:
              pass
      self.app.quit()

    def apply_hotkey_config(self, config):
      self.config = config
      # Применяем новые размеры окна сразу после сохранения
      self.window.resize(config["window"]["width"], config["window"]["height"])
      self.restart_hotkey()

    def start_hotkey(self):
      self.active_hotkeys = []
      cfg = self.config.get("hotkeys", {})
      scroll_spd = self.config.get("scroll_speed", 100) # Читаем скорость из конфига
      
      base_id = 1000
      for action, keys in cfg.items():
          hk = GlobalHotkey(keys["modifiers"], keys["key"], base_id)
          
          if action == "toggle":
              hk.activated.connect(self.toggle)
          elif action == "scroll_down":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.scroll_text(scroll_spd))
          elif action == "scroll_up":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.scroll_text(-scroll_spd))
          elif action == "next_law":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.switch_law(1))
          elif action == "prev_law":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.switch_law(-1))
          elif action == "search":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.focus_search())

          hk.registration_failed.connect(self.hotkey_failed)
          hk.start()
          self.active_hotkeys.append(hk)
          base_id += 1

    def hotkey_failed(self, message):
        if self.tray:
            self.tray.showMessage(
                "Law Overlay",
                message,
                QSystemTrayIcon.Warning,
                5000,
            )

    def toggle(self):
        if self.window.isVisible():
            self.window.hide()
        else:
            self.window.laws = load_laws()
            self.window.filtered = [(law, 0) for law in self.window.laws]
            self.window.populate()
            self.window.show_centered()

    def reindex(self):
        if hasattr(self, "worker") and self.worker.isRunning():
            return

        self.laws = load_laws()
        self.window.laws = self.laws
        self.window.filtered = [(law, 0) for law in self.window.laws]
        self.window.populate()
        self.window.status.setText("Проверка файлов...")

        self.worker = IndexWorker(self.laws)
        self.worker.progress.connect(
            lambda i, n: self.window.status.setText(f"Файлы: {i}/{n}")
        )
        self.worker.ready.connect(
            lambda: self.window.status.setText("Готово")
        )
        self.worker.error.connect(
            lambda msg: self.window.status.setText("Ошибка: " + msg)
        )
        self.worker.start()

    def setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        icon = self.app.style().standardIcon(
            QStyle.SP_FileDialogDetailedView
        )
        self.tray = QSystemTrayIcon(icon, self.app)
        self.tray.setToolTip("Законодательство — Ctrl+Shift+L")

        menu = QMenu()
        show = QAction("Открыть", self.app)
        show.triggered.connect(self.toggle)
        menu.addAction(show)

        settings = QAction("Горячая клавиша", self.app)
        settings.triggered.connect(self.window.open_settings)
        menu.addAction(settings)

        menu.addSeparator()

        reindex = QAction("Обновить законы", self.app)
        reindex.triggered.connect(self.reindex)
        menu.addAction(reindex)

        menu.addSeparator()

        quit_action = QAction("Выход", self.app)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.toggle()
            if reason == QSystemTrayIcon.Trigger else None
        )
        self.tray.show()

    def quit(self):
        try:
            self.hotkey.stop()
        except Exception:
            pass
        self.app.quit()


class AIWorker(QThread):
    response_ready = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, api_key: str, user_text: str, context_text: str):
        super().__init__()
        self.api_key = api_key
        self.user_text = user_text
        self.context_text = context_text

    def run(self):
        if not self.api_key:
            self.error_occurred.emit("Укажите API-ключ в настройках (значок шестеренки).")
            return

        # Официальное название новой быстрой модели на 8 миллиардов параметров
        model = "gemini-3.5-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        
        prompt = f"Контекст (текст закона):\n{self.context_text[:150000]}\n\nВопрос пользователя: {self.user_text}"
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": "Ты юридический ИИ-помощник. Отвечай кратко, по существу и опирайся на предоставленный текст закона. При формировании ответа избегай выдумок и не добавляй информацию, которой нет в тексте закона. Каждый ответ подвержай ссылкой на источник (закон, номер статьи, пункт)."}],},
        }
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                result = json.loads(response.read().decode('utf-8'))
                answer = result['candidates'][0]['content']['parts'][0]['text']
                self.response_ready.emit(answer)
                
        except urllib.error.HTTPError as e:
            try:
                err_msg = json.loads(e.read().decode('utf-8'))['error']['message']
            except Exception:
                err_msg = str(e)
            # Выводим точную ошибку от Google (например, 400 API key not valid)
            self.error_occurred.emit(f"API ({e.code}): {err_msg}")
            
        except urllib.error.URLError as e:
            # Выводим системную ошибку сети (как в тестовом файле)
            self.error_occurred.emit(f"Сеть: {e.reason}")
            
        except Exception as e:
            # Отлов любых других сбоев
            self.error_occurred.emit(f"Сбой: {str(e)}")


def main():
    if sys.platform != "win32":
        raise SystemExit("Только Windows.")

    app = QApplication(sys.argv)
    app.setApplicationName("Law Overlay")
    app.setQuitOnLastWindowClosed(False)

    controller = AppController(app)
    app.aboutToQuit.connect(controller.quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
