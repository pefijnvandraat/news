"""User preferences, behavioural signals and the personalised feed.

Design rules enforced here:
  * explicit favourites always outweigh inferred interests;
  * recent behaviour decays slower than old behaviour;
  * the feed reserves slots for important stories outside the user's interests
    so it cannot collapse into a filter bubble;
  * every recommendation carries human-readable reasons;
  * nothing sensitive is inferred - only topic, publisher and story ids are
    stored, all locally.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

from .db import one, query, tx
from .ranking import (diversify, load_story_topics, my_news_score,
                      story_publishers)
from .topics import topic_kind, topic_label
from .util import age_hours, iso, new_id, now, parse_dt

USER = "local"

# How much each observed action says about interest. Negative actions are
# weaker in magnitude than explicit "less like this".
ACTION_WEIGHTS = {
    "open": 1.0,
    "open_article": 1.3,
    "save": 2.2,
    "more": 3.0,
    "unsave": -0.6,
    "hide": -2.0,
    "less": -3.0,
}
HALF_LIFE_DAYS = 10.0
SERENDIPITY_EVERY = 7     # 1 discovery slot per 7 personalised stories


# ---------------------------------------------------------------------------
# Favourites
# ---------------------------------------------------------------------------
def list_favourites(user: str = USER) -> list[dict]:
    rows = query(
        "SELECT topic_slug, weight, created_at FROM user_preferences "
        "WHERE user_id=? AND kind='favorite_topic' ORDER BY created_at",
        (user,))
    return [{"slug": r["topic_slug"], "label": _label(r["topic_slug"]),
             "weight": r["weight"], "since": r["created_at"]} for r in rows]


def add_favourite(slug: str, label: str | None = None, user: str = USER) -> dict:
    ts = iso(now())
    with tx() as c:
        if not c.execute("SELECT id FROM topics WHERE slug=?", (slug,)).fetchone():
            c.execute("INSERT INTO topics(id,slug,label,kind,created_at) VALUES(?,?,?,?,?)",
                      (new_id("top_"), slug, label or topic_label(slug), topic_kind(slug), ts))
        c.execute(
            "INSERT INTO user_preferences(id,user_id,kind,topic_slug,weight,created_at,updated_at)"
            " VALUES(?,?,'favorite_topic',?,1.0,?,?) "
            "ON CONFLICT(user_id,kind,COALESCE(topic_slug,'')) DO UPDATE SET updated_at=excluded.updated_at",
            (new_id("pref_"), user, slug, ts, ts))
    return {"slug": slug, "label": label or _label(slug)}


def remove_favourite(slug: str, user: str = USER) -> None:
    with tx() as c:
        c.execute("DELETE FROM user_preferences WHERE user_id=? AND kind='favorite_topic' "
                  "AND topic_slug=?", (user, slug))


def get_setting(key: str, default: str, user: str = USER) -> str:
    row = one("SELECT value FROM user_preferences WHERE user_id=? AND kind='setting' "
              "AND topic_slug=?", (user, key))
    return row["value"] if row else default


def set_setting(key: str, value: str, user: str = USER) -> None:
    ts = iso(now())
    with tx() as c:
        c.execute(
            "INSERT INTO user_preferences(id,user_id,kind,topic_slug,value,created_at,updated_at)"
            " VALUES(?,?,'setting',?,?,?,?) "
            "ON CONFLICT(user_id,kind,COALESCE(topic_slug,'')) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            (new_id("pref_"), user, key, value, ts, ts))


# ---------------------------------------------------------------------------
# Behavioural signals
# ---------------------------------------------------------------------------
def record_interaction(action: str, story_id: str | None = None,
                       article_id: str | None = None,
                       publisher_id: str | None = None, user: str = USER) -> None:
    if action not in ACTION_WEIGHTS:
        return
    with tx() as c:
        c.execute(
            "INSERT INTO user_interactions(id,user_id,story_id,article_id,publisher_id,"
            "action,weight,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (new_id("int_"), user, story_id, article_id, publisher_id, action,
             ACTION_WEIGHTS[action], iso(now())))


def _decay(created_at: str) -> float:
    days = age_hours(parse_dt(created_at)) / 24.0
    return math.pow(0.5, days / HALF_LIFE_DAYS)


def learned_interests(user: str = USER) -> dict[str, float]:
    """Decayed topic affinity inferred from behaviour, normalised to 0..1."""
    rows = query(
        """SELECT ui.action, ui.weight, ui.created_at, t.slug
           FROM user_interactions ui
           JOIN story_topics st ON st.story_id = ui.story_id
           JOIN topics t ON t.id = st.topic_id
           WHERE ui.user_id=? AND ui.story_id IS NOT NULL""", (user,))
    acc: dict[str, float] = defaultdict(float)
    for r in rows:
        acc[r["slug"]] += r["weight"] * _decay(r["created_at"])
    if not acc:
        return {}
    peak = max(abs(v) for v in acc.values()) or 1.0
    return {k: max(-1.0, min(1.0, v / peak)) for k, v in acc.items() if abs(v) > 0.05}


def publisher_affinity(user: str = USER) -> dict[str, float]:
    rows = query(
        "SELECT publisher_id, weight, created_at FROM user_interactions "
        "WHERE user_id=? AND publisher_id IS NOT NULL", (user,))
    acc: dict[str, float] = defaultdict(float)
    for r in rows:
        acc[r["publisher_id"]] += r["weight"] * _decay(r["created_at"])
    peak = max(acc.values(), default=0.0) or 1.0
    return {k: max(0.0, v / peak) for k, v in acc.items()}


def negative_signals(user: str = USER) -> dict[str, float]:
    """Per-story suppression from explicit 'less like this' / hide actions."""
    rows = query(
        "SELECT story_id, action, created_at FROM user_interactions "
        "WHERE user_id=? AND action IN ('hide','less') AND story_id IS NOT NULL", (user,))
    acc: dict[str, float] = defaultdict(float)
    for r in rows:
        acc[r["story_id"]] += (1.0 if r["action"] == "hide" else 0.7) * _decay(r["created_at"])
    return acc


def hidden_story_ids(user: str = USER) -> set[str]:
    hidden = {r["story_id"] for r in query(
        "SELECT story_id FROM user_interactions WHERE user_id=? AND action='hide'", (user,))}
    unhidden = {r["story_id"] for r in query(
        "SELECT story_id FROM user_interactions WHERE user_id=? AND action='unhide'", (user,))}
    return {s for s in hidden - unhidden if s}


def saved_story_ids(user: str = USER) -> set[str]:
    saved = {r["story_id"] for r in query(
        "SELECT story_id FROM user_interactions WHERE user_id=? AND action='save'", (user,))}
    unsaved = {r["story_id"] for r in query(
        "SELECT story_id FROM user_interactions WHERE user_id=? AND action='unsave'", (user,))}
    return {s for s in saved - unsaved if s}


# ---------------------------------------------------------------------------
# Personalised feed
# ---------------------------------------------------------------------------
def build_my_news(stories: list[dict], limit: int = 40, user: str = USER) -> dict:
    favourites = {f["slug"]: f["weight"] for f in list_favourites(user)}
    learned_all = learned_interests(user)
    learned = {k: v for k, v in learned_all.items() if v > 0}
    pub_aff = publisher_affinity(user)
    negatives = negative_signals(user)
    hidden = hidden_story_ids(user)
    regional = any(s in favourites for s in ("friesland", "fryslan", "makkum",
                                             "sc-heerenveen"))

    ids = [s["id"] for s in stories]
    topics_map = load_story_topics(ids)
    pubs_map = story_publishers(ids)

    scored: list[dict] = []
    for s in stories:
        if s["id"] in hidden:
            continue
        s = dict(s)
        s["topics"] = topics_map.get(s["id"], [])
        s["publisher_ids"] = pubs_map.get(s["id"], [])
        score, reasons = my_news_score(
            s, favourites, learned, pub_aff, s["topics"],
            negatives.get(s["id"], 0.0), regional)
        s["score"] = score
        s["reasons"] = reasons
        s["personalised"] = bool(favourites or learned)
        scored.append(s)

    scored.sort(key=lambda s: -s["score"])
    personalised = diversify(scored)

    cold_start = not favourites and not learned
    result = _inject_serendipity(personalised, favourites, learned) if not cold_start \
        else _cold_start(personalised)

    return {
        "cold_start": cold_start,
        "has_favourites": bool(favourites),
        "learned_count": len(learned),
        "stories": result[:limit],
    }


def _inject_serendipity(ranked: list[dict], favourites: dict, learned: dict) -> list[dict]:
    """Reserve slots for important stories the user would otherwise never see."""
    matched, unmatched = [], []
    for s in ranked:
        if any(t in favourites or t in learned for t in s["topics"]):
            matched.append(s)
        else:
            unmatched.append(s)
    unmatched.sort(key=lambda s: -float(s.get("importance") or 0))

    out: list[dict] = []
    u = iter(unmatched)
    for i, s in enumerate(matched):
        out.append(s)
        if (i + 1) % SERENDIPITY_EVERY == 0:
            extra = next(u, None)
            if extra is not None:
                extra = dict(extra)
                extra["reasons"] = [{"kind": "discovery",
                                     "text": "Buiten je interesses, maar belangrijk nieuws"}]
                extra["discovery"] = True
                out.append(extra)
    out.extend(x for x in unmatched if x not in out)
    return out


def _cold_start(ranked: list[dict]) -> list[dict]:
    for s in ranked:
        s["reasons"] = [{"kind": "cold_start",
                         "text": "Nog geen voorkeuren bekend \u2014 belangrijkste nieuws van nu"}]
    ranked.sort(key=lambda s: -(float(s.get("frontpage_score") or 0)))
    return ranked


def store_recommendations(stories: list[dict], surface: str = "mynews",
                          user: str = USER) -> None:
    ts = iso(now())
    with tx() as c:
        c.execute("DELETE FROM recommendations WHERE user_id=? AND surface=?", (user, surface))
        c.executemany(
            "INSERT INTO recommendations(id,user_id,story_id,surface,score,rank,reasons,"
            "created_at) VALUES(?,?,?,?,?,?,?,?)",
            [(new_id("rec_"), user, s["id"], surface, float(s.get("score") or 0), i,
              json.dumps(s.get("reasons") or [], ensure_ascii=False), ts)
             for i, s in enumerate(stories)])


# ---------------------------------------------------------------------------
# Privacy controls
# ---------------------------------------------------------------------------
def privacy_snapshot(user: str = USER) -> dict:
    counts = query(
        "SELECT action, COUNT(*) AS n FROM user_interactions WHERE user_id=? GROUP BY action",
        (user,))
    learned = learned_interests(user)
    return {
        "favourites": list_favourites(user),
        "interaction_counts": {r["action"]: r["n"] for r in counts},
        "total_interactions": sum(r["n"] for r in counts),
        "learned_interests": sorted(
            ({"slug": k, "label": _label(k), "score": round(v, 3)}
             for k, v in learned.items() if v > 0),
            key=lambda x: -x["score"])[:40],
        "publisher_affinity": {k: round(v, 3) for k, v in publisher_affinity(user).items()},
        "storage": "Alles wordt lokaal opgeslagen in nieuws/data/nieuws.db en verlaat je machine niet.",
    }


def reset_learned(user: str = USER) -> int:
    with tx() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM user_interactions WHERE user_id=?",
                      (user,)).fetchone()["n"]
        c.execute("DELETE FROM user_interactions WHERE user_id=?", (user,))
        c.execute("DELETE FROM recommendations WHERE user_id=?", (user,))
    return n


def forget_topic(slug: str, user: str = USER) -> None:
    """Remove an inferred interest without touching explicit favourites."""
    with tx() as c:
        c.execute(
            """DELETE FROM user_interactions WHERE user_id=? AND id IN (
                   SELECT ui.id FROM user_interactions ui
                   JOIN story_topics st ON st.story_id = ui.story_id
                   JOIN topics t ON t.id = st.topic_id
                   WHERE ui.user_id=? AND t.slug=?)""", (user, user, slug))


def _label(slug: str | None) -> str:
    if not slug:
        return ""
    row = one("SELECT label FROM topics WHERE slug=?", (slug,))
    return row["label"] if row else topic_label(slug)
