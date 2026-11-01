# Amazing Law Overlay 2

Новая версия разделена на бэкенд, окно управления источниками и настройками,
внешний оверлей и экспериментальный x86 ASI/DLL-клиент.

- `run_manager.bat` — парсер, источники, ИИ и настройки.
- `run_overlay.bat` — внешний клиент; сам подключает/запускает бэкенд.
- `run_backend.bat` — самостоятельный сервис.
- `build_native.bat` — сборка ASI/DLL для Direct3D 9.
- `build_portable.bat` — сборка обоих клиентов и переносимого комплекта в `Release/`.

**[Готовый комплект для скачивания и запуска — Release](Release/README.md)**.
Передавайте всю папку `Release`, включая `_internal`; Python пользователям не нужен.

**[Запуск, архитектура и ограничения v2](docs/V2.md)** ·
[Локальный API](docs/PROTOCOL.md) · [Проверка в игре](docs/GAME_ACCEPTANCE.md)

DLL ещё требует приёмки непосредственно в Amazing: standalone-тест не подтверждает отсутствие АФК.

---

Ниже сохранена историческая документация v1; для новых запусков используйте инструкции v2.

LAW OVERLAY (legacy)

1. Put these files into the SAME folder that contains the parser's `laws` directory:

   law_overlay.py
   requirements_overlay.txt
   config.json
   run_overlay.bat
   run_overlay_console.bat

2. Run `run_overlay.bat`.

The program creates its own `.venv`, installs PySide6, then launches in the background.

Default hotkey:
    Ctrl + Shift + L

Controls:
    Ctrl + Shift + L  - show/hide overlay
    Esc              - hide overlay
    Alt + F           - enable interaction and focus search
    Alt + F           - return to click-through mode
    Double click law - open it in the reader
    "Open original"  - open the forum thread in the browser

When the overlay is shown, it is click-through by default, so mouse clicks
reach the application below it. Use the configured search hotkey to interact
with the overlay. The window is centered again after opening or closing the
AI assistant panel.

The overlay reads:
    laws\index.csv
and:
    laws\*.txt

The URL for "Open original" comes from `index.csv`.

AI settings are available through the gear button. Select `OpenAI-compatible`,
choose a model from the list (or type a custom model), and save. API credentials
are stored in `.env`, not in `config.json`. Copy `.env.example` to `.env` for
manual setup. The default endpoint is `https://api.openai.com/v1`. NVIDIA and
other OpenAI-compatible services can be used by changing `Base URL` and model.

To change the hotkey:
    tray -> Hotkey
or click `⚙` inside the overlay.

To refresh after the parser creates new laws:
    tray -> Update laws
or the `Обновить` button in the overlay.

AI providers:
    Open the settings and select "OpenAI-compatible / NVIDIA".
    Enter the API key, Base URL and model. For the NVIDIA example use:
    Base URL: https://integrate.api.nvidia.com/v1
    Model:    nvidia/nemotron-3-super-120b-a12b

The OpenAI-compatible provider uses the `openai` Python package and supports
NVIDIA NIM and other APIs that implement `/chat/completions`.

Recommended folder structure:

    C:\Users\ilusha\Desktop\подсказка\
        amazing_laws_parser.py
        law_overlay.py
        requirements_overlay.txt
        config.json
        run_overlay.bat
        laws\
            index.csv
            1043294__...
            ...
