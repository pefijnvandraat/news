"""Feed discovery and validation for user-supplied URLs.

A reader pasting "tweakers.net" means "add this publisher", not "this exact
path is an RSS document". This module works out what the URL actually is and,
when it is not a feed, finds the publisher's real feeds so the user can pick
one instead of being told it failed.

It also names the specific reason a page is unusable - a consent gate reads
very differently from a 404 - because that is the difference between "try
another URL" and "this publisher has no open feed".
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx

from ..util import strip_html, truncate
from .rss import TIMEOUT, USER_AGENT

# Consent-management platforms and their tell-tale markup. Matching the vendor
# is more reliable than matching Dutch wording, which varies per publisher.
CONSENT_MARKERS = (
    "privacygate", "consent-wall", "cmp-container", "didomi", "onetrust",
    "cookiebot", "usercentrics", "sourcepoint", "quantcast", "trustarc",
    "cookie-consent", "consentmanager", "__tcfapi", "gdpr-consent",
    "toestemming geven", "cookies accepteren", "accepteer cookies",
)

# Paths publishers actually use, most conventional first. Probed only when the
# page itself is not a feed and declares no alternate.
COMMON_PATHS = (
    "/rss", "/rss.xml", "/feed", "/feed.xml", "/index.xml", "/atom.xml",
    "/rss/nieuws", "/nieuws/rss", "/feeds/nieuws.xml", "/feeds/mixed.xml",
    "/feeds/rss.xml", "/rss/feeds/nieuws.xml", "/nieuws/rss.xml", "/feeds",
)

MAX_CANDIDATES = 8
# Paths worth trying on a derived host; the full list only runs on the host the
# user actually gave us, to keep the probe count bounded.
CORE_PATHS = ("/rss", "/rss.xml", "/feed", "/index.xml", "/feeds/nieuws.xml")
_LINK_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""(\w[\w:-]*)\s*=\s*["']([^"']*)["']""")


def _host_variants(netloc: str) -> list[str]:
    """Sibling hostnames Dutch publishers commonly put their feeds on.

    nos.nl -> feeds.nos.nl, rtl.nl -> rtlnieuws.nl. Both are real cases: the
    feed frequently lives on a separate host that the main domain's consent
    gate does not cover.
    """
    bare = netloc[4:] if netloc.startswith("www.") else netloc
    out = [netloc, bare, "www." + bare, "feeds." + bare, "nieuws." + bare]
    parts = bare.split(".")
    if len(parts) >= 2:
        brand, tld = parts[0], ".".join(parts[1:])
        out.append(f"{brand}nieuws.{tld}")
    seen: set[str] = set()
    return [h for h in out if h and not (h in seen or seen.add(h))]


class ProbeResult(dict):
    """Plain dict; subclassed only to make the shape obvious at call sites."""


def _fetch(url: str) -> tuple[int | None, bytes, str, str | None]:
    """Return (status, body, final_url, error)."""
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT,
                                   "Accept": "application/rss+xml, application/xml, "
                                             "text/xml, text/html, */*"}) as client:
            r = client.get(url)
        return r.status_code, r.content, str(r.url), None
    except Exception as exc:
        return None, b"", url, f"{type(exc).__name__}: {exc}"


def _is_feedish(body: bytes) -> bool:
    head = body[:600].lstrip().lower()
    return (head.startswith(b"<?xml") or head.startswith(b"<rss")
            or head.startswith(b"<feed") or b"<rss" in head or b"<feed" in head)


def _is_gated(body: bytes) -> str | None:
    low = body[:30000].decode("utf-8", "ignore").lower()
    for marker in CONSENT_MARKERS:
        if marker in low:
            return marker
    return None


def _parse_feed(url: str, body: bytes) -> dict | None:
    """Validate a feed and summarise it, or None when it is not usable."""
    parsed = feedparser.parse(body)
    entries = [e for e in parsed.entries
               if (e.get("link") or "").strip() and (e.get("title") or "").strip()]
    if not entries:
        return None
    feed = parsed.feed or {}
    title = strip_html(feed.get("title") or "") or urlsplit(url).netloc
    return {
        "url": url,
        "title": truncate(title, 80),
        "description": truncate(strip_html(feed.get("subtitle") or ""), 140) or None,
        "item_count": len(entries),
        "sample": truncate(strip_html(entries[0].get("title") or ""), 110),
        "has_images": any(e.get("enclosures") or e.get("media_content")
                          or e.get("media_thumbnail") for e in entries),
        "site": strip_html(feed.get("link") or "") or None,
    }


def _check_one(url: str) -> dict | None:
    status, body, final_url, error = _fetch(url)
    if error or status is None or status >= 400 or not _is_feedish(body):
        return None
    return _parse_feed(final_url, body)


def _declared_feeds(html: bytes, base_url: str) -> list[str]:
    """Feeds the page advertises via <link rel=alternate> (RSS autodiscovery)."""
    text = html[:200000].decode("utf-8", "ignore")
    found: list[str] = []
    for tag in _LINK_RE.findall(text):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(tag)}
        rel = (attrs.get("rel") or "").lower()
        typ = (attrs.get("type") or "").lower()
        href = attrs.get("href")
        if not href or "alternate" not in rel:
            continue
        if "rss" in typ or "atom" in typ or "xml" in typ:
            full = urljoin(base_url, href)
            if full not in found:
                found.append(full)
    return found


def discover(url: str) -> ProbeResult:
    """Classify a URL and, where it is not a feed, find the publisher's feeds.

    Always returns a result; never raises. 'status' is one of:
        feed      - the URL itself is a usable feed
        gated     - the page is behind a consent/cookie wall
        html      - a normal page, not a feed
        error     - unreachable or an HTTP error
    'candidates' holds validated feeds the user can choose from.
    """
    url = (url or "").strip()
    if not url:
        return ProbeResult(status="error", message="Geen URL opgegeven.", candidates=[])
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    status, body, final_url, error = _fetch(url)

    if error is not None:
        return ProbeResult(status="error", url=url, candidates=[],
                           message=f"Niet bereikbaar: {error}")

    # The URL is itself a feed - nothing to choose.
    if _is_feedish(body):
        feed = _parse_feed(final_url, body)
        if feed:
            return ProbeResult(status="feed", url=final_url, feed=feed, candidates=[feed],
                               message=f"Geldige feed: {feed['item_count']} artikelen.")
        return ProbeResult(status="error", url=final_url, candidates=[],
                           message="Dit is XML, maar er staan geen bruikbare artikelen in.")

    gate = _is_gated(body) if status and status < 400 else None

    # Look for the publisher's real feeds: what the page declares, then the
    # conventional paths. Probe BOTH the original host and the host we landed
    # on: a consent gate often redirects to the CMP vendor's domain
    # (tweakers.net -> myprivacy.dpgmedia.nl), and feeds live on the publisher's
    # own host, not the vendor's.
    tried: list[str] = []
    if status and status < 400:
        tried.extend(_declared_feeds(body, final_url))

    # The host the user gave us is the authoritative one; the host we landed on
    # may be the consent vendor's. Probe the user's host in full, then sibling
    # hosts with the core paths only.
    origin = urlsplit(url)
    scheme = origin.scheme or "https"
    primary = origin.netloc or urlsplit(final_url).netloc
    for path in COMMON_PATHS:
        tried.append(f"{scheme}://{primary}{path}")
    for host in _host_variants(primary)[1:]:
        for path in CORE_PATHS:
            tried.append(f"{scheme}://{host}{path}")

    seen: set[str] = set()
    ordered = [u for u in tried if not (u in seen or seen.add(u))][:44]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_check_one, ordered))

    candidates: list[dict] = []
    by_url: set[str] = set()
    for c in results:
        if c and c["url"] not in by_url:
            by_url.add(c["url"])
            candidates.append(c)
    # Richest feed first: more articles, images preferred.
    candidates.sort(key=lambda c: (-c["item_count"], not c["has_images"]))
    candidates = candidates[:MAX_CANDIDATES]

    if gate:
        message = (f"Deze pagina vraagt eerst toestemming (consent: '{gate}'). "
                   "Die wordt niet omzeild.")
        if candidates:
            message += (f" Er zijn wel {len(candidates)} open feed(s) gevonden die "
                        "daarbuiten vallen:")
        else:
            message += " Er is geen open feed gevonden op deze site."
        return ProbeResult(status="gated", url=final_url, gate=gate,
                           candidates=candidates, message=message)

    if status and status >= 400:
        return ProbeResult(status="error", url=final_url, candidates=candidates,
                           message=f"HTTP {status} op deze URL."
                                   + (f" Wel {len(candidates)} feed(s) gevonden:"
                                      if candidates else ""))

    if candidates:
        return ProbeResult(status="html", url=final_url, candidates=candidates,
                           message=f"Dit is een webpagina, geen feed. "
                                   f"{len(candidates)} feed(s) gevonden op deze site:")
    return ProbeResult(status="html", url=final_url, candidates=[],
                       message="Dit is een webpagina en er is geen RSS-feed gevonden. "
                               "Zoek de feed-URL op de site zelf en plak die hier.")
