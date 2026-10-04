"""Hammer the database from many threads at once.

Reproduces the Azure failure mode locally: several readers and a writer hitting
one SQLite file simultaneously. Run with NIEUWS_SERIALISE=1 to exercise the
shared-connection path that the network share needs.
"""
import os
import sys
import threading
import time

sys.path.insert(0, ".")

from app import db  # noqa: E402

READERS = 12
WRITERS = 3
SECONDS = 6

errors: list[str] = []
counts = {"read": 0, "write": 0}
lock = threading.Lock()
stop = threading.Event()


def reader():
    while not stop.is_set():
        try:
            db.query("SELECT id, headline FROM stories ORDER BY frontpage_score DESC LIMIT 20")
            db.one("SELECT COUNT(*) AS n FROM articles")
            with lock:
                counts["read"] += 1
        except Exception as exc:
            with lock:
                errors.append(f"read: {type(exc).__name__}: {exc}")


def writer(n: int):
    i = 0
    while not stop.is_set():
        i += 1
        try:
            db.set_meta(f"stresstest_{n}", str(i))
            with lock:
                counts["write"] += 1
        except Exception as exc:
            with lock:
                errors.append(f"write: {type(exc).__name__}: {exc}")
        time.sleep(0.01)


print(f"journal={db.JOURNAL_MODE} serialise={db.SERIALISE} db={db.DB_PATH}")
db.init_db()

threads = [threading.Thread(target=reader, daemon=True) for _ in range(READERS)]
threads += [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(WRITERS)]
for t in threads:
    t.start()
time.sleep(SECONDS)
stop.set()
for t in threads:
    t.join(timeout=10)

with db.tx() as c:
    c.execute("DELETE FROM meta WHERE key LIKE 'stresstest_%'")

print(f"reads={counts['read']} writes={counts['write']} fouten={len(errors)}")
for e in errors[:5]:
    print("  ", e)
sys.exit(1 if errors else 0)
