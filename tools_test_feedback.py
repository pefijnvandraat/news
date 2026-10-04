"""Verify that explicit more/less feedback behaves as a three-way state."""
import sys
sys.path.insert(0, '.')

from app.db import init_db, query, tx
from app import personalise as P

init_db()
story = query("SELECT id FROM stories ORDER BY frontpage_score DESC LIMIT 1")[0]["id"]
USER = "test_feedback"

with tx() as c:
    c.execute("DELETE FROM user_interactions WHERE user_id=?", (USER,))


def state():
    fb = P.explicit_feedback(USER).get(story)
    neg = P.negative_signals(USER).get(story, 0.0)
    return (fb[0] if fb else None), round(neg, 3)


def step(action, expect_fb, expect_neg_positive, label):
    if action:
        P.record_interaction(action, story_id=story, user=USER)
    fb, neg = state()
    ok_fb = fb == expect_fb
    ok_neg = (neg > 0) == expect_neg_positive
    print("%-28s -> feedback=%-14s neg=%-6s %s"
          % (label, fb, neg, "OK" if (ok_fb and ok_neg) else "FAIL"))
    return ok_fb and ok_neg


results = [
    step(None, None, False, "start: geen feedback"),
    step("more", "more", False, "klik Meer zo"),
    step("more", "more", False, "  (log blijft append-only)"),
    step("feedback_clear", None, False, "klik Meer zo opnieuw -> uit"),
    step("less", "less", True, "klik Minder zo"),
    step("more", "more", False, "klik Meer zo -> vervangt Minder"),
    step("less", "less", True, "klik Minder zo -> vervangt Meer"),
    step("feedback_clear", None, False, "klik Minder zo opnieuw -> uit"),
]

# Topic affinity must not keep a withdrawn verdict.
P.record_interaction("more", story_id=story, user=USER)
with_more = P.learned_interests(USER)
P.record_interaction("feedback_clear", story_id=story, user=USER)
cleared = P.learned_interests(USER)
same = with_more != cleared
print("%-28s -> affinity verandert na intrekken: %s" % ("learned_interests", "OK" if same else "FAIL"))
results.append(same)

with tx() as c:
    c.execute("DELETE FROM user_interactions WHERE user_id=?", (USER,))

print("\n%d/%d geslaagd" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
