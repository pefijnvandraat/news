"""Show which stories carry the 'Wordt bijgewerkt' badge and why."""
import sys
sys.path.insert(0, '.')

from app.db import init_db, one, query
from app.cluster import recluster
from app.ranking import compute_front_page_scores
from app.util import age_hours, parse_dt

init_db()
recluster()
compute_front_page_scores()

tot = one('SELECT COUNT(*) n FROM stories')['n']
upd = one('SELECT COUNT(*) n FROM stories WHERE is_updating=1')['n']
print('flagged overall : %d of %d (%.0f%%)' % (upd, tot, 100.0 * upd / tot))

top = query("""SELECT id, headline, is_updating, article_count, publisher_count,
                      first_published_at, last_updated_at
               FROM stories ORDER BY frontpage_score DESC LIMIT 12""")
print('front-page top12: %d flagged\n' % sum(1 for r in top if r['is_updating']))

for r in top:
    first = parse_dt(r['first_published_at'])
    last = parse_dt(r['last_updated_at'])
    span = (last - first).total_seconds() / 3600.0 if first and last else 0.0
    rev = one('SELECT MAX(revision) m FROM articles WHERE story_id=?', (r['id'],))['m']
    why = []
    if rev > 1:
        why.append('herzien(rev=%d)' % rev)
    if span >= 3.0 and r['article_count'] > 1:
        why.append('late coverage(%.1fu)' % span)
    if age_hours(last) > 3.0:
        why = ['te oud (%.1fu stil)' % age_hours(last)]
    print('%s span=%4.1fu vers=%4.1fu arts=%-3d %-52s %s'
          % ('UPD' if r['is_updating'] else '   ', span, age_hours(last),
             r['article_count'], r['headline'][:52], ', '.join(why) or '-'))
