from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .documents import canonical_url, chunks, document_text, thread_id


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Connections are short-lived and owned by the calling worker thread."""
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '', updated TEXT);
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, updated TEXT NOT NULL,
                    structured INTEGER NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (
                    doc_id TEXT NOT NULL, digest TEXT NOT NULL, updated TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(doc_id,digest));
                CREATE TABLE IF NOT EXISTS sections (
                    id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    title TEXT NOT NULL, heading TEXT NOT NULL, post_id TEXT NOT NULL, text TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS section_document ON sections(doc_id,ordinal);
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                    title,heading,text, tokenize='unicode61 remove_diacritics 2');
                CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS legacy_imports(path TEXT PRIMARY KEY,digest TEXT NOT NULL,doc_id TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_source(self, url: str):
        url = canonical_url(url)
        ident = thread_id(url)
        with self.connection() as db:
            db.execute("INSERT INTO sources(id,url) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET url=excluded.url", (ident, url))
            db.execute("UPDATE sources SET title=coalesce((SELECT title FROM documents WHERE id=?),title), updated=coalesce((SELECT updated FROM documents WHERE id=?),updated) WHERE id=?", (ident, ident, ident))
        return ident

    def sources(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY title,id")]

    def update_source(self, ident, enabled=None, delete=False):
        with self.connection() as db:
            if delete:
                db.execute("DELETE FROM sources WHERE id=?", (ident,))
            elif enabled is not None:
                db.execute("UPDATE sources SET enabled=? WHERE id=?", (int(bool(enabled)), ident))

    def status(self, ident, message):
        with self.connection() as db:
            db.execute("UPDATE sources SET status=? WHERE id=?", (message, ident))

    def publish(self, doc):
        if doc.get("schema_version") != 1 or not doc.get("posts") or not document_text(doc).strip():
            raise ValueError("Пустой или неподдерживаемый документ: предыдущая версия сохранена")
        body = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        # Capture time doesn't constitute a new content version.
        content = {k: v for k, v in doc.items() if k != "fetched_at"}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        ident = str(doc["id"])
        timestamp = doc.get("fetched_at", now())
        sections = chunks(doc)
        with self.connection() as db:
            old = db.execute("SELECT digest FROM documents WHERE id=?", (ident,)).fetchone()
            if old and old[0] == digest:
                db.execute("UPDATE documents SET updated=? WHERE id=?", (timestamp, ident))
                db.execute("UPDATE sources SET updated=?,status='Без изменений' WHERE id=?", (timestamp, ident))
                return False
            db.execute("INSERT OR IGNORE INTO versions VALUES(?,?,?,?)", (ident, digest, timestamp, body))
            db.execute("INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?,?)",
                       (ident, doc["title"], doc.get("url", ""), timestamp, int(doc.get("structured", True)), digest, body))
            db.execute("DELETE FROM search_index WHERE rowid IN (SELECT id FROM sections WHERE doc_id=?)", (ident,))
            db.execute("DELETE FROM sections WHERE doc_id=?", (ident,))
            for section in sections:
                cursor = db.execute("INSERT INTO sections(doc_id,ordinal,title,heading,post_id,text) VALUES(?,?,?,?,?,?)",
                                    (ident, section["ordinal"], doc["title"], section["heading"], section["post_id"], section["text"]))
                db.execute("INSERT INTO search_index(rowid,title,heading,text) VALUES(?,?,?,?)",
                           (cursor.lastrowid, doc["title"], section["heading"], section["text"]))
            db.execute("UPDATE sources SET title=?,updated=?,status='Обновлено' WHERE id=?", (doc["title"], timestamp, ident))
        return True

    def documents(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT id,title,url,updated,structured FROM documents ORDER BY title")]

    def document(self, ident):
        with self.connection() as db:
            row = db.execute("SELECT body FROM documents WHERE id=?", (str(ident),)).fetchone()
            if row is None:
                raise ValueError("Документ не найден")
            return json.loads(row[0])

    def search(self, query, doc_ids=None, limit=40):
        terms = re.findall(r"[\w]+", query.casefold(), flags=re.UNICODE)[:24]
        if not terms:
            return []
        match = " OR ".join('"' + term.replace('"', '') + '"*' for term in terms)
        where = ""
        args = [match]
        if doc_ids is not None:
            if not doc_ids:
                return []
            where = " AND s.doc_id IN (" + ",".join("?" for _ in doc_ids) + ")"
            args.extend(map(str, doc_ids))
        args.append(min(100, max(1, int(limit))))
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT s.*,bm25(search_index,5,3,1) AS rank,"
                "snippet(search_index,2,'[',']',' … ',24) AS snippet "
                "FROM search_index JOIN sections s ON s.id=search_index.rowid WHERE search_index MATCH ?" +
                where + " ORDER BY rank LIMIT ?", args)]

    def context(self, query, doc_ids=None, budget=32000):
        hits = self.search(query, doc_ids, 12)
        selected, seen = [], set()
        with self.connection() as db:
            for hit in hits:
                for row in db.execute("SELECT * FROM sections WHERE doc_id=? AND ordinal BETWEEN ? AND ? ORDER BY ordinal",
                                      (hit["doc_id"], hit["ordinal"] - 1, hit["ordinal"] + 1)):
                    if row["id"] not in seen:
                        seen.add(row["id"])
                        selected.append(dict(row))
        result, used = [], 0
        for section in selected:
            if used + len(section["text"]) > budget:
                continue
            used += len(section["text"])
            result.append(section)
        return result

    def get_preference(self, key, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_preference(self, key, value):
        if len(json.dumps(value)) > 100000:
            raise ValueError("Слишком большой размер состояния")
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def versions(self, ident):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT digest,updated FROM versions WHERE doc_id=? ORDER BY updated DESC", (str(ident),))]

    def import_legacy(self, directory: Path):
        warnings = []
        for path in sorted(directory.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            with self.connection() as db:
                previous = db.execute("SELECT digest FROM legacy_imports WHERE path=?", (str(path),)).fetchone()
            if previous and previous[0] == digest:
                continue
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("cp1251", errors="replace")
            text = text.replace("\u2800", " ").replace("\u200b", "")
            url_match = re.search(r"Источник:\s*(https?://\S+)", text)
            url = ""
            if url_match:
                try:
                    url = canonical_url(url_match[1])
                except ValueError:
                    pass
            ident = thread_id(url) if url else "legacy-" + hashlib.sha256(str(path.name).encode()).hexdigest()[:16]
            with self.connection() as db:
                existing = db.execute("SELECT structured FROM documents WHERE id=?", (ident,)).fetchone()
            if not existing:
                self.publish({"schema_version": 1, "id": ident, "url": url, "title": text.splitlines()[0] if text else path.stem,
                              "structured": False, "fetched_at": now(), "pages": 0,
                              "posts": [{"post_id": "legacy", "author": "", "blocks": [
                                  {"tag": "h3" if re.match(r"^(Статья\s+\d+|ГЛАВА\s+\d+)", line) else "p",
                                   "children": [{"tag": "text", "text": line}]} for line in text.splitlines() if line.strip()]}]})
            else:
                warnings.append(f"{path.name}: сохранён как исходный файл; документ {ident} уже импортирован")
            if url:
                self.add_source(url)
            with self.connection() as db:
                db.execute("INSERT OR REPLACE INTO legacy_imports VALUES(?,?,?)", (str(path), digest, ident))
        return warnings
