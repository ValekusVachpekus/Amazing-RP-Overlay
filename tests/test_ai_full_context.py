"""Full-document chat scopes never fall back to retrieval or truncate laws."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from amazing_overlay import ai
from amazing_overlay.documents import document_text, parse_html
from amazing_overlay.store import Store


class FullContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "laws.db")
        self.settings = {"context_chars": 120000, "ai_model": "test", "ai_provider": "openai"}
        self.docs = []
        for ident, title in (("uk", "Уголовный кодекс от 28.09.2026"), ("tk", "Трудовой кодекс")):
            doc = {"schema_version": 1, "id": ident, "title": title, "url": "", "structured": True,
                   "posts": [{"post_id": "body", "blocks": parse_html(
                       f"<p>Преамбула {ident}</p><h3>Статья 51. Положение</h3><p>" +
                       f"Полное содержание {ident}. " * 250 +
                       "</p><table><tr><th>Должность</th><th>Срок</th></tr><tr><td>Судья</td><td>Год</td></tr></table>", "")},
                       {"post_id": "appendix", "blocks": parse_html(f"<p>Последнее приложение {ident}</p>", "")}]}
            self.store.publish(doc)
            self.docs.append(doc)

    def tearDown(self):
        self.temp.cleanup()

    def ask(self, params, settings=None):
        calls = []
        def complete(config, messages, cancel, progress, limit=None):
            calls.append(messages)
            return "Ответ [S1]", {}
        with patch.object(ai, "completion", complete), patch.object(self.store, "context", side_effect=AssertionError("No retrieval")):
            result = ai.ask(self.store, settings or self.settings, {"question": "51 УК", **params}, threading.Event(), lambda _: None)
        return calls, result

    def test_selected_and_current_include_every_post_without_retrieval(self):
        for mode in ("current", "selected"):
            calls, result = self.ask({"context_mode": mode, "doc_ids": ["uk"]})
            self.assertEqual(len(calls), 1)
            prompt = calls[0][-1]["content"]
            self.assertIn(document_text(self.docs[0]), prompt)
            self.assertNotIn("Преамбула tk", prompt)
            self.assertTrue(result["sources"])
            self.assertEqual({source["doc_id"] for source in result["sources"]}, {"uk"})
            for source in result["sources"]:
                self.assertEqual(self.store.citation(source)["document"]["id"], "uk")

    def test_all_includes_whole_documents_and_latest_version_marker(self):
        older = {**self.docs[0], "id": "old", "title": "Уголовный кодекс от 01.01.2025"}
        self.store.publish(older)
        calls, result = self.ask({"context_mode": "all", "doc_ids": []})
        for doc in self.docs:
            self.assertIn(document_text(doc), calls[0][-1]["content"])
        self.assertEqual({source["doc_id"] for source in result["sources"]}, {"uk", "tk", "old"})
        self.assertIn("Документ: Уголовный кодекс от 28.09.2026\nАктуальная версия УК", calls[0][-1]["content"])
        self.assertNotIn("Документ: Уголовный кодекс от 01.01.2025\nАктуальная версия УК", calls[0][-1]["content"])

    def test_plain_chat_never_reads_laws_or_expands_query(self):
        with patch.object(self.store, "complete_documents", side_effect=AssertionError("No law access")):
            calls, result = self.ask({"context_mode": "none"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], {"role": "user", "content": "51 УК"})
        self.assertEqual(calls[0][0]["content"], ai.GENERAL_SYSTEM)
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["citations"], [])

    def test_budget_rejects_entire_request_before_model_call(self):
        params = {"context_mode": "selected", "doc_ids": ["uk"]}
        plan = ai.context_plan(self.store, self.settings, params)
        self.assertEqual(plan["context_chars"], len("\n\n".join(plan["pieces"])))
        with patch.object(ai, "completion", side_effect=AssertionError("No paid call")):
            with self.assertRaisesRegex(ValueError, "Текст не обрезан, запрос не отправлен"):
                ai.ask(self.store, {**self.settings, "context_chars": plan["context_chars"] - 1},
                       {"question": "Привет", **params}, threading.Event(), lambda _: None)
        calls, _ = self.ask(params, {**self.settings, "context_chars": plan["context_chars"]})
        self.assertEqual(len(calls), 1)

    def test_invalid_or_empty_selection_never_becomes_all_laws(self):
        for params in ({"context_mode": "selected", "doc_ids": []}, {"context_mode": "current", "doc_ids": []},
                       {"context_mode": "selected", "doc_ids": ["deleted"]},
                       {"context_mode": "selected", "doc_ids": "uk"},
                       {"context_mode": "current", "doc_ids": ["uk", "tk"]}, {"context_mode": "unknown"}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                ai.context_plan(self.store, self.settings, params)
        plan = ai.context_plan(self.store, self.settings, {"context_mode": "selected", "doc_ids": ["uk", "uk"]})
        self.assertEqual(plan["documents"], 1)


if __name__ == "__main__":
    unittest.main()
