"""Opt-in online regression: isolated browser, never replaces the user's laws."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    with tempfile.TemporaryDirectory() as directory:
        os.environ["AMAZING_OVERLAY_DATA"] = directory
        from playwright.sync_api import sync_playwright
        from amazing_overlay.documents import document_text
        from amazing_overlay.parser import browser_context, parse_thread
        from amazing_overlay.settings import ROOT
        from amazing_overlay.browsers import configure_browser_environment

        configure_browser_environment()
        with sync_playwright() as playwright:
            context = browser_context(playwright, visible="--visible" in sys.argv)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                doc = parse_thread(page, "https://forum.amazing-online.com/threads/trudovoj-kodeks.1034845/",
                                   progress=lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
                tables = []
                def visit(node):
                    if node["tag"] == "table":
                        tables.append(node)
                    for child in node.get("children", []):
                        visit(child)
                for post in doc["posts"]:
                    for node in post["blocks"]:
                        visit(node)
                assert tables, "No tables found in the labour code"
                assert "Статья 12" in document_text(doc)
                output = ROOT / ".cache" / "live-labour-code.json"
                output.parent.mkdir(exist_ok=True)
                output.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
                (ROOT / ".cache" / "live-forum.html").write_text(page.content(), encoding="utf-8")
                print(f"OK: posts={len(doc['posts'])}, tables={len(tables)}; {output}")
            finally:
                context.close()


if __name__ == "__main__":
    main()
