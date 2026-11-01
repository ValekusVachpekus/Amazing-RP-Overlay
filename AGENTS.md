# AGENTS.md — Amazing RP Overlay

## Текущая версия v2

Основной код находится в `amazing_overlay/` (бэкенд, парсер, менеджер,
внешний клиент) и `native/` (ASI/DLL). Архитектура и запуск описаны в `docs/V2.md`.
`Release/` — готовый переносимый комплект; `scripts/publish_release.py --check`
проверяет его целостность. Нельзя исключать из Git бинарные зависимости Release,
в том числе `.pyd`, DLL и встроенный Playwright.

Рабочие данные находятся в `%LOCALAPPDATA%\AmazingLawOverlay\v2`.
Корневые `laws/`, `config.json`, `urls.txt` — локальные данные для совместимости,
они игнорируются Git. Публичный пример источников: `examples/urls.txt`.
Все исходные клиенты устанавливают `requirements_backend.txt`;
`requirements.txt` — совместимая ссылка на этот список.
Диагностика API доступна в менеджере и в настройках ASI.

Проверки v2: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
с отдельным `AMAZING_OVERLAY_PIPE` и `QT_QPA_PLATFORM=offscreen`;
`ctest --test-dir native/out-local --output-on-failure` после локальной нативной сборки.

Ниже описаны сохранённые компоненты **v1**. Старые точки входа при обычном
запуске перенаправляют в v2; классы прежнего окна остаются для регрессионного smoke.

## Суть проекта
Оверлей для игры Amazing Online (RP): быстрый доступ к законам/кодексам
с форума прямо поверх игры + парсер форума + ИИ-помощник по тексту законов.

Два независимых инструмента с общей папкой `laws/`:
1. `playwright_laws_parser.py` — качает темы форума в `laws/*.txt` + `laws/index.csv`.
2. `law_overlay.py` — Windows-оверлей на PySide6: always-on-top окно,
   click-through режим, поиск по базе, ИИ-чат.

Вспомогательное:
- Диагностика ИИ — в менеджере v2 (устаревший `debugAI.py` удалён из Git).
- `test_overlay_linux.py` — запуск только `OverlayWindow` на Linux
  (без `main()` и без глобальных хоткеев).
- `urls.txt` — входной список тем форума для парсера.

## Структура файлов
- `law_overlay.py` — весь оверлей (~1500 строк, без разбиения на модули).
- `playwright_laws_parser.py` — парсер XenForo через Playwright Chromium.
- `config.json` — хоткеи, размер окна, AI provider/base_url/model. Без секретов.
- `.env` / `.env.example` — секреты: `AI_API_KEY`, дубли `AI_PROVIDER/BASE_URL/MODEL`.
- `laws/<thread_id>__<title>.txt` — тексты законов, `laws/index.csv` — индекс.
- `run_overlay.bat` / `run_parser.bat` — создание `.venv` + установка + запуск.
- `requirements_backend.txt` — общие зависимости; прежние отдельные списки удалены.
- `.browser-profile/` — persistent Chromium profile (куки JS-защиты), не коммитить.
- `debug/` — скриншоты/HTML при непроходе JS-защиты, не коммитить.

## Формат данных laws/
- Имя: `<thread_id>__<title>.txt`, `thread_id` из URL `threads/...<id>/`.
- Шапка файла от парсера:
  ```
  <title>
  ===...
  Источник: <url>
  Страниц темы: N
  Сообщений: M
  [Сообщение i | Автор: x] / текст / ==== разделитель
  ```
- `index.csv`: `url;title;file;pages;posts;status`, кодировка `utf-8-sig`.
- Оверлей умеет работать и без `index.csv`: подхватывает все `laws/*.txt`,
  `law_id/title` выводит из имени файла.

## Архитектура оверлея (law_overlay.py)
- `load_config()` — merge `DEFAULT_CONFIG` + `config.json`, затем override
  из env/`.env` (`AI_*`). Ключ хранится только в `.env`, в `config.json` не пишется.
- `OverlayWindow(QMainWindow)` — флаги
  `FramelessWindowHint | WindowStaysOnTopHint | Tool` + `WA_ShowWithoutActivating`.
  - Режимы: `set_click_through(True)` = `WS_EX_TRANSPARENT`, клики идут в игру;
    `False` = интерактивный (поиск, чат, drag/resize).
  - Состояние hide/show (только между свернуть/развернуть, не между запусками):
    `hideEvent` сохраняет `_saved_law_filename/_saved_scroll`,
    `showEvent` + `show_preserved()` восстанавливают. `toggle()` НЕ перезагружает
    базу и НЕ центрирует. `center_on_screen()` только при `_first_show`.
  - `populate(restore_filename, preserve_current=True)` — сохраняет выбор;
    поиск (`filter_laws`) вызывает с `preserve_current=False` (прыжок на топ-результат).
  - `select_law` при `_restoring_state` не сбрасывает скролл в 0.
  - Drag: только в фокусе, за `self.top_bar` (`_header_hit` + mousePress/Move/Release).
    Клики по кнопкам/инпутам drag не начинают. В компактном режиме drag за `compact_bar`.
  - Resize: свободный, только в фокусе, через `QSizeGrip` справа внизу.
  - Сворачиваемые панели (состояние НЕ сохраняется между запусками):
    список (`toggle_left_panel`, кнопка ◀/▶), чат (`toggle_chat`, кнопка AI/▶),
    компактный режим «только ридер» (`toggle_compact_mode`, кнопки ▲/▼).
    Анимация только панелей: `_animate_max_width` (`QPropertyAnimation`,
    180мс, `InOutCubic`).
- `AppController` — владеет окном, треем, хоткеями, `IndexWorker`.
  `toggle()` = hide/show без сброса. `reindex()` сохраняет поиск/выбор.
- `GlobalHotkey` — отдельный `threading.Thread` на хоткей через Win32
  `RegisterHotKey/GetMessageW`. ID с 1000. Сигналы `activated/registration_failed`.
- `SettingsDialog` — хоткеи, ширина/высота/скорость скролла, AI provider/key/base_url/model.
- `IndexWorker(QThread)` — только прогрев файлового кэша, настоящего индекса нет.
- `AIWorker(QThread)` — `openai`-совместимый путь использует настройки модели;
  Gemini-путь использует захардкоженную модель (см. Known quirks).

## Хоткеи по умолчанию (config.json)
- toggle `Ctrl+L`, search `Alt+F`, scroll `Alt+Up/Down` (speed 100px),
  prev/next law `Alt+Left/Right`, `Esc` = hide.
- `Alt+F` переключает click-through <-> интерактив.

## Конфиг
- `config.json`: `hotkeys.*`, `window.{width,height,opacity}`, `scroll_speed`,
  `ai_provider/ai_base_url/ai_model`, `close_on_original_click`.
- `.env`: `AI_API_KEY` + дубли провайдера. Приоритет env > config.json.

## Запуск
Windows (прод):
- `run_overlay.bat` — venv + `requirements_backend.txt` + внешний клиент v2.
- `run_parser.bat` — venv + `requirements_backend.txt` + Chromium + CLI парсера v2.
Linux (только тест окна):
- `.venv/bin/python test_overlay_linux.py` — ручной тест.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python test_overlay_linux.py --smoke` — headless.
- Системным python не запускать (нет deps); Windows-путь `main()` на Linux упадет — так и должно.

## Правила для агентов
- Проверки после правок: `.venv/bin/python -m py_compile law_overlay.py`
  и smoke выше. GUI-логику Windows-хоткеев на Linux не чинить — они там не работают.
- Кодировки: новые `txt` писать в `utf-8`, `index.csv` читать/писать `utf-8-sig`,
  разделитель `;`. Фолбэк чтения `cp1251` уже есть в `read_text_file`.
- Не коммитить: `.venv/`, `.browser-profile/`, `debug/`, `.env`, `__pycache__/`.
- Также не коммитить локальные законы, `config.json`, корневой `urls.txt`
  и установщики Python; готовые бинарные зависимости находятся только в `Release/`.
- Окно frameless: любые новые кнопки в шапке должны оставаться кликабельными
  (drag игнорирует `QPushButton/QLineEdit/QComboBox`).
- Не возвращать центрирование при каждом show и сброс списка в `toggle()` —
  это уже починено, не регрессировать.

## Known quirks (не ломать молча, чинить явно)
- `filter_laws` читает все файлы на каждый ввод — на большой базе фризит UI.
- Gemini в `AIWorker` игнорирует выбранную модель, всегда `gemini-3.5-flash-lite`.
- `receive_chat_message` удаляет плейсхолдер «ИИ думает...» через `BlockUnderCursor` — хрупко.
- `GlobalHotkey.activated` эмитится из не-Qt потока.
