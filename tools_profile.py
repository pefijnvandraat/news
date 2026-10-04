"""Profile the stages of a priority sweep. Usage: py tools_profile.py [priority|full]"""
import sys
import time
sys.path.insert(0, '.')

from app.db import init_db
from app.cluster import recluster
from app.ingest.pipeline import priority_source_ids, run_ingest
from app.ranking import compute_front_page_scores

scope = sys.argv[1] if len(sys.argv) > 1 else 'priority'
init_db()

ids = priority_source_ids(24, 12) if scope == 'priority' else None
t = time.time(); rep = run_ingest(source_ids=ids, scope=scope); t_ingest = time.time() - t
t = time.time(); cl = recluster(); t_cluster = time.time() - t
t = time.time(); compute_front_page_scores(); t_score = time.time() - t

print("scope   %s" % scope)
print("ingest  %6.1fs  (%s feeds, new=%s updated=%s)"
      % (t_ingest, rep['source_count'], rep['new'], rep['updated']))
print("cluster %6.1fs  (%s)" % (t_cluster, cl))
print("score   %6.1fs" % t_score)
print("TOTAL   %6.1fs" % (t_ingest + t_cluster + t_score))
