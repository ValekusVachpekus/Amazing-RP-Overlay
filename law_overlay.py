from __future__ import annotations

import urllib.request
import urllib.error
import csv
import ctypes
from ctypes import wintypes
import html
import json
import os
import re
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv, set_key

from PySide6.QtCore import QObject, QThread, Qt, Signal, QTimer, QPoint, QPropertyAnimation, QEasingCurve
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
    QSizeGrip,
)

BASE_DIR = Path(__file__).resolve().parent
LAWS_DIR = BASE_DIR / "laws"
INDEX_FILE = LAWS_DIR / "index.csv"
CONFIG_FILE = BASE_DIR / "config.json"
ENV_FILE = BASE_DIR / ".env"

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
    "ai_provider": "openai",
    "ai_base_url": "https://api.openai.com/v1",
    "ai_model": "gpt-4o-mini",
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
    load_dotenv(ENV_FILE, override=False)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for section, values in data.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section].update(values)
        else:
            merged[section] = values
    legacy_api_key = data.pop("ai_api_key", "")
    env_values = dotenv_values(ENV_FILE)
    if legacy_api_key and not env_values.get("AI_API_KEY"):
        set_key(str(ENV_FILE), "AI_API_KEY", legacy_api_key)
        os.environ["AI_API_KEY"] = legacy_api_key
        env_values["AI_API_KEY"] = legacy_api_key
    if "ai_provider" not in data and legacy_api_key:
        merged["ai_provider"] = "gemini"
    merged["ai_api_key"] = os.getenv("AI_API_KEY", env_values.get("AI_API_KEY", ""))
    merged["ai_provider"] = os.getenv("AI_PROVIDER", merged.get("ai_provider", "gemini"))
    merged["ai_base_url"] = os.getenv("AI_BASE_URL", merged.get("ai_base_url", ""))
    merged["ai_model"] = os.getenv("AI_MODEL", merged.get("ai_model", ""))
    config_to_save = dict(merged)
    config_to_save.pop("ai_api_key", None)
    if legacy_api_key or not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            json.dumps(config_to_save, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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


def ai_markdown_to_html(text: str) -> str:
    """Render the safe Markdown subset commonly returned by chat models."""
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    in_code = False
    code_lines = []
    list_type = None

    def close_list():
        nonlocal list_type
        if list_type:
            out.append(f"</{list_type}>")
            list_type = None

    def inline_markup(value: str) -> str:
        escaped = html.escape(value, quote=False)
        code_parts = []

        def save_code(match):
            code_parts.append(f"<code>{match.group(1)}</code>")
            return f"\x00{len(code_parts) - 1}\x00"

        escaped = re.sub(r"`([^`\n]+)`", save_code, escaped)
        escaped = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", escaped)
        escaped = re.sub(r"~~(.+?)~~", r"<del>\1</del>", escaped)
        escaped = re.sub(r"(?<!\w)(\*|_)([^\n]+?)\1(?!\w)", r"<em>\2</em>", escaped)
        for index, code in enumerate(code_parts):
            escaped = escaped.replace(f"\x00{index}\x00", code)
        return escaped

    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                out.append(f"<pre><code>{html.escape(chr(10).join(code_lines), quote=False)}</code></pre>")
                code_lines = []
                in_code = False
            else:
                close_list()
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        stripped = line.strip()
        if not stripped:
            close_list()
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        ordered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        quote = re.match(r"^>\s?(.*)$", stripped)

        if heading:
            close_list()
            level = len(heading.group(1)) + 1
            out.append(f"<h{level}>{inline_markup(heading.group(2))}</h{level}>")
        elif bullet or ordered:
            current_type = "ul" if bullet else "ol"
            if list_type != current_type:
                close_list()
                list_type = current_type
                out.append(f"<{list_type}>")
            item_text = bullet.group(1) if bullet else ordered.group(1)
            out.append(f"<li>{inline_markup(item_text)}</li>")
        else:
            close_list()
            if quote:
                out.append(f"<blockquote>{inline_markup(quote.group(1))}</blockquote>")
            else:
                out.append(f"<p>{inline_markup(stripped)}</p>")

    if in_code:
        out.append(f"<pre><code>{html.escape(chr(10).join(code_lines), quote=False)}</code></pre>")
    close_list()
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
      self.resize(560, 560)

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
      self.api_key_input.setPlaceholderText("API-ключ провайдера")
      self.api_key_input.setEchoMode(QLineEdit.PasswordEchoOnEdit)
      self.provider_input = QComboBox()
      self.provider_input.addItem("Gemini", "gemini")
      self.provider_input.addItem("OpenAI-compatible / NVIDIA", "openai")
      provider = self.config.get("ai_provider", "gemini")
      provider_index = self.provider_input.findData(provider)
      self.provider_input.setCurrentIndex(max(0, provider_index))

      self.base_url_input = QLineEdit(
          self.config.get("ai_base_url", DEFAULT_CONFIG["ai_base_url"])
      )
      self.base_url_input.setPlaceholderText("https://api.example.com/v1")
      self.model_input = QComboBox()
      self.model_input.setEditable(True)
      self.model_input.addItems([
          "gpt-4o-mini",
          "gpt-4o",
          "gpt-4.1-mini",
          "gpt-4.1",
          "o3-mini",
      ])
      self.model_input.setCurrentText(
          self.config.get("ai_model", DEFAULT_CONFIG["ai_model"])
      )
      self.model_input.setToolTip(
          "Выберите модель OpenAI или введите название модели совместимого провайдера"
      )

      form.addRow("Провайдер ИИ:", self.provider_input)
      form.addRow("API ключ ИИ:", self.api_key_input)
      form.addRow("Base URL:", self.base_url_input)
      form.addRow("Модель:", self.model_input)

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

      if not 640 <= w <= 5000 or not 400 <= h <= 3000 or not 10 <= spd <= 2000:
          QMessageBox.warning(
              self,
              "Ошибка",
              "Допустимые значения: ширина 640-5000, высота 400-3000, скорость 10-2000.",
          )
          return

      provider = self.provider_input.currentData()
      base_url = self.base_url_input.text().strip().rstrip("/")
      model = self.model_input.currentText().strip()
      if provider == "openai" and (not base_url or not model):
          QMessageBox.warning(self, "Ошибка", "Для OpenAI нужны Base URL и модель.")
          return

      self.config["hotkeys"] = new_hotkeys
      self.config.setdefault("window", {})["width"] = w
      self.config.setdefault("window", {})["height"] = h
      self.config["scroll_speed"] = spd
      self.config["ai_provider"] = provider
      self.config["ai_base_url"] = base_url
      self.config["ai_model"] = model

      env_values = {
          "AI_API_KEY": self.api_key_input.text().strip(),
          "AI_PROVIDER": provider,
          "AI_BASE_URL": base_url,
          "AI_MODEL": model,
      }
      for key, value in env_values.items():
          set_key(str(ENV_FILE), key, value)
          os.environ[key] = value

      config_to_save = dict(self.config)
      config_to_save.pop("ai_api_key", None)
      CONFIG_FILE.write_text(
          json.dumps(config_to_save, ensure_ascii=False, indent=2),
          encoding="utf-8",
      )
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
        self.is_click_through = True
        # Состояние между свернуть/развернуть (не между перезапусками)
        self._saved_law_filename: str | None = None
        self._saved_scroll: int = 0
        self._restoring_state: bool = False
        self._first_show: bool = True
        # Drag за шапку (только в фокусе)
        self._drag_offset: QPoint | None = None
        self._size_grip: QSizeGrip | None = None
        # Сворачиваемые панели (состояние сбрасывается при перезапуске)
        self._left_collapsed: bool = False
        self._left_width: int = 390
        self._chat_width: int = 400
        self._compact_mode: bool = False
        self._panel_anim: QPropertyAnimation | None = None
        self._chat_anim: QPropertyAnimation | None = None

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
        self.setMinimumSize(640, 400)

        self.build_ui()

    def set_click_through(self, enabled: bool):
        """Переключает прохождение кликов сквозь окно на Windows."""
        if sys.platform != "win32" or not self.winId():
            self.is_click_through = enabled
            if hasattr(self, "status") and self.status is not None:
                self.status.setText("Только просмотр" if enabled else "Интерактивный режим")
            if getattr(self, "_size_grip", None) is not None:
                self._size_grip.setVisible(not enabled)
            self._drag_offset = None
            return

        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        get_style = user32.GetWindowLongW
        set_style = user32.SetWindowLongW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_long
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        set_style.restype = ctypes.c_long

        ex_style = get_style(hwnd, -20)
        if enabled:
            ex_style |= 0x20
        else:
            ex_style &= ~0x20
        ex_style |= 0x80000
        set_style(hwnd, -20, ex_style)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
        self.is_click_through = enabled
        self.status.setText("Только просмотр" if enabled else "Интерактивный режим")
        # В click-through клики идут в игру, grip и drag не нужны
        if self._size_grip is not None:
            self._size_grip.setVisible(not enabled)
        self._drag_offset = None

    def build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.top_bar = QWidget()
        self.top_bar.setObjectName("topBar")
        self.top_bar.setCursor(Qt.ArrowCursor)
        top = QHBoxLayout(self.top_bar)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        self.btn_toggle_list = QPushButton("◀")
        self.btn_toggle_list.setObjectName("ghostButton")
        self.btn_toggle_list.setFixedSize(36, 32)
        self.btn_toggle_list.setToolTip("Скрыть/показать список законов")
        self.btn_toggle_list.clicked.connect(self.toggle_left_panel)
        top.addWidget(self.btn_toggle_list)
        title = QLabel("Zakonka by Vladislav_Shetkov")
        title.setObjectName("windowTitle")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        top.addWidget(title)
        top.addStretch()

        self.status = QLabel("Готово")
        self.status.setObjectName("status")
        top.addWidget(self.status)

        self.btn_chat = QPushButton("AI")
        self.btn_chat.setObjectName("ghostButton")
        self.btn_chat.setFixedSize(50, 32)
        self.btn_chat.setToolTip("Показать/скрыть ИИ-помощника")
        self.btn_chat.clicked.connect(self.toggle_chat)
        top.addWidget(self.btn_chat)

        self.btn_compact = QPushButton("▲")
        self.btn_compact.setObjectName("ghostButton")
        self.btn_compact.setFixedSize(36, 32)
        self.btn_compact.setToolTip("Компактный режим: только текст закона")
        self.btn_compact.clicked.connect(self.toggle_compact_mode)
        top.addWidget(self.btn_compact)

        settings = QPushButton("⚙")
        settings.setObjectName("ghostButton")
        settings.setFixedSize(36, 32)
        settings.setToolTip("Горячая клавиша")
        settings.clicked.connect(self.open_settings)
        top.addWidget(settings)

        close = QPushButton("✕")
        close.setObjectName("ghostButton")
        close.setFixedSize(36, 32)
        close.clicked.connect(self.hide)
        top.addWidget(close)
        layout.addWidget(self.top_bar)

        # Тонкая полоса компактного режима (только ридер + кнопка развернуть)
        self.compact_bar = QWidget()
        self.compact_bar.setObjectName("compactBar")
        compact_layout = QHBoxLayout(self.compact_bar)
        compact_layout.setContentsMargins(2, 0, 2, 0)
        compact_layout.setSpacing(6)
        self.btn_expand = QPushButton("▼")
        self.btn_expand.setObjectName("ghostButton")
        self.btn_expand.setFixedSize(36, 28)
        self.btn_expand.setToolTip("Вернуть панели")
        self.btn_expand.clicked.connect(self.toggle_compact_mode)
        compact_layout.addWidget(self.btn_expand)
        self.compact_title = QLabel("")
        self.compact_title.setObjectName("compactTitle")
        compact_layout.addWidget(self.compact_title, 1)
        self.compact_bar.hide()
        layout.addWidget(self.compact_bar)

        self.search_bar = QWidget()
        self.search_bar.setObjectName("searchBar")
        search_row = QHBoxLayout(self.search_bar)
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(8)
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
        layout.addWidget(self.search_bar)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(6)

        self.left_panel = QFrame()
        self.left_panel.setObjectName("leftPanel")
        self.left_panel.setMinimumWidth(0)
        left_l = QVBoxLayout(self.left_panel)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(6)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.currentItemChanged.connect(self.select_law)
        left_l.addWidget(self.list)
        self.count = QLabel()
        self.count.setObjectName("count")
        left_l.addWidget(self.count)
        self.splitter.addWidget(self.left_panel)

        self.right_panel = QFrame()
        self.right_panel.setObjectName("rightPanel")
        right_l = QVBoxLayout(self.right_panel)
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
        self.btn_prev.setObjectName("ghostButton")
        self.btn_prev.setFixedSize(28, 28)
        self.btn_prev.clicked.connect(self.find_prev)
        
        self.btn_next = QPushButton("▼")
        self.btn_next.setObjectName("ghostButton")
        self.btn_next.setFixedSize(28, 28)
        self.btn_next.clicked.connect(self.find_next)
        
        nav_layout.addWidget(QLabel("Поиск:"))
        nav_layout.addWidget(self.btn_prev)
        nav_layout.addWidget(self.btn_next)
        self.nav_widget.hide() # Скрываем, пока нет активного поиска
        
        header.addWidget(self.nav_widget)
        header.addWidget(self.original)
        right_l.addLayout(header)

        self.reader = QTextBrowser()
        self.reader.setOpenLinks(False)
        self.reader.anchorClicked.connect(
            lambda url: webbrowser.open(url.toString())
        )
        right_l.addWidget(self.reader, 1)
        self.splitter.addWidget(self.right_panel)
        self.splitter.setSizes([390, 790])

        # Обертка для сплиттера и чата
        self.main_h_layout = QHBoxLayout()
        self.main_h_layout.setSpacing(8)
        self.main_h_layout.addWidget(self.splitter, 1)

        # --- ПАНЕЛЬ ИИ ЧАТА ---
        self.chat_panel = QFrame()
        self.chat_panel.setObjectName("chatPanel")
        self.chat_panel.setMinimumWidth(0)
        self.chat_panel.setMaximumWidth(0)
        chat_l = QVBoxLayout(self.chat_panel)
        chat_l.setContentsMargins(0, 0, 0, 0)
        chat_l.setSpacing(6)
        
        chat_header = QHBoxLayout()
        chat_title = QLabel("ИИ Помощник")
        chat_title.setObjectName("chatTitle")
        chat_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        chat_header.addWidget(chat_title, 1)
        self.btn_hide_chat = QPushButton("▶")
        self.btn_hide_chat.setObjectName("ghostButton")
        self.btn_hide_chat.setFixedSize(32, 28)
        self.btn_hide_chat.setToolTip("Скрыть чат")
        self.btn_hide_chat.clicked.connect(self.toggle_chat)
        chat_header.addWidget(self.btn_hide_chat)
        chat_l.addLayout(chat_header)

        self.chat_history = QTextBrowser()
        self.chat_history.setHtml("""
            <style>
                p { margin: 5px 0; color: #e5e7eb; }
                h2, h3, h4 { color: #fbbf24; margin: 10px 0 5px; }
                ul, ol { margin: 4px 0 6px 18px; color: #e5e7eb; }
                li { margin: 2px 0; }
                blockquote {
                    margin: 6px 0;
                    padding-left: 10px;
                    border-left: 3px solid #6b7280;
                    color: #cbd5e1;
                }
                code {
                    background: #24272e;
                    color: #fbbf24;
                    padding: 1px 4px;
                }
                pre {
                    background: #0b0d10;
                    border: 1px solid #34373f;
                    padding: 8px;
                    white-space: pre-wrap;
                }
            </style>
            <div style='color:#9ca3af'>Чат готов. ИИ видит открытый сейчас закон.</div>
        """)
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

        self.main_h_layout.addWidget(self.chat_panel)
        layout.addLayout(self.main_h_layout, 1)

        # Ручка свободного ресайза (видна только в фокусе)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch()
        self._size_grip = QSizeGrip(self)
        self._size_grip.setVisible(False)
        self._size_grip.setToolTip("Тяните, чтобы изменить размер")
        grip_row.addWidget(self._size_grip)
        layout.addLayout(grip_row)

        self.setStyleSheet("""
        QWidget#root { background:#1a1d22; border:1px solid #43464f; border-radius:10px; }
        QWidget#topBar { background:transparent; }
        QWidget#compactBar { background:#22252c; border:1px solid #3a3d44; border-radius:8px; }
        QWidget#searchBar { background:transparent; }
        QFrame#leftPanel, QFrame#rightPanel, QFrame#chatPanel {
            background:transparent; border:none;
        }

        QLabel { color: #e5e7eb; }
        QLabel#windowTitle { color:#f3f4f6; font-size:15px; letter-spacing:0.3px; }
        QLabel#status, QLabel#count { color:#9ca3af; font-size:12px; }
        QLabel#lawTitle { color:#ffffff; font-size:16px; font-weight:bold; }
        QLabel#chatTitle { color:#f3f4f6; }
        QLabel#compactTitle { color:#d1d5db; font-size:13px; }

        QLineEdit {
            background:#22252c; border:1px solid #43464f;
            border-radius:10px; padding:9px 12px; color:#f3f4f6;
            font-size:14px; selection-background-color:#fbbf24; selection-color:#111316;
        }
        QLineEdit:focus { border:1px solid #fbbf24; background:#24272f; }
        QLineEdit::clear-button { background:transparent; }

        QPushButton {
            background:#262931; border:1px solid #43464f;
            border-radius:9px; padding:7px 11px; color:#e5e7eb;
            font-weight:600;
        }
        QPushButton:hover { background:#30343d; border-color:#5b606b; }
        QPushButton:pressed { background:#1f2227; }
        QPushButton:disabled { color:#6b7280; background:#22252b; border-color:#34373f; }
        QPushButton#ghostButton {
            background:#22252c; border:1px solid transparent;
            border-radius:8px; font-weight:bold;
        }
        QPushButton#ghostButton:hover { background:#30343d; border-color:#43464f; }

        QListWidget {
            background:#1f2227; alternate-background-color:#252931;
            border:1px solid #3a3d44;
            border-radius:10px; color:#e5e7eb;
            outline:none; font-size:13px; padding:4px;
        }
        QListWidget::item {
            padding:9px 10px; border:none; border-radius:7px; margin:1px 2px;
        }
        QListWidget::item:hover { background:#2a2e36; }
        QListWidget::item:selected {
            background:#32363f;
            border-left:3px solid #fbbf24;
        }

        QTextBrowser {
            background:#121418; border:1px solid #3a3d44;
            border-radius:10px; color:#e5e7eb;
            padding:12px; font-family:"Segoe UI"; font-size:14px;
        }
        QTextBrowser::selection { background-color:#fbbf24; color:#111316; }

        QSplitter::handle { background:#2c3038; border-radius:3px; }
        QSplitter::handle:horizontal { width:6px; margin:0 2px; }
        QSplitter::handle:hover { background:#fbbf24; }
        """)

        self.populate()
        self.set_click_through(True)

        self.esc = QShortcut(QKeySequence("Esc"), self)
        self.esc.activated.connect(self.hide)

    def populate(self, restore_filename: str | None = None, preserve_current: bool = True):
      # Сохраняем выбор, чтобы свернуть/развернуть не сбрасывал закон.
      # Для поиска preserve_current=False — показываем лучший результат (row 0).
      if preserve_current and restore_filename is None and self.current_law is not None:
          restore_filename = self.current_law.filename
      self.list.blockSignals(True)
      try:
          self.list.clear()
          for law, count in self.filtered:
              # Оставляем только приписку с количеством, без перекраски всей строки
              if count > 0 and self.search.text().strip():
                  item = QListWidgetItem(f"[{count}] {law.title}")
              else:
                  item = QListWidgetItem(law.title)
                  
              item.setData(Qt.UserRole, law)
              self.list.addItem(item)
      finally:
          self.list.blockSignals(False)

      self.count.setText(f"Показано: {len(self.filtered)} из {len(self.laws)}")
      if self.filtered:
          restore_row = -1
          if restore_filename:
              for row, (law, _count) in enumerate(self.filtered):
                  if law.filename == restore_filename:
                      restore_row = row
                      break
          if restore_row >= 0:
              # Тот же закон: выбираем без сброса скролла через select_law
              self._restoring_state = True
              try:
                  self.list.setCurrentRow(restore_row)
              finally:
                  self._restoring_state = False
          else:
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
                self.populate(preserve_current=False)
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
            self.populate(preserve_current=False)

    def select_law(self, current, previous):
            if current is None:
                return
            law = current.data(Qt.UserRole)
            if not isinstance(law, Law):
                return

            self.current_law = law
            self.law_title.setText(law.title)
            if hasattr(self, "compact_title"):
                self.compact_title.setText(law.title)
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
            if self._restoring_state:
                # Восстановление после свернуть/развернуть: та же страница
                if self._saved_scroll:
                    QTimer.singleShot(
                        0,
                        lambda v=self._saved_scroll: self.reader.verticalScrollBar().setValue(v),
                    )
                if terms:
                    self.nav_widget.show()
                else:
                    self.nav_widget.hide()
                return
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
        current_filename = self.current_law.filename if self.current_law else None
        self.laws = load_laws()
        query = self.search.text().strip()
        if query:
            # filter_laws сам вызовет populate на лучший результат
            self.filter_laws(query)
        else:
            self.filtered = [(law, 0) for law in self.laws]
            self.populate(restore_filename=current_filename)
        self.want_reindex.emit()

    def show_centered(self):
        # Центрируем только при первом показе; дальше сохраняем позицию/размер
        if self._first_show:
            self.center_on_screen()
            self._first_show = False
        self.show()
        self.raise_()
        self.set_click_through(True)

    def show_preserved(self):
        """Показать без сброса позиции, размера, закона и скролла."""
        if self._first_show:
            self.center_on_screen()
            self._first_show = False
        self.show()
        self.raise_()
        self.set_click_through(True)
        if self._saved_scroll:
            QTimer.singleShot(
                0,
                lambda v=self._saved_scroll: self.reader.verticalScrollBar().setValue(v),
            )

    def hideEvent(self, event):
        try:
            self._saved_law_filename = (
                self.current_law.filename if self.current_law else None
            )
            self._saved_scroll = self.reader.verticalScrollBar().value()
        except Exception:
            pass
        self._drag_offset = None
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # Если список пуст из-за внешнего обновления — восстановить выбор
        if (
            self._saved_law_filename
            and self.current_law is None
            and self.filtered
        ):
            self.populate(restore_filename=self._saved_law_filename)
        if self._saved_scroll:
            QTimer.singleShot(
                0,
                lambda v=self._saved_scroll: self.reader.verticalScrollBar().setValue(v),
            )

    def _header_hit(self, pos) -> bool:
        """Клик по шапке (не по кнопкам), только в фокусе."""
        if self.is_click_through:
            return False
        bars = []
        if getattr(self, "_compact_mode", False) and hasattr(self, "compact_bar"):
            bars.append(self.compact_bar)
        elif hasattr(self, "top_bar"):
            bars.append(self.top_bar)
        for bar in bars:
            try:
                origin = bar.mapTo(self, QPoint(0, 0))
                in_x = origin.x() <= pos.x() <= origin.x() + bar.width()
                in_y = origin.y() <= pos.y() <= origin.y() + bar.height()
                if in_x and in_y:
                    child = self.childAt(pos)
                    if isinstance(child, (QPushButton, QLineEdit, QComboBox)):
                        return False
                    return True
            except Exception:
                continue
        return False

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self._header_hit(event.position().toPoint())
        ):
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.LeftButton
            and not self.is_click_through
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        if not self.isVisible():
            self.show_preserved()
            self.set_click_through(False)
        elif not self.is_click_through:
            self.set_click_through(True)
            return
        else:
            self.set_click_through(False)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()
        self.search.selectAll()

    def _animate_max_width(self, widget, start: int, end: int, duration: int = 180, slot: str = "panel"):
        """Плавное сворачивание/разворачивание панели (только панели)."""
        attr = "_chat_anim" if slot == "chat" else "_panel_anim"
        prev = getattr(self, attr, None)
        try:
            if prev is not None:
                prev.stop()
        except Exception:
            pass
        anim = QPropertyAnimation(widget, b"maximumWidth", self)
        anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        # Держим ссылку, иначе анимация остановится сборщиком мусора
        setattr(self, attr, anim)
        anim.start()
        return anim

    def toggle_left_panel(self):
        if self._compact_mode:
            self.toggle_compact_mode()
            return
        if self._left_collapsed:
            self.left_panel.setVisible(True)
            self._animate_max_width(self.left_panel, 0, self._left_width)
            self._left_collapsed = False
            self.btn_toggle_list.setText("◀")
        else:
            if self.left_panel.width() > 0:
                self._left_width = self.left_panel.width()
            anim = self._animate_max_width(self.left_panel, self.left_panel.width(), 0)
            self._left_collapsed = True
            self.btn_toggle_list.setText("▶")

            def _hide():
                if self._left_collapsed:
                    self.left_panel.setVisible(False)

            anim.finished.connect(_hide)

    def toggle_chat(self):
        if self._compact_mode:
            self.toggle_compact_mode()
            return
        is_hidden = self.chat_panel.isHidden()
        base_width = int(self.config.get("window", {}).get("width", 1180))
        if is_hidden:
            self.chat_panel.setVisible(True)
            self.chat_panel.setMaximumWidth(0)
            self._chat_anim = self._animate_max_width(
                self.chat_panel, 0, self._chat_width, slot="chat"
            )
            self.resize(base_width + self._chat_width + 10, self.height())
            # Не центрируем: иначе теряется позиция после drag/resize
            if not self.is_click_through:
                self.chat_input.setFocus()
        else:
            anim = self._animate_max_width(
                self.chat_panel, self.chat_panel.width(), 0, slot="chat"
            )
            self._chat_anim = anim
            self.resize(base_width, self.height())

            def _hide():
                self.chat_panel.setVisible(False)

            anim.finished.connect(_hide)

    def toggle_compact_mode(self):
        """Компактный режим: только текст закона + тонкая полоса."""
        self._compact_mode = not self._compact_mode
        if self._compact_mode:
            self.top_bar.hide()
            self.search_bar.hide()
            self.left_panel.hide()
            self.chat_panel.hide()
            title = self.current_law.title if self.current_law else "Законодательство"
            self.compact_title.setText(title)
            self.compact_bar.show()
            self.btn_compact.setText("▼")
        else:
            self.compact_bar.hide()
            self.top_bar.show()
            self.search_bar.show()
            if not self._left_collapsed:
                self.left_panel.show()
                self.left_panel.setMaximumWidth(self._left_width)
            self.btn_compact.setText("▲")

    def center_on_screen(self):
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
            self.move(
                geometry.center().x() - self.width() // 2,
                geometry.center().y() - self.height() // 2,
            )
            
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
      self.chat_history.append(f"<div style='color:#60a5fa; margin-top:8px;'><b>Вы:</b> {html.escape(text)} <br><span style='font-size:10px; color:#9ca3af;'>{mode_msg}</span></div>")
      self.chat_input.clear()
      self.chat_input.setEnabled(False)
      self.btn_send.setEnabled(False)
      self.chat_history.append("<div style='color:#9ca3af;'><i>ИИ думает...</i></div>")

      # Запускаем поток
      api_key = self.config.get("ai_api_key", "")
      self.ai_worker = AIWorker(
          api_key=api_key,
          user_text=text,
          context_text=context_text,
          provider=self.config.get("ai_provider", "gemini"),
          base_url=self.config.get("ai_base_url", DEFAULT_CONFIG["ai_base_url"]),
          model=self.config.get("ai_model", DEFAULT_CONFIG["ai_model"]),
      )
      self.ai_worker.response_ready.connect(self.receive_chat_message)
      self.ai_worker.error_occurred.connect(self.receive_chat_error)
      self.ai_worker.start()


    def receive_chat_message(self, response):
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.removeSelectedText()
        
        html_resp = ai_markdown_to_html(response)
        self.chat_history.append(
            "<div style='color:#fbbf24; margin-bottom:10px;'>"
            f"<b>ИИ:</b><div class='ai-response'>{html_resp}</div></div>"
        )
        self.chat_input.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.chat_input.setFocus()

    def receive_chat_error(self, error_text):
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.removeSelectedText()
        
        self.chat_history.append(f"<div style='color:#f87171; margin-bottom:10px;'><b>Ошибка:</b> {html.escape(error_text)}</div>")
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

    def restart_hotkey(self):
      for hk in getattr(self, "active_hotkeys", []):
          try:
              hk.stop()
          except Exception:
              pass
      time.sleep(0.1)
      self.start_hotkey()

    def apply_hotkey_config(self, config):
        self.config = config
        self.window.config = config
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
              hk.activated.connect(
                  lambda speed=scroll_spd: self.window.isVisible()
                  and self.window.scroll_text(speed)
              )
          elif action == "scroll_up":
              hk.activated.connect(
                  lambda speed=scroll_spd: self.window.isVisible()
                  and self.window.scroll_text(-speed)
              )
          elif action == "next_law":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.switch_law(1))
          elif action == "prev_law":
              hk.activated.connect(lambda: self.window.isVisible() and self.window.switch_law(-1))
          elif action == "search":
              hk.activated.connect(self.window.focus_search)

          hk.registration_failed.connect(self.hotkey_failed)
          hk.start()
          self.active_hotkeys.append(hk)
          base_id += 1

    def quit(self):
        for hotkey in getattr(self, "active_hotkeys", []):
            try:
                hotkey.stop()
            except Exception:
                pass
        self.app.quit()

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
            # Не перезагружаем и не сбрасываем список: закон и страница остаются.
            # Обновление базы — только через «Обновить» / reindex.
            self.window.show_preserved()

    def reindex(self):
        if hasattr(self, "worker") and self.worker.isRunning():
            return

        saved_filename = (
            self.window.current_law.filename
            if self.window.current_law else None
        )
        saved_query = self.window.search.text().strip() if hasattr(self.window, "search") else ""
        self.laws = load_laws()
        self.window.laws = self.laws
        if saved_query:
            # Сохраняем активный поиск при фоновом обновлении
            self.window.filter_laws(saved_query)
            if saved_filename:
                self.window.populate(restore_filename=saved_filename)
        else:
            self.window.filtered = [(law, 0) for law in self.window.laws]
            self.window.populate(restore_filename=saved_filename)
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

class AIWorker(QThread):
    response_ready = Signal(str)
    error_occurred = Signal(str)

    def __init__(
        self,
        api_key: str,
        user_text: str,
        context_text: str,
        provider: str = "gemini",
        base_url: str = "",
        model: str = "",
    ):
        super().__init__()
        self.api_key = api_key
        self.user_text = user_text
        self.context_text = context_text
        self.provider = provider
        self.base_url = base_url
        self.model = model

    def run(self):
        if not self.api_key:
            self.error_occurred.emit("Укажите API-ключ в настройках (значок шестеренки).")
            return

        if self.provider == "openai":
            self.run_openai_compatible()
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

    def run_openai_compatible(self):
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            system_message = (
                "Ты юридический ИИ-помощник. Отвечай кратко, по существу и "
                "опирайся на предоставленный текст закона. Не выдумывай "
                "информацию и каждый ответ подтверждай ссылкой на закон, "
                "номер статьи или пункт."
            )
            user_message = (
                f"Контекст (текст закона):\n{self.context_text[:150000]}\n\n"
                f"Вопрос пользователя: {self.user_text}"
            )
            completion = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.5,
                top_p=1,
                max_tokens=1024,
                stream=False,
            )
            answer = completion.choices[0].message.content
            if not answer:
                raise RuntimeError("Провайдер вернул пустой ответ.")
            self.response_ready.emit(answer)
        except Exception as exc:
            self.error_occurred.emit(f"OpenAI API: {exc}")


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
    # The old window remains importable for regression tests during migration.
    from amazing_overlay.__main__ import main as client_main
    sys.argv.insert(1, "overlay")
    raise SystemExit(client_main())
