from __future__ import annotations

import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "laws"
URLS_FILE = BASE_DIR / "urls.txt"

# По умолчанию браузер ОТКРЫТЫЙ, чтобы ты видел прохождение JS-защиты.
# После первого успешного запуска можно поставить HEADLESS = True.
HEADLESS = False

# Сколько ждать загрузку страницы.
NAVIGATION_TIMEOUT_MS = 60_000

# Пауза между страницами, чтобы не долбить форум запросами.
DELAY_SECONDS = 1.2

# Если сайт долго держит JS-защиту, скрипт даст время пройти её в браузере.
CHALLENGE_WAIT_SECONDS = 30

# Селекторы XenForo 2. У форума они могут немного меняться,
# поэтому ниже используется несколько вариантов.
POST_SELECTORS = [
    "article.message.message--post",
    "article.message--post",
    "article.message",
]

CONTENT_SELECTORS = [
    ".message-body .bbWrapper",
    ".message-body",
    ".message-content .bbWrapper",
    ".message-content",
]

TITLE_SELECTORS = [
    "h1.p-title-value",
    "h1",
]


def load_urls() -> list[str]:
    if not URLS_FILE.exists():
        raise FileNotFoundError(
            f"Не найден {URLS_FILE.name}. Создай его и помести туда ссылки, по одной на строку."
        )

    urls = []
    for raw in URLS_FILE.read_text(encoding="utf-8").splitlines():
        url = raw.strip()
        if not url or url.startswith("#"):
            continue
        if not url.startswith(("http://", "https://")):
            continue
        urls.append(url)

    # Убираем дубли, сохраняя порядок.
    return list(dict.fromkeys(urls))


def safe_filename(name: str, max_len: int = 150) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "law"
    return name[:max_len].rstrip(" .")


def thread_id_from_url(url: str) -> str:
    m = re.search(r"\.(\d+)(?:/)?(?:\?|$)", url)
    if m:
        return m.group(1)
    return str(abs(hash(url)))


def make_page_url(url: str, page_number: int) -> str:
    """Возвращает URL конкретной страницы темы."""
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["page"] = [str(page_number)]

    # На первой странице XenForo обычно можно оставить исходный URL.
    if page_number == 1:
        query.pop("page", None)

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query, doseq=True),
            parts.fragment,
        )
    )


def wait_for_real_forum(page) -> None:
    """
    Ждём, пока JS-защита перестанет отдавать служебную страницу.
    vDDoS может сначала показать "Please turn JavaScript on..."
    и через JS поставить cookie/редиректнуть обратно.
    """
    deadline = time.time() + CHALLENGE_WAIT_SECONDS

    while time.time() < deadline:
        url = page.url.lower()
        body_text = ""
        try:
            body_text = page.locator("body").inner_text(timeout=2_000)[:5_000].lower()
        except Exception:
            pass

        looks_like_challenge = (
            "please turn javascript on" in body_text
            or "vddos" in body_text
            or "aes.min.js" in body_text
            or "w3includhtml" in body_text
            or len(body_text.strip()) < 50
        )

        has_forum_content = (
            page.locator("article.message").count() > 0
            or page.locator("h1").count() > 0
            or page.locator(".block-container").count() > 0
            or "/threads/" in url
        )

        if not looks_like_challenge and has_forum_content:
            return

        page.wait_for_timeout(1_000)

    # Не падаем сразу: сохраним скриншот/HTML для диагностики.
    debug_dir = BASE_DIR / "debug"
    debug_dir.mkdir(exist_ok=True)

    try:
        page.screenshot(path=str(debug_dir / "challenge.png"), full_page=True)
    except Exception:
        pass

    try:
        (debug_dir / "challenge.html").write_text(
            page.content(), encoding="utf-8"
        )
    except Exception:
        pass

    raise RuntimeError(
        "Сайт не пропустил браузер через JS-защиту за "
        f"{CHALLENGE_WAIT_SECONDS} секунд.\n"
        f"Текущий URL: {page.url}\n"
        f"Диагностика сохранена в: {debug_dir}"
    )


def get_thread_title(page, fallback_url: str) -> str:
    for selector in TITLE_SELECTORS:
        locator = page.locator(selector).first
        try:
            text = locator.inner_text(timeout=3_000).strip()
            if text:
                return re.sub(r"\s+", " ", text)
        except Exception:
            pass

    # Запасной вариант: title вкладки.
    try:
        title = page.title().strip()
        if title:
            return re.sub(r"\s+", " ", title)
    except Exception:
        pass

    # Последний fallback.
    return f"law_{thread_id_from_url(fallback_url)}"


def get_max_page(page) -> int:
    """
    Ищет максимальный номер страницы в пагинации.
    XenForo обычно использует ?page=N.
    """
    max_page = 1

    links = page.locator("a").all()
    for link in links:
        try:
            href = link.get_attribute("href")
            text = link.inner_text().strip()
        except Exception:
            continue

        if not href:
            continue

        m = re.search(r"[?&]page=(\d+)", href)
        if m:
            max_page = max(max_page, int(m.group(1)))

        if text.isdigit():
            # Ссылка "2", "3", ...
            num = int(text)
            if num > max_page and re.search(r"page=\d+", href):
                max_page = num

    return max_page


def extract_posts(page) -> list[dict]:
    """
    Извлекает сообщения закона со страницы.
    Для XenForo стараемся брать только основной текст сообщения,
    без подписи/цитаты/реакций.
    """
    posts = []

    chosen_selector = None
    for selector in POST_SELECTORS:
        if page.locator(selector).count() > 0:
            chosen_selector = selector
            break

    if not chosen_selector:
        return posts

    articles = page.locator(chosen_selector)

    for i in range(articles.count()):
        article = articles.nth(i)

        # ID поста для дедупликации.
        post_id = ""
        try:
            post_id = (
                article.get_attribute("id")
                or article.get_attribute("data-content")
                or ""
            )
        except Exception:
            pass

        post_id = re.sub(r"[^0-9]+", "", post_id)

        # Ищем тело.
        content = ""
        for selector in CONTENT_SELECTORS:
            try:
                locator = article.locator(selector).first
                if locator.count() > 0:
                    content = locator.inner_text(timeout=2_000).strip()
                    if content:
                        break
            except Exception:
                pass

        if not content:
            continue

        # Чистим лишние пробелы, но сохраняем абзацы/строки.
        content = content.replace("\xa0", " ")
        lines = []
        previous_blank = False

        for line in content.splitlines():
            line = re.sub(r"[ \t]+", " ", line).strip()

            # Часто внутри поста остаются технические строки цитирования.
            if line in {"Цитата", "Quote", "Reply"}:
                continue

            if not line:
                if not previous_blank:
                    lines.append("")
                previous_blank = True
            else:
                lines.append(line)
                previous_blank = False

        content = "\n".join(lines).strip()

        if not content:
            continue

        author = ""
        try:
            # Несколько типичных мест, где XenForo хранит имя автора.
            for selector in [
                ".username",
                ".message-name .username",
                ".message-attribution-opposite a.username",
            ]:
                loc = article.locator(selector).first
                if loc.count() > 0:
                    author = loc.inner_text(timeout=1_500).strip()
                    if author:
                        break
        except Exception:
            pass

        posts.append(
            {
                "post_id": post_id,
                "author": author,
                "text": content,
            }
        )

    return posts


def normalize_thread_url(url: str) -> str:
    # Убираем ?page=..., anchor и т.п., сохраняя адрес темы.
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "", "")
    )


def save_law(
    output_dir: Path,
    thread_url: str,
    title: str,
    posts: list[dict],
    page_count: int,
) -> Path:
    thread_id = thread_id_from_url(thread_url)
    filename = safe_filename(f"{thread_id}__{title}.txt")
    path = output_dir / filename

    chunks = [
        title,
        "=" * min(100, max(20, len(title))),
        f"Источник: {thread_url}",
        f"Страниц темы: {page_count}",
        f"Сообщений: {len(posts)}",
        "",
    ]

    for index, post in enumerate(posts, start=1):
        if post["author"]:
            chunks.append(f"[Сообщение {index} | Автор: {post['author']}]")
        else:
            chunks.append(f"[Сообщение {index}]")

        chunks.append(post["text"])
        chunks.append("")
        chunks.append("=" * 80)
        chunks.append("")

    path.write_text("\n".join(chunks), encoding="utf-8")
    return path


def parse_thread(page, url: str) -> dict:
    base_url = normalize_thread_url(url)

    print(f"\n[THREAD] {base_url}")

    page.goto(
        make_page_url(base_url, 1),
        wait_until="domcontentloaded",
        timeout=NAVIGATION_TIMEOUT_MS,
    )

    wait_for_real_forum(page)

    # Иногда форум дозагружает контент после JS.
    try:
        page.locator("article.message, h1").first.wait_for(timeout=15_000)
    except PlaywrightTimeoutError:
        pass

    title = get_thread_title(page, base_url)
    max_page = get_max_page(page)

    print(f"  Название: {title}")
    print(f"  Страниц найдено: {max_page}")

    all_posts = []
    seen = set()

    for page_number in range(1, max_page + 1):
        target_url = make_page_url(base_url, page_number)
        print(f"  [Страница {page_number}/{max_page}] {target_url}")

        if page_number > 1:
            page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT_MS,
            )
            wait_for_real_forum(page)

        # Немного времени для динамических элементов.
        page.wait_for_timeout(500)

        posts = extract_posts(page)

        # Если пагинация изменилась после загрузки, обновляем максимум.
        current_max = get_max_page(page)
        if current_max > max_page:
            max_page = current_max
            print(f"    Пагинация расширилась: теперь страниц {max_page}")

        added = 0
        for post in posts:
            key = post["post_id"]

            # Если ID не нашли, используем первые 200 символов как fallback.
            if not key:
                key = (post["author"], post["text"][:200])

            if key in seen:
                continue

            seen.add(key)
            all_posts.append(post)
            added += 1

        print(f"    Получено: {len(posts)}, новых: {added}")

        time.sleep(DELAY_SECONDS)

    return {
        "url": base_url,
        "title": title,
        "posts": all_posts,
        "pages": max_page,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(exist_ok=True)

    urls = load_urls()

    if not urls:
        print("Файл urls.txt пуст.")
        return 1

    print(f"URL в очереди: {len(urls)}")
    print(f"Выходная папка: {OUTPUT_DIR}")
    print(f"HEADLESS: {HEADLESS}")
    print()

    results = []

    with sync_playwright() as p:
        # Chromium сохраняет cookies/localStorage в profile,
        # чтобы JS-защиту не приходилось проходить заново после каждого запуска.
        profile_dir = BASE_DIR / ".browser-profile"

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )

        page = context.pages[0] if context.pages else context.new_page()

        # Не загружаем тяжёлые изображения/видео — текст закона от этого не зависит.
        def route_handler(route):
            resource_type = route.request.resource_type
            if resource_type in {"image", "media", "font"}:
                route.abort()
            else:
                route.continue_()

        page.route("**/*", route_handler)

        try:
            for index, url in enumerate(urls, start=1):
                print(f"\n{'#' * 70}")
                print(f"ЗАКОН {index}/{len(urls)}")
                print(f"{'#' * 70}")

                try:
                    result = parse_thread(page, url)

                    out = save_law(
                        OUTPUT_DIR,
                        result["url"],
                        result["title"],
                        result["posts"],
                        result["pages"],
                    )

                    print(
                        f"  ГОТОВО: {out.name} "
                        f"({result['pages']} стр., {len(result['posts'])} сообщений)"
                    )

                    results.append(
                        {
                            "url": result["url"],
                            "title": result["title"],
                            "file": str(out.relative_to(OUTPUT_DIR)),
                            "pages": result["pages"],
                            "posts": len(result["posts"]),
                            "status": "OK",
                        }
                    )

                except Exception as exc:
                    print(f"  ОШИБКА: {type(exc).__name__}: {exc}")
                    results.append(
                        {
                            "url": normalize_thread_url(url),
                            "title": "",
                            "file": "",
                            "pages": "",
                            "posts": "",
                            "status": f"ERROR: {exc}",
                        }
                    )

                    # При ошибке сохраняем диагностический скриншот.
                    debug_dir = BASE_DIR / "debug"
                    debug_dir.mkdir(exist_ok=True)

                    try:
                        page.screenshot(
                            path=str(
                                debug_dir
                                / f"error_{thread_id_from_url(url)}.png"
                            ),
                            full_page=True,
                        )
                    except Exception:
                        pass

                    # Переходим дальше к следующему закону.
                    continue

        finally:
            context.close()

    # CSV-индекс.
    index_file = OUTPUT_DIR / "index.csv"
    with index_file.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["url", "title", "file", "pages", "posts", "status"],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(results)

    ok = sum(1 for r in results if r["status"] == "OK")
    errors = len(results) - ok

    print("\n" + "=" * 70)
    print("ГОТОВО")
    print("=" * 70)
    print(f"Успешно: {ok}")
    print(f"Ошибок:  {errors}")
    print(f"Файлы:   {OUTPUT_DIR}")
    print(f"Индекс:  {index_file}")

    return 0 if errors == 0 else 2


if __name__ == "__main__":
    # Keep the historical entry point; all new parsing lives in the backend.
    from amazing_overlay.cli import main as backend_main
    sys.exit(backend_main())
