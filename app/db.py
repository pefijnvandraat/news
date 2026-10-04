"""SQLite storage layer: schema, connection handling and small helpers.

Deliberately dependency-free so the persistence concern stays isolated from
ingestion, clustering, ranking and personalisation.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

_DEFAULT_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
# NIEUWS_DATA moves the whole data directory (database, WAL, log); NIEUWS_DB
# overrides just the database file. Hosted platforms need the first, because
# the deployment directory is wiped on every deploy.
DATA_DIR = os.environ.get("NIEUWS_DATA") or _DEFAULT_DATA
DB_PATH = os.environ.get("NIEUWS_DB") or os.path.join(DATA_DIR, "nieuws.db")

_local = threading.local()
_shared: "sqlite3.Connection | None" = None
# Reentrant: a transaction may call query() while already holding the lock.
_lock = threading.RLock()

# WAL needs shared-memory mapping, which SMB network shares do not provide.
# Azure App Service mounts /home over SMB, so WAL there corrupts the file with
# "database disk image is malformed". A rollback journal works fine on SMB; it
# is slower, but this workload is one writer and a modest write volume.
JOURNAL_MODE = (os.environ.get("NIEUWS_JOURNAL")
                or ("DELETE" if os.environ.get("WEBSITE_SITE_NAME") else "WAL")).upper()

# On SMB, several open handles to one database file produce "disk I/O error"
# under concurrent access: the share emulates POSIX advisory locks too loosely
# for SQLite. One connection behind one lock means one handle and no
# contention. Locally that is unnecessary, and thread-local connections are
# faster, so this only switches on where it has to.
SERIALISE = (os.environ.get("NIEUWS_SERIALISE", "").lower() in ("1", "true", "yes")
             or JOURNAL_MODE != "WAL")

SCHEMA = f"""
PRAGMA journal_mode={JOURNAL_MODE};
PRAGMA foreign_keys=ON;
""" + """
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


def _new_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # Set per connection as well as in the schema: a database created by an
    # earlier run carries its own journal mode in the file header, and on a
    # network share the wrong one corrupts it.
    conn.execute(f"PRAGMA journal_mode={JOURNAL_MODE}")
    return conn


def connect() -> sqlite3.Connection:
    """The connection for the caller.

    On a local disk each thread gets its own, which is the fastest arrangement
    and what SQLite expects. On a network share every thread shares one, and
    `lock()` keeps them from overlapping - see SERIALISE above.
    """
    if SERIALISE:
        global _shared
        if _shared is None:
            with _lock:
                if _shared is None:
                    _shared = _new_conn()
        return _shared
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _new_conn()
        _local.conn = conn
    return conn


@contextmanager
def lock():
    """Held around every database operation when serialising, a no-op otherwise."""
    if SERIALISE:
        with _lock:
            yield
    else:
        yield


@contextmanager
def tx():
    with lock():
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_db() -> None:
    _quarantine_if_corrupt()
    with lock():
        conn = connect()
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()


def _quarantine_if_corrupt() -> None:
    """Move an unreadable database aside so the app can still start.

    A corrupt file otherwise fails every single request forever. The file is
    renamed rather than deleted: it may still be recoverable, and silently
    destroying someone's favourites and reading history is not ours to do.
    Content rebuilds from the feeds within a refresh cycle.
    """
    if not os.path.exists(DB_PATH):
        return
    try:
        probe = sqlite3.connect(DB_PATH, timeout=10)
        try:
            probe.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        finally:
            probe.close()
        return
    except sqlite3.DatabaseError as exc:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        spoiled = f"{DB_PATH}.corrupt-{stamp}"
        logging.getLogger("nieuws.db").error(
            "database unreadable (%s); moving it to %s and starting a fresh one",
            exc, spoiled)
        for suffix in ("", "-wal", "-shm", "-journal"):
            src = DB_PATH + suffix
            if os.path.exists(src):
                try:
                    os.replace(src, spoiled + suffix)
                except OSError:
                    try:
                        os.remove(src)
                    except OSError:
                        pass


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release.

    CREATE TABLE IF NOT EXISTS leaves existing tables untouched, so new columns
    have to be added explicitly for databases created by an earlier version.
    """
    have = {r["name"] for r in conn.execute("PRAGMA table_info(publishers)")}
    if "default_category" not in have:
        conn.execute("ALTER TABLE publishers ADD COLUMN default_category TEXT")


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with lock():
        return connect().execute(sql, tuple(params)).fetchall()


def one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    with lock():
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
