"""Тестовый запуск оверлея на Linux.

Оригинал работает только на Windows (main() + RegisterHotKey через
ctypes.windll). Здесь тестируем только OverlayWindow: сохранение
закона/скролла между hide/show и drag за шапку + QSizeGrip.

Запуск:
    pip install -r requirements_overlay.txt
    python3 test_overlay_linux.py            # ручной тест с окном
    python3 test_overlay_linux.py --smoke    # headless-проверка (offscreen)
"""
from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtWidgets import QApplication

from law_overlay import OverlayWindow, load_config, load_laws


def smoke_test(window: OverlayWindow) -> bool:
    """Без GUI-цикла: hide/show не должны сбрасывать закон и скролл."""
    if not window.filtered:
        print("SMOKE SKIP: нет законов в laws/")
        return True

    window.show()
    window.list.setCurrentRow(0)
    first_law = window.current_law
    if first_law is None:
        print("SMOKE FAIL: current_law не выбрался")
        return False

    # Проскроллить вниз (если контента хватает)
    sb = window.reader.verticalScrollBar()
    sb.setValue(sb.maximum())
    scrolled = sb.value()
    geom_before = window.geometry()

    window.hide()  # hideEvent сохраняет состояние
    saved_law = window._saved_law_filename
    saved_scroll = window._saved_scroll

    window.show_preserved()
    # showEvent ставит скролл через QTimer, обработаем события
    QApplication.processEvents()

    ok_law = saved_law == first_law.filename
    ok_current = (
        window.current_law is not None
        and window.current_law.filename == first_law.filename
    )
    # Геометрия не должна сброситься в центр
    ok_geom = window.geometry() == geom_before

    print(f"saved_law={saved_law} saved_scroll={saved_scroll} scrolled={scrolled}")
    print(f"law saved: {'OK' if ok_law else 'FAIL'}")
    print(f"law restored: {'OK' if ok_current else 'FAIL'}")
    print(f"geometry kept: {'OK' if ok_geom else 'FAIL'}")

    # Интерактивный режим нужен для drag/resize
    window.set_click_through(False)
    grip_ok = window._size_grip is not None and window._size_grip.isVisible()
    print(f"grip visible in focus mode: {'OK' if grip_ok else 'FAIL'}")
    window.set_click_through(True)
    grip_hidden = window._size_grip is not None and not window._size_grip.isVisible()
    print(f"grip hidden in click-through: {'OK' if grip_hidden else 'FAIL'}")

    return all([ok_law, ok_current, ok_geom, grip_ok, grip_hidden])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    if args.smoke:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    app = QApplication(sys.argv)
    config = load_config()
    laws = load_laws()
    window = OverlayWindow(config, laws)
    window.show_preserved()
    # Для ручного теста сразу включаем фокус: drag за шапку + grip
    window.set_click_through(False)

    if args.smoke:
        ok = smoke_test(window)
        print("SMOKE", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    print("Ручной тест: тяни за шапку, ресайз через grip справа внизу.")
    print("Сверни через hide (крестик/Esc) и разверни через show — закон и скролл должны остаться.")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
