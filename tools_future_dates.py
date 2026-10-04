"""Report articles whose publication date lies in the future."""
import sys
sys.path.insert(0, '.')

from datetime import datetime, timezone
from app.db import one, query

print("nu (UTC):", datetime.now(timezone.utc).isoformat()[:19])

FUTURE = "datetime(published_at) > datetime('now','+1 hour')"
n = one("SELECT COUNT(*) n FROM articles WHERE " + FUTURE)["n"]
tot = one("SELECT COUNT(*) n FROM articles")["n"]
print("artikelen met toekomstige datum: %d van %d" % (n, tot))

by_pub = query("SELECT publisher_id, COUNT(*) n FROM articles WHERE " + FUTURE
               + " GROUP BY publisher_id ORDER BY n DESC")
for r in by_pub:
    print("  %-7s %d" % (r["publisher_id"], r["n"]))

print()
for a in query("SELECT publisher_id, published_at, title FROM articles WHERE "
               + FUTURE + " ORDER BY published_at DESC LIMIT 8"):
    print("  %-6s %s  %s" % (a["publisher_id"], a["published_at"][:16], a["title"][:60]))
