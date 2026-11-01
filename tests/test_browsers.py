import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from amazing_overlay.browsers import browser_environment, configure_browser_environment
from amazing_overlay.parser import browser_context


class BrowserEnvironmentTest(unittest.TestCase):
    def test_frozen_and_source_use_the_same_user_cache(self):
        environment = {"LOCALAPPDATA": "C:/Users/Test/AppData/Local", "USERPROFILE": "C:/Users/Test"}
        with patch("sys.platform", "win32"):
            source = browser_environment(environment)["PLAYWRIGHT_BROWSERS_PATH"]
            with patch("sys.frozen", True, create=True):
                frozen = browser_environment(environment)["PLAYWRIGHT_BROWSERS_PATH"]
        self.assertEqual(source, frozen)
        self.assertEqual(Path(frozen), Path(environment["LOCALAPPDATA"]) / "ms-playwright")
        self.assertNotIn("_internal", frozen)

    def test_explicit_cache_and_hermetic_setting_are_respected(self):
        for value in ("C:/custom/playwright", "0"):
            self.assertEqual(browser_environment({"PLAYWRIGHT_BROWSERS_PATH": value})["PLAYWRIGHT_BROWSERS_PATH"], value)

    def test_configured_value_prevents_frozen_default_zero(self):
        with patch.dict("os.environ", {"LOCALAPPDATA": "C:/cache", "USERPROFILE": "C:/user"}, clear=True), patch("sys.platform", "win32"):
            expected = configure_browser_environment()
            driver_environment = browser_environment()
            driver_environment.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
            self.assertEqual(driver_environment["PLAYWRIGHT_BROWSERS_PATH"], expected)
            self.assertNotEqual(expected, "0")

    def test_missing_browser_has_actionable_error(self):
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=Mock(side_effect=RuntimeError("Executable doesn't exist at missing.exe"))))
        with tempfile.TemporaryDirectory() as directory, patch("amazing_overlay.parser.DATA", Path(directory)):
            with self.assertRaisesRegex(RuntimeError, "Установить Chromium"):
                browser_context(playwright)

    def test_other_browser_errors_are_not_hidden(self):
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=Mock(side_effect=RuntimeError("Profile is already in use"))))
        with tempfile.TemporaryDirectory() as directory, patch("amazing_overlay.parser.DATA", Path(directory)):
            with self.assertRaisesRegex(RuntimeError, "Profile is already in use"):
                browser_context(playwright)


if __name__ == "__main__":
    unittest.main()
