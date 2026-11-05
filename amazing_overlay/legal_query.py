"""Resolve explicit RP-code/article references without mixing different codes."""
from __future__ import annotations

import re
from datetime import datetime

# Full titles deliberately precede abbreviations. УК must never match УПК/УИК.
CODES = {
    "УК": r"уголовн(?:ый|ого|ому|ом)\s+кодекс\w*|ук",
    "УПК": r"уголовно[\s-]+процессуальн\w*\s+кодекс\w*|упк",
    "УИК": r"уголовно[\s-]+исполнительн\w*\s+кодекс\w*|уик",
    "ТК": r"трудов\w*\s+кодекс\w*|тк",
    "КоАП": r"кодекс\w*\s+об\s+административн\w*\s+правонарушени\w*|коап",
    "КАС": r"кодекс\w*\s+административн\w*\s+судопроизводств\w*|кас",
    "ГК": r"гражданск\w*\s+кодекс\w*|гк",
    "СК": r"семейн\w*\s+кодекс\w*|ск",
}
ARTICLE = re.compile(r"^\s*(?:статья|ст\.?)\s*(\d+(?:\.\d+)*)(?=\s|[.:—-]|$)", re.I)
NUMBER = r"\d+(?:\.\d+)*"


def code_in(text: str):
    text = text.casefold().replace("ё", "е")
    for code, pattern in CODES.items():
        match = re.search(r"(?<![^\W\d_])(?:" + pattern + r")(?![^\W\d_])", text)
        if match:
            return code, match
    return None, None


def reference(query: str):
    """Return an explicit code and article numbers, never arbitrary body numbers."""
    code, match = code_in(query)
    numbers = re.findall(r"(?<!\w)(?:статья|статьи|статью|ст\.?)\s*(" + NUMBER + r")", query, re.I)
    if match and not numbers:
        before, after = query[:match.start()], query[match.end():]
        adjacent = re.search(r"(?<![\w.])(" + NUMBER + r")\s*$", before)
        if not adjacent:
            adjacent = re.match(r"\s*(" + NUMBER + r")(?![\w.])", after)
        if adjacent:
            numbers = [adjacent.group(1)]
    return code, list(dict.fromkeys(numbers))


def version_key(doc):
    dates = []
    for day, month, year in re.findall(r"\b(\d{1,2})[./](\d{1,2})[./](20\d{2})\b", doc["title"]):
        try:
            dates.append(datetime(int(year), int(month), int(day)))
        except ValueError:
            pass
    # Publication date in the title describes the law version. Fetch time alone
    # must not make an old TXT imported today newer than a dated current code.
    return (max(dates).isoformat() if dates else "", doc.get("updated", ""),
            bool(doc.get("structured")), doc["id"])


def latest_code(documents, code):
    candidates = [doc for doc in documents if code_in(doc["title"])[0] == code]
    return max(candidates, key=version_key) if candidates else None
