"""Inspect the local personalisation profile (favourites, feedback, signals)."""
import sys
sys.path.insert(0, '.')

from app.db import query
from app import personalise as P

print("favorieten        :", [f["label"] for f in P.list_favourites()])
print("bewaarde verhalen :", len(P.saved_story_ids()))
print("verborgen verhalen:", len(P.hidden_story_ids()))
print()

fb = P.explicit_feedback()
print("actieve duim-feedback (%d):" % len(fb))
for sid, (action, _ts) in fb.items():
    row = query("SELECT headline FROM stories WHERE id=?", (sid,))
    print("  %-6s %s" % (action, row[0]["headline"][:62] if row else sid))
print()

print("interacties per type:")
for r in query("SELECT action, COUNT(*) n FROM user_interactions "
               "WHERE user_id='local' GROUP BY action ORDER BY n DESC"):
    print("  %-16s %d" % (r["action"], r["n"]))
