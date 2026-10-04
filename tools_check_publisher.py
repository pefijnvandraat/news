"""Show how a publisher's articles were categorised. Usage: py tools_check_publisher.py <id>"""
import sys
sys.path.insert(0, '.')

from app.db import query

pid = sys.argv[1] if len(sys.argv) > 1 else "tweakers"

print("categorieverdeling:")
for r in query("SELECT category, COUNT(*) n FROM articles WHERE publisher_id=? "
               "GROUP BY category ORDER BY n DESC", (pid,)):
    print("  %-14s %d" % (r["category"], r["n"]))

print("\nvoorbeelden:")
for a in query("SELECT category, author, image_url, title FROM articles "
               "WHERE publisher_id=? ORDER BY published_at DESC LIMIT 8", (pid,)):
    img = "img" if a["image_url"] else "—"
    print("  %-14s %-18s %-4s %s" % (a["category"], (a["author"] or "—")[:18], img,
                                     a["title"][:58]))

print("\nin verhalen met andere uitgevers:")
rows = query("""SELECT s.id, s.headline, s.publisher_count,
                       (SELECT GROUP_CONCAT(DISTINCT a2.publisher_id) FROM articles a2
                         WHERE a2.story_id = s.id) pubs
                FROM stories s JOIN articles a ON a.story_id = s.id
                WHERE a.publisher_id=? AND s.publisher_count > 1
                GROUP BY s.id ORDER BY s.frontpage_score DESC LIMIT 6""", (pid,))
for r in rows:
    print("  [%s] %s" % (r["pubs"], r["headline"][:62]))
if not rows:
    print("  (nog geen gedeelde verhalen)")
