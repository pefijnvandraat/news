"""Story ranking.

Two deliberately separate models:
  * front_page_score  - editorial importance, identical for everyone
  * my_news_score     - personal relevance, computed per user

Importance never reduces to "many articles were published": source count is
one of several capped signals alongside recency, independent-publisher
breadth, whether the story is still moving, publisher prominence and the
newsworthiness of its category.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

from .db import query, tx
from .util import age_hours, parse_dt

# Categories differ in general news weight. Soft weights only.
CATEGORY_IMPORTANCE = {
    "politiek": 1.00, "nederland": 0.95, "buitenland": 0.92, "economie": 0.88,
    "fryslan": 0.80, "tech": 0.76, "cultuur": 0.62, "sport": 0.60,
    "entertainment": 0.48, "overig": 0.55,
}

FP_WEIGHTS = {
    "recency": 0.34,
    "breadth": 0.26,
    "updating": 0.10,
    "prominence": 0.14,
    "category": 0.16,
}


def _recency(hours: float, half_life: float = 7.0) -> float:
    return math.exp(-hours / half_life)


def _breadth(publisher_count: int) -> float:
    """Diminishing returns: 4+ independent publishers is already maximal."""
    return min(1.0, math.log(1 + publisher_count) / math.log(5))


def compute_front_page_scores() -> int:
    rows = query(
        """SELECT s.id, s.category, s.publisher_count, s.article_count, s.is_updating,
                  s.last_updated_at, s.first_published_at,
                  (SELECT GROUP_CONCAT(DISTINCT a.publisher_id) FROM articles a
                    WHERE a.story_id = s.id) AS pubs
           FROM stories s"""
    )
    pub_weights = {r["id"]: float(r["weight"]) for r in query("SELECT id,weight FROM publishers")}

    scored: list[tuple[str, float, float, float]] = []
    for r in rows:
        hours = age_hours(parse_dt(r["last_updated_at"]))
        pubs = [p for p in (r["pubs"] or "").split(",") if p]
        prominence = (max((pub_weights.get(p, 1.0) for p in pubs), default=1.0) - 0.8) / 0.45
        prominence = max(0.0, min(1.0, prominence))
        parts = {
            "recency": _recency(hours),
            "breadth": _breadth(r["publisher_count"]),
            "updating": 1.0 if r["is_updating"] else 0.0,
            "prominence": prominence,
            "category": CATEGORY_IMPORTANCE.get(r["category"] or "overig", 0.55),
        }
        fp = sum(FP_WEIGHTS[k] * v for k, v in parts.items())

        # Importance is the time-independent part: it answers "how big is this
        # story", not "how fresh is it".
        importance = (0.45 * parts["breadth"] + 0.25 * parts["category"]
                      + 0.18 * parts["prominence"] + 0.12 * parts["updating"])

        # Trending compares coverage breadth against how quickly it arrived.
        first = parse_dt(r["first_published_at"])
        span = max(1.0, age_hours(first) - hours) if first else 1.0
        trending = parts["breadth"] * _recency(hours, 10.0) * min(1.0, 3.0 / span)

        scored.append((r["id"], round(fp, 6), round(importance, 6), round(trending, 6)))

    with tx() as c:
        c.executemany(
            "UPDATE stories SET frontpage_score=?, importance=?, trending_score=? WHERE id=?",
            [(fp, imp, tr, sid) for sid, fp, imp, tr in scored],
        )
    return len(scored)


# ---------------------------------------------------------------------------
# Personal ranking
# ---------------------------------------------------------------------------
MY_WEIGHTS = {
    "explicit": 0.40,     # topics the user chose themselves
    "learned": 0.18,      # inferred from reading behaviour
    "recency": 0.16,
    "importance": 0.14,
    "regional": 0.07,
    "publisher": 0.05,
}


def my_news_score(story: dict, favourites: dict[str, float],
                  learned: dict[str, float], publisher_affinity: dict[str, float],
                  story_topics: list[str], negative: float,
                  regional_interest: bool) -> tuple[float, list[dict]]:
    """Return (score, reasons). Reasons drive the 'Waarom zie ik dit?' panel."""
    reasons: list[dict] = []

    explicit_hits = [t for t in story_topics if t in favourites]
    explicit = min(1.0, sum(favourites[t] for t in explicit_hits) / 1.6) if explicit_hits else 0.0
    for t in explicit_hits[:3]:
        reasons.append({"kind": "favourite", "topic": t,
                        "text": f"Omdat je '{_label(t)}' volgt"})

    learned_hits = [(t, learned[t]) for t in story_topics if t in learned and learned[t] > 0.08]
    learned_hits.sort(key=lambda kv: -kv[1])
    learned_score = min(1.0, sum(v for _t, v in learned_hits) / 1.4) if learned_hits else 0.0
    for t, _v in learned_hits[:2]:
        if t not in favourites:
            reasons.append({"kind": "learned", "topic": t,
                            "text": f"Omdat je vaker nieuws over '{_label(t)}' leest"})

    hours = age_hours(parse_dt(story.get("last_updated_at")))
    recency = _recency(hours, 9.0)
    importance = float(story.get("importance") or 0.0)
    if importance > 0.62 and not explicit_hits and not learned_hits:
        reasons.append({"kind": "importance", "text": "Groot nieuws op dit moment"})

    regional = 1.0 if (regional_interest and story.get("geo_scope") == "fryslan") else 0.0
    if regional:
        reasons.append({"kind": "regional", "text": "Regionaal nieuws uit Frysl\u00e2n"})

    pub_pref = max((publisher_affinity.get(p, 0.0)
                    for p in story.get("publisher_ids", [])), default=0.0)
    if pub_pref > 0.35:
        reasons.append({"kind": "publisher",
                        "text": "Omdat je vaker artikelen van deze uitgever opent"})

    score = (MY_WEIGHTS["explicit"] * explicit
             + MY_WEIGHTS["learned"] * learned_score
             + MY_WEIGHTS["recency"] * recency
             + MY_WEIGHTS["importance"] * importance
             + MY_WEIGHTS["regional"] * regional
             + MY_WEIGHTS["publisher"] * min(1.0, pub_pref))
    score *= (1.0 - min(0.85, negative))
    if negative > 0.2:
        reasons.append({"kind": "negative", "text": "Lager gerangschikt door je feedback"})
    if not reasons:
        reasons.append({"kind": "fallback", "text": "Actueel nieuws van vandaag"})
    return score, reasons


def _label(slug: str) -> str:
    from .topics import topic_label
    row = query("SELECT label FROM topics WHERE slug=?", (slug,))
    return row[0]["label"] if row else topic_label(slug)


def diversify(ranked: list[dict], max_per_topic: int = 3,
              max_per_publisher: int = 4) -> list[dict]:
    """Stop one topic or publisher from dominating the feed."""
    topic_seen: dict[str, int] = defaultdict(int)
    pub_seen: dict[str, int] = defaultdict(int)
    out, deferred = [], []
    for item in ranked:
        primary = (item.get("topics") or [None])[0]
        pubs = item.get("publisher_ids") or []
        over_topic = primary is not None and topic_seen[primary] >= max_per_topic
        over_pub = bool(pubs) and all(pub_seen[p] >= max_per_publisher for p in pubs)
        if over_topic or over_pub:
            deferred.append(item)
            continue
        if primary:
            topic_seen[primary] += 1
        for p in pubs:
            pub_seen[p] += 1
        out.append(item)
    return out + deferred


def load_story_topics(story_ids: list[str]) -> dict[str, list[str]]:
    if not story_ids:
        return {}
    marks = ",".join("?" * len(story_ids))
    rows = query(
        f"""SELECT st.story_id, t.slug, st.weight FROM story_topics st
            JOIN topics t ON t.id = st.topic_id
            WHERE st.story_id IN ({marks}) ORDER BY st.weight DESC""",
        story_ids,
    )
    out: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        out[r["story_id"]].append(r["slug"])
    return out


def story_publishers(story_ids: list[str]) -> dict[str, list[str]]:
    if not story_ids:
        return {}
    marks = ",".join("?" * len(story_ids))
    rows = query(
        f"SELECT story_id, GROUP_CONCAT(DISTINCT publisher_id) AS pubs FROM articles "
        f"WHERE story_id IN ({marks}) GROUP BY story_id", story_ids)
    return {r["story_id"]: [p for p in (r["pubs"] or "").split(",") if p] for r in rows}


def parse_json_list(value) -> list:
    try:
        return json.loads(value or "[]")
    except (ValueError, TypeError):
        return []
