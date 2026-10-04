"""RSS / Atom adapter. Primary ingestion mechanism for all current publishers."""
from __future__ import annotations

import httpx
import feedparser

from .base import FetchResult, RawItem

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 NieuwsAggregator/1.0 (persoonlijke nieuwslezer)"
)
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class RssAdapter:
    kind = "rss"

    def fetch(self, source: dict) -> FetchResult:
        url = source["url"]
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                              headers={"User-Agent": USER_AGENT,
                                       "Accept": "application/rss+xml, application/xml, text/xml, */*"}) as client:
                resp = client.get(url)
        except Exception as exc:  # network down, DNS, TLS, timeout
            return FetchResult([], "error", f"{type(exc).__name__}: {exc}")

        if resp.status_code >= 400:
            return FetchResult([], "error", f"HTTP {resp.status_code}", resp.status_code)

        body = resp.content
        head = body[:200].lstrip().lower()
        if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
            # A consent/interstitial page rather than a feed. We do not try to
            # defeat it; we report it so the UI can surface the degraded source.
            return FetchResult([], "error",
                               "Geen feed ontvangen (HTML-pagina, mogelijk consent-scherm)",
                               resp.status_code)

        parsed = feedparser.parse(body)
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            return FetchResult([], "error",
                               f"Onleesbare feed: {getattr(parsed, 'bozo_exception', 'parse error')}",
                               resp.status_code)

        feed_lang = (parsed.feed.get("language") or "nl")[:5] if parsed.feed else "nl"
        items: list[RawItem] = []
        for e in parsed.entries:
            link = (e.get("link") or "").strip()
            title = (e.get("title") or "").strip()
            if not link or not title:
                continue  # malformed entry, skip without failing the whole feed
            items.append(RawItem(
                url=link,
                title=title,
                guid=e.get("id") or e.get("guid") or link,
                description=_description(e),
                image_url=_image(e),
                author=(e.get("author") or "").strip() or None,
                category=_category(e),
                language=feed_lang,
                published_at=e.get("published_parsed") or e.get("published"),
                updated_at=e.get("updated_parsed") or e.get("updated"),
            ))
        status = "ok" if items else "empty"
        return FetchResult(items, status, None if items else "Feed bevat geen items",
                           resp.status_code)


def _description(entry) -> str | None:
    for key in ("summary", "description", "subtitle"):
        val = entry.get(key)
        if val:
            return val
    content = entry.get("content")
    if content and isinstance(content, list) and content:
        return content[0].get("value")
    return None


def _image(entry) -> str | None:
    for enc in entry.get("enclosures") or []:
        if str(enc.get("type", "")).startswith("image") and enc.get("href"):
            return enc["href"]
    media = entry.get("media_content") or []
    for m in media:
        if m.get("url"):
            return m["url"]
    thumbs = entry.get("media_thumbnail") or []
    for t in thumbs:
        if t.get("url"):
            return t["url"]
    for link in entry.get("links") or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image"):
            return link.get("href")
    return None


def _category(entry) -> str | None:
    tags = entry.get("tags") or []
    for t in tags:
        term = t.get("term")
        if term:
            return term
    return entry.get("category")
