import base64
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from amazing_overlay import __version__, updates, game_process, native_launch
from amazing_overlay.game_process import GameProcess
from amazing_overlay.native_session import NativeRegistry
from amazing_overlay.product import default_notes, verify_bundle


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.content = b"verified setup bytes"
        self.manifest = {"format": 1, "repository": updates.REPOSITORY, "version": "2.9.0", "packages": [
            {"type": "installer", "name": "AmazingHelperSetup.exe", "arch": "x64", "bytes": len(self.content), "sha256": hashlib.sha256(self.content).hexdigest()},
            {"type": "portable", "name": "AmazingHelper-2.9.0-portable-x64.zip", "arch": "x64", "bytes": 50, "sha256": "0" * 64}]}
        self.url = f"https://github.com/{updates.REPOSITORY}/releases/download/v2.9.0/AmazingHelperSetup.exe"
        self.update = {"version": "2.9.0", "packages": [{**self.manifest["packages"][0], "url": self.url}]}

    def verify(self, value=None):
        payload = json.dumps(value or self.manifest).encode()
        return updates.verify_manifest(payload, self.key.sign(payload), self.public)

    def test_signature_and_metadata_validation(self):
        self.assertEqual(self.verify()["version"], "2.9.0")
        payload = json.dumps(self.manifest).encode()
        with self.assertRaises(InvalidSignature):
            updates.verify_manifest(payload + b" ", self.key.sign(payload), self.public)
        other = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        with self.assertRaises(InvalidSignature):
            updates.verify_manifest(payload, self.key.sign(payload), other)
        for field, value in (("name", "../attack.exe"), ("arch", "x86"), ("bytes", True), ("bytes", 0), ("sha256", "invalid")):
            broken = json.loads(payload)
            broken["packages"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.verify(broken)

    def test_download_atomic_verification_and_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "AmazingHelperSetup-2.9.0.exe"
            target.write_bytes(b"existing")
            for content in (b"bad", self.content + b"extra"):
                with patch.object(updates, "open_url", return_value=io.BytesIO(content)), self.assertRaises(ValueError):
                    updates.download_installer(self.update, directory)
                self.assertEqual(target.read_bytes(), b"existing")
                self.assertFalse(list(Path(directory).glob("*.part")))
            cancel = threading.Event()
            cancel.set()
            with patch.object(updates, "open_url", return_value=io.BytesIO(self.content)), self.assertRaises(InterruptedError):
                updates.download_installer(self.update, directory, cancel)
            self.assertEqual(target.read_bytes(), b"existing")
            with patch.object(updates, "open_url", return_value=io.BytesIO(self.content)):
                self.assertEqual(updates.download_installer(self.update, directory).read_bytes(), self.content)

    def test_network_failure_space_and_allowed_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(updates.shutil, "disk_usage", return_value=Mock(free=0)), self.assertRaises(OSError):
                updates.download_installer(self.update, directory)
            with patch.object(updates, "open_url", side_effect=OSError("offline")), self.assertRaises(OSError):
                updates.download_installer(self.update, directory)
            self.assertFalse(list(Path(directory).glob("*.part")))
        for address in ("http://github.com/x", "https://evil.test/file", self.url.replace(updates.REPOSITORY, "someone/else"), self.url + "?evil=1"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                updates.asset_url(address)
        for value in ("2.8.0-beta", "02.8.0", "v2.8.0", "2.8", "2.8.0.1"):
            with self.assertRaises(ValueError):
                updates.version(value)
        self.assertGreater(updates.version("2.10.0"), updates.version("2.9.0"))

    def test_release_must_match_signed_version(self):
        payload = json.dumps(self.manifest).encode()
        release = {"tag_name": "v3.0.0", "assets": [
            {"name": "updates.json", "browser_download_url": self.url.rsplit("/", 1)[0] + "/updates.json"},
            {"name": "updates.sig", "browser_download_url": self.url.rsplit("/", 1)[0] + "/updates.sig"}]}
        with patch.object(updates, "fetch", side_effect=[json.dumps(release).encode(), payload, self.key.sign(payload)]), patch.object(updates, "public_key", return_value=self.public):
            with self.assertRaisesRegex(ValueError, "Версия"):
                updates.check_update()


class NativeTests(unittest.TestCase):
    def test_registry_expiry_identity_and_validation(self):
        registry = NativeRegistry()
        value = {"pid": 123, "created": "1000", "version": __version__, "stage": "ready"}
        with patch("amazing_overlay.native_session.time.monotonic", return_value=10):
            registry.register(value)
            self.assertEqual(registry.status(123, "1000")["stage"], "ready")
            self.assertIsNone(registry.status(123, "1001"))
        with patch("amazing_overlay.native_session.time.monotonic", return_value=26):
            self.assertIsNone(registry.status(123, "1000"))
        for invalid in ({**value, "pid": True}, {**value, "created": "bad"}, {**value, "stage": "unknown"}):
            with self.assertRaises(ValueError):
                registry.register(invalid)

    def test_find_both_names_multiple_and_denied(self):
        one = GameProcess(1, "C:/Games/amazing.exe", "100", True)
        with patch.object(game_process, "process_names", return_value=[(1, "AMAZING.EXE"), (2, "gta_sa.exe"), (3, "other.exe")]), patch.object(game_process, "process_info", side_effect=[one, PermissionError("access denied")]):
            games = game_process.find_games()
        self.assertEqual(len(games), 2)
        self.assertTrue(any(game.error for game in games))
        with patch.object(game_process, "process_names", return_value=[]):
            self.assertEqual(game_process.find_games(), [])

    def test_launch_rejects_changed_process_wrong_arch_old_backend(self):
        game = GameProcess(100, "C:/Games/amazing.exe", "100", True)
        backend, rpc = Mock(), Mock()
        for current in (GameProcess(100, game.path, "101", True), GameProcess(100, game.path, "100", False)):
            with patch.object(native_launch, "process_info", return_value=current), self.assertRaises(RuntimeError):
                native_launch.launch_native(game, Path("."), backend, rpc)
        with patch.object(native_launch, "process_info", return_value=game), self.assertRaises(RuntimeError):
            backend.return_value = {"app_version": "2.7.0"}
            native_launch.launch_native(game, Path("."), backend, rpc)
        rpc.assert_not_called()

    def test_ready_reuses_session_without_injection(self):
        game = GameProcess(100, "C:/Games/amazing.exe", "100", True)
        health = lambda: {"app_version": __version__, "native_sessions": True}
        rpc = Mock(return_value={"stage": "ready", "version": __version__})
        with patch.object(native_launch, "process_info", return_value=game), patch.object(native_launch.subprocess, "run") as helper:
            self.assertIn("уже", native_launch.launch_native(game, Path("."), health, rpc))
            helper.assert_not_called()

    def test_success_and_ambiguous_failure_never_remap(self):
        game = GameProcess(400, "C:/Games/amazing.exe", "400", True)
        health = lambda: {"app_version": __version__, "native_sessions": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({"files": {"AmazingLawClient.dll": {"sha256": "0" * 64}}}))
            native_launch._uncertain.clear()
            with patch.object(native_launch, "process_info", return_value=game), patch.object(native_launch, "verify_bundle"), patch.object(native_launch.subprocess, "run", return_value=Mock(returncode=0, stdout=b'{"ok":true}')) as helper:
                rpc = Mock(side_effect=[None, {"stage": "ready", "version": __version__}])
                self.assertIn("запущен", native_launch.launch_native(game, root, health, rpc))
                helper.assert_called_once()
                with self.assertRaisesRegex(RuntimeError, "Предыдущая"):
                    native_launch.launch_native(game, root, health, Mock(return_value=None))
            native_launch._uncertain.clear()


class DataTests(unittest.TestCase):
    def test_notes_distribution_and_bundle_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(default_notes(root), root / "Notes")
            (root / "installed.json").write_text('{"distribution":"installed"}')
            with patch("amazing_overlay.product.documents", return_value=root / "Документы"):
                self.assertEqual(default_notes(root), root / "Документы/AmazingHelper/Notes")
            (root / "payload.dll").write_bytes(b"abc")
            (root / "manifest.json").write_text(json.dumps({"files": {"payload.dll": {"bytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}}}))
            verify_bundle(root, ["payload.dll"])
            (root / "payload.dll").write_bytes(b"xyz")
            with self.assertRaises(ValueError):
                verify_bundle(root, ["payload.dll"])


class ClientSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_client_refuses_unsaved_notes_then_acknowledges_saved_notes(self):
        from PySide6.QtWidgets import QApplication, QWidget
        from amazing_overlay.client_control import ClientControl
        app = QApplication.instance() or QApplication([])
        window = QWidget()
        window.notebook = Mock(saving=False, dirty=True)
        window.quit = Mock()
        control = ClientControl(window, "overlay")
        socket = Mock()
        try:
            control.prepare(socket)
            self.assertFalse(json.loads(socket.write.call_args.args[0])["ok"])
            window.quit.assert_not_called()
            window.notebook.dirty = False
            control.prepare(socket)
            self.assertTrue(json.loads(socket.write.call_args.args[0])["ok"])
            deadline = time.monotonic() + .3
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            window.quit.assert_called_once()
        finally:
            control.server.close()
            window.close()

    def test_launcher_does_not_inject_on_open_or_without_game(self):
        from PySide6.QtWidgets import QApplication
        from amazing_overlay import launcher
        app = QApplication.instance() or QApplication([])
        calls = []
        with patch.object(launcher, "background", side_effect=lambda work, done, failed: calls.append((work, done, failed))), patch.object(native_launch, "launch_native") as inject:
            window = launcher.LauncherWindow()
            window.timer.stop()
            try:
                inject.assert_not_called()
                window.native_button.click()
                self.assertTrue(window.native_busy)
                self.assertFalse(window.native_button.isEnabled())
                calls[-1][1]([])
                self.assertFalse(window.native_busy)
                self.assertIn("Запустите", window.native_status.text())
                inject.assert_not_called()
            finally:
                window.close()
                previous = len(calls)
                window.check_updates()
                self.assertEqual(len(calls), previous, "Closed launchers cannot start delayed update requests")


if __name__ == "__main__":
    unittest.main()
