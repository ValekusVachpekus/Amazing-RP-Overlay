from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from .documents import canonical_url, document_text, parse_html, thread_id
from .settings import DATA, atomic_json
from .store import now


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled("Операция отменена; последняя успешная версия сохранена")


def page_count(page):
    numbers = [1]
    for href in page.locator(".pageNav a, .pageNavSimple a, a.pageNav-jump").evaluate_all("nodes => nodes.map(n => n.getAttribute('href') || '')"):
        found = re.search(r"(?:[?&]page=|/page-)(\d+)", href)
        if found:
            numbers.append(int(found[1]))
    return min(500, max(numbers))


def parse_thread(page, url, cancel=None, progress=lambda value: None):
    cancel = cancel or threading.Event()
    url = canonical_url(url)
    maximum, number, posts, seen, title = 1, 1, [], set(), ""
    while number <= maximum:
        check_cancel(cancel)
        progress({"url": url, "page": number, "pages": maximum, "message": "Загрузка страницы"})
        response = page.goto(url if number == 1 else url + f"page-{number}", wait_until="domcontentloaded", timeout=45000)
        if response and response.status >= 400:
            raise ValueError(f"Форум вернул HTTP {response.status}")
        # Challenge pages must never be published as laws.
        try:
            page.locator("article.message--post .message-body .bbWrapper").first.wait_for(timeout=15000)
        except Exception as exc:
            raise ValueError("Нет сообщений темы. Возможно, нужна проверка в браузере: запустите предпросмотр с открытым браузером.") from exc
        check_cancel(cancel)
        if not title:
            title = page.locator("h1.p-title-value, h1").first.inner_text().strip()
        maximum = max(maximum, page_count(page))
        raw_posts = page.locator("article.message--post").evaluate_all("""nodes => nodes.map(n => ({
            post_id: (n.id || n.getAttribute('data-content') || '').replace(/[^0-9]/g,''),
            author: n.querySelector('.message-name .username, .username')?.textContent?.trim() || '',
            html: n.querySelector('.message-body .bbWrapper')?.innerHTML || ''
        }))""")
        if not raw_posts:
            raise ValueError(f"На странице {number} не найдены сообщения")
        added = 0
        for post in raw_posts:
            blocks = parse_html(post["html"], url)
            ident = post["post_id"] or hashlib.sha256(post["html"].encode()).hexdigest()[:20]
            if ident not in seen and blocks:
                seen.add(ident)
                posts.append({"post_id": ident, "author": post["author"], "blocks": blocks})
                added += 1
        if number > 1 and added == 0:
            raise ValueError("Пагинация вернула повтор страницы; неполная версия не сохранена")
        progress({"url": url, "page": number, "pages": maximum, "message": f"Получено сообщений: {len(posts)}"})
        number += 1
        if number <= maximum and cancel.wait(1.2):
            check_cancel(cancel)
    check_cancel(cancel)
    return {"schema_version": 1, "id": thread_id(url), "url": url, "title": title, "structured": True,
            "fetched_at": now(), "pages": maximum, "posts": posts}


def browser_context(playwright, visible=False):
    profile = DATA / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    context = playwright.chromium.launch_persistent_context(str(profile), headless=not visible,
                                                            locale="ru-RU", viewport={"width": 1280, "height": 900})
    context.set_default_timeout(15000)
    context.route("**/*", lambda route: route.abort() if route.request.resource_type in {"media", "font"} else route.continue_())
    return context


def update_sources(store, ids, cancel, progress, visible=False):
    from playwright.sync_api import sync_playwright
    sources = [source for source in store.sources() if source["enabled"] and (ids is None or source["id"] in ids)]
    results = []
    with sync_playwright() as playwright:
        context = browser_context(playwright, visible)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            for index, source in enumerate(sources):
                check_cancel(cancel)
                progress({"item": index + 1, "total": len(sources), "message": source["url"]})
                try:
                    doc = parse_thread(page, source["url"], cancel, progress)
                    check_cancel(cancel)
                    store.publish(doc)
                    results.append({"id": source["id"], "ok": True})
                except Cancelled:
                    raise
                except Exception as exc:
                    store.status(source["id"], str(exc))
                    results.append({"id": source["id"], "ok": False, "error": str(exc)})
                    progress({"message": str(exc), "error": True})
        finally:
            context.close()
    return results


def atomic_text(path, text, encoding="utf-8"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_laws(store, directory):
    import io
    directory = Path(directory)
    rows = []
    for meta in store.documents():
        doc = store.document(meta["id"])
        # Stable names prevent duplicates when a forum topic is renamed.
        filename = f"{doc['id']}__law.txt"
        text = f"{doc['title']}\n{'=' * 60}\nИсточник: {doc['url']}\nСтраниц темы: {doc['pages']}\nСообщений: {len(doc['posts'])}\n\n" + document_text(doc)
        atomic_text(directory / filename, text)
        atomic_json(directory / f"{doc['id']}.json", doc)
        rows.append({"url": doc["url"], "title": doc["title"], "file": filename, "pages": doc["pages"], "posts": len(doc["posts"]), "status": "OK"})
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["url", "title", "file", "pages", "posts", "status"], delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(directory / "index.csv", buffer.getvalue(), "utf-8-sig")
    return len(rows)
