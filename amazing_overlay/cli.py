"""Headless parser command, using the same backend as the clients."""
import argparse
import json
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from .settings import ROOT
from .transport import ensure_backend, rpc


def main():
    parser = argparse.ArgumentParser(description="Amazing laws parser / shared backend")
    parser.add_argument("--urls", type=Path, default=ROOT / "local" / "urls.txt" if (ROOT / "local" / "urls.txt").exists() else ROOT / "urls.txt")
    parser.add_argument("--visible", action="store_true", help="Show browser for manual challenge checks")
    parser.add_argument("--export", type=Path, help="Export successful database documents to TXT/JSON/CSV")
    args = parser.parse_args()
    app = QCoreApplication.instance() or QCoreApplication([])
    ensure_backend()
    results = rpc("sources_import", {"text": args.urls.read_text(encoding="utf-8-sig")})
    ids = [item["id"] for item in results if "id" in item]
    errors = [item for item in results if "error" in item]
    for error in errors:
        print(error["error"] + ": " + error["url"])
    if not ids:
        print("Нет корректных ссылок")
        return 2
    ident = rpc("update", {"ids": ids, "visible": args.visible})["job_id"]
    last = None
    try:
        while True:
            state = rpc("job_status", {"id": ident})
            progress = state.get("progress", {})
            if progress != last:
                print(json.dumps(progress, ensure_ascii=False), flush=True)
                last = progress
            if state["state"] != "running":
                break
            time.sleep(0.35)
    except KeyboardInterrupt:
        rpc("job_cancel", {"id": ident})
        return 130
    if state["state"] != "done":
        print(state.get("error", "Ошибка"))
        return 2
    failed = errors or any(not item["ok"] for item in state["result"])
    if args.export:
        ident = rpc("export", {"directory": str(args.export.resolve())})["job_id"]
        while (state := rpc("job_status", {"id": ident}))["state"] == "running":
            time.sleep(0.2)
        if state["state"] != "done":
            print(state.get("error", "Ошибка экспорта"))
            return 2
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
