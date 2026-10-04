"""Proof that two signed-in users cannot see each other's data.

This is the test that matters. The bug being fixed was invisible with one
user: everything worked, it just all belonged to the same account. So the
check is explicitly adversarial - user A does something, then we assert user B
cannot see it, for every kind of personal state the app stores.
"""
import os
import sys

sys.path.insert(0, ".")

from app import personalise as P  # noqa: E402
from app import quickfilters as Q  # noqa: E402
from app import stocks as S  # noqa: E402
from app.db import query, tx  # noqa: E402

A = "u:anna@example.com"
B = "u:bob@example.com"

ok = fail = 0


def check(label, got, want):
    global ok, fail
    good = got == want
    ok, fail = ok + good, fail + (not good)
    print(f"{label:<58} -> {str(got):<22} (verwacht {want}) {'OK' if good else 'FOUT'}")


def wipe():
    with tx() as c:
        for u in (A, B):
            c.execute("DELETE FROM user_preferences WHERE user_id=?", (u,))
            c.execute("DELETE FROM user_interactions WHERE user_id=?", (u,))
            c.execute("DELETE FROM recommendations WHERE user_id=?", (u,))


story = query("SELECT id FROM stories LIMIT 2")
if len(story) < 2:
    print("te weinig verhalen in de database om te testen")
    sys.exit(1)
S1, S2 = story[0]["id"], story[1]["id"]

wipe()
legacy_before = query("SELECT COUNT(*) AS n FROM user_preferences "
                      "WHERE user_id='local'")[0]["n"]
legacy_int_before = query("SELECT COUNT(*) AS n FROM user_interactions "
                          "WHERE user_id='local'")[0]["n"]
try:
    # --- favourites ------------------------------------------------------
    P.add_favourite("ai", "AI", user=A)
    P.add_favourite("voetbal", "Voetbal", user=B)
    check("A ziet alleen eigen favoriet",
          [f["slug"] for f in P.list_favourites(user=A)], ["ai"])
    check("B ziet alleen eigen favoriet",
          [f["slug"] for f in P.list_favourites(user=B)], ["voetbal"])

    P.remove_favourite("ai", user=B)          # B may not delete A's
    check("B kan favoriet van A niet verwijderen",
          [f["slug"] for f in P.list_favourites(user=A)], ["ai"])

    # --- reading history (the privacy leak) -------------------------------
    P.record_interaction("open", story_id=S1, user=A)
    check("A heeft S1 gelezen", S1 in P.read_state(user=A), True)
    check("B ziet leesgeschiedenis van A NIET", S1 in P.read_state(user=B), False)
    check("B's leeslijst is leeg", len(P.read_stories(user=B)), 0)

    # --- hidden ----------------------------------------------------------
    P.record_interaction("hide", story_id=S2, user=A)
    check("A heeft S2 verborgen", S2 in P.hidden_story_ids(user=A), True)
    check("bij B blijft S2 zichtbaar", S2 in P.hidden_story_ids(user=B), False)

    # --- follow / save ---------------------------------------------------
    P.record_interaction("follow", story_id=S1, user=A)
    P.record_interaction("save", story_id=S2, user=B)
    check("volgen is persoonlijk",
          (S1 in P.followed_story_ids(user=A), S1 in P.followed_story_ids(user=B)),
          (True, False))
    check("bewaren is persoonlijk",
          (S2 in P.saved_story_ids(user=B), S2 in P.saved_story_ids(user=A)),
          (True, False))

    # --- thumbs ----------------------------------------------------------
    P.record_interaction("more", story_id=S1, user=A)
    P.record_interaction("less", story_id=S1, user=B)
    check("duim omhoog bij A", P.explicit_feedback(user=A)[S1][0], "more")
    check("duim omlaag bij B", P.explicit_feedback(user=B)[S1][0], "less")

    # --- settings --------------------------------------------------------
    P.set_setting("hide_read", "off", user=A)
    check("instelling van A", P.get_setting("hide_read", "on", user=A), "off")
    check("B houdt de standaardwaarde", P.get_setting("hide_read", "on", user=B), "on")

    # --- quick filters ---------------------------------------------------
    Q.add(A, "Fryslân", "location=fryslan")
    Q.add(B, "Tech", "category=tech")
    check("snelfilters van A", [f["label"] for f in Q.list_all(A)], ["Fryslân"])
    check("snelfilters van B", [f["label"] for f in Q.list_all(B)], ["Tech"])
    Q.remove(B, "fryslan")                     # B may not delete A's
    check("B kan snelfilter van A niet verwijderen", len(Q.list_all(A)), 1)

    # Same label for both users must not collide on the slug.
    Q.add(B, "Fryslân", "location=fryslan&hours=24")
    check("zelfde naam bij twee gebruikers kan",
          (len(Q.list_all(A)), len(Q.list_all(B))), (1, 2))

    # --- stocks ----------------------------------------------------------
    with tx() as c:
        for u, sym in ((A, "AAA.AS"), (B, "BBB.AS")):
            c.execute("INSERT INTO user_preferences(id,user_id,kind,topic_slug,value,"
                      "weight,created_at,updated_at) VALUES(?,?,'stock',?,?,1.0,'t','t')",
                      (f"stk_{u}_{sym}", u, sym, sym))
    check("koerslijst van A", [w["symbol"] for w in S.watchlist(A)], ["AAA.AS"])
    check("koerslijst van B", [w["symbol"] for w in S.watchlist(B)], ["BBB.AS"])
    S.remove(B, "AAA.AS")
    check("B kan koers van A niet verwijderen", len(S.watchlist(A)), 1)

    # --- privacy snapshot ------------------------------------------------
    snap = P.privacy_snapshot(user=B)
    check("privacy-overzicht van B toont A's favoriet niet",
          any(f["slug"] == "ai" for f in snap["favourites"]), False)
    check("privacy-overzicht van B telt A's gelezen niet", snap["read_total"], 0)

    # --- reset is scoped -------------------------------------------------
    before_a = len(query("SELECT id FROM user_interactions WHERE user_id=?", (A,)))
    P.reset_learned(user=B)
    after_a = len(query("SELECT id FROM user_interactions WHERE user_id=?", (A,)))
    check("reset door B laat A's gedrag staan", after_a, before_a)

    # --- no leakage into the legacy account ------------------------------
    # Count before and after rather than checking whether a slug exists: the
    # local account legitimately holds the owner's own pre-sign-in favourites,
    # so "ai is present" proves nothing. A change in count would.
    legacy_after = query("SELECT COUNT(*) AS n FROM user_preferences "
                         "WHERE user_id='local'")[0]["n"]
    check("'local' is niet gegroeid tijdens de test", legacy_after, legacy_before)
    legacy_int_after = query("SELECT COUNT(*) AS n FROM user_interactions "
                             "WHERE user_id='local'")[0]["n"]
    check("geen interacties naar 'local' geschreven",
          legacy_int_after, legacy_int_before)
finally:
    wipe()

print(f"\n{ok}/{ok + fail} geslaagd")
sys.exit(1 if fail else 0)
