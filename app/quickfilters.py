"""Saved filter combinations ("snelfilters").

A quick filter is stored as a **canonical query string**, not as a row of
columns. The filtered view is already fully described by its URL, so keeping
one string means saving, restoring, comparing and sharing a filter are all the
same operation - and adding a new filter dimension later needs no migration.

Canonical means: only known fields, values validated, empties dropped, keys in
a fixed order. Without that last part `category=tech&hours=24` and
`hours=24&category=tech` would be the same filter but compare as different, so
the UI could never tell which chip is currently active.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode, parse_qsl

from .db import query, tx
from .util import iso, new_id, now, slugify

# Fixed order: this is what makes two equivalent filters compare equal.
FIELDS = ("q", "topic", "publisher", "category", "location", "hours", "saved")
LOCATIONS = {"fryslan", "nl", "world"}
HOURS = {"6", "24", "72", "168"}
MAX_FILTERS = 12
MAX_LABEL = 40


def normalise(raw: str | dict) -> str:
    """Reduce a query string to its canonical form, dropping anything unknown.

    Unknown keys are discarded rather than rejected: a stored filter must keep
    working when a future version stops using one of its fields.
    """
    if isinstance(raw, str):
        raw = raw.lstrip("?")
        items = dict(parse_qsl(raw, keep_blank_values=False))
    else:
        items = {k: ("" if v is None else str(v)) for k, v in (raw or {}).items()}

    out: dict[str, str] = {}
    for key in FIELDS:
        val = (items.get(key) or "").strip()
        if not val:
            continue
        if key == "hours":
            if val not in HOURS:
                continue
        elif key == "location":
            if val not in LOCATIONS:
                continue
        elif key == "saved":
            if val.lower() not in ("true", "1", "ja"):
                continue
            val = "true"
        out[key] = val
    return urlencode(out)


def _row_to_filter(row) -> dict:
    try:
        payload = json.loads(row["value"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "slug": row["topic_slug"],
        "label": payload.get("label") or row["topic_slug"],
        "qs": payload.get("qs") or "",
        "order": row["weight"],
        "since": row["created_at"],
    }


def list_all() -> list[dict]:
    rows = query("SELECT topic_slug, value, weight, created_at FROM user_preferences "
                 "WHERE kind='quickfilter' ORDER BY weight, rowid")
    return [_row_to_filter(r) for r in rows]


def _unique_slug(label: str, existing: set[str]) -> str:
    base = slugify(label)[:32] or "filter"
    slug, n = base, 2
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    return slug


def add(label: str, qs: str) -> dict:
    label = (label or "").strip()[:MAX_LABEL]
    canonical = normalise(qs)
    if not label:
        raise ValueError("geef het snelfilter een naam")
    if not canonical:
        raise ValueError("dit filter is leeg — stel eerst een filter in")

    current = list_all()
    if len(current) >= MAX_FILTERS:
        raise ValueError(f"maximaal {MAX_FILTERS} snelfilters")
    # Same filter under a different name is almost always a mistake, and two
    # identical chips would both light up as active.
    for f in current:
        if f["qs"] == canonical:
            raise ValueError(f"je hebt dit filter al als '{f['label']}'")

    slug = _unique_slug(label, {f["slug"] for f in current})
    order = max([f["order"] for f in current], default=0) + 1
    ts = iso(now())
    with tx() as c:
        c.execute("INSERT INTO user_preferences(id,user_id,kind,topic_slug,value,"
                  "weight,created_at,updated_at) VALUES(?,'local','quickfilter',?,?,?,?,?)",
                  (new_id("qf_"), slug, json.dumps({"label": label, "qs": canonical}),
                   order, ts, ts))
    return {"slug": slug, "label": label, "qs": canonical, "order": order}


def rename(slug: str, label: str) -> None:
    label = (label or "").strip()[:MAX_LABEL]
    if not label:
        raise ValueError("naam mag niet leeg zijn")
    row = query("SELECT value FROM user_preferences WHERE kind='quickfilter' "
                "AND topic_slug=?", (slug,))
    if not row:
        raise LookupError(slug)
    payload = json.loads(row[0]["value"] or "{}")
    payload["label"] = label
    with tx() as c:
        c.execute("UPDATE user_preferences SET value=?, updated_at=? "
                  "WHERE kind='quickfilter' AND topic_slug=?",
                  (json.dumps(payload), iso(now()), slug))


def remove(slug: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM user_preferences WHERE kind='quickfilter' AND topic_slug=?",
                  (slug,))


def reorder(slugs: list[str]) -> None:
    """Apply a new order. Slugs not mentioned keep their relative position
    after the ones that are, so a partial list can never drop a filter."""
    current = [f["slug"] for f in list_all()]
    known = set(current)
    wanted = [s for s in dict.fromkeys(slugs) if s in known]
    # Keep the remainder in its existing order. A set difference would lose
    # that ordering, so a partial reorder would shuffle untouched filters.
    rest = [s for s in current if s not in set(wanted)]
    with tx() as c:
        for order, slug in enumerate(wanted + rest):
            c.execute("UPDATE user_preferences SET weight=? WHERE kind='quickfilter' "
                      "AND topic_slug=?", (order, slug))
