"""Transport-independent backend API. No widgets and no game dependencies."""
from __future__ import annotations

import threading
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ai
from . import __version__
from .documents import canonical_url, document_html
from .parser import Cancelled, browser_session, check_cancel, export_laws, parse_thread, update_sources
from .browsers import configure_browser_environment
from .settings import DATA, ROOT, load_settings, save_settings, preview_settings
from .store import Store


class Backend:
    def __init__(self, data=DATA, root=ROOT):
        configure_browser_environment()
        self.store = Store(data / "laws.db")
        self.lock = threading.RLock()
        self.browser_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="backend-job")
        self.jobs = {}
        self.import_warnings = self.store.import_legacy(root / "local" / "laws" if (root / "local" / "laws").exists() else root / "laws")
        urls = root / "local" / "urls.txt" if (root / "local" / "urls.txt").exists() else root / "urls.txt"
        if not self.store.get_preference("sources_initialized", False):
            if urls.exists():
                for line in urls.read_text(encoding="utf-8-sig").splitlines():
                    try:
                        self.store.add_source(line)
                    except ValueError:
                        pass
            self.store.set_preference("sources_initialized", True)

    def job(self, kind, action, error_settings=None):
        ident = uuid.uuid4().hex
        with self.lock:
            # Bound retained responses without evicting active jobs.
            finished = [key for key, value in self.jobs.items() if value["state"] != "running"]
            for key in finished[:-24]:
                del self.jobs[key]
            if sum(v["state"] == "running" for v in self.jobs.values()) >= 8:
                raise ValueError("Слишком много активных задач")
            self.jobs[ident] = {"state": "running", "kind": kind, "cancel": threading.Event(), "progress": {}}

        def progress(value):
            with self.lock:
                self.jobs[ident]["progress"].update(value)

        def run():
            try:
                result = action(self.jobs[ident]["cancel"], progress)
                with self.lock:
                    self.jobs[ident].update(state="done", result=result)
            except Cancelled as exc:
                with self.lock:
                    self.jobs[ident].update(state="cancelled", error=str(exc))
            except Exception as exc:
                message = ai.friendly_error(exc, error_settings or load_settings(True)) if kind in {"ai", "ai-test"} else str(exc)
                with self.lock:
                    self.jobs[ident].update(state="error", error=message)
        self.executor.submit(run)
        return {"job_id": ident}

    def dispatch(self, method, params):
        if method == "health":
            return {"version": 1, "app_version": __version__, "native_settings": True, "documents": len(self.store.documents()), "data_dir": str(self.store.path.parent),
                    "browsers_dir": configure_browser_environment(), "import_warnings": self.import_warnings}
        if method == "documents":
            return self.store.documents()
        if method == "document":
            doc = self.store.document(params["id"])
            return {"document": doc, "html": document_html(doc)}
        if method == "search":
            return self.store.search(params.get("query", ""), params.get("doc_ids"), params.get("limit", 40))
        if method == "versions":
            return self.store.versions(params["id"])
        if method == "sources":
            return self.store.sources()
        if method == "source_add":
            return {"id": self.store.add_source(params["url"])}
        if method == "source_update":
            self.store.update_source(params["id"], params.get("enabled"), params.get("delete", False))
            return True
        if method == "sources_import":
            results = []
            for line in params["text"].splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                try:
                    results.append({"url": line, "id": self.store.add_source(line)})
                except ValueError as exc:
                    results.append({"url": line, "error": str(exc)})
            return results
        if method == "settings":
            return load_settings()
        if method == "settings_save":
            return save_settings(params)
        if method == "state_get":
            return self.store.get_preference(params["key"], params.get("default"))
        if method == "state_set":
            self.store.set_preference(params["key"], params["value"])
            return True
        if method == "job_status":
            with self.lock:
                return copy.deepcopy({k: v for k, v in self.jobs[params["id"]].items() if k != "cancel"})
        if method == "job_cancel":
            with self.lock:
                self.jobs[params["id"]]["cancel"].set()
            return True
        if method in {"preview", "update", "browser_check"}:
            if method == "preview":
                canonical_url(params["url"])

            def parse(cancel, progress):
                if not self.browser_lock.acquire(blocking=False):
                    raise ValueError("Браузер занят другой задачей парсинга")
                try:
                    if method == "update":
                        return update_sources(self.store, params.get("ids"), cancel, progress, params.get("visible", False))
                    with browser_session(params.get("visible", False)) as context:
                        page = context.pages[0] if context.pages else context.new_page()
                        if method == "browser_check":
                            page.set_content("<html><head><title>Amazing browser check</title></head><body>OK</body></html>")
                            return {"ok": page.title() == "Amazing browser check", "browsers_dir": configure_browser_environment()}
                        doc = parse_thread(page, params["url"], cancel, progress)
                        return {"document": doc, "html": document_html(doc)}
                finally:
                    self.browser_lock.release()
            return self.job(method, parse)
        if method == "preview_save":
            with self.lock:
                job = self.jobs[params["job_id"]]
                if job["kind"] != "preview" or job["state"] != "done":
                    raise ValueError("Предпросмотр ещё не завершён")
                doc = job["result"]["document"]
            self.store.add_source(doc["url"])
            self.store.publish(doc)
            if params.get("replaces") and params["replaces"] != doc["id"]:
                self.store.update_source(params["replaces"], delete=True)
            return {"id": doc["id"]}
        if method == "export":
            return self.job("export", lambda cancel, progress: export_laws(self.store, Path(params["directory"])))
        if method == "ai":
            return self.job("ai", lambda cancel, progress: ai.ask(self.store, load_settings(True), params, cancel, progress))
        if method == "ai_test":
            settings = preview_settings(params) if params else load_settings(True)
            def test(cancel, progress):
                answer, usage = ai.completion(settings, [{"role": "user", "content": "Ответь одним словом: работает"}], cancel, progress, 128)
                return {"answer": answer, "usage": usage, "provider": settings["ai_provider"], "model": settings["ai_model"]}
            return self.job("ai-test", test, error_settings=settings)
        raise ValueError("Неизвестный метод API")

    def close(self):
        with self.lock:
            for job in self.jobs.values():
                job["cancel"].set()
        self.executor.shutdown(wait=True, cancel_futures=True)
