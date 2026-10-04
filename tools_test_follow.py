"""Verify that following keeps a read story on the front page."""
import sys
sys.path.insert(0, '.')

from app.db import init_db, query, tx
from app import personalise as P

init_db()
rows = query("SELECT id, last_updated_at FROM stories ORDER BY frontpage_score DESC LIMIT 2")
story, other = rows[0]["id"], rows[1]["id"]
stories = [dict(r) for r in rows]
USER = "test_follow"

with tx() as c:
    c.execute("DELETE FROM user_interactions WHERE user_id=?", (USER,))

results = []


def check(label, got, want):
    ok = got == want
    print("%-52s -> %-6s (verwacht %-6s) %s" % (label, got, want, "OK" if ok else "FAIL"))
    results.append(ok)


def visible(sid):
    return sid in P.unread_story_ids(
        stories, P.read_state(user=USER), P.followed_story_ids(user=USER))


check("start: zichtbaar", visible(story), True)

P.record_interaction("open", story_id=story, user=USER)
check("na lezen: verdwenen", visible(story), False)

P.record_interaction("follow", story_id=story, user=USER)
check("na volgen: weer zichtbaar", visible(story), True)
check("staat in followed_story_ids", story in P.followed_story_ids(user=USER), True)

P.record_interaction("unfollow", story_id=story, user=USER)
check("na ontvolgen: weer verdwenen", visible(story), False)

# volgen -> ontvolgen -> volgen (laatste wint, geen setverschil-bug)
P.record_interaction("follow", story_id=story, user=USER)
P.record_interaction("unfollow", story_id=story, user=USER)
P.record_interaction("follow", story_id=story, user=USER)
check("volgen/ontvolgen/volgen", visible(story), True)

# volgen werkt ook vóór het lezen
P.record_interaction("follow", story_id=other, user=USER)
P.record_interaction("open", story_id=other, user=USER)
check("eerst volgen, dan lezen: blijft", visible(other), True)

listed = P.followed_stories(user=USER)
check("followed_stories levert koppen",
      len(listed) == 2 and all(s.get("headline") for s in listed), True)

# uitgezet filter laat alles zien, volgen verandert daar niets aan
check("ongevolgd verhaal nog steeds verborgen",
      visible(story) and story in P.followed_story_ids(user=USER), True)

with tx() as c:
    c.execute("DELETE FROM user_interactions WHERE user_id=?", (USER,))

print("\n%d/%d geslaagd" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
