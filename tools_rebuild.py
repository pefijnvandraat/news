"""Full rebuild: re-apply taxonomy/entity rules to every stored article,
then rebuild all stories from scratch. Safe to re-run."""
import sys
sys.path.insert(0, '.')

from app.db import init_db, one, query, tx
from app import config
from app.normalise import renormalise_all
from app.cluster import recluster
from app.ranking import compute_front_page_scores

init_db()
config.seed()
print('renormalised', renormalise_all())

with tx() as c:
    c.execute("DELETE FROM sources WHERE url='https://www.omropfryslan.nl/rss/sport'")
    c.execute("UPDATE articles SET story_id=NULL")
    c.execute("DELETE FROM stories")
    c.execute("""DELETE FROM topics WHERE id NOT IN (SELECT topic_id FROM story_topics)
                 AND slug NOT IN (SELECT COALESCE(topic_slug,'') FROM user_preferences)""")

print('cluster', recluster())
compute_front_page_scores()

print('\ntop multi-publisher stories')
for s in query("SELECT headline, publisher_count, article_count, category, entity_set, "
               "frontpage_score f FROM stories WHERE publisher_count>1 "
               "ORDER BY frontpage_score DESC LIMIT 10"):
    print("  %.3f [%dp/%da] %-12s %s" % (s['f'], s['publisher_count'], s['article_count'],
                                         s['category'], s['headline'][:66]))
    print("        entiteiten:", s['entity_set'][:110])

print('\nstories', one('SELECT COUNT(*) n FROM stories')['n'],
      '| multi', one('SELECT COUNT(*) n FROM stories WHERE publisher_count>1')['n'],
      '| fryslan', one("SELECT COUNT(*) n FROM stories WHERE geo_scope='fryslan'")['n'])
