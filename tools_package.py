"""Build the App Service deployment zip.

Deliberately an allow-list, not an exclude-list: the data directory holds the
reading history, favourites and inferred interests, and a forgotten exclude
pattern would ship them to a public URL. Listing what goes in means a mistake
fails closed.
"""
from __future__ import annotations

import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "deploy.zip")

FILES = ["run.py", "requirements.txt", "gunicorn.conf.py"]
DIRS = ["app", "web"]
SKIP_SUFFIX = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log")
SKIP_DIRS = {"__pycache__", "data", ".venv", "venv"}


def main() -> None:
    if os.path.exists(OUT):
        os.remove(OUT)
    written = []
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            path = os.path.join(HERE, name)
            if os.path.exists(path):
                z.write(path, name)
                written.append(name)
        for folder in DIRS:
            root_dir = os.path.join(HERE, folder)
            for root, dirs, files in os.walk(root_dir):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in files:
                    if f.endswith(SKIP_SUFFIX):
                        continue
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, HERE).replace("\\", "/")
                    z.write(full, rel)
                    written.append(rel)

    leaked = [w for w in written if w.startswith("data/") or w.endswith(".db")]
    if leaked:
        raise SystemExit(f"ABORT: persoonlijke data in pakket: {leaked}")

    size = os.path.getsize(OUT) / 1024
    print(f"{OUT}  ({size:.0f} KB, {len(written)} bestanden)")
    for w in sorted(written):
        print("  ", w)


if __name__ == "__main__":
    main()
