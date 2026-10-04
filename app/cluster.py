"""Story clustering: group articles that cover the same underlying event.

Signals combined: TF-IDF cosine over title+description tokens, named-entity
overlap, category agreement and publication-time proximity. Assignment is
incremental so story ids stay stable across runs (user interactions point at
them), with a periodic re-knit pass that merges stories that have drifted
together.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

from .db import query, tx
from .summarise import build_story_view
from .util import iso, new_id, now, parse_dt

# Tuning. Deliberately conservative: a false merge is far more damaging to
# trust than a missed merge, which merely shows two stories.
WINDOW_HOURS = 60            # candidate stories must be this recent
ASSIGN_THRESHOLD = 0.42      # combined similarity needed to join a story
MERGE_THRESHOLD = 0.56       # higher bar for merging two existing stories
MIN_TOKEN_COSINE = 0.16      # guard: shared names alone must never merge events
TIME_DECAY_HOURS = 30.0
W_TOKENS, W_ENTITIES, W_CATEGORY, W_TIME = 0.44, 0.40, 0.04, 0.12


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------
def _idf(docs: list[list[str]]) -> dict[str, float]:
    n = max(1, len(docs))
    df: dict[str, int] = defaultdict(int)
    for tokens in docs:
        for t in set(tokens):
            df[t] += 1
    return {t: math.log((n + 1) / (c + 0.5)) + 1.0 for t, c in df.items()}


def _vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf: dict[str, float] = defaultdict(float)
    for t in tokens:
        tf[t] += 1.0
    vec = {t: (1.0 + math.log(c)) * idf.get(t, 1.0) for t, c in tf.items()}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {t: v / norm for t, v in vec.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return sum(v * large.get(k, 0.0) for k, v in small.items())


def _entity_overlap(a: set[str], b: set[str]) -> float:
    """Blend of Jaccard and containment.

    Publishers name the same actor differently ("Jan Roelof Kruithof" vs
    "Jan Kruithof"), so pure Jaccard under-counts real agreement. Containment
    rescues those cases; Jaccard keeps it from over-firing on big sets.
    """
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    jac = inter / float(len(a | b))
    cont = inter / float(min(len(a), len(b)))
    return 0.45 * jac + 0.55 * cont


def _time_affinity(t1, t2) -> float:
    if t1 is None or t2 is None:
        return 0.3
    gap = abs((t1 - t2).total_seconds()) / 3600.0
    return math.exp(-gap / TIME_DECAY_HOURS)


def _entity_keys(entities_json: str | None) -> set[str]:
    """Full entity names plus their distinctive sub-tokens (surnames, places)."""
    from .topics import STOPWORDS

    try:
        ents = json.loads(entities_json or "[]")
    except (ValueError, TypeError):
        return set()
    keys: set[str] = set()
    for e in ents:
        if e.get("weight", 0) < 1.0:
            continue
        name = str(e.get("name", "")).lower()
        if not name:
            continue
        keys.add(name)
        for part in name.replace("'", " ").split():
            if len(part) >= 4 and part not in STOPWORDS:
                keys.add(part)
    return keys


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------
def _similarity(av, ae, acat, at, sv, se, scat, st) -> float:
    token_sim = _cosine(av, sv)
    if token_sim < MIN_TOKEN_COSINE:
        return 0.0   # shared names without shared wording is not the same event
    return (W_TOKENS * token_sim
            + W_ENTITIES * _entity_overlap(ae, se)
            + W_CATEGORY * (1.0 if acat and acat == scat else 0.0)
            + W_TIME * _time_affinity(at, st))


def recluster() -> dict:
    """Assign unclustered articles, then refresh every touched story."""
    rows = [dict(r) for r in query(
        "SELECT id,publisher_id,title,description,category,entities,tokens,topics,"
        "geo_scope,published_at,updated_at,story_id,image_url,url,revision "
        "FROM articles WHERE datetime(COALESCE(published_at, first_seen_at)) "
        ">= datetime('now', ?) ORDER BY datetime(COALESCE(published_at,first_seen_at)) ASC",
        (f"-{WINDOW_HOURS} hours",),
    )]
    if not rows:
        return {"assigned": 0, "created": 0, "merged": 0, "stories": 0}

    docs = []
    for r in rows:
        try:
            docs.append(json.loads(r["tokens"] or "[]"))
        except (ValueError, TypeError):
            docs.append([])
    idf = _idf(docs)

    for r, tokens in zip(rows, docs):
        r["_vec"] = _vector(tokens, idf)
        r["_ent"] = _entity_keys(r["entities"])
        r["_t"] = parse_dt(r["updated_at"] or r["published_at"])

    # Seed clusters from articles that already belong to a story.
    clusters: dict[str, dict] = {}
    for r in rows:
        sid = r["story_id"]
        if not sid:
            continue
        c = clusters.setdefault(sid, {"id": sid, "members": [], "vec": {}, "ent": set(),
                                      "cat": r["category"], "t": r["_t"], "new": False})
        c["members"].append(r)

    for c in clusters.values():
        _refresh_centroid(c)

    assigned = created = 0
    for r in rows:
        if r["story_id"]:
            continue
        best_id, best_score = None, 0.0
        for cid, c in clusters.items():
            if _time_affinity(r["_t"], c["t"]) < 0.08:
                continue
            s = _similarity(r["_vec"], r["_ent"], r["category"], r["_t"],
                            c["vec"], c["ent"], c["cat"], c["t"])
            if s > best_score:
                best_id, best_score = cid, s
        if best_id is not None and best_score >= ASSIGN_THRESHOLD:
            clusters[best_id]["members"].append(r)
            _refresh_centroid(clusters[best_id])
            r["story_id"] = best_id
            assigned += 1
        else:
            sid = new_id("sty_")
            clusters[sid] = {"id": sid, "members": [r], "vec": dict(r["_vec"]),
                             "ent": set(r["_ent"]), "cat": r["category"], "t": r["_t"],
                             "new": True}
            r["story_id"] = sid
            created += 1

    merged = _merge_pass(clusters)
    _persist(clusters, rows)
    return {"assigned": assigned, "created": created, "merged": merged,
            "stories": len(clusters)}


def _refresh_centroid(cluster: dict) -> None:
    members = cluster["members"]
    acc: dict[str, float] = defaultdict(float)
    ents: set[str] = set()
    cats: dict[str, int] = defaultdict(int)
    latest = None
    for m in members:
        for k, v in m["_vec"].items():
            acc[k] += v
        ents |= m["_ent"]
        if m["category"]:
            cats[m["category"]] += 1
        if m["_t"] and (latest is None or m["_t"] > latest):
            latest = m["_t"]
    norm = math.sqrt(sum(v * v for v in acc.values())) or 1.0
    cluster["vec"] = {k: v / norm for k, v in acc.items()}
    cluster["ent"] = ents
    cluster["cat"] = max(cats, key=cats.get) if cats else None
    cluster["t"] = latest


def _merge_pass(clusters: dict[str, dict]) -> int:
    """Merge clusters that have converged (e.g. after late coverage arrives)."""
    ids = list(clusters.keys())
    merged = 0
    for i, a_id in enumerate(ids):
        a = clusters.get(a_id)
        if a is None:
            continue
        for b_id in ids[i + 1:]:
            b = clusters.get(b_id)
            if b is None:
                continue
            s = _similarity(a["vec"], a["ent"], a["cat"], a["t"],
                            b["vec"], b["ent"], b["cat"], b["t"])
            if s < MERGE_THRESHOLD:
                continue
            # Keep the older story id so existing interactions keep resolving.
            keep, drop = (a, b) if len(a["members"]) >= len(b["members"]) else (b, a)
            keep["members"].extend(drop["members"])
            for m in drop["members"]:
                m["story_id"] = keep["id"]
            _refresh_centroid(keep)
            clusters.pop(drop["id"], None)
            merged += 1
            if drop is a:
                break
    return merged


def _display_entities(members: list[dict]) -> list[str]:
    """Human-presentable entity names (not the expanded matching keys)."""
    from collections import defaultdict as _dd

    weights: dict[str, float] = _dd(float)
    display: dict[str, str] = {}
    for m in members:
        try:
            ents = json.loads(m.get("entities") or "[]")
        except (ValueError, TypeError):
            continue
        for e in ents:
            name = str(e.get("name", "")).strip()
            if len(name) < 4 or not name[0].isupper():
                continue
            key = name.lower()
            weights[key] += float(e.get("weight", 0))
            if key not in display or len(name) > len(display[key]):
                display[key] = name
    ranked = sorted(weights.items(), key=lambda kv: -kv[1])
    out: list[str] = []
    for key, weight in ranked:
        name = display[key]
        multiword = " " in name
        # Multi-word phrases are rarely false positives; a lone capitalised word
        # needs stronger evidence because it is often just a sentence opener.
        if weight < (1.5 if multiword else 2.6):
            continue
        # Drop names already contained in a longer one we kept ("Christa" vs
        # "Christa Pike") so the chip row stays readable.
        if any(key in kept.lower() for kept in out):
            continue
        out.append(name)
        if len(out) >= 10:
            break
    return out


def _persist(clusters: dict[str, dict], rows: list[dict]) -> None:
    ts = iso(now())
    with tx() as c:
        for r in rows:
            c.execute("UPDATE articles SET story_id=? WHERE id=?", (r["story_id"], r["id"]))

        for cid, cluster in clusters.items():
            view = build_story_view(cluster["members"])
            exists = c.execute("SELECT id FROM stories WHERE id=?", (cid,)).fetchone()
            payload = {
                "id": cid,
                "headline": view["headline"],
                "headline_source": view["headline_source"],
                "summary": view["summary"],
                "summary_kind": "generated",
                "category": cluster["cat"] or "overig",
                "geo_scope": view["geo_scope"],
                "article_count": len(cluster["members"]),
                "publisher_count": view["publisher_count"],
                "first_published_at": view["first_published_at"],
                "last_updated_at": view["last_updated_at"],
                "image_url": view["image_url"],
                "centroid": json.dumps(dict(sorted(cluster["vec"].items(),
                                                   key=lambda kv: -kv[1])[:40])),
                "entity_set": json.dumps(_display_entities(cluster["members"]),
                                         ensure_ascii=False),
                "disagreements": json.dumps(view["disagreements"], ensure_ascii=False),
                "is_updating": view["is_updating"],
                "created_at": ts,
            }
            if exists:
                c.execute(
                    """UPDATE stories SET headline=:headline, headline_source=:headline_source,
                           summary=:summary, summary_kind=:summary_kind, category=:category,
                           geo_scope=:geo_scope, article_count=:article_count,
                           publisher_count=:publisher_count,
                           first_published_at=:first_published_at,
                           last_updated_at=:last_updated_at, image_url=:image_url,
                           centroid=:centroid, entity_set=:entity_set,
                           disagreements=:disagreements, is_updating=:is_updating
                       WHERE id=:id""", payload)
            else:
                c.execute(
                    """INSERT INTO stories(id,headline,headline_source,summary,summary_kind,
                           category,geo_scope,article_count,publisher_count,
                           first_published_at,last_updated_at,created_at,image_url,centroid,
                           entity_set,disagreements,is_updating)
                       VALUES(:id,:headline,:headline_source,:summary,:summary_kind,:category,
                           :geo_scope,:article_count,:publisher_count,:first_published_at,
                           :last_updated_at,:created_at,:image_url,:centroid,:entity_set,
                           :disagreements,:is_updating)""", payload)

            _sync_topics(c, cid, cluster["members"], ts)

        # Stories that lost all their articles (e.g. after a merge) are removed.
        c.execute("DELETE FROM stories WHERE id NOT IN (SELECT DISTINCT story_id "
                  "FROM articles WHERE story_id IS NOT NULL)")


def _sync_topics(c, story_id: str, members: list[dict], ts: str) -> None:
    from .topics import topic_kind, topic_label

    weights: dict[str, float] = defaultdict(float)
    for m in members:
        try:
            slugs = json.loads(m["topics"] or "[]")
        except (ValueError, TypeError):
            slugs = []
        for s in slugs:
            weights[s] += 1.0
    c.execute("DELETE FROM story_topics WHERE story_id=?", (story_id,))
    for slug, w in sorted(weights.items(), key=lambda kv: -kv[1])[:14]:
        row = c.execute("SELECT id FROM topics WHERE slug=?", (slug,)).fetchone()
        if row:
            topic_id = row["id"]
        else:
            topic_id = new_id("top_")
            c.execute("INSERT INTO topics(id,slug,label,kind,created_at) VALUES(?,?,?,?,?)",
                      (topic_id, slug, topic_label(slug), topic_kind(slug), ts))
        c.execute("INSERT OR REPLACE INTO story_topics(story_id,topic_id,weight) VALUES(?,?,?)",
                  (story_id, topic_id, w / max(1, len(members))))
