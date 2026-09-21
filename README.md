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
    Double click law - open it in the reader
    "Open original"  - open the forum thread in the browser

The overlay reads:
    laws\index.csv
and:
    laws\*.txt

The URL for "Open original" comes from `index.csv`.

To change the hotkey:
    tray -> Hotkey
or click `⚙` inside the overlay.

To refresh after the parser creates new laws:
    tray -> Update laws
or the `Обновить` button in the overlay.

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
