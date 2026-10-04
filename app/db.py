"""SQLite storage layer: schema, connection handling and small helpers.

Deliberately dependency-free so the persistence concern stays isolated from
ingestion, clustering, ranking and personalisation.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.environ.get("NIEUWS_DB", os.path.join(DATA_DIR, "nieuws.db"))

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS publishers (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    homepage         TEXT,
    region           TEXT,                 -- 'nl' | 'fryslan'
    weight           REAL NOT NULL DEFAULT 1.0,
    colour           TEXT,
    default_category TEXT,                 -- fallback when the feed says nothing useful
    enabled          INTEGER NOT NULL DEFAULT 1,
    user_added       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id                   TEXT PRIMARY KEY,
    publisher_id         TEXT NOT NULL REFERENCES publishers(id) ON DELETE CASCADE,
    name                 TEXT NOT NULL,
    url                  TEXT NOT NULL,
    kind                 TEXT NOT NULL DEFAULT 'rss',   -- 'rss' | 'html'
    category_hint        TEXT,
    enabled              INTEGER NOT NULL DEFAULT 1,
    user_added           INTEGER NOT NULL DEFAULT 0,
    last_fetch_at        TEXT,
    last_status          TEXT,
    last_error           TEXT,
    last_item_count      INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id            TEXT PRIMARY KEY,
    publisher_id  TEXT NOT NULL REFERENCES publishers(id) ON DELETE CASCADE,
    source_id     TEXT,
    guid          TEXT,
    url           TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    url_hash      TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    description   TEXT,
    image_url     TEXT,
    author        TEXT,
    category      TEXT,
    language      TEXT NOT NULL DEFAULT 'nl',
    published_at  TEXT,
    updated_at    TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    revision      INTEGER NOT NULL DEFAULT 1,
    geo_scope     TEXT,                 -- 'fryslan' | 'nl' | 'world'
    geo_places    TEXT,                 -- json list
    topics        TEXT,                 -- json list of slugs
    entities      TEXT,                 -- json list of {name,kind}
    tokens        TEXT,                 -- json list of normalised tokens
    story_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_story ON articles(story_id);
CREATE INDEX IF NOT EXISTS idx_articles_pub ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_publisher ON articles(publisher_id);

CREATE TABLE IF NOT EXISTS stories (
    id                TEXT PRIMARY KEY,
    headline          TEXT NOT NULL,
    headline_source   TEXT,             -- which publisher headline it was derived from
    summary           TEXT,
    summary_kind      TEXT NOT NULL DEFAULT 'generated',
    category          TEXT,
    geo_scope         TEXT,
    article_count     INTEGER NOT NULL DEFAULT 0,
    publisher_count   INTEGER NOT NULL DEFAULT 0,
    first_published_at TEXT,
    last_updated_at   TEXT,
    created_at        TEXT NOT NULL,
    image_url         TEXT,
    centroid          TEXT,             -- json {token: weight}
    entity_set        TEXT,             -- json list
    disagreements     TEXT,             -- json list
    is_updating       INTEGER NOT NULL DEFAULT 0,
    importance        REAL NOT NULL DEFAULT 0,
    frontpage_score   REAL NOT NULL DEFAULT 0,
    trending_score    REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_stories_updated ON stories(last_updated_at);
CREATE INDEX IF NOT EXISTS idx_stories_fp ON stories(frontpage_score);

CREATE TABLE IF NOT EXISTS topics (
    id         TEXT PRIMARY KEY,
    slug       TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'topic',  -- topic|category|person|org|place
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS story_topics (
    story_id TEXT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    weight   REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (story_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_story_topics_topic ON story_topics(topic_id);

CREATE TABLE IF NOT EXISTS user_preferences (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'local',
    kind       TEXT NOT NULL,           -- 'favorite_topic' | 'setting'
    topic_slug TEXT,
    value      TEXT,
    weight     REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prefs_unique
    ON user_preferences(user_id, kind, COALESCE(topic_slug,''));

CREATE TABLE IF NOT EXISTS user_interactions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'local',
    story_id     TEXT,
    article_id   TEXT,
    publisher_id TEXT,
    action       TEXT NOT NULL,         -- open|open_article|save|unsave|hide|unhide|more|less|dwell
    weight       REAL NOT NULL DEFAULT 1.0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inter_user ON user_interactions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_inter_story ON user_interactions(story_id);

CREATE TABLE IF NOT EXISTS recommendations (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'local',
    story_id   TEXT NOT NULL,
    surface    TEXT NOT NULL DEFAULT 'mynews',
    score      REAL NOT NULL,
    rank       INTEGER NOT NULL,
    reasons    TEXT,                    -- json list
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recs_user ON recommendations(user_id, surface, rank);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release.

    CREATE TABLE IF NOT EXISTS leaves existing tables untouched, so new columns
    have to be added explicitly for databases created by an earlier version.
    """
    have = {r["name"] for r in conn.execute("PRAGMA table_info(publishers)")}
    if "default_category" not in have:
        conn.execute("ALTER TABLE publishers ADD COLUMN default_category TEXT")


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, tuple(params)).fetchall()


def one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, tuple(params)).fetchone()


def get_meta(key: str, default: str | None = None) -> str | None:
    row = one("SELECT value FROM meta WHERE key=?", (key,))
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
