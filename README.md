LAW OVERLAY

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
enter the API key, choose a model from the list (or type a custom model), and
save. The default endpoint is `https://api.openai.com/v1`. NVIDIA and other
OpenAI-compatible services can be used by changing `Base URL` and the model.

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
