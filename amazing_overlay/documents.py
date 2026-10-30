"""Versioned, sanitized document tree shared by both renderers and AI."""
from __future__ import annotations

import html
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag

FORUM_HOST = "forum.amazing-online.com"
ALLOWED = {"p", "div", "span", "br", "strong", "b", "em", "i", "u", "s",
           "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table",
           "thead", "tbody", "tfoot", "tr", "th", "td", "blockquote", "a", "img", "hr"}


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or parts.hostname != FORUM_HOST or parts.username or parts.port:
        raise ValueError("Нужна ссылка на тему forum.amazing-online.com")
    match = re.match(r"^/threads/((?:[^/]*\.)?\d+)(?:/|$)", parts.path)
    if not match:
        raise ValueError("В ссылке не найден номер темы")
    return urlunsplit(("https", FORUM_HOST, f"/threads/{match[1]}/", "", ""))


def thread_id(value: str) -> str:
    return canonical_url(value).rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[-1]


def safe_url(value: str, base: str) -> str:
    target = urljoin(base, value)
    return target if urlsplit(target).scheme in {"http", "https"} else ""


def parse_html(value: str, base: str) -> list[dict]:
    soup = BeautifulSoup(value, "html.parser")
    for node in soup.select("script,style,iframe,object,form,.message-signature,.reactionsBar,.bbCodeBlock-expandLink,.bbCodeBlock-title,div[style*='user-select:none'][style*='border-radius: 3px 20px 3px 20px']"):
        node.decompose()

    def convert(node):
        if isinstance(node, NavigableString):
            return {"tag": "text", "text": str(node).replace("\xa0", " ").replace("\u2800", " ").replace("\u200b", "")}
        if not isinstance(node, Tag):
            return None
        children = [converted for child in node.children if (converted := convert(child)) is not None]
        tag = node.name if node.name in ALLOWED else "div"
        result = {"tag": tag, "children": children}
        if tag in {"td", "th"}:
            for attr in ("rowspan", "colspan"):
                try:
                    result[attr] = max(1, min(100, int(node.get(attr, 1))))
                except (ValueError, TypeError):
                    result[attr] = 1
        if tag == "a":
            result["href"] = safe_url(node.get("href", ""), base)
        if tag == "img":
            result["src"] = safe_url(node.get("src", ""), base)
            result["alt"] = node.get("alt", "Изображение")
        # XenForo headings are often styled spans rather than h2 elements.
        plain = node.get_text(" ", strip=True)
        if tag in {"p", "div", "span"} and len(plain) < 240 and re.match(r"^(Статья\s+\d+|ГЛАВА\s+\d+)", plain):
            if not any(c.get("tag") in {"p", "div", "table"} for c in children):
                result["tag"] = "h3"
        return result

    return [converted for child in soup.children if (converted := convert(child)) is not None]


def node_html(node: dict) -> str:
    tag = node["tag"]
    if tag == "text":
        return html.escape(node.get("text", ""))
    if tag not in ALLOWED:
        return ""
    attrs = ""
    if tag in {"td", "th"}:
        attrs = f' rowspan="{int(node.get("rowspan", 1))}" colspan="{int(node.get("colspan", 1))}"'
    if tag == "a":
        attrs = f' href="{html.escape(node.get("href", ""), quote=True)}"'
    if tag == "img":
        # Remote loading is deliberate: a local reader must work offline.
        url = html.escape(node.get("src", ""), quote=True)
        return f'<a href="{url}">[{html.escape(node.get("alt", "Изображение"))}]</a>'
    body = "".join(node_html(c) for c in node.get("children", []))
    return f"<{tag}{attrs}>{body}</{tag}>"


def node_text(node: dict) -> str:
    tag = node["tag"]
    if tag == "text":
        return node.get("text", "")
    if tag == "img":
        return f"[{node.get('alt', 'Изображение')}: {node.get('src', '')}]"
    if tag == "br":
        return "\n"
    if tag == "table":
        return table_text(node)
    body = "".join(node_text(c) for c in node.get("children", []))
    if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "blockquote"}:
        return "\n" + body + "\n"
    return body


def table_grid(table: dict) -> list[list[str]]:
    rows = []

    def visit(node):
        if node["tag"] == "tr":
            rows.append(node)
        elif node["tag"] != "table" or node is table:
            for child in node.get("children", []):
                visit(child)
    visit(table)
    grid = {}
    for r, row in enumerate(rows):
        col = 0
        for cell in row.get("children", []):
            if cell["tag"] not in {"td", "th"}:
                continue
            while (r, col) in grid:
                col += 1
            text = re.sub(r"\s+", " ", node_text(cell)).strip()
            for dr in range(cell.get("rowspan", 1)):
                for dc in range(cell.get("colspan", 1)):
                    grid[r + dr, col + dc] = text
            col += cell.get("colspan", 1)
    width = max((c + 1 for _, c in grid), default=0)
    return [[grid.get((r, c), "") for c in range(width)] for r in range(len(rows))]


def table_text(table: dict) -> str:
    rows = table_grid(table)
    if not rows:
        return ""
    # Multi-row headers (including rowspan/colspan) contribute to column labels.
    # The first row is a header even when XenForo uses td instead of th.
    raw_rows = []
    def visit(node):
        if node["tag"] == "tr":
            raw_rows.append(node)
        elif node["tag"] != "table" or node is table:
            for child in node.get("children", []):
                visit(child)
    visit(table)
    header_count = 1
    for row in raw_rows[1:]:
        cells = [c for c in row.get("children", []) if c["tag"] in {"td", "th"}]
        if cells and all(c["tag"] == "th" for c in cells):
            header_count += 1
        else:
            break
    headings = [" / ".join(dict.fromkeys(rows[r][c] for r in range(min(header_count, len(rows))) if rows[r][c])) for c in range(len(rows[0]))]
    lines = [" | ".join(headings)]
    for row in rows[header_count:]:
        lines.append(" | ".join(f"{headings[i] or 'Колонка ' + str(i + 1)}: {cell}" for i, cell in enumerate(row)))
    return "\n" + "\n".join(lines) + "\n"


def document_html(doc: dict) -> str:
    parts = ["<style>body{color:#e2e8f0;} table{border-collapse:collapse;} td,th{border:1px solid #64748b;padding:8px;} th{background:#253348;} h3{color:#93c5fd;} a{color:#7dd3fc;}</style>"]
    for post in doc["posts"]:
        parts.append(f'<a name="post-{post["post_id"]}"></a>')
        parts.extend(node_html(node) for node in post["blocks"])
    return "".join(parts)


def document_text(doc: dict) -> str:
    return "\n\n".join("".join(node_text(n) for n in p["blocks"]) for p in doc["posts"])


def chunks(doc: dict) -> list[dict]:
    result = []
    for post in doc["posts"]:
        text = "".join(node_text(n) for n in post["blocks"])
        sections = re.split(r"(?=Статья\s+\d+[.\s])", text)
        for section in sections:
            section = section.strip()
            if not section:
                continue
            heading = section.splitlines()[0][:180]
            # Bound individual chunks while keeping adjacent chunks retrievable.
            for start in range(0, len(section), 4000):
                result.append({"ordinal": len(result), "heading": heading,
                               "post_id": post["post_id"], "text": section[start:start + 4000]})
    return result
