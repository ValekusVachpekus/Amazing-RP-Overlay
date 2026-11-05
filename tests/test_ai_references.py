import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from amazing_overlay import ai
from amazing_overlay.documents import parse_html
from amazing_overlay.store import Store
from amazing_overlay.legal_query import reference


def law(ident, title, text, fetched="2026-11-02T00:00:00Z"):
    return {"schema_version": 1, "id": ident, "title": title, "url": "", "structured": True,
            "fetched_at": fetched, "posts": [{"post_id": "p", "blocks": parse_html("<p>" + text + "</p>", "")}]}


class CitationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "laws.db")
        self.store.publish(law("old", "Уголовный кодекс от 01.01.2025", "Статья 51. Старое\nСтарая редакция", "2026-11-03"))
        self.store.publish(law("uk", '"Уголовный кодекс" от 28.09.2026 № 5-ФЗ',
                               "Статья 50. Соседняя\nСоседняя статья\nСтатья 51. Превышение полномочий\nДолжностное лицо превышает полномочия.\nСтатья 51.1. Отдельная\nИная статья\nСтатья 151. Другая\nНе та статья", "2026-10-01"))
        self.store.publish(law("upk", "Уголовно-процессуальный кодекс", "Статья 51. Допрос\nДругой кодекс"))
        self.store.publish(law("tk", "Трудовой кодекс", "Статья 51. Работа\nДругой кодекс"))

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_code_and_article_and_publication_date(self):
        for query in ("что такое 51 УК", "ст. 51 ук", "УК 51", "Статья 51 Уголовного кодекса", "51УК", "ук51", "ст51УК"):
            with self.subTest(query=query):
                result = self.store.context(query)
                self.assertTrue(result)
                self.assertEqual({s["doc_id"] for s in result}, {"uk"})
                self.assertTrue(all(s["heading"].startswith("Статья 51.") for s in result))
                self.assertNotIn("Соседняя статья", " ".join(s["text"] for s in result))
        self.assertEqual(reference("51 УПК"), ("УПК", ["51"]))
        self.assertEqual({s["doc_id"] for s in self.store.context("51 УПК")}, {"upk"})
        self.assertEqual({s["heading"] for s in self.store.context("51.1 УК")}, {"Статья 51.1. Отдельная"})
        self.assertEqual(self.store.context("999 УК"), [])
        self.assertEqual(self.store.context("51 УК", ["upk"]), [])

    def test_long_article_keeps_all_chunks_without_neighbours(self):
        self.store.publish(law("uk", "Уголовный кодекс от 28.09.2026", "Статья 51. Длинная\n" + "Содержание " * 950 + "\nСтатья 52. Чужая\nНе включать"))
        result = self.store.context("51 УК")
        self.assertGreater(len(result), 2)
        self.assertNotIn("Не включать", "".join(s["text"] for s in result))

    def test_only_cited_links_and_no_api_expansion_for_exact_question(self):
        calls = []
        def fake(settings, messages, cancel, progress, limit=None):
            calls.append(messages)
            self.assertNotIn("Другой кодекс", messages[-1]["content"])
            return "**Ответ** [S1]. Неизвестная ссылка [S999]", {}
        with patch.object(ai, "completion", fake):
            answer = ai.ask(self.store, {"context_chars": 32000, "ai_model": "test", "ai_provider": "openai"},
                            {"question": "что такое 51 УК", "history": [{"role": "assistant", "content": "Старый ответ [S9]"}]}, threading.Event(), lambda _: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual([s["label"] for s in answer["citations"]], ["S1"])
        self.assertIn({"role": "assistant", "content": "Старый ответ "}, calls[-1])
        source = answer["citations"][0]
        target = self.store.citation(source)
        self.assertEqual(target["document"]["id"], "uk")
        self.assertEqual(target["target"]["text"], source["heading"])
        self.store.publish(law("uk", "Уголовный кодекс от 28.09.2026", "Статья 51. Изменённая\nНовая норма"))
        with self.assertRaisesRegex(ValueError, "изменился"):
            self.store.citation(source)
        with patch.object(ai, "completion", side_effect=AssertionError("No paid request for missing article")):
            missing = ai.ask(self.store, {"context_chars": 32000}, {"question": "999 УК"}, threading.Event(), lambda _: None)
        self.assertEqual(missing["citations"], [])
        self.assertIn("не найдена", missing["answer"])

    def test_citation_skips_table_of_contents_heading(self):
        doc = law("toc", "Уголовный кодекс от 02.11.2026", "Статья 51. Полномочия\nСтатья 52. Следующая\nСтатья 51. Полномочия\n" + "Настоящее содержание. " * 20)
        self.store.publish(doc)
        selected = self.store.context("51 УК")[0]
        locator = {**selected, "fingerprint": hashlib.sha256(selected["text"].encode()).hexdigest()}
        target = self.store.citation(locator)
        self.assertEqual(target["target"]["occurrence"], 1)

    def test_follow_up_keeps_article_but_new_topic_does_not(self):
        history = [{"role": "user", "content": "что такое 51 УК"}, {"role": "assistant", "content": "Ответ [S1]"}]
        self.assertEqual({s["doc_id"] for s in self.store.context(ai.retrieval_query("а какое наказание?", history))}, {"uk"})
        self.assertIn("51", ai.retrieval_query("объясни эту статью", history))
        self.assertEqual(ai.retrieval_query("ст. 50", history), "ст. 50 УК")
        self.assertEqual(ai.retrieval_query("что такое задержание", history), "что такое задержание")
        self.assertEqual(ai.retrieval_query("51 УПК", history), "51 УПК")
        changed = history + [{"role": "user", "content": "что такое задержание"}]
        self.assertEqual(ai.retrieval_query("а какое наказание?", changed), "а какое наказание?")

    def test_chat_restart_clear_and_late_write_conflict(self):
        state = self.store.chat()
        messages = [{"id": "u", "role": "user", "content": "Привет"}, {"id": "a", "role": "assistant", "content": "Ответ", "status": "cancelled"}]
        saved = self.store.save_chat(state["revision"], messages)
        restarted = Store(self.store.path)
        self.assertEqual(restarted.chat()["messages"], messages)
        cleared = restarted.save_chat(saved["revision"], [])
        with self.assertRaisesRegex(ValueError, "другим клиентом"):
            self.store.save_chat(saved["revision"], messages)
        self.assertEqual(restarted.chat(), {"revision": cleared["revision"], "messages": []})
