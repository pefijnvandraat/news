"""Report (and optionally purge) user-added publishers that have no feeds left."""
import sqlite3
import sys

c = sqlite3.connect("data/nieuws.db")
c.row_factory = sqlite3.Row
rows = c.execute(
    "SELECT p.id, p.name FROM publishers p "
    "WHERE p.user_added=1 "
    "AND NOT EXISTS (SELECT 1 FROM sources s WHERE s.publisher_id=p.id)"
).fetchall()

if not rows:
    print("geen wees-uitgevers")
else:
    for r in rows:
        print("wees:", r["id"], "-", r["name"])
    if "--purge" in sys.argv:
        c.executemany("DELETE FROM publishers WHERE id=?", [(r["id"],) for r in rows])
        c.commit()
        print(f"{len(rows)} opgeruimd")
