"""Publisher & feed registry plus the category taxonomy.

Adding a publisher later means adding a row here (or via the /api/sources
endpoint at runtime) — nothing else in the pipeline needs to change.
"""
from __future__ import annotations

from .db import one, tx
from .util import iso, new_id, now

DEFAULT_USER = "local"

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
CATEGORIES: list[tuple[str, str]] = [
    ("nederland", "Nederland"),
    ("buitenland", "Wereld"),
    ("politiek", "Politiek"),
    ("economie", "Economie"),
    ("tech", "Technologie"),
    ("sport", "Sport"),
    ("cultuur", "Cultuur"),
    ("entertainment", "Entertainment"),
    ("fryslan", "Frysl\u00e2n"),
    ("overig", "Overig"),
]
CATEGORY_LABELS = dict(CATEGORIES)

# Mapping of raw feed-category / URL-path fragments onto our taxonomy.
CATEGORY_ALIASES: dict[str, str] = {
    "binnenland": "nederland", "nederland": "nederland",
    "gezond": "nederland", "wonen": "nederland", "dieren": "nederland",
    "buitenland": "buitenland", "wereld": "buitenland", "world": "buitenland",
    "spanningen-oekraine": "buitenland", "oorlog": "buitenland",
    "politiek": "politiek", "politics": "politiek", "polityk": "politiek",
    "verkiezingen": "politiek", "kabinet": "politiek",
    "economie": "economie", "geld": "economie", "werk": "economie",
    "beurs": "economie", "business": "economie", "auto": "economie",
    "tech": "tech", "technologie": "tech", "digitaal": "tech", "wetenschap": "tech",
    # Tweakers labels items as "Nieuws / <sectie> / <subsectie>"; these are the
    # recurring sections. Their "Politiek en recht" is tech law and regulation,
    # not Dutch cabinet politics, so it stays under tech where readers expect it.
    "it-pro": "tech", "computers": "tech", "hardware": "tech", "software": "tech",
    "pc-s": "tech", "processors": "tech", "videokaarten": "tech", "opslag": "tech",
    "tablets-en-telefoons": "tech", "smartphones": "tech", "e-readers": "tech",
    "beeld-en-geluid": "tech", "mediaspelers": "tech", "televisies": "tech",
    "internettoegang": "tech", "websites-en-community-s": "tech",
    "politiek-en-recht": "tech", "singleboardcomputers": "tech",
    "security": "tech", "privacy": "tech", "apps": "tech", "ai": "tech",
    "economie-en-maatschappij": "economie",
    # Gaming on a tech site is hardware and industry news ("AMD-driver verwijst
    # naar mogelijke gpu PS6"), not showbiz. Entertainment stays reserved for
    # AD Show / NU Achterklap so it keeps meaning something.
    "gaming": "tech", "games": "tech", "consoles": "tech",
    "sport": "sport", "voetbal": "sport", "wielrennen": "sport", "sporten": "sport",
    "schaatsen": "sport", "formule-1": "sport", "tennis": "sport", "darts": "sport",
    "cultuur": "cultuur", "cultuur-en-media": "cultuur", "boeken": "cultuur",
    "kunst": "cultuur", "media": "cultuur", "opmerkelijk": "cultuur",
    "entertainment": "entertainment", "achterklap": "entertainment",
    "show": "entertainment", "muziek": "entertainment", "film": "entertainment",
    "tv": "entertainment", "lifestyle": "entertainment", "boulevard": "entertainment",
    "fryslan": "fryslan", "friesland": "fryslan", "regio": "fryslan",
    "regionaal": "fryslan", "ljouwert": "fryslan",
}

# Generic path/feed labels that carry no categorical information. They must
# never win over a specific segment such as ".../nieuws/buitenland/...".
GENERIC_CATEGORY_TOKENS = {
    "nieuws", "news", "algemeen", "artikel", "article", "video", "videos",
    "live", "l", "nl", "fy", "index", "rss", "feed", "explainers", "overig",
    "reviews", "review", "achtergrond", "dossiers", "onderwerpen", "nieuwsbrief",
}

# ---------------------------------------------------------------------------
# Publishers and their official feeds
# ---------------------------------------------------------------------------
PUBLISHERS: list[dict] = [
    {"id": "nu", "name": "NU.nl", "homepage": "https://www.nu.nl/",
     "region": "nl", "weight": 1.0, "colour": "#d6002a"},
    {"id": "nos", "name": "NOS", "homepage": "https://nos.nl/",
     "region": "nl", "weight": 1.15, "colour": "#ab0000"},
    {"id": "rtl", "name": "RTL Nieuws", "homepage": "https://www.rtl.nl/nieuws",
     "region": "nl", "weight": 1.0, "colour": "#0a4ea2"},
    {"id": "omrop", "name": "Omrop Frysl\u00e2n", "homepage": "https://www.omropfryslan.nl/nl/nieuws",
     "region": "fryslan", "weight": 0.95, "colour": "#0a7d4b",
     "default_category": "fryslan"},
    {"id": "ad", "name": "AD", "homepage": "https://www.ad.nl/nieuws/",
     "region": "nl", "weight": 1.0, "colour": "#e2001a"},
    # Specialist tech titles. Their feeds label almost everything "Nieuws", so
    # the publisher default carries the category instead of the feed.
    {"id": "tweakers", "name": "Tweakers", "homepage": "https://tweakers.net/",
     "region": "nl", "weight": 0.9, "colour": "#e4454f",
     "default_category": "tech"},
    {"id": "bright", "name": "Bright", "homepage": "https://www.bright.nl/",
     "region": "nl", "weight": 0.85, "colour": "#00a1e4",
     "default_category": "tech"},
]

FEEDS: list[dict] = [
    # NU.nl
    {"publisher_id": "nu", "name": "NU.nl Algemeen", "url": "https://www.nu.nl/rss/Algemeen", "category_hint": None},
    {"publisher_id": "nu", "name": "NU.nl Binnenland", "url": "https://www.nu.nl/rss/Binnenland", "category_hint": "nederland"},
    {"publisher_id": "nu", "name": "NU.nl Buitenland", "url": "https://www.nu.nl/rss/Buitenland", "category_hint": "buitenland"},
    {"publisher_id": "nu", "name": "NU.nl Politiek", "url": "https://www.nu.nl/rss/Politiek", "category_hint": "politiek"},
    {"publisher_id": "nu", "name": "NU.nl Economie", "url": "https://www.nu.nl/rss/Economie", "category_hint": "economie"},
    {"publisher_id": "nu", "name": "NU.nl Tech", "url": "https://www.nu.nl/rss/Tech", "category_hint": "tech"},
    {"publisher_id": "nu", "name": "NU.nl Sport", "url": "https://www.nu.nl/rss/Sport", "category_hint": "sport"},
    {"publisher_id": "nu", "name": "NU.nl Achterklap", "url": "https://www.nu.nl/rss/Achterklap", "category_hint": "entertainment"},
    # NOS
    {"publisher_id": "nos", "name": "NOS Algemeen", "url": "https://feeds.nos.nl/nosnieuwsalgemeen", "category_hint": None},
    {"publisher_id": "nos", "name": "NOS Binnenland", "url": "https://feeds.nos.nl/nosnieuwsbinnenland", "category_hint": "nederland"},
    {"publisher_id": "nos", "name": "NOS Buitenland", "url": "https://feeds.nos.nl/nosnieuwsbuitenland", "category_hint": "buitenland"},
    {"publisher_id": "nos", "name": "NOS Politiek", "url": "https://feeds.nos.nl/nosnieuwspolitiek", "category_hint": "politiek"},
    {"publisher_id": "nos", "name": "NOS Economie", "url": "https://feeds.nos.nl/nosnieuwseconomie", "category_hint": "economie"},
    {"publisher_id": "nos", "name": "NOS Tech", "url": "https://feeds.nos.nl/nosnieuwstech", "category_hint": "tech"},
    {"publisher_id": "nos", "name": "NOS Sport", "url": "https://feeds.nos.nl/nossportalgemeen", "category_hint": "sport"},
    {"publisher_id": "nos", "name": "NOS Cultuur & Media", "url": "https://feeds.nos.nl/nosnieuwscultuurenmedia", "category_hint": "cultuur"},
    # RTL Nieuws (official open feed; www.rtl.nl/rss/* only redirects through a consent gate)
    {"publisher_id": "rtl", "name": "RTL Nieuws", "url": "https://www.rtlnieuws.nl/rss.xml", "category_hint": None},
    # Omrop Frysl\u00e2n. Note: their /rss/<path> endpoints all return the same
    # newsroom feed, so a single source is registered; the category is derived
    # from the article itself instead of from a feed hint.
    {"publisher_id": "omrop", "name": "Omrop Frysl\u00e2n Nieuws", "url": "https://www.omropfryslan.nl/rss/nieuws", "category_hint": None},
    # AD
    {"publisher_id": "ad", "name": "AD Nieuws", "url": "https://www.ad.nl/nieuws/rss.xml", "category_hint": None},
    {"publisher_id": "ad", "name": "AD Binnenland", "url": "https://www.ad.nl/binnenland/rss.xml", "category_hint": "nederland"},
    {"publisher_id": "ad", "name": "AD Buitenland", "url": "https://www.ad.nl/buitenland/rss.xml", "category_hint": "buitenland"},
    {"publisher_id": "ad", "name": "AD Politiek", "url": "https://www.ad.nl/politiek/rss.xml", "category_hint": "politiek"},
    {"publisher_id": "ad", "name": "AD Economie", "url": "https://www.ad.nl/economie/rss.xml", "category_hint": "economie"},
    {"publisher_id": "ad", "name": "AD Tech", "url": "https://www.ad.nl/tech/rss.xml", "category_hint": "tech"},
    {"publisher_id": "ad", "name": "AD Sport", "url": "https://www.ad.nl/sport/rss.xml", "category_hint": "sport"},
    {"publisher_id": "ad", "name": "AD Show", "url": "https://www.ad.nl/show/rss.xml", "category_hint": "entertainment"},
    # Tweakers. Official open feed; 'nieuws.xml' is news only, 'mixed.xml' also
    # carries reviews and background pieces.
    {"publisher_id": "tweakers", "name": "Tweakers Nieuws", "url": "https://tweakers.net/feeds/nieuws.xml", "category_hint": None},
    # Bright (DPG). Official open feed - note this one is NOT behind the
    # consent gate that fronts the rest of the site.
    {"publisher_id": "bright", "name": "Bright", "url": "https://www.bright.nl/rss", "category_hint": None},
]


def seed() -> None:
    """Insert the built-in publishers/feeds once. User edits are never overwritten."""
    ts = iso(now())
    with tx() as c:
        for p in PUBLISHERS:
            c.execute(
                "INSERT INTO publishers(id,name,homepage,region,weight,colour,"
                "default_category,enabled,user_added,created_at) "
                "VALUES(?,?,?,?,?,?,?,1,0,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, homepage=excluded.homepage, region=excluded.region, "
                "weight=excluded.weight, colour=excluded.colour, "
                "default_category=excluded.default_category",
                (p["id"], p["name"], p["homepage"], p["region"], p["weight"], p["colour"],
                 p.get("default_category"), ts),
            )
        for f in FEEDS:
            exists = c.execute("SELECT id FROM sources WHERE url=?", (f["url"],)).fetchone()
            if exists:
                continue
            c.execute(
                "INSERT INTO sources(id,publisher_id,name,url,kind,category_hint,enabled,"
                "user_added,created_at) VALUES(?,?,?,?,'rss',?,1,0,?)",
                (new_id("src_"), f["publisher_id"], f["name"], f["url"], f["category_hint"], ts),
            )


def publisher_weight(publisher_id: str) -> float:
    row = one("SELECT weight FROM publishers WHERE id=?", (publisher_id,))
    return float(row["weight"]) if row else 1.0
