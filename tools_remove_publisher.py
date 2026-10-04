"""Remove a publisher and all of its articles. Usage: py tools_remove_publisher.py <id>"""
import sys
sys.path.insert(0, '.')
from app.db import init_db, tx

pid = sys.argv[1]
init_db()
with tx() as c:
    n = c.execute("SELECT COUNT(*) n FROM articles WHERE publisher_id=?", (pid,)).fetchone()["n"]
    c.execute("DELETE FROM articles WHERE publisher_id=?", (pid,))
    c.execute("DELETE FROM sources WHERE publisher_id=?", (pid,))
    c.execute("DELETE FROM publishers WHERE id=?", (pid,))
print(f"removed publisher {pid} and {n} articles")
