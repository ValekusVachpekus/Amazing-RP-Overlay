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
            self.assertTrue(window.grab().save(str(ROOT / '.cache/launcher-preview.png')))
        finally:
            window.timer.stop()
            window.close()

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
        result = subprocess.run([str(binary), "--self-test", "--notebook"], cwd=binary.parent, timeout=20,
                                env=dict(os.environ, AMAZING_OVERLAY_DATA=self.temporary.name, AMAZING_OVERLAY_CAPTURE=str(capture)),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue(capture.exists())

    def test_manager_offscreen(self):
        from amazing_overlay.ui import ManagerWindow
        window = ManagerWindow()
        try:
            window.show()
            self.events_until(lambda: bool(window.source_values))
            self.assertEqual(window.tabs.count(), 5)
            self.assertGreater(window.sources.rowCount(), 0)
            self.assertIn("Ключ", window.env_label.text())
            output = ROOT / ".cache" / "manager-preview.png"
            output.parent.mkdir(exist_ok=True)
            self.assertTrue(window.grab().save(str(output)))
        finally:
            window.close()
            self.events_until(lambda: not window.work)

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
                window.timer.stop()
                window.hotkeys.clear()
                window.tray.hide()
                window.save_state()
                self.events_until(lambda: not window.work)
                window.closed = True
                window.hide()


if __name__ == "__main__":
    unittest.main()
