"""Report the local database's journal mode and user-data counts."""
import sqlite3

c = sqlite3.connect("data/nieuws.db")
print("journal mode:", c.execute("PRAGMA journal_mode").fetchone()[0])
for kind in ("favorite_topic", "quickfilter", "stock", "setting"):
    n = c.execute("SELECT COUNT(*) FROM user_preferences WHERE kind=?",
                  (kind,)).fetchone()[0]
    print(f"  {kind:<16} {n}")
print("interacties:", c.execute("SELECT COUNT(*) FROM user_interactions").fetchone()[0])
