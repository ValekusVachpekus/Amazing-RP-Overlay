"""Collect installed dependency notices into a portable build."""
import importlib.metadata
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-path", type=Path, default=ROOT / ".cache/dist" / "AmazingLawOverlay")
    args = parser.parse_args()
    OUTPUT = args.bundle_path.resolve() / "licenses"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name", "unknown")
        for entry in distribution.files or []:
            if not (str(entry).endswith((".txt", ".md")) or "LICENSE" in entry.name.upper() or "COPYING" in entry.name.upper()):
                continue
            if "license" not in str(entry).lower() and "copying" not in entry.name.lower() and "notice" not in entry.name.lower():
                continue
            source = Path(distribution.locate_file(entry))
            if source.is_file():
                target = OUTPUT / name / entry.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    for name, filename in (("imgui", "LICENSE.txt"), ("minhook", "LICENSE.txt"), ("json", "LICENSE.MIT")):
        for build in (ROOT / "native" / "out-local", ROOT / "native" / "out"):
            source = build / "_deps" / (name + "-src") / filename
            if source.exists():
                target = OUTPUT / name / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                break
    print(f"Dependency notices: {OUTPUT}")


if __name__ == "__main__":
    main()
