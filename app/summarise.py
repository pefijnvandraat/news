"""Story-level synthesis: neutral headline, factual summary, disagreement flags.

Everything produced here is extractive and derived from publisher-supplied
headlines and summaries. Nothing is invented, and the API labels the output as
automatically generated so the UI can keep it visually separate from
publisher-written text.
"""
from __future__ import annotations

import re
from collections import Counter

from .topics import tokenize
from .util import age_hours, iso, parse_dt, strip_html, truncate

# "Wordt bijgewerkt" must mean the story is moving *now*. Two separate traps:
#   * no recency gate -> a story keeps the badge days after it settled;
#   * too short a span -> a story that merely broke an hour ago and was picked
#     up by four outlets at once looks "developing", which is just normal
#     clustering. Real updating means movement that continues well after the
#     initial burst of coverage.
DEVELOPING_RECENT_HOURS = 3.0       # last activity must be at least this fresh
DEVELOPING_MIN_SPAN_HOURS = 3.0     # and coverage must still be arriving late

# Headline noise publishers add that is not part of the fact.
_PREFIX_NOISE = re.compile(
    r"^(live|liveblog|update|breaking|video|kijk|column|opinie|analyse|interview|"
    r"podcast|achtergrond|reportage|samenvatting|wekdienst[^:]*)\s*[:|\u2022\-\u2013]\s*",
    re.IGNORECASE)
_TRAIL_NOISE = re.compile(r"\s*[\u2022|]\s*[^\u2022|]{0,60}$")
_CLICKBAIT = re.compile(
    r"^(dit is waarom|dit zijn|zo |waarom |hoe |wat |de reden|je gelooft|"
    r"deze |hier |kijk )", re.IGNORECASE)

_NUM_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:[.\u00a0]\d{3})+|\d+(?:,\d+)?)\s*"
                     r"(procent|%|miljoen|miljard|doden|gewonden|mensen|jaar|euro|"
                     r"graden|kilometer|km|uur|keer|zetels|stemmen)?", re.IGNORECASE)


def build_story_view(members: list[dict]) -> dict:
    """Derive the presentable story fields from its member articles."""
    ordered = sorted(members, key=lambda m: _ts(m) or "")
    publishers = {m["publisher_id"] for m in members}
    times = [parse_dt(m.get("updated_at") or m.get("published_at")) for m in members]
    times = [t for t in times if t]
    first = sorted(times)[0] if times else None
    last = sorted(times)[-1] if times else None

    headline, headline_source = _neutral_headline(members)
    summary = _summary(members)
    disagreements = _disagreements(members)
    image = next((m.get("image_url") for m in ordered[::-1] if m.get("image_url")), None)
    scopes = Counter(m.get("geo_scope") or "nl" for m in members)
    is_updating = _is_developing(members, first, last)

    return {
        "headline": headline,
        "headline_source": headline_source,
        "summary": summary,
        "publisher_count": len(publishers),
        "first_published_at": iso(first),
        "last_updated_at": iso(last),
        "image_url": image,
        "geo_scope": scopes.most_common(1)[0][0],
        "disagreements": disagreements,
        "is_updating": is_updating,
    }


def _ts(m: dict) -> str | None:
    return m.get("updated_at") or m.get("published_at")


def _is_developing(members: list[dict], first, last) -> int:
    """Is this story still actively being updated?

    Distinguishes "still unfolding" from "broke recently". Two independent
    pieces of evidence, both requiring recent activity:

      * a publisher revised an article after publishing it - unambiguous, a
        revision only happens when the text actually changed; or
      * coverage is still arriving hours after the first report, rather than
        several outlets publishing at once, which is ordinary clustering.

    The recency gate matters most: a story that broke yesterday and has since
    settled is not "being updated", however heavily it was covered at the time.
    """
    if last is None or age_hours(last) > DEVELOPING_RECENT_HOURS:
        return 0
    if any(int(m.get("revision") or 1) > 1 for m in members):
        return 1
    return 1 if (len(members) > 1 and first is not None
                 and (last - first).total_seconds() / 3600.0 >= DEVELOPING_MIN_SPAN_HOURS) else 0


def clean_headline(title: str) -> str:
    t = strip_html(title or "").strip()
    for _ in range(2):
        t = _PREFIX_NOISE.sub("", t).strip()
    t = _TRAIL_NOISE.sub("", t).strip() if t.count("\u2022") or t.count("|") else t
    t = t.strip(" -\u2013\u2014")
    return t


def _neutral_headline(members: list[dict]) -> tuple[str, str | None]:
    """Pick the most central, least sensational publisher headline.

    Selecting rather than paraphrasing keeps the headline factual; the source
    publisher is recorded so the UI can attribute it.
    """
    cands = []
    for m in members:
        title = clean_headline(m["title"])
        if len(title) < 10:
            continue
        cands.append((m, title, set(tokenize(title))))
    if not cands:
        return strip_html(members[0]["title"])[:160], members[0]["publisher_id"]

    best, best_score = cands[0], -1e9
    for m, title, toks in cands:
        overlap = sum(len(toks & other) / max(1, len(toks | other))
                      for _m2, _t2, other in cands if other is not toks)
        centrality = overlap / max(1, len(cands) - 1)
        penalty = 0.0
        if _CLICKBAIT.match(title):
            penalty += 0.18
        if title.endswith("?"):
            penalty += 0.12
        if "'" in title or "\u2018" in title:   # quoted opinion fragments
            penalty += 0.06
        length_fit = 1.0 - abs(len(title) - 78) / 160.0
        score = centrality * 1.0 + length_fit * 0.35 - penalty
        if score > best_score:
            best, best_score = (m, title, toks), score
    m, title, _ = best
    return truncate(title, 160), m["publisher_id"]


def _summary(members: list[dict]) -> str:
    """Extractive multi-source summary: 1-3 sentences carrying the shared facts."""
    pool: list[tuple[str, str]] = []           # (sentence, publisher)
    for m in members:
        text = strip_html(m.get("description") or "")
        for sent in re.split(r"(?<=[.!?])\s+", text):
            sent = sent.strip()
            if 40 <= len(sent) <= 320:
                pool.append((sent, m["publisher_id"]))
    if not pool:
        base = strip_html(members[0].get("description") or members[0]["title"])
        return truncate(base, 260)

    term_freq = Counter()
    for sent, _p in pool:
        term_freq.update(set(tokenize(sent)))

    scored = []
    for idx, (sent, pub) in enumerate(pool):
        toks = set(tokenize(sent))
        if not toks:
            continue
        density = sum(term_freq[t] for t in toks) / len(toks)
        scored.append((density - idx * 0.01, sent, pub, toks))
    scored.sort(key=lambda s: -s[0])

    chosen: list[str] = []
    used_tokens: set[str] = set()
    used_pubs: set[str] = set()
    for _score, sent, pub, toks in scored:
        if len(chosen) >= 3:
            break
        if toks and len(toks & used_tokens) / len(toks) > 0.62:
            continue  # near-duplicate of a sentence we already have
        if pub in used_pubs and len(chosen) >= 1 and len(used_pubs) < 3:
            continue  # prefer breadth across publishers first
        chosen.append(sent)
        used_tokens |= toks
        used_pubs.add(pub)
    return truncate(" ".join(chosen), 480)


def _disagreements(members: list[dict]) -> list[dict]:
    """Surface materially different figures reported by different publishers.

    Only numbers carrying the same unit are compared, and only a meaningful
    relative gap is reported, so routine rounding is not flagged.
    """
    by_unit: dict[str, list[tuple[float, str, str]]] = {}
    for m in members:
        text = f"{strip_html(m['title'])} {strip_html(m.get('description') or '')}"
        for match in _NUM_RE.finditer(text):
            unit = (match.group(2) or "").lower()
            if not unit:
                continue
            raw = match.group(1).replace("\u00a0", "").replace(".", "").replace(",", ".")
            try:
                value = float(raw)
            except ValueError:
                continue
            unit = "procent" if unit == "%" else unit
            by_unit.setdefault(unit, []).append((value, m["publisher_id"], match.group(0).strip()))

    out: list[dict] = []
    for unit, vals in by_unit.items():
        pubs = {p for _v, p, _r in vals}
        if len(pubs) < 2:
            continue
        lo = min(vals, key=lambda v: v[0])
        hi = max(vals, key=lambda v: v[0])
        if lo[1] == hi[1] or hi[0] == 0:
            continue
        spread = (hi[0] - lo[0]) / max(abs(hi[0]), 1.0)
        if spread < 0.12:
            continue
        out.append({
            "unit": unit,
            "detail": f"{lo[1]} meldt {lo[2]}, {hi[1]} meldt {hi[2]}",
            "low": {"publisher_id": lo[1], "value": lo[2]},
            "high": {"publisher_id": hi[1], "value": hi[2]},
        })
    return out[:3]
