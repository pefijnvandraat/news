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


def run_ingest(max_workers: int = 8) -> dict:
    """Fetch all enabled sources in parallel. Returns a per-source report."""
    sources = [dict(r) for r in query(
        "SELECT s.*, p.enabled AS pub_enabled FROM sources s "
        "JOIN publishers p ON p.id = s.publisher_id "
        "WHERE s.enabled=1 AND p.enabled=1"
    )]
    if not sources:
        return {"started_at": iso(now()), "sources": [], "new": 0, "updated": 0}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        reports = list(pool.map(_fetch_source, sources))

    for r in reports:
        _record(r)

    return {
        "finished_at": iso(now()),
        "sources": reports,
        "new": sum(r["new"] for r in reports),
        "updated": sum(r["updated"] for r in reports),
        "failed": [r["name"] for r in reports if r["status"] == "error"],
    }
