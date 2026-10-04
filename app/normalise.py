"""Normalisation: RawItem -> the common Article model, plus persistence.

This is the single place where publisher-specific shapes are flattened, so
clustering, ranking and the API never see feed quirks.
"""
from __future__ import annotations

import json

from . import topics as T
from .db import one, tx
from .ingest.base import RawItem
from .util import (canonical_url, iso, new_id, now, parse_dt, sha1, strip_html,
                   truncate)

MIN_TITLE = 8


def normalise(item: RawItem, source: dict) -> dict | None:
    """Turn a RawItem into a dict matching the `articles` table, or None if unusable."""
    title = strip_html(item.title)
    if len(title) < MIN_TITLE:
        return None
    url = (item.url or "").strip()
    if not url.startswith("http"):
        return None

    canon = canonical_url(url)
    description = truncate(strip_html(item.description), 600) or None
    published = parse_dt(item.published_at)
    updated = parse_dt(item.updated_at) or published
    if published is None:
        published = updated or now()
    if updated is not None and published is not None and updated < published:
        updated = published

    scope, places = T.geo_scope(title, description or "", source["publisher_id"])
    entities = T.extract_entities(title, description or "")
    topic_slugs = T.detect_topics(title, description or "", None, entities)
    category = T.map_category(item.category, url, source.get("category_hint"),
                              source["publisher_id"], scope, topic_slugs)
    if category not in topic_slugs:
        topic_slugs.append(category)
    tokens = T.tokenize(title) * 2 + T.tokenize(description or "")

    return {
        "publisher_id": source["publisher_id"],
        "source_id": source["id"],
        "guid": item.guid or canon,
        "url": url,
        "canonical_url": canon,
        "url_hash": sha1(canon),
        "title": title,
        "description": description,
        "image_url": _clean_image(item.image_url),
        "author": (item.author or None),
        "category": category,
        "language": (item.language or "nl")[:5],
        "published_at": iso(published),
        "updated_at": iso(updated),
        "content_hash": sha1(title, description or ""),
        "geo_scope": scope,
        "geo_places": json.dumps(places, ensure_ascii=False),
        "topics": json.dumps(topic_slugs, ensure_ascii=False),
        "entities": json.dumps(entities, ensure_ascii=False),
        "tokens": json.dumps(tokens, ensure_ascii=False),
    }


def _clean_image(url: str | None) -> str | None:
    if not url or not str(url).startswith("http"):
        return None
    return str(url)


def renormalise_all() -> int:
    """Recompute derived fields for stored articles.

    Run after changing the taxonomy, gazetteers or entity rules so existing
    content benefits without re-fetching the publishers.
    """
    from .db import query

    rows = [dict(r) for r in query(
        "SELECT a.id, a.title, a.description, a.url, a.publisher_id, a.category, "
        "s.category_hint FROM articles a LEFT JOIN sources s ON s.id = a.source_id")]
    updates = []
    for r in rows:
        title, desc = r["title"], r["description"] or ""
        scope, places = T.geo_scope(title, desc, r["publisher_id"])
        entities = T.extract_entities(title, desc)
        slugs = T.detect_topics(title, desc, None, entities)
        category = T.map_category(None, r["url"], r["category_hint"],
                                  r["publisher_id"], scope, slugs)
        if category not in slugs:
            slugs.append(category)
        tokens = T.tokenize(title) * 2 + T.tokenize(desc)
        updates.append((category, scope, json.dumps(places, ensure_ascii=False),
                        json.dumps(slugs, ensure_ascii=False),
                        json.dumps(entities, ensure_ascii=False),
                        json.dumps(tokens, ensure_ascii=False), r["id"]))
    with tx() as c:
        c.executemany(
            "UPDATE articles SET category=?, geo_scope=?, geo_places=?, topics=?, "
            "entities=?, tokens=? WHERE id=?", updates)
    return len(updates)


def upsert(article: dict) -> str:
    """Insert a new article, or update an existing one when its content changed.

    Returns 'new', 'updated' or 'unchanged'. Guarantees the same URL is never
    imported twice.
    """
    ts = iso(now())
    existing = one(
        "SELECT id, content_hash, revision, story_id, title, updated_at "
        "FROM articles WHERE url_hash=?",
        (article["url_hash"],),
    )
    if existing is None:
        article_id = new_id("art_")
        with tx() as c:
            c.execute(
                """INSERT INTO articles(id,publisher_id,source_id,guid,url,canonical_url,
                       url_hash,title,description,image_url,author,category,language,
                       published_at,updated_at,first_seen_at,last_seen_at,content_hash,
                       revision,geo_scope,geo_places,topics,entities,tokens,story_id)
                   VALUES(:id,:publisher_id,:source_id,:guid,:url,:canonical_url,:url_hash,
                       :title,:description,:image_url,:author,:category,:language,
                       :published_at,:updated_at,:first_seen_at,:last_seen_at,:content_hash,
                       1,:geo_scope,:geo_places,:topics,:entities,:tokens,NULL)""",
                {**article, "id": article_id, "first_seen_at": ts, "last_seen_at": ts},
            )
        return "new"

    if existing["content_hash"] == article["content_hash"]:
        with tx() as c:
            c.execute("UPDATE articles SET last_seen_at=? WHERE id=?", (ts, existing["id"]))
        return "unchanged"

    # The publisher revised the article after publication. Many feeds reuse the
    # original pubDate even when the text changes, so if the feed's timestamp is
    # not newer than what we already stored we record the detection time instead.
    # Without this a genuine revision would never reach "Laatste nieuws" or mark
    # the story as still developing.
    feed_updated = article.get("updated_at")
    if not feed_updated or feed_updated <= (existing["updated_at"] or ""):
        feed_updated = ts

    with tx() as c:
        c.execute(
            """UPDATE articles SET title=:title, description=:description,
                   image_url=COALESCE(:image_url, image_url), category=:category,
                   updated_at=:updated_at, last_seen_at=:last_seen_at,
                   content_hash=:content_hash, revision=revision+1,
                   geo_scope=:geo_scope, geo_places=:geo_places, topics=:topics,
                   entities=:entities, tokens=:tokens
               WHERE id=:id""",
            {**article, "id": existing["id"], "last_seen_at": ts,
             "updated_at": feed_updated},
        )
    return "updated"
