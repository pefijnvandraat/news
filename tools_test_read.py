"""Verify that read stories drop off the front page and return when updated."""
import sys
sys.path.insert(0, '.')

from app.db import init_db, query, tx
from app import personalise as P
from app.util import iso, now

init_db()
rows = query("SELECT id, last_updated_at FROM stories ORDER BY frontpage_score DESC LIMIT 2")
story, other = rows[0]["id"], rows[1]["id"]
USER = "test_read"

with tx() as c:
    c.execute("DELETE FROM user_interactions WHERE user_id=?", (USER,))

results = []


def check(label, got, want):
    ok = got == want
    print("%-50s -> %-6s (verwacht %-6s) %s" % (label, got, want, "OK" if ok else "FAIL"))
    results.append(ok)


def is_unread(sid, stories=None):
    stories = stories or [dict(r) for r in query(
        "SELECT id, last_updated_at FROM stories WHERE id IN (?,?)", (story, other))]
    return sid in P.unread_story_ids(stories, P.read_state(USER))


check("start: ongelezen", is_unread(story), True)

P.record_interaction("open", story_id=story, user=USER)
check("na openen: niet meer ongelezen", is_unread(story), False)
check("ander verhaal blijft ongelezen", is_unread(other), True)

# Doorklikken naar de bron telt ook als gelezen.
P.record_interaction("unread", story_id=story, user=USER)
check("als ongelezen gemarkeerd", is_unread(story), True)
P.record_interaction("open_article", story_id=story, user=USER)
check("na doorklik naar bron: gelezen", is_unread(story), False)

# Opnieuw lezen na ongelezen markeren moet weer werken (laatste-wint).
P.record_interaction("unread", story_id=story, user=USER)
P.record_interaction("open", story_id=story, user=USER)
check("lezen -> ongelezen -> lezen", is_unread(story), False)

# Bijgewerkt na het leesmoment -> opnieuw nieuws voor de gebruiker.
future = iso(now().replace(year=now().year + 1))
fake = [{"id": story, "last_updated_at": future}]
check("bijgewerkt na lezen: komt terug",
      story in P.unread_story_ids(fake, P.read_state(USER)), True)

listed = P.read_stories(USER)
check("read_stories bevat het verhaal",
      any(s["id"] == story and s.get("headline") for s in listed), True)
check("read_stories meldt bijwerking", listed[0]["updated_since_read"], False)

n = P.mark_unread(user=USER)
check("alles als ongelezen", (n >= 1, is_unread(story)), (True, True))

with tx() as c:
    c.execute("DELETE FROM user_interactions WHERE user_id=?", (USER,))

print("\n%d/%d geslaagd" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
