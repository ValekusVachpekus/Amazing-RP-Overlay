import copy
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from amazing_overlay import ai
from amazing_overlay.documents import canonical_url, document_html, document_text, parse_html, thread_id
from amazing_overlay.parser import Cancelled, export_laws
from amazing_overlay.service import Backend
from amazing_overlay.store import Store

URL = "https://forum.amazing-online.com/threads/trudovoj-kodeks.1034845/"


def document(ident="1034845", text="Статья 12. Должности\nРаботодатель и сотрудник"):
    return {"schema_version": 1, "id": ident, "url": URL, "title": "Трудовой кодекс", "structured": True,
            "fetched_at": "2026-10-30T10:00:00Z", "pages": 1,
            "posts": [{"post_id": "5", "author": "author", "blocks": parse_html("<p>" + text + "</p>", URL)}]}


class DocumentsTest(unittest.TestCase):
    def test_identity_unread_pages_and_renames(self):
        for suffix in ("unread", "page-2", "?page=3", "#post-123"):
            self.assertEqual(thread_id(URL + suffix), "1034845")
            self.assertEqual(canonical_url(URL + suffix), URL)
        self.assertEqual(thread_id("https://forum.amazing-online.com/threads/new-title.1034845/"), "1034845")
        self.assertEqual(thread_id("https://forum.amazing-online.com/threads/1034845/"), "1034845")
        for invalid in ("file:///test", "https://evil.example/threads/a.123/", "https://forum.amazing-online.com/members/123/"):
            with self.assertRaises(ValueError):
                canonical_url(invalid)

    def test_spans_and_semantic_table_text(self):
        doc = document()
        doc["posts"][0]["blocks"] = parse_html("""<table><tr><th rowspan='2'>Ведомство</th><th colspan='2'>Должности</th></tr>
          <tr><th>Руководитель</th><th>Заместитель</th></tr>
          <tr><td>МВД</td><td>Генерал</td><td>Полковник</td></tr></table>""", URL)
        rendered = document_html(doc)
        self.assertIn('rowspan="2"', rendered)
        self.assertIn('colspan="2"', rendered)
        text = document_text(doc)
        self.assertIn("Ведомство: МВД", text)
        self.assertIn("Генерал", text)
        self.assertIn("Должности / Руководитель: Генерал", text)

    def test_untrusted_markup_is_not_executable(self):
        doc = document()
        doc["posts"][0]["blocks"] = parse_html('<script>alert(1)</script><a href="javascript:alert(1)">закон</a><p onclick="evil()">Текст</p><div class="bbCodeSpoiler-content">Скрытое положение</div>', URL)
        output = document_html(doc)
        self.assertNotIn("alert", output)
        self.assertNotIn("onclick", output)
        self.assertIn("Скрытое положение", output)

    def test_forum_disclaimer_removed_by_dom_not_words(self):
        blocks = parse_html('<div style="user-select:none; border-radius: 3px 20px 3px 20px">Служебный блок</div><p>Не является формой — часть закона.</p>', URL)
        doc = document()
        doc["posts"][0]["blocks"] = blocks
        text = document_text(doc)
        self.assertNotIn("Служебный блок", text)
        self.assertIn("часть закона", text)


class SettingsTest(unittest.TestCase):
    def test_appearance_bounds_and_unsaved_api_preview(self):
        from amazing_overlay import settings
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clean_env = {k: v for k, v in os.environ.items() if not k.startswith("AI_")}
            with patch.object(settings, "ROOT", root), patch.object(settings, "DATA", root / "data"), patch.dict(os.environ, clean_env, clear=True):
                draft = settings.preview_settings({"ai_api_key": "draft-secret", "ai_model": "draft-model", "opacity": .1, "font_size": 50, "theme": "light", "animations": False})
                self.assertEqual((draft["opacity"], draft["font_size"], draft["theme"], draft["animations"]), (.3, 26, "light", False))
                self.assertEqual(draft["ai_api_key"], "draft-secret")
                self.assertFalse((root / "data").exists())
                saved = settings.save_settings({"theme": "light", "animations": False, "opacity": .3, "font_size": 26})
                self.assertEqual(saved["theme"], "light")
                self.assertFalse(settings.load_settings()["animations"])
                self.assertNotIn("ai_api_key", saved)
                with self.assertRaises(ValueError):
                    settings.save_settings({"theme": "unknown"})

    def test_private_key_and_environment_precedence(self):
        from amazing_overlay import settings
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            (root / "config.json").write_text(json.dumps({"ai_model": "legacy", "hotkeys": {"toggle": {"modifiers": ["ctrl", "shift"], "key": "l"}}}), encoding="utf-8")
            (root / ".env").write_text("AI_API_KEY=old-key\n", encoding="utf-8")
            (data / ".env").write_text("AI_API_KEY=user-key\n", encoding="utf-8")
            clean_env = {k: v for k, v in os.environ.items() if not k.startswith("AI_")}
            clean_env["AI_MODEL"] = "environment-model"
            with patch.object(settings, "ROOT", root), patch.object(settings, "DATA", data), patch.dict(os.environ, clean_env, clear=True):
                effective = settings.load_settings(True)
                self.assertEqual(effective["ai_api_key"], "user-key")
                self.assertEqual(effective["ai_model"], "environment-model")
                self.assertEqual(effective["toggle_hotkey"], "ctrl+shift+l")
                self.assertNotIn("ai_api_key", settings.load_settings())
                settings.save_settings({"ai_api_key": "replacement-key", "ai_model": "saved"})
                self.assertNotIn("replacement-key", (data / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual(settings.load_settings(True)["ai_api_key"], "replacement-key")
                with self.assertRaises(ValueError):
                    settings.save_settings({"toggle_hotkey": "Ctrl+L", "input_hotkey": "Ctrl+L"})
                with self.assertRaises(ValueError):
                    settings.save_settings({"prev_law_hotkey": "Alt+Right", "next_law_hotkey": "Alt+Right"})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / "laws.db")

    def tearDown(self):
        self.temporary.cleanup()

    def test_publish_rollback_versions_and_index(self):
        doc = document()
        self.store.publish(doc)
        self.assertTrue(self.store.search("работодатель"))
        self.assertFalse(self.store.publish({**doc, "fetched_at": "2026-10-31T10:00:00Z"}))
        self.assertEqual(len(self.store.versions(doc["id"])), 1)
        with self.assertRaises(ValueError):
            self.store.publish({**doc, "posts": []})
        self.assertEqual(self.store.document(doc["id"])["title"], doc["title"])
        changed = document(text="Статья 18. Повышение\nНовый срок повышения")
        self.store.publish(changed)
        self.assertFalse(self.store.search("работодатель"))
        self.assertTrue(self.store.search("повышение"))
        self.assertEqual(len(self.store.versions(doc["id"])), 2)

    def test_sources_deduplicated_by_id(self):
        self.store.add_source(URL + "unread")
        self.store.add_source(URL.replace("trudovoj-kodeks", "new-name"))
        self.assertEqual(len(self.store.sources()), 1)

    def test_context_finds_end_of_large_database(self):
        for i in range(100):
            self.store.publish(document(str(i), "Статья 1. Общие положения\n" + "Обычный текст " * 150))
        self.store.publish(document("last", "Статья 99. Кварцит\nОсобый кварцитовый порядок"))
        hits = self.store.context("кварцит")
        self.assertEqual(hits[0]["doc_id"], "last")
        self.assertEqual(self.store.context("кварцит", ["1"]), [])

    def test_legacy_keeps_ambiguous_files_and_never_overwrites_structured(self):
        directory = Path(self.temporary.name) / "legacy"
        directory.mkdir()
        one = directory / "1__a.txt"
        two = directory / "2__b.txt"
        one.write_text("Старый закон\nИсточник: " + URL + "\nТекст", encoding="utf-8")
        two.write_text("Дубль\nИсточник: " + URL + "unread\nТекст", encoding="utf-8")
        self.store.publish(document())
        self.store.import_legacy(directory)
        self.assertEqual(len(self.store.documents()), 1)
        self.assertTrue(self.store.document("1034845")["structured"])
        self.assertTrue(one.exists() and two.exists())

    def test_export_encoding_and_stable_filename(self):
        self.store.publish(document())
        directory = Path(self.temporary.name) / "export"
        export_laws(self.store, directory)
        self.assertTrue((directory / "index.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertTrue((directory / "1034845__law.txt").exists())
        self.assertTrue((directory / "1034845.json").exists())


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backend = Backend(self.root, self.root)

    def tearDown(self):
        self.backend.close()
        self.temporary.cleanup()

    def test_shutdown_cancels_jobs_and_rejects_new_writes(self):
        entered = threading.Event()
        def action(cancel, progress):
            entered.set()
            self.assertTrue(cancel.wait(5))
            return "cancelled safely"
        ident = self.backend.job("test", action)["job_id"]
        self.assertTrue(entered.wait(5))
        health = self.backend.dispatch("health", {})
        self.assertTrue(health["shutdown"])
        self.assertFalse(health["stopping"])
        self.assertTrue(self.backend.dispatch("shutdown", {})["stopping"])
        self.assertTrue(self.backend.dispatch("health", {})["stopping"])
        with self.assertRaisesRegex(ValueError, "останавливается"):
            self.backend.dispatch("notes_create", {"path": "should-not-exist.txt"})
        self.backend.close()
        self.assertEqual(self.backend.jobs[ident]["state"], "done")
        self.assertFalse((self.root / "Notes/should-not-exist.txt").exists())

    def test_cancellation_and_previous_document(self):
        self.backend.store.publish(document())
        def cancellable(cancel, progress):
            cancel.wait(2)
            raise Cancelled("cancelled")
        ident = self.backend.job("test", cancellable)["job_id"]
        self.backend.dispatch("job_cancel", {"id": ident})
        deadline = time.monotonic() + 2
        while self.backend.dispatch("job_status", {"id": ident})["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.backend.dispatch("job_status", {"id": ident})["state"], "cancelled")
        self.assertTrue(self.backend.store.documents())

    def test_ai_passes_history_and_all_matching_documents(self):
        self.backend.store.publish(document("1", "Статья 1. Задержание\nПорядок задержания"))
        self.backend.store.publish(document("2", "Статья 2. Задержание\nСрок задержания"))
        self.backend.store.publish(document("3", "Статья 3. Задержание\nОснования задержания"))
        calls = []
        def fake(settings, messages, cancel, progress, limit=None):
            calls.append(messages)
            return "Ответ [S1]", {"total_tokens": 100}
        with patch.object(ai, "completion", fake):
            result = ai.ask(self.backend.store, {"context_chars": 32000, "ai_model": "test", "ai_provider": "openai"},
                            {"question": "задержание", "history": [{"role": "user", "content": "Уточняю ситуацию"}]}, threading.Event(), lambda _: None)
        self.assertEqual(len({s["doc_id"] for s in result["sources"]}), 3)
        self.assertTrue(any(m["content"] == "Уточняю ситуацию" for m in calls[-1]))

    def test_api_key_not_leaked_by_errors(self):
        self.assertNotIn("super-secret", ai.friendly_error(RuntimeError("key=super-secret"), {"ai_api_key": "super-secret"}))

    def test_gemini_uses_configured_api_address_and_model(self):
        class Response:
            def __enter__(self): return iter([b'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'])
            def __exit__(self, *args): pass
        with patch.object(ai.urllib.request, "urlopen", return_value=Response()) as request:
            answer, _ = ai.completion({"ai_provider": "gemini", "ai_base_url": "https://proxy.example/v1beta", "ai_model": "chosen-model",
                                       "ai_api_key": "fake-key", "max_tokens": 128}, [{"role": "user", "content": "test"}], threading.Event(), lambda _: None)
        self.assertEqual(answer, "ok")
        self.assertTrue(request.call_args.args[0].full_url.startswith("https://proxy.example/v1beta/models/chosen-model:"))

    def test_unsaved_connection_test_and_error_redaction(self):
        from amazing_overlay import settings
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("AI_")}
        with patch.object(settings, "ROOT", self.root), patch.object(settings, "DATA", self.root / "settings"), patch.dict(os.environ, clean_env, clear=True):
            captured = []
            def fake(value, *args):
                captured.append(value)
                raise RuntimeError("failed draft-secret")
            with patch.object(ai, "completion", fake):
                job = self.backend.dispatch("ai_test", {"ai_api_key": "draft-secret", "ai_model": "draft-model"})["job_id"]
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    result = self.backend.dispatch("job_status", {"id": job})
                    if result["state"] != "running":
                        break
                    time.sleep(.01)
            self.assertEqual(captured[0]["ai_model"], "draft-model")
            self.assertEqual(captured[0]["ai_api_key"], "draft-secret")
            self.assertEqual(result["state"], "error")
            self.assertNotIn("draft-secret", json.dumps(result))
            self.assertFalse((self.root / "settings").exists())


if __name__ == "__main__":
    unittest.main()
