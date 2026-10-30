import threading
import unittest

from amazing_overlay.parser import Cancelled, parse_thread


class Locator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector
        self.first = self

    def wait_for(self, **kwargs):
        pass

    def inner_text(self):
        return "Трудовой кодекс"

    def evaluate_all(self, script):
        if "pageNav" in self.selector:
            # Discover page 3 only after visiting page 2.
            maximum = 2 if self.page.number == 1 else 3
            return [f"/threads/a.123/page-{maximum}"]
        return [{"post_id": str(self.page.number), "author": "author", "html": f"<p>Статья {self.page.number}. Текст</p>"}]


class Page:
    def __init__(self):
        self.number = 0
        self.visited = []

    def goto(self, url, **kwargs):
        self.number = int(url.rsplit("page-", 1)[-1]) if "page-" in url else 1
        self.visited.append(self.number)

    def locator(self, selector):
        return Locator(self, selector)


class NoWaitCancel:
    def is_set(self):
        return False

    def wait(self, timeout):
        return False


class ParserTest(unittest.TestCase):
    def test_dynamic_pagination_is_fully_visited(self):
        page = Page()
        result = parse_thread(page, "https://forum.amazing-online.com/threads/a.123/unread", NoWaitCancel())
        self.assertEqual(page.visited, [1, 2, 3])
        self.assertEqual(result["pages"], 3)
        self.assertEqual(len(result["posts"]), 3)

    def test_cancel_before_navigation(self):
        page = Page()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            parse_thread(page, "https://forum.amazing-online.com/threads/a.123/", cancel)
        self.assertEqual(page.visited, [])


if __name__ == "__main__":
    unittest.main()
