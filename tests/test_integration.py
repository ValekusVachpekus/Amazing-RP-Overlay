"""Real named-pipe RPC and offscreen Qt windows, isolated from user data."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont

from amazing_overlay.settings import PIPE_NAME, ROOT
from amazing_overlay.transport import rpc


class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from amazing_overlay.ui import STYLE
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
        cls.app.setFont(QFont("Segoe UI", 10))
        cls.app.setStyleSheet(STYLE)
        cls.temporary = tempfile.TemporaryDirectory()
        env = dict(os.environ, AMAZING_OVERLAY_DATA=cls.temporary.name, AMAZING_OVERLAY_PIPE=PIPE_NAME)
        cls.process = subprocess.Popen([sys.executable, "-m", "amazing_overlay", "backend"], cwd=ROOT, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                rpc("health")
                from amazing_overlay.store import Store
                from amazing_overlay.documents import parse_html
                store = Store(Path(cls.temporary.name) / "laws.db")
                store.publish({"schema_version": 1, "id": "test-table", "url": "", "title": "000 • Таблицы", "structured": True,
                               "pages": 1, "posts": [{"post_id": "table", "author": "", "blocks": parse_html(
                                   '<h3>Статья 12. Таблица</h3><table><tr><th rowspan="2">Ведомство</th><th colspan="2">Должности</th></tr><tr><th>Руководитель</th><th>Заместитель</th></tr><tr><td>МВД</td><td>Генерал</td><td>Полковник</td></tr></table>', "")} ]})
                rpc("settings_save", {"notes_dir": str(Path(cls.temporary.name) / "notes")})
                rpc("notes_create", {"path": "Пример.txt", "text": "Мои игровые заметки\n\nСтатья 12 — пример записи.\nМожно читать закон и писать рядом.\n\nРусский текст, поиск, сохранение и отмена изменений."})
                rpc("state_set", {"key": "notebook-native", "value": {"path": "Пример.txt"}})
                return
            except ConnectionError:
                time.sleep(0.1)
        cls.process.terminate()
        output = cls.process.communicate(timeout=5)
        raise AssertionError("Backend did not start: " + repr(output))

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.communicate(timeout=5)
        cls.temporary.cleanup()

    def events_until(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Qt operation timed out")

    def test_roundtrip_and_errors(self):
        health = rpc("health")
        self.assertEqual(health["version"], 1)
        self.assertNotIn("ai_api_key", rpc("settings"))
        rpc("state_set", {"key": "test", "value": {"hello": "Привет"}})
        self.assertEqual(rpc("state_get", {"key": "test"}), {"hello": "Привет"})
        with self.assertRaises(RuntimeError):
            rpc("unknown_method")

    def test_notebook_widget_autosave_search_undo_and_recovery(self):
        from amazing_overlay import notebook_ui
        from PySide6.QtGui import QTextCursor
        root = Path(self.temporary.name) / "Мои заметки"
        rpc("settings_save", {"notes_dir": str(root)})
        value = rpc("notes_create", {"path": "Тест.txt", "text": "Первая строка\nСтатья 12 и статья 13"})
        with patch.object(notebook_ui, "DATA", Path(self.temporary.name)):
            widget = notebook_ui.NotebookWidget()
            widget.resize(1000, 640)
            widget.show()
            try:
                self.events_until(lambda: bool(widget.root_path))
                widget.open_note(value["path"])
                self.events_until(lambda: widget.current is not None and not widget.loading)
                widget.editor.moveCursor(QTextCursor.End)
                widget.editor.insertPlainText("\nДополнение")
                self.events_until(lambda: not widget.dirty and not widget.saving)
                self.assertIn("Дополнение", rpc("notes_read", {"path": value["path"]})["text"])
                widget.find_text.setText("СТАТЬЯ")
                self.assertEqual(len(widget.matches), 2)
                widget.step(1)
                self.assertEqual(widget.editor.textCursor().selectedText().casefold(), "статья")
                widget.find_text.clear()
                widget.editor.undo()
                self.assertNotIn("Дополнение", widget.editor.toPlainText())
                widget.editor.redo()
                self.assertIn("Дополнение", widget.editor.toPlainText())
                widget.save()
                self.events_until(lambda: not widget.dirty and not widget.saving)
                widget.editor.moveCursor(QTextCursor.End)
                widget.editor.insertPlainText("\nЧерновик при разрыве")
                real_rpc = notebook_ui.rpc
                def disconnected(method, params=None):
                    if method == "notes_save":
                        raise ConnectionError("Тест: бэкенд недоступен")
                    return real_rpc(method, params)
                with patch.object(notebook_ui, "rpc", disconnected):
                    widget.save()
                    self.events_until(lambda: not widget.saving)
                drafts = list((Path(self.temporary.name) / 'notes-recovery').glob('*.json'))
                self.assertTrue(drafts)
                self.assertIn('Черновик при разрыве', drafts[0].read_text(encoding='utf-8'))
                widget.grab().save(str(ROOT / ".cache/notebook-qt-preview.png"))
                widget.save()
                self.events_until(lambda: not widget.dirty and not widget.saving)
            finally:
                widget.close()
                widget.deleteLater()

    def test_launcher_window(self):
        from amazing_overlay.launcher import LauncherWindow
        window = LauncherWindow()
        window.show()
        try:
            self.events_until(lambda: 'Бэкенд работает' in window.status.text())
            self.assertIn('Автор: Vladislav_Shetkov', window.windowTitle())
            self.assertFalse(window.start_button.isEnabled())
            self.assertTrue(window.stop_button.isEnabled())
            self.assertIn(window.stop_button.grab().toImage().pixelColor(10, 10).name(),
                          ('#23232f', '#30283f', '#403052'))
            from PySide6.QtGui import QPixmap
            for asset in ('check.svg', 'up.svg', 'down.svg'):
                self.assertFalse(QPixmap(str(ROOT / 'amazing_overlay/assets' / asset)).isNull(), asset)
            with patch('amazing_overlay.launcher.open_folder') as opened:
                window.open_laws()
                opened.assert_called_once_with(self.temporary.name)
            from PySide6.QtWidgets import QLabel
            from PySide6.QtGui import QFont
            title = window.manager_card.findChild(QLabel, 'cardTitle')
            self.assertEqual(title.font().pixelSize(), 17)
            self.assertEqual(title.font().weight(), QFont.Bold)
            for width, columns in ((1440, 3), (980, 2), (760, 1), (1440, 3)):
                window.resize(width, 900)
                self.app.processEvents()
                self.assertEqual(window.apps_grid.columns, columns)
                cards = [window.manager_card, window.overlay_card, window.native_card]
                for first, second in zip(cards, cards[1:]):
                    self.assertFalse(first.geometry().intersects(second.geometry()))
                self.assertLessEqual(window.apps_grid.width(), window.overview.width())
            window.folder.set_path('C:/' + 'Очень длинная папка заметок/' * 40)
            self.app.processEvents()
            self.assertEqual(window.width(), 1440)
            self.assertIn('Очень длинная', window.folder.toolTip())
            with patch.object(window, 'launch') as launch:
                window.nav_buttons['manager'].click()
                window.nav_buttons['overlay'].click()
                window.nav_buttons['settings'].click()
                self.assertEqual(launch.call_args_list, [unittest.mock.call('manager'), unittest.mock.call('overlay'), unittest.mock.call('manager', 'appearance')])
            window.nav_buttons['notes'].click()
            self.assertTrue(window.nav_buttons['notes'].isChecked())
            window.nav_buttons['overview'].click()
            self.assertEqual(window.scroll.verticalScrollBar().value(), 0)
            # Late worker callbacks after closing must not touch a deleted window.
            self.assertTrue(window.grab().save(str(ROOT / '.cache/launcher-preview.png')))
        finally:
            window.timer.stop()
            window.close()

    def test_launcher_states_and_stale_results(self):
        from amazing_overlay import launcher
        calls = []
        health = rpc('health')
        with patch.object(launcher, 'background', side_effect=lambda work, done, failed: calls.append((work, done, failed))):
            window = launcher.LauncherWindow()
            window.timer.stop()
            try:
                self.assertFalse(window.start_button.isEnabled())
                calls[0][2]('Бэкенд не запущен')
                self.assertTrue(window.start_button.isEnabled())
                self.assertFalse(window.online)
                with patch('amazing_overlay.launcher.open_folder') as opened:
                    window.open_laws()
                    opened.assert_called_once()
                window.start_button.click()
                self.assertTrue(window.operation)
                self.assertFalse(window.start_button.isEnabled())
                self.assertFalse(window.stop_button.isEnabled())
                self.assertIn('Запускается', window.badge.text())
                count = len(calls)
                window.start_button.click()
                self.assertEqual(len(calls), count)
                # An old poll may finish after an explicit start operation.
                calls[0][1]((health, {}, ''))
                self.assertFalse(window.online)
                calls[-1][1](health)
                self.assertTrue(window.online)
                window.can_stop = True
                window.update_buttons()
                window.stop_button.click()
                self.assertIn('Останавливается', window.badge.text())
                calls[-1][2]('Не удалось завершить запись')
                self.assertFalse(window.operation)
                self.assertIn('Не удалось', window.notice.text())
                # A notes-folder error must not turn a healthy backend offline.
                window.check()
                self.assertTrue(window.pending)
                calls[0][1]((health, {}, ''))
                self.assertTrue(window.pending)  # A stale reply cannot clear a newer poll.
                calls[-1][1]((health, {}, 'Папка недоступна'))
                self.assertTrue(window.online)
                self.assertIn('Заметки недоступны', window.folder.full_text)
                self.assertIn('Не удалось', window.notice.text())
                window.close()
                calls[-1][1]((health, {}, ''))  # Late reply after close is harmless.
            finally:
                window.close()
        with patch.object(launcher, 'background'), patch.object(sys, 'frozen', True, create=True):
            portable = launcher.LauncherWindow()
            try:
                self.assertNotIn('development', portable.nav_buttons)
                self.assertIsNone(portable.developer_card)
                self.assertEqual(len(portable.secondary_grid.cards), 1)
                self.assertEqual(portable.secondary_grid.columns, 1)
            finally:
                portable.close()

    def test_manager_navigation_reuses_window(self):
        from PySide6.QtCore import QLockFile
        from amazing_overlay import manager_navigation
        from amazing_overlay.ui import ManagerWindow
        window = ManagerWindow()
        server = manager_navigation.ManagerNavigation(window)
        lock = QLockFile(str(Path(self.temporary.name) / "manager.lock"))
        self.assertTrue(lock.tryLock(0))
        try:
            self.assertTrue(server.listening)
            for page, index in (("appearance", 3), ("ai", 2)):
                # A second real CLI process must forward to this existing
                # manager and exit, rather than opening another GUI instance.
                child = subprocess.Popen([sys.executable, "-m", "amazing_overlay", "manager", "--page", page],
                                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         env=dict(os.environ, AMAZING_OVERLAY_DATA=self.temporary.name))
                try:
                    self.events_until(lambda: child.poll() is not None)
                    output, error = child.communicate(timeout=3)
                    self.assertEqual(child.returncode, 0, error.decode(errors="replace"))
                    self.assertEqual(window.tabs.currentIndex(), index)
                    self.assertTrue(window.nav_buttons[index].isChecked())
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.communicate(timeout=3)
            with self.assertRaises(ValueError):
                manager_navigation.send("nonexistent")
        finally:
            lock.unlock()
            server.server.close()
            window.close()
            self.events_until(lambda: not window.work)

    def test_native_dx9_host_and_same_rpc(self):
        binary = ROOT / "native" / "out-local" / "overlay_native_smoke.exe"
        if not binary.exists():
            self.skipTest("Build native/out-local first; game input still requires manual acceptance")
        result = subprocess.run([str(binary), "--self-test"], cwd=binary.parent, timeout=20,
                                env=dict(os.environ, AMAZING_OVERLAY_DATA=self.temporary.name), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        logs = list(Path(self.temporary.name).glob("native-client-*.log"))
        self.assertTrue(logs, "Native startup must produce a diagnostic log")
        log = "\n".join(path.read_text(encoding="utf-8") for path in logs)
        self.assertIn("Hooking game cursor recenter / clip", log)
        self.assertIn("First EndScene callback received", log)
        self.assertIn("Renderer initialized", log)
        capture = ROOT / ".cache/notebook-native-preview.bmp"
        settings = rpc('settings')
        rpc('settings_save', {'input_hotkey': 'F9', 'toggle_hotkey': 'F11'})
        try:
            result = subprocess.run([str(binary), "--self-test", "--notebook", "--tabs"], cwd=binary.parent, timeout=20,
                                    env=dict(os.environ, AMAZING_OVERLAY_DATA=self.temporary.name, AMAZING_OVERLAY_CAPTURE=str(capture)),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        finally:
            rpc('settings_save', {key: settings[key] for key in ('input_hotkey', 'toggle_hotkey')})
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue(capture.exists())

    def test_native_obsidian_design_screens(self):
        """Actual D3D9/ImGui frames; this does not validate game input/focus."""
        binary = ROOT / "native/out-local/overlay_native_smoke.exe"
        if not binary.exists():
            self.skipTest("Build native/out-local first")
        original = rpc("settings")
        rpc("settings_save", {"font_size": 17, "opacity": 1, "theme": "dark"})
        rpc("state_set", {"key": "native", "value": {"left": True, "chat": False, "document": "test-table", "favorites": ["test-table"]}})
        rpc("state_set", {"key": "notebook-native", "value": {"path": "Пример.txt", "beside": False}})
        output = ROOT / ".cache/obsidian-visual"
        output.mkdir(exist_ok=True)
        try:
            for scenario in range(12):
                with self.subTest(scenario=scenario):
                    capture = output / f"{scenario:02}.bmp"
                    result = subprocess.run([str(binary), "--self-test", f"--design={scenario}"],
                                            cwd=binary.parent, timeout=25, capture_output=True,
                                            env=dict(os.environ, AMAZING_OVERLAY_DATA=self.temporary.name,
                                                     AMAZING_OVERLAY_CAPTURE=str(capture)))
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                    self.assertGreater(capture.stat().st_size, 100000)
            # The notebook layout's late first response must not replace the
            # user's explicit choice of "beside" (scenario 4 checks that bit).
            self.assertIn("test-table", rpc("state_get", {"key": "native"})["favorites"])
        finally:
            rpc("settings_save", {key: original[key] for key in ("font_size", "opacity", "theme")})

    def test_manager_offscreen(self):
        from amazing_overlay.ui import ManagerWindow
        window = ManagerWindow()
        try:
            window.show()
            self.events_until(lambda: bool(window.source_values))
            self.assertIn("Автор: Vladislav_Shetkov", window.windowTitle())
            self.assertEqual(window.tabs.count(), 5)
            self.assertGreater(window.sources.rowCount(), 0)
            self.assertIn("Ключ", window.env_label.text())
            output = ROOT / ".cache" / "manager-preview.png"
            output.parent.mkdir(exist_ok=True)
            self.assertTrue(window.grab().save(str(output)))
        finally:
            window.close()
            self.events_until(lambda: not window.work)

    def test_manager_header_drag_filter_and_saved_widths(self):
        from amazing_overlay.ui import ManagerWindow
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        window = ManagerWindow()
        window.show()
        try:
            self.events_until(lambda: window.header_ready)
            header = window.sources.horizontalHeader()
            # Reset prior test preferences; the last divider must resize Status
            # without stealing width from Updated or the URL column.
            for i, width in enumerate((64, 260, 220, 150, 130)):
                header.resizeSection(i, width)
            old = [header.sectionSize(i) for i in range(5)]
            pos = QPoint(header.sectionViewportPosition(4) + old[4] - 1, header.height() // 2)
            QTest.mousePress(header.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
            QTest.mouseMove(header.viewport(), pos + QPoint(48, 0), 50)
            QTest.mouseRelease(header.viewport(), Qt.LeftButton, Qt.NoModifier, pos + QPoint(48, 0))
            self.assertGreater(header.sectionSize(4), old[4])
            self.assertEqual([header.sectionSize(i) for i in range(4)], old[:4])
            resized = header.sectionSize(4)
            for width in (1040, 1500, 1200):
                window.resize(width, 700)
                self.app.processEvents()
                self.assertEqual(header.sectionSize(4), resized)
            window.sources.selectRow(0)
            ident = window.selected_ids()[0]
            window.show_sources(window.source_values)
            self.assertIn(ident, window.selected_ids())
            window.source_filter.setText("no-such-title-test")
            self.assertFalse(window.selected_ids())
            self.assertTrue(all(window.sources.isRowHidden(i) for i in range(window.sources.rowCount())))
            window.source_filter.clear()
            self.assertFalse(window.sources.isRowHidden(0))
            window.save_header()
            self.events_until(lambda: not window.work)
        finally:
            window.close()
            self.events_until(lambda: not window.work)
        restored = ManagerWindow()
        restored.show()
        try:
            self.events_until(lambda: restored.header_ready)
            self.assertEqual(restored.sources.horizontalHeader().sectionSize(4), resized)
            for i in range(5):
                restored.tabs.setCurrentIndex(i)
                self.app.processEvents()
                self.assertTrue(restored.nav_buttons[i].isChecked())
                restored.nav_buttons[i].click()
                self.assertTrue(restored.nav_buttons[i].isChecked(), 'The current section cannot be unchecked')
                restored.grab().save(str(ROOT / f'.cache/desktop-manager-{i}.png'))
        finally:
            restored.close()
            self.events_until(lambda: not restored.work)

    def test_backend_graceful_stop_and_restart(self):
        from amazing_overlay import transport
        import uuid
        pipe = 'amazing-desktop-stop-' + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='Amazing тест ') as directory:
            env = dict(os.environ, AMAZING_OVERLAY_PIPE=pipe, AMAZING_OVERLAY_DATA=directory)
            process = subprocess.Popen([sys.executable, '-m', 'amazing_overlay', 'backend'], cwd=directory, env=dict(env, PYTHONPATH=str(ROOT)),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with patch.object(transport, 'PIPE_NAME', pipe):
                try:
                    self.events_until(lambda: self.backend_reachable(transport), timeout=20)
                    transport.rpc('settings_save', {'notes_dir': str(Path(directory) / 'Notes')})
                    transport.rpc('notes_create', {'path': 'Запись.txt', 'text': 'Сохранено перед остановкой'})
                    self.assertFalse(transport.stop_backend(timeout=15))
                    process.wait(timeout=10)
                    self.assertEqual(process.returncode, 0)
                    process.communicate(timeout=5)
                    self.assertEqual((Path(directory) / 'Notes/Запись.txt').read_text(encoding='utf-8'), 'Сохранено перед остановкой')
                    process = subprocess.Popen([sys.executable, '-m', 'amazing_overlay', 'backend'], cwd=directory,
                                               env=dict(env, PYTHONPATH=str(ROOT)), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    self.events_until(lambda: self.backend_reachable(transport), timeout=20)
                    self.assertEqual(transport.rpc('notes_read', {'path': 'Запись.txt'})['text'], 'Сохранено перед остановкой')
                    transport.stop_backend(timeout=15)
                    process.wait(timeout=10)
                    self.assertEqual(process.returncode, 0)
                finally:
                    if process.poll() is None:
                        process.terminate()
                    process.communicate(timeout=10)

    @staticmethod
    def backend_reachable(transport):
        try:
            return bool(transport.rpc('health', timeout=1000))
        except (ConnectionError, TimeoutError):
            return False

    def test_overlay_preserves_document_and_search_stays_responsive(self):
        from amazing_overlay.ui import OverlayWindow
        with patch("amazing_overlay.ui.set_input"), patch("amazing_overlay.ui.Hotkeys"):
            window = OverlayWindow()
            try:
                window.show()
                self.events_until(lambda: window.current_doc is not None)
                ids = [window.law_list.item(i).data(256)["id"] for i in range(window.law_list.count())]
                self.assertGreaterEqual(len(ids), 3)
                window.switch_law(1)
                window.switch_law(1)
                self.events_until(lambda: window.current_id == ids[2] and not window.work)
                window.switch_law(-1)
                self.events_until(lambda: window.current_id == ids[1] and not window.work)
                ident = window.current_id
                for _ in range(100):
                    window.toggle()
                    window.toggle()
                self.assertEqual(window.current_id, ident)
                window.set_mode("input")
                window.search.setText("задержание")
                self.events_until(lambda: window.law_list.count() > 0 and "Совпадений" in window.status.text())
                self.assertTrue(window.left.isVisible())
                window.law_list.setCurrentRow(0)
                self.events_until(lambda: not window.work)
                output = ROOT / ".cache" / "overlay-preview.png"
                self.assertTrue(window.grab().save(str(output)))
            finally:
                window.scope_timer.stop()
                window.settings_timer.stop()
                window.timer.stop()
                window.hotkeys.clear()
                window.tray.hide()
                window.save_state()
                self.events_until(lambda: not window.work)
                window.closed = True
                window.hide()

    def test_external_releases_hotkeys_to_native_and_updates_notebook_font(self):
        from amazing_overlay.ui import OverlayWindow
        from amazing_overlay import win32
        from ctypes import wintypes
        with patch("amazing_overlay.ui.set_input"), patch("amazing_overlay.ui.Hotkeys"):
            window = OverlayWindow()
            try:
                self.events_until(lambda: not window.restoring)
                window.toggle_notebook(force=True)
                self.events_until(lambda: window.notebook.root_path != '')
                settings = rpc('settings')
                settings['font_size'] = 26
                window.apply_appearance(settings)
                self.assertEqual(window.notebook.editor.font().pixelSize(), 26)
                self.assertEqual(window.notebook.editor.document().defaultFont().pixelSize(), 26)
                self.assertEqual(window.reader.font().pixelSize(), 26)
                settings.update(input_hotkey='F9', toggle_hotkey='F11')
                window.apply_appearance(settings)
                window.set_mode('view')
                self.assertIn('F9', window.status.text())
                self.assertIn('F11', window.status.text())
                self.assertNotIn('Alt+F', window.status.text())
                window.set_mode('input')
                self.assertIn('F9 / Esc', window.status.text())
                rpc('settings_save', {'font_size': 14})
                window.refresh_settings()
                self.events_until(lambda: window.notebook.editor.font().pixelSize() == 14)
                with patch('amazing_overlay.ui.foreground', return_value=1234), patch('amazing_overlay.ui.belongs_to_game', return_value=True), patch('amazing_overlay.ui.native_owns_input', return_value=True):
                    window.hotkeys.reset_mock()
                    window.update_hotkey_scope()
                    self.assertFalse(window.hotkeys.set_active.call_args.args[0])
                    with patch.object(window, 'toggle_notebook') as opened, patch('amazing_overlay.ui.open_native_notebook') as forwarded:
                        window.hotkey('notes')
                        opened.assert_not_called()
                        forwarded.assert_called_once_with(1234)
                with patch('amazing_overlay.ui.foreground', return_value=1234), patch('amazing_overlay.ui.belongs_to_game', return_value=True), patch('amazing_overlay.ui.native_owns_input', return_value=False):
                    window.update_hotkey_scope()
                    self.assertTrue(window.hotkeys.set_active.call_args.args[0])
                if sys.platform == 'win32':
                    import ctypes
                    # Qt's offscreen winId isn't an HWND. Use our own invisible
                    # Win32 STATIC window to exercise the actual property API.
                    win32.user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
                    win32.user32.CreateWindowExW.restype = wintypes.HWND
                    win32.user32.DestroyWindow.argtypes = [wintypes.HWND]
                    handle = win32.user32.CreateWindowExW(0, 'STATIC', 'Ownership test', 0, 0, 0, 1, 1, None, None, None, None)
                    self.assertTrue(handle)
                    win32.user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
                    win32.user32.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
                    prop = 'AmazingLawOverlay.NativeInputOwner.v1'
                    self.assertTrue(win32.user32.SetPropW(handle, prop, os.getpid()))
                    sibling = win32.user32.CreateWindowExW(0, 'STATIC', 'Game sibling test', 0, 0, 0, 1, 1, None, None, None, None)
                    self.assertTrue(sibling)
                    command = 'AmazingLawOverlay.NativeNotebookCommand.v1'
                    try:
                        self.assertTrue(win32.native_owns_input(handle))
                        self.assertEqual(win32.native_owner_window(sibling), handle)
                        self.assertFalse(win32.open_native_notebook(sibling))
                        self.assertTrue(win32.user32.SetPropW(handle, command, 1))
                        with patch.object(win32.user32, 'PostMessageW', return_value=1) as sent:
                            self.assertTrue(win32.open_native_notebook(sibling))
                            sent.assert_called_once_with(handle, 0x8416, 1, 0)
                    finally:
                        win32.user32.RemovePropW(handle, command)
                        win32.user32.RemovePropW(handle, prop)
                        win32.user32.DestroyWindow(sibling)
                    self.assertFalse(win32.native_owns_input(handle))
                    win32.user32.DestroyWindow(handle)
            finally:
                window.scope_timer.stop()
                window.settings_timer.stop()
                window.timer.stop()
                window.tray.hide()
                self.events_until(lambda: not window.work)
                window.closed = True
                window.hide()


if __name__ == "__main__":
    unittest.main()
