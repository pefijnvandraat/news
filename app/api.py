"""HTTP API + static hosting for the Dutch news aggregator."""
from __future__ import annotations

import json
import logging
import threading
import time

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import os

from . import config, personalise
from .cluster import recluster
from .db import init_db, one, query, tx
from .ingest.pipeline import priority_source_ids, run_ingest
from .ranking import (compute_front_page_scores, load_story_topics,
                      parse_json_list, story_publishers)
from .topics import CURATED_TOPICS, topic_label
from .util import iso, new_id, now, slugify

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

# Two-tier refresh. The full sweep keeps every section current; the priority
# sweep re-polls only the feeds behind the current front-page top stories,
# because those are the ones most likely to be corrected, extended or overtaken.
REFRESH_SECONDS = int(os.environ.get("NIEUWS_REFRESH_SECONDS", "600"))
PRIORITY_REFRESH_SECONDS = int(os.environ.get("NIEUWS_PRIORITY_SECONDS", "120"))
PRIORITY_TOP_N = int(os.environ.get("NIEUWS_PRIORITY_TOP_N", "24"))
PRIORITY_MAX_SOURCES = int(os.environ.get("NIEUWS_PRIORITY_MAX_SOURCES", "12"))

log = logging.getLogger("nieuws.api")

app = FastAPI(title="Nieuws", docs_url="/api/docs", openapi_url="/api/openapi.json")

_state = {
    "status": "idle",
    "last_run": None,
    "last_priority_run": None,
    "last_error": None,
    "report": None,
    "priority_sources": 0,
}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def refresh(scope: str = "full", wait: float = 0.0) -> dict:
    """Ingest, re-cluster and re-score.

    scope='priority' narrows ingestion to the feeds behind the current top
    stories; clustering and scoring always run over the full recent window so a
    partial fetch can never leave the front page inconsistent.

    wait>0 blocks for up to that many seconds for an in-flight run to finish.
    Scheduled sweeps pass 0 and skip, because another run is already underway;
    an explicit request from the user waits so it does not silently no-op.
    """
    if not _lock.acquire(blocking=wait > 0, timeout=wait if wait > 0 else -1):
        return {"status": "busy", "scope": scope}
    try:
        _state["status"] = f"running:{scope}"
        source_ids = None
        if scope == "priority":
            source_ids = priority_source_ids(PRIORITY_TOP_N, PRIORITY_MAX_SOURCES)
            _state["priority_sources"] = len(source_ids)
            if not source_ids:
                scope = "full"  # nothing ranked yet - fall back to a full sweep

        report = run_ingest(source_ids=source_ids, scope=scope)
        cluster_report = recluster()
        compute_front_page_scores()

        stamp = iso(now())
        _state.update({
            "status": "idle",
            "last_error": None,
            "last_priority_run": stamp,
            "report": {"ingest": report, "cluster": cluster_report},
        })
        if scope == "full":
            _state["last_run"] = stamp
        log.info("refresh scope=%s sources=%s new=%s updated=%s failed=%s",
                 scope, report.get("source_count"), report["new"],
                 report["updated"], len(report["failed"]))
        return _state["report"]
    except Exception as exc:
        _state.update({"status": "error", "last_error": f"{type(exc).__name__}: {exc}"})
        log.exception("refresh failed")
        return {"error": _state["last_error"]}
    finally:
        _lock.release()


def _background_loop() -> None:
    """Interleave priority and full sweeps on a single timer."""
    elapsed = 0
    tick = max(15, min(PRIORITY_REFRESH_SECONDS, REFRESH_SECONDS))
    while True:
        time.sleep(tick)
        elapsed += tick
        try:
            if elapsed >= REFRESH_SECONDS:
                elapsed = 0
                refresh("full")
            else:
                refresh("priority")
        except Exception:
            log.exception("background refresh loop error")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    config.seed()
    _ensure_curated_topics()
    threading.Thread(target=_first_run, daemon=True).start()
    threading.Thread(target=_background_loop, daemon=True).start()


def _first_run() -> None:
    row = one("SELECT COUNT(*) AS n FROM articles")
    if row and row["n"] == 0:
        refresh("full")
    else:
        compute_front_page_scores()
        # Existing content is already ranked, so the first sweep can be the
        # cheap one that keeps the visible top stories current.
        refresh("priority")


def _ensure_curated_topics() -> None:
    ts = iso(now())
    with tx() as c:
        for label, _keys, kind in CURATED_TOPICS:
            slug = slugify(label)
            if not c.execute("SELECT id FROM topics WHERE slug=?", (slug,)).fetchone():
                c.execute("INSERT INTO topics(id,slug,label,kind,created_at) VALUES(?,?,?,?,?)",
                          (new_id("top_"), slug, label, kind, ts))


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def _story_rows(where: str = "", params: tuple = (), order: str = "frontpage_score DESC",
                limit: int = 60) -> list[dict]:
    sql = f"SELECT * FROM stories {where} ORDER BY {order} LIMIT ?"
    return [dict(r) for r in query(sql, (*params, limit))]


def _decorate(stories: list[dict], with_articles: bool = False) -> list[dict]:
    ids = [s["id"] for s in stories]
    topics_map = load_story_topics(ids)
    pubs_map = story_publishers(ids)
    pub_meta = {r["id"]: dict(r) for r in query("SELECT * FROM publishers")}
    saved = personalise.saved_story_ids()
    hidden = personalise.hidden_story_ids()
    feedback = personalise.explicit_feedback()
    arts: dict[str, list[dict]] = {}
    if with_articles and ids:
        marks = ",".join("?" * len(ids))
        for r in query(
            f"SELECT * FROM articles WHERE story_id IN ({marks}) "
            f"ORDER BY datetime(COALESCE(updated_at,published_at)) DESC", ids):
            arts.setdefault(r["story_id"], []).append(_article_json(dict(r), pub_meta))

    out = []
    for s in stories:
        slugs = topics_map.get(s["id"], [])
        pubs = pubs_map.get(s["id"], [])
        topic_slug_set = set(slugs)
        entities = [e for e in parse_json_list(s.get("entity_set"))
                    if slugify(e) not in topic_slug_set][:10]
        out.append({
            "id": s["id"],
            "headline": s["headline"],
            "headline_source": s.get("headline_source"),
            "headline_source_name": pub_meta.get(s.get("headline_source"), {}).get("name"),
            "summary": s.get("summary"),
            "summary_kind": s.get("summary_kind", "generated"),
            "category": s.get("category"),
            "category_label": config.CATEGORY_LABELS.get(s.get("category"), s.get("category")),
            "geo_scope": s.get("geo_scope"),
            "article_count": s.get("article_count"),
            "publisher_count": s.get("publisher_count"),
            "first_published_at": s.get("first_published_at"),
            "last_updated_at": s.get("last_updated_at"),
            "image_url": s.get("image_url"),
            "is_updating": bool(s.get("is_updating")),
            "importance": round(float(s.get("importance") or 0), 4),
            "frontpage_score": round(float(s.get("frontpage_score") or 0), 4),
            "trending_score": round(float(s.get("trending_score") or 0), 4),
            "disagreements": parse_json_list(s.get("disagreements")),
            "topics": [{"slug": t, "label": _tlabel(t)} for t in slugs[:8]],
            "entities": entities,
            "publishers": [{"id": p, "name": pub_meta.get(p, {}).get("name", p),
                            "colour": pub_meta.get(p, {}).get("colour")} for p in pubs],
            "saved": s["id"] in saved,
            "hidden": s["id"] in hidden,
            "feedback": (feedback.get(s["id"]) or (None,))[0],
            "score": s.get("score"),
            "reasons": s.get("reasons"),
            "discovery": s.get("discovery", False),
            "articles": arts.get(s["id"], []) if with_articles else None,
        })
    return out


def _article_json(a: dict, pub_meta: dict) -> dict:
    pub = pub_meta.get(a["publisher_id"], {})
    return {
        "id": a["id"],
        "title": a["title"],
        "description": a.get("description"),
        "url": a["url"],
        "image_url": a.get("image_url"),
        "author": a.get("author"),
        "category": a.get("category"),
        "language": a.get("language"),
        "published_at": a.get("published_at"),
        "updated_at": a.get("updated_at"),
        "revision": a.get("revision"),
        "geo_scope": a.get("geo_scope"),
        "geo_places": parse_json_list(a.get("geo_places")),
        "topics": parse_json_list(a.get("topics"))[:8],
        "entities": parse_json_list(a.get("entities"))[:8],
        "publisher": {"id": a["publisher_id"], "name": pub.get("name", a["publisher_id"]),
                      "colour": pub.get("colour"), "homepage": pub.get("homepage")},
    }


_LABEL_CACHE: dict[str, str] = {}


def _tlabel(slug: str) -> str:
    if slug not in _LABEL_CACHE:
        row = one("SELECT label FROM topics WHERE slug=?", (slug,))
        _LABEL_CACHE[slug] = row["label"] if row else topic_label(slug)
    return _LABEL_CACHE[slug]


# ---------------------------------------------------------------------------
# Meta / health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    arts = one("SELECT COUNT(*) AS n FROM articles")["n"]
    stories = one("SELECT COUNT(*) AS n FROM stories")["n"]
    return {"ok": True, "articles": arts, "stories": stories, **_state}


@app.get("/api/meta")
def meta():
    sources = [dict(r) for r in query(
        "SELECT s.*, p.name AS publisher_name, p.colour FROM sources s "
        "JOIN publishers p ON p.id=s.publisher_id ORDER BY p.name, s.name")]
    priority = set(priority_source_ids(PRIORITY_TOP_N, PRIORITY_MAX_SOURCES))
    for s in sources:
        s["priority"] = s["id"] in priority
    return {
        "categories": [{"slug": s, "label": l} for s, l in config.CATEGORIES],
        "publishers": [dict(r) for r in query("SELECT * FROM publishers ORDER BY name")],
        "sources": sources,
        "degraded": [s["name"] for s in sources if s["last_status"] == "error"],
        "status": _state,
        "refresh_seconds": REFRESH_SECONDS,
        "priority_refresh_seconds": PRIORITY_REFRESH_SECONDS,
        "priority_source_count": len(priority),
    }


@app.post("/api/refresh")
def api_refresh(scope: str = Query("full", pattern="^(full|priority)$")):
    """scope=priority re-polls only the feeds behind the current top stories."""
    return refresh(scope, wait=90.0)


# ---------------------------------------------------------------------------
# Front page
# ---------------------------------------------------------------------------
@app.get("/api/frontpage")
def frontpage(limit: int = Query(16, ge=1, le=60)):
    hidden = personalise.hidden_story_ids()

    def pick(rows, n, used):
        out = []
        for r in rows:
            if r["id"] in used or r["id"] in hidden:
                continue
            used.add(r["id"])
            out.append(r)
            if len(out) >= n:
                break
        return out

    used: set[str] = set()
    top = pick(_story_rows(order="frontpage_score DESC", limit=limit * 4), limit, used)
    latest = pick(_story_rows(order="datetime(last_updated_at) DESC", limit=limit * 4),
                  limit, used)
    trending = pick(_story_rows("WHERE publisher_count > 1",
                                order="trending_score DESC", limit=limit * 4), limit, used)
    fryslan = pick(_story_rows("WHERE geo_scope='fryslan'",
                               order="frontpage_score DESC", limit=limit * 3), limit, used)

    categories = {}
    for slug, _label in config.CATEGORIES:
        if slug in ("fryslan", "overig"):
            continue
        rows = pick(_story_rows("WHERE category=?", (slug,),
                                order="frontpage_score DESC", limit=limit * 2), 6, set())
        if rows:
            categories[slug] = _decorate(rows)

    return {
        "generated_at": iso(now()),
        "top": _decorate(top),
        "latest": _decorate(latest),
        "trending": _decorate(trending),
        "fryslan": _decorate(fryslan),
        "categories": categories,
        "empty": not (top or latest or trending or fryslan),
    }


@app.get("/api/stories")
def stories(q: str | None = None, topic: str | None = None, publisher: str | None = None,
            category: str | None = None, location: str | None = None,
            hours: int = Query(0, ge=0, le=720), saved: bool = False,
            limit: int = Query(40, ge=1, le=120), offset: int = Query(0, ge=0)):
    wheres, params = [], []
    if category:
        wheres.append("s.category=?")
        params.append(category)
    if location:
        wheres.append("s.geo_scope=?")
        params.append(location)
    if hours:
        wheres.append(f"datetime(s.last_updated_at) >= datetime('now','-{int(hours)} hours')")
    if q:
        wheres.append("(s.headline LIKE ? OR s.summary LIKE ? OR s.entity_set LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    if topic:
        wheres.append("s.id IN (SELECT st.story_id FROM story_topics st JOIN topics t "
                      "ON t.id=st.topic_id WHERE t.slug=?)")
        params.append(topic)
    if publisher:
        wheres.append("s.id IN (SELECT a.story_id FROM articles a WHERE a.publisher_id=?)")
        params.append(publisher)
    if saved:
        ids = personalise.saved_story_ids()
        if not ids:
            return {"stories": [], "total": 0}
        wheres.append(f"s.id IN ({','.join('?' * len(ids))})")
        params += list(ids)

    clause = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    total = one(f"SELECT COUNT(*) AS n FROM stories s {clause}", params)["n"]
    rows = [dict(r) for r in query(
        f"SELECT s.* FROM stories s {clause} ORDER BY s.frontpage_score DESC LIMIT ? OFFSET ?",
        (*params, limit, offset))]
    hidden = personalise.hidden_story_ids()
    rows = [r for r in rows if r["id"] not in hidden]
    return {"stories": _decorate(rows), "total": total}


@app.get("/api/stories/{story_id}")
def story_detail(story_id: str):
    row = one("SELECT * FROM stories WHERE id=?", (story_id,))
    if row is None:
        raise HTTPException(404, "Story niet gevonden")
    story = _decorate([dict(row)], with_articles=True)[0]

    timeline = []
    for a in story["articles"] or []:
        timeline.append({
            "at": a["updated_at"] or a["published_at"],
            "publisher": a["publisher"]["name"],
            "publisher_id": a["publisher"]["id"],
            "title": a["title"],
            "url": a["url"],
            "revised": (a.get("revision") or 1) > 1,
        })
    timeline.sort(key=lambda t: t["at"] or "")
    story["timeline"] = timeline

    related = [dict(r) for r in query(
        "SELECT * FROM stories WHERE id!=? AND category=? ORDER BY frontpage_score DESC LIMIT 5",
        (story_id, row["category"]))]
    story["related"] = _decorate(related)
    return story


# ---------------------------------------------------------------------------
# Topics & favourites
# ---------------------------------------------------------------------------
@app.get("/api/topics")
def topics(q: str | None = None, limit: int = Query(60, ge=1, le=300)):
    favs = {f["slug"] for f in personalise.list_favourites()}
    sql = """SELECT t.slug, t.label, t.kind, COUNT(st.story_id) AS story_count
             FROM topics t LEFT JOIN story_topics st ON st.topic_id=t.id """
    params: list = []
    if q:
        sql += "WHERE t.label LIKE ? OR t.slug LIKE ? "
        params += [f"%{q}%", f"%{q}%"]
    sql += "GROUP BY t.id ORDER BY story_count DESC, t.label ASC LIMIT ?"
    params.append(limit)
    rows = query(sql, params)
    return {"topics": [{"slug": r["slug"], "label": r["label"], "kind": r["kind"],
                        "story_count": r["story_count"],
                        "favourite": r["slug"] in favs} for r in rows]}


@app.get("/api/favourites")
def get_favourites():
    return {"favourites": personalise.list_favourites()}


@app.post("/api/favourites")
def post_favourite(payload: dict = Body(...)):
    raw = (payload.get("slug") or payload.get("label") or "").strip()
    if not raw:
        raise HTTPException(400, "slug of label is verplicht")
    slug = slugify(raw)
    personalise.add_favourite(slug, payload.get("label") or raw)
    return {"ok": True, "favourites": personalise.list_favourites()}


@app.delete("/api/favourites/{slug}")
def delete_favourite(slug: str):
    personalise.remove_favourite(slug)
    return {"ok": True, "favourites": personalise.list_favourites()}


# ---------------------------------------------------------------------------
# My News
# ---------------------------------------------------------------------------
@app.get("/api/mynews")
def mynews(limit: int = Query(40, ge=1, le=100)):
    rows = _story_rows(order="frontpage_score DESC", limit=220)
    result = personalise.build_my_news(rows, limit=limit)
    decorated = _decorate(result["stories"])
    for src, dst in zip(result["stories"], decorated):
        dst["score"] = round(float(src.get("score") or 0), 4)
        dst["reasons"] = src.get("reasons")
        dst["discovery"] = src.get("discovery", False)
    personalise.store_recommendations(result["stories"])
    return {"cold_start": result["cold_start"], "has_favourites": result["has_favourites"],
            "learned_count": result["learned_count"], "stories": decorated}


@app.post("/api/interactions")
def post_interaction(payload: dict = Body(...)):
    action = (payload.get("action") or "").strip()
    personalise.record_interaction(action, payload.get("story_id"),
                                   payload.get("article_id"), payload.get("publisher_id"))
    return {"ok": True}


@app.get("/api/privacy")
def privacy():
    return personalise.privacy_snapshot()


@app.post("/api/privacy/reset")
def privacy_reset():
    return {"ok": True, "deleted": personalise.reset_learned()}


@app.post("/api/privacy/forget")
def privacy_forget(payload: dict = Body(...)):
    slug = (payload.get("slug") or "").strip()
    if not slug:
        raise HTTPException(400, "slug is verplicht")
    personalise.forget_topic(slug)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Sources (add your own publisher)
# ---------------------------------------------------------------------------
@app.get("/api/sources")
def get_sources():
    return {"sources": [dict(r) for r in query(
        "SELECT s.*, p.name AS publisher_name FROM sources s "
        "JOIN publishers p ON p.id=s.publisher_id ORDER BY p.name, s.name")],
        "publishers": [dict(r) for r in query("SELECT * FROM publishers ORDER BY name")]}


@app.post("/api/sources")
def add_source(payload: dict = Body(...)):
    url = (payload.get("url") or "").strip()
    pub_name = (payload.get("publisher_name") or "").strip()
    if not url.startswith("http") or not pub_name:
        raise HTTPException(400, "publisher_name en een geldige feed-url zijn verplicht")
    pub_id = (payload.get("publisher_id") or slugify(pub_name))[:40]
    ts = iso(now())
    with tx() as c:
        if not c.execute("SELECT id FROM publishers WHERE id=?", (pub_id,)).fetchone():
            c.execute("INSERT INTO publishers(id,name,homepage,region,weight,colour,enabled,"
                      "user_added,created_at) VALUES(?,?,?,?,?,?,1,1,?)",
                      (pub_id, pub_name, payload.get("homepage"),
                       payload.get("region") or "nl", 0.9,
                       payload.get("colour") or "#6b7280", ts))
        if c.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchone():
            raise HTTPException(409, "Deze feed bestaat al")
        c.execute("INSERT INTO sources(id,publisher_id,name,url,kind,category_hint,enabled,"
                  "user_added,created_at) VALUES(?,?,?,?,?,?,1,1,?)",
                  (new_id("src_"), pub_id, payload.get("name") or pub_name, url,
                   payload.get("kind") or "rss", payload.get("category_hint"), ts))
    threading.Thread(target=refresh, args=("full",), daemon=True).start()
    return {"ok": True}


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: str):
    with tx() as c:
        c.execute("DELETE FROM sources WHERE id=? AND user_added=1", (source_id,))
    return {"ok": True}


@app.patch("/api/sources/{source_id}")
def toggle_source(source_id: str, payload: dict = Body(...)):
    with tx() as c:
        c.execute("UPDATE sources SET enabled=? WHERE id=?",
                  (1 if payload.get("enabled", True) else 0, source_id))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"),
                        headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def _revalidate_static(request, call_next):
    """Force the browser to revalidate front-end assets.

    Without this the browser serves a cached styles.css/app.js indefinitely and
    front-end changes silently never reach the user. 'no-cache' still allows a
    cheap 304 via ETag, so this costs a conditional request, not a re-download.
    """
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
