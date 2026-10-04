"""Small shared helpers: time, hashing, ids, URL canonicalisation, text cleanup."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

UTC = timezone.utc

# Tracking parameters that must not create duplicate articles.
_TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "at_")
_TRACKING_KEYS = {
    "cmpid", "cid", "ref", "referrer", "source", "fbclid", "gclid",
    "igshid", "ns_campaign", "ns_source", "ns_mchannel", "ns_linkname",
}


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse_dt(value) -> datetime | None:
    """Best-effort parse of a date coming from a feed or from our own storage."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            return datetime(*value[:6], tzinfo=UTC)
        except (ValueError, TypeError):
            return None
    s = str(value).strip()
    for fmt in (None, "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00")) if fmt is None \
                else datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            continue
    return None


def age_hours(dt: datetime | None, ref: datetime | None = None) -> float:
    if dt is None:
        return 1e6
    ref = ref or now()
    return max(0.0, (ref - dt).total_seconds() / 3600.0)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def sha1(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update((p or "").encode("utf-8", "ignore"))
        h.update(b"\x00")
    return h.hexdigest()


def canonical_url(url: str) -> str:
    """Strip tracking noise and fragments so the same article maps to one key."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    qs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_KEYS
    ]
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme or "https", netloc, path, urlencode(qs), ""))


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "\u2026"


def deaccent(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def slugify(text: str) -> str:
    s = deaccent(text.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "onbekend"


def within_hours(dt: datetime | None, hours: int) -> bool:
    return dt is not None and (now() - dt) <= timedelta(hours=hours)
