"""Collect installed dependency notices into a portable build."""
import importlib.metadata
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-path", type=Path, default=ROOT / ".cache/dist" / "AmazingLawOverlay")
    parser.add_argument("--native-dir", type=Path)
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
        for build in ([args.native_dir, args.native_dir.parent] if args.native_dir else (ROOT / "native" / "out-local", ROOT / "native" / "out")):
            source = build / "_deps" / (name + "-src") / filename
            if source.exists():
                target = OUTPUT / name / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                break
    if args.native_dir:
        build_root = args.native_dir if (args.native_dir / "CMakeCache.txt").exists() else args.native_dir.parent
        dependency = build_root / "_deps/blackbone-src"
        cache = build_root / "CMakeCache.txt"
        if cache.exists():
            for line in cache.read_text(encoding="utf-8").splitlines():
                if line.startswith("FETCHCONTENT_SOURCE_DIR_BLACKBONE:PATH=") and line.split("=", 1)[1]:
                    dependency = Path(line.split("=", 1)[1])
        for source in dependency.rglob("*"):
            if source.is_file() and any(word in source.name.lower() for word in ("license", "copying", "notice")):
                target = OUTPUT / "blackbone" / source.relative_to(dependency)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        if not (OUTPUT / "blackbone/LICENSE").is_file():
            raise ValueError("Blackbone license missing")
    print(f"Dependency notices: {OUTPUT}")


if __name__ == "__main__":
    main()
