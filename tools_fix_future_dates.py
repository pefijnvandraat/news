"""Clamp already-stored future publication dates to when we first saw them."""
import sys
sys.path.insert(0, '.')

from app.db import init_db, one, query, tx

init_db()
FUTURE = "datetime(published_at) > datetime('now','+10 minutes')"
rows = query("SELECT id, published_at, updated_at, first_seen_at FROM articles WHERE " + FUTURE)
print("te corrigeren:", len(rows))

with tx() as c:
    for r in rows:
        seen = r["first_seen_at"]
        pub = seen
        upd = r["updated_at"]
        if upd and upd > seen:
            upd = seen
        c.execute("UPDATE articles SET published_at=?, updated_at=? WHERE id=?",
                  (pub, upd or pub, r["id"]))

left = one("SELECT COUNT(*) n FROM articles WHERE " + FUTURE)["n"]
print("resterend in de toekomst:", left)
