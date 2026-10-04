"""Ingestion orchestration: fetch every enabled source, normalise, persist.

A failing publisher degrades that source only; the run always completes and
records per-source health so the UI can show it.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..db import query, tx
from ..normalise import normalise, upsert
from ..util import iso, now
from .html import HtmlAdapter
from .rss import RssAdapter

ADAPTERS = {"rss": RssAdapter(), "html": HtmlAdapter()}


def _fetch_source(source: dict) -> dict:
    adapter = ADAPTERS.get(source.get("kind") or "rss", ADAPTERS["rss"])
    report = {"source_id": source["id"], "name": source["name"],
              "publisher_id": source["publisher_id"], "new": 0, "updated": 0,
              "unchanged": 0, "status": "ok", "error": None}
    try:
        result = adapter.fetch(source)
    except Exception as exc:  # adapter bug must not kill the run
        report["status"] = "error"
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report

    report["status"] = result.status
    report["error"] = result.error
    for item in result.items:
        try:
            article = normalise(item, source)
            if article is None:
                continue
            outcome = upsert(article)
            report[outcome] += 1
        except Exception as exc:  # one malformed item must not kill the feed
            report["error"] = report["error"] or f"item: {type(exc).__name__}: {exc}"
    return report


def _record(report: dict) -> None:
    ts = iso(now())
    failed = report["status"] == "error"
    with tx() as c:
        c.execute(
            """UPDATE sources SET last_fetch_at=?, last_status=?, last_error=?,
                   last_item_count=?,
                   consecutive_failures=CASE WHEN ? THEN consecutive_failures+1 ELSE 0 END
               WHERE id=?""",
            (ts, report["status"], report["error"],
             report["new"] + report["updated"] + report["unchanged"],
             1 if failed else 0, report["source_id"]),
        )


def load_sources(source_ids: list[str] | None = None) -> list[dict]:
    """Enabled sources, optionally narrowed to an explicit set."""
    sql = ("SELECT s.* FROM sources s JOIN publishers p ON p.id = s.publisher_id "
           "WHERE s.enabled=1 AND p.enabled=1")
    params: tuple = ()
    if source_ids:
        marks = ",".join("?" * len(source_ids))
        sql += f" AND s.id IN ({marks})"
        params = tuple(source_ids)
    return [dict(r) for r in query(sql, params)]


def run_ingest(max_workers: int | None = None, source_ids: list[str] | None = None,
               scope: str = "full") -> dict:
    """Fetch sources in parallel. Pass source_ids to refresh only those feeds.

    Returns a per-source report. A failing source degrades only itself.
    """
    sources = load_sources(source_ids)
    if not sources:
        return {"finished_at": iso(now()), "scope": scope, "sources": [],
                "new": 0, "updated": 0, "failed": []}

    # Feed fetching is I/O-bound, so a single wave over the whole batch costs
    # about as much as the slowest feed. The cap keeps us from opening an
    # unreasonable number of sockets if many publishers are ever added.
    if max_workers is None:
        max_workers = min(len(sources), 16)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        reports = list(pool.map(_fetch_source, sources))

    for r in reports:
        _record(r)

    return {
        "finished_at": iso(now()),
        "scope": scope,
        "source_count": len(sources),
        "sources": reports,
        "new": sum(r["new"] for r in reports),
        "updated": sum(r["updated"] for r in reports),
        "failed": [r["name"] for r in reports if r["status"] == "error"],
    }


def priority_source_ids(top_n: int = 24, max_sources: int = 12) -> list[str]:
    """Feeds worth polling more often than the rest.

    A story on the front page is the one most likely to be corrected, extended
    or overtaken, so we re-poll exactly the feeds that produced it. Each
    contributing publisher's general feed is added too, because follow-up
    coverage of a developing story usually lands there first rather than in the
    narrow section feed the original article came from.
    """
    rows = query(
        """SELECT DISTINCT a.source_id, a.publisher_id
           FROM articles a
           JOIN (SELECT id FROM stories ORDER BY frontpage_score DESC LIMIT ?) s
             ON s.id = a.story_id
           WHERE a.source_id IS NOT NULL""",
        (top_n,),
    )
    if not rows:
        return []

    direct = {r["source_id"] for r in rows}
    publishers = {r["publisher_id"] for r in rows}

    marks = ",".join("?" * len(publishers))
    general = {r["id"] for r in query(
        f"""SELECT s.id FROM sources s JOIN publishers p ON p.id = s.publisher_id
            WHERE s.enabled=1 AND p.enabled=1 AND s.category_hint IS NULL
              AND s.publisher_id IN ({marks})""", tuple(publishers))}

    enabled = {r["id"] for r in query(
        "SELECT s.id FROM sources s JOIN publishers p ON p.id = s.publisher_id "
        "WHERE s.enabled=1 AND p.enabled=1")}

    # General feeds first: they are the cheapest way to catch a developing story.
    ordered = [sid for sid in general if sid in enabled]
    ordered += [sid for sid in direct if sid in enabled and sid not in general]
    return ordered[:max_sources]
