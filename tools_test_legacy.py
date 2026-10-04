"""Checks on the one-off hand-over of the pre-sign-in 'local' account."""
import sys

sys.path.insert(0, ".")

from app import personalise as P  # noqa: E402
from app.db import query, tx  # noqa: E402

ok = fail = 0


def check(label, got, want):
    global ok, fail
    good = got == want
    ok, fail = ok + good, fail + (not good)
    print(f"{label:<56} -> {str(got):<10} (verwacht {want}) {'OK' if good else 'FOUT'}")


def count(user):
    """Rows that belong to a user across all three personal tables.

    Must match what claim_legacy_data() moves, including recommendations -
    an earlier version of this helper left those out and made the migration
    look wrong when it was the count that was incomplete.
    """
    total = 0
    for table in ("user_preferences", "user_interactions", "recommendations"):
        total += query(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id=?",
                       (user,))[0]["n"]
    return total


NEW = "u:newowner@example.com"
BUSY = "u:busy@example.com"

legacy_before = count("local")
print(f"'local' bevat {legacy_before} rijen")

with tx() as c:
    for u in (NEW, BUSY):
        c.execute("DELETE FROM user_preferences WHERE user_id=?", (u,))
        c.execute("DELETE FROM user_interactions WHERE user_id=?", (u,))

try:
    # A user who already has data must not swallow the legacy account.
    P.add_favourite("eigen", "Eigen", user=BUSY)
    check("gebruiker met eigen data erft niets", P.claim_legacy_data(BUSY), 0)
    check("en 'local' is ongemoeid", count("local"), legacy_before)

    # The legacy account cannot claim itself.
    check("'local' claimt zichzelf niet", P.claim_legacy_data("local"), 0)

    # A fresh owner inherits everything, once.
    moved = P.claim_legacy_data(NEW)
    check("verse eigenaar erft alles", moved, legacy_before)
    check("'local' is nu leeg", count("local"), 0)
    check("tweede keer doet niets", P.claim_legacy_data(NEW), 0)
    check("favorieten staan bij de nieuwe eigenaar",
          len(P.list_favourites(user=NEW)) > 0, True)
finally:
    # Put everything back exactly where it was.
    with tx() as c:
        for table in ("user_preferences", "user_interactions", "recommendations"):
            c.execute(f"UPDATE {table} SET user_id='local' WHERE user_id=?", (NEW,))
        for u in (NEW, BUSY):
            c.execute("DELETE FROM user_preferences WHERE user_id=?", (u,))
            c.execute("DELETE FROM user_interactions WHERE user_id=?", (u,))

restored = count("local")
print(f"\n'local' hersteld: {restored} rijen "
      f"({'ongewijzigd' if restored == legacy_before else 'AFWIJKING!'})")
if restored != legacy_before:
    fail += 1

print(f"\n{ok}/{ok + fail} geslaagd")
sys.exit(1 if fail else 0)
