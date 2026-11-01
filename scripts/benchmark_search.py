"""Opt-in search benchmark on at least ten times the current TXT corpus."""
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from amazing_overlay.settings import ROOT
from amazing_overlay.store import Store


def main():
    base_bytes = sum(path.stat().st_size for path in (ROOT / "local" / "laws").glob("*.txt"))
    target_bytes = max(12_500_000, base_bytes * 10)
    text = "Статья 1. Порядок задержания\n" + "Сотрудник полиции соблюдает порядок задержания гражданина и основания досмотра. " * 1500
    documents = max(100, target_bytes // len(text.encode("utf-8")) + 1)
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "laws.db")
        for index in range(documents):
            store.publish({"schema_version": 1, "id": str(index), "title": f"Документ {index}", "url": "", "pages": 1,
                           "posts": [{"post_id": str(index), "author": "", "blocks": [{"tag": "p", "children": [{"tag": "text", "text": text}]}]}]})
        times = []
        for query in ["порядок задержания", "полиц", "досмотр", "несуществующийкварцит"] * 10:
            started = time.perf_counter()
            store.search(query)
            times.append((time.perf_counter() - started) * 1000)
        p95 = sorted(times)[int(len(times) * 0.95) - 1]
        result = {"documents": documents, "text_bytes": documents * len(text.encode("utf-8")), "queries": len(times),
                  "median_ms": round(statistics.median(times), 2), "p95_ms": round(p95, 2), "maximum_ms": round(max(times), 2),
                  "threshold_ms": 200, "passed": max(times) <= 200}
        output = ROOT / ".cache" / "search-benchmark.json"
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
