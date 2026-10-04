"""Verify hide/unhide and save/unsave behave as a latest-wins toggle."""
import sys
sys.path.insert(0, '.')

from app.db import init_db, query, tx
from app import personalise as P

init_db()
rows = query("SELECT id FROM stories ORDER BY frontpage_score DESC LIMIT 2")
story, other = rows[0]["id"], rows[1]["id"]
USER = "test_toggle"

with tx() as c:
    c.execute("DELETE FROM user_interactions WHERE user_id=?", (USER,))

results = []


def check(label, got, want):
    ok = got == want
    print("%-44s -> %-5s (verwacht %-5s) %s" % (label, got, want, "OK" if ok else "FAIL"))
    results.append(ok)


def hidden():
    return story in P.hidden_story_ids(user=USER)


def saved():
    return story in P.saved_story_ids(user=USER)


check("start: niet verborgen", hidden(), False)
P.record_interaction("hide", story_id=story, user=USER)
check("verberg", hidden(), True)
P.record_interaction("unhide", story_id=story, user=USER)
check("weer tonen", hidden(), False)
P.record_interaction("hide", story_id=story, user=USER)
check("opnieuw verbergen (setverschil faalde hier)", hidden(), True)
P.record_interaction("unhide", story_id=story, user=USER)
check("opnieuw tonen", hidden(), False)

check("start: niet bewaard", saved(), False)
P.record_interaction("save", story_id=story, user=USER)
check("bewaar", saved(), True)
P.record_interaction("unsave", story_id=story, user=USER)
check("verwijder uit bewaard", saved(), False)
P.record_interaction("save", story_id=story, user=USER)
check("opnieuw bewaren", saved(), True)

# unhide_all moet alles terugzetten
P.record_interaction("hide", story_id=story, user=USER)
P.record_interaction("hide", story_id=other, user=USER)
n = P.unhide_all(user=USER)
check("unhide_all zet beide terug", (n, len(P.hidden_story_ids(user=USER))), (2, 0))

# hidden_stories levert leesbare rijen
P.record_interaction("hide", story_id=story, user=USER)
listed = P.hidden_stories(user=USER)
check("hidden_stories geeft kop terug",
      bool(listed) and bool(listed[0].get("headline")), True)

with tx() as c:
    c.execute("DELETE FROM user_interactions WHERE user_id=?", (USER,))

print("\n%d/%d geslaagd" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
