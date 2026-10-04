"""Stock quotes for the ticker.

Uses Yahoo Finance's public chart endpoint, which needs no API key. Two
deliberate constraints shape this module:

* **One request per symbol.** The batch endpoint (`/v7/finance/quote`) returns
  401 without a session crumb, so quotes are fetched in parallel instead. That
  is why the watchlist is capped - a long list would mean a long wait.
* **Quotes are cached.** The ticker polls on a timer and several browser tabs
  may do so at once; without a cache every poll would hit Yahoo once per
  symbol. A minute of staleness is invisible in a ticker.

A failing symbol never breaks the ticker: it is returned with `ok: false` and
the UI simply shows it as unavailable.
"""
from __future__ import annotations

import threading
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from .db import query, tx
from .util import iso, new_id, now

SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Yahoo serves a different (often empty) payload to clients that do not look
# like a browser.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json,text/plain,*/*",
}
TIMEOUT = httpx.Timeout(8.0, connect=4.0)

MAX_WATCHLIST = 12
CACHE_SECONDS = 60

# Instrument types worth showing. Options and futures have symbols that mean
# nothing in a ticker, so they are filtered out of search results.
USEFUL_TYPES = {"EQUITY", "ETF", "INDEX", "MUTUALFUND", "CRYPTOCURRENCY", "CURRENCY"}

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


_SUFFIXES = re.compile(
    r"\s*(,)?\s*\b("
    r"corporation|corp|incorporated|inc|company|co|holding|holdings|group|"
    r"n\.?v|b\.?v|s\.?a|a\.?g|plc|ltd|limited|se|sa|ag|nv|bv|class [a-c]"
    r")\b\.?\s*$", re.IGNORECASE)


def short_label(name: str, symbol: str) -> str:
    """A chip-sized label.

    "Microsoft Corporation" does not fit a one-line ticker, and "MSFT" means
    nothing to a reader who searched for the company by name. Trimming the
    legal suffix keeps the recognisable part.
    """
    label = (name or "").strip()
    for _ in range(3):                      # "Heineken Holding N.V." needs two
        trimmed = _SUFFIXES.sub("", label).strip(" .,-")
        if trimmed == label or not trimmed:
            break
        label = trimmed
    if not label:
        return symbol
    return label if len(label) <= 22 else label[:21].rstrip() + "…"


# ---------------------------------------------------------------------------
# Yahoo access
# ---------------------------------------------------------------------------
def search(term: str, limit: int = 8) -> list[dict]:
    """Resolve a company name or ticker to candidate symbols.

    Accepts "heineken" as readily as "HEIA.AS" - a reader should not need to
    know that Heineken trades as HEIA on Amsterdam.
    """
    term = (term or "").strip()
    if len(term) < 2:
        return []
    try:
        with httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True) as c:
            r = c.get(SEARCH_URL, params={"q": term, "quotesCount": 16, "newsCount": 0})
            r.raise_for_status()
            payload = r.json()
    except Exception:
        return []

    out, seen = [], set()
    for q in payload.get("quotes") or []:
        sym = (q.get("symbol") or "").strip()
        kind = (q.get("quoteType") or "").upper()
        if not sym or sym in seen or kind not in USEFUL_TYPES:
            continue
        seen.add(sym)
        out.append({
            "symbol": sym,
            "name": q.get("shortname") or q.get("longname") or sym,
            "exchange": q.get("exchDisp") or q.get("exchange") or "",
            "type": kind,
        })
        if len(out) >= limit:
            break
    return out


def _fetch_one(symbol: str) -> dict:
    try:
        with httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True) as c:
            r = c.get(CHART_URL.format(symbol=symbol),
                      params={"range": "1d", "interval": "1d"})
            r.raise_for_status()
            meta = (r.json().get("chart", {}).get("result") or [{}])[0].get("meta") or {}
    except Exception as exc:
        return {"symbol": symbol, "ok": False, "error": type(exc).__name__}

    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None:
        return {"symbol": symbol, "ok": False, "error": "geen koers"}

    # Yahoo gives a percentage but not an absolute change, and omits the
    # percentage entirely for some instruments. Derive whatever is missing.
    pct = meta.get("regularMarketChangePercent")
    change = None
    if prev:
        change = price - prev
        if pct is None:
            pct = change / prev * 100 if prev else None
    elif pct is not None:
        change = price * pct / 100

    return {
        "symbol": meta.get("symbol") or symbol,
        "ok": True,
        "name": meta.get("shortName") or meta.get("longName") or symbol,
        "price": round(float(price), 4),
        "change": round(float(change), 4) if change is not None else None,
        "change_pct": round(float(pct), 2) if pct is not None else None,
        "currency": meta.get("currency") or "",
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        "at": meta.get("regularMarketTime"),
    }


def quotes(symbols: list[str], force: bool = False) -> list[dict]:
    """Current quotes, served from a short-lived cache where possible."""
    symbols = [s for s in dict.fromkeys(s.strip().upper() for s in symbols) if s]
    if not symbols:
        return []

    cutoff = time.time() - CACHE_SECONDS
    fresh: dict[str, dict] = {}
    if not force:
        with _cache_lock:
            for s in symbols:
                hit = _cache.get(s)
                if hit and hit[0] > cutoff:
                    fresh[s] = hit[1]

    missing = [s for s in symbols if s not in fresh]
    if missing:
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
            for res in pool.map(_fetch_one, missing):
                fresh[res["symbol"].upper()] = res
                # Cache failures too, briefly, so a dead symbol cannot turn
                # every poll into a fresh round of timeouts.
                with _cache_lock:
                    _cache[res["symbol"].upper()] = (time.time(), res)
                    # The symbol Yahoo echoes back can differ in case from the
                    # one asked for; key both so the next lookup still hits.
                    _cache[res["symbol"]] = (time.time(), res)

    return [fresh[s] for s in symbols if s in fresh]


# ---------------------------------------------------------------------------
# Watchlist (stored alongside the other user preferences)
# ---------------------------------------------------------------------------
def watchlist(user: str) -> list[dict]:
    rows = query("SELECT topic_slug, value, created_at FROM user_preferences "
                 "WHERE user_id=? AND kind='stock' ORDER BY rowid", (user,))
    return [{"symbol": r["topic_slug"], "name": r["value"] or r["topic_slug"],
             "since": r["created_at"]} for r in rows]


def add(user: str, symbol: str, name: str | None = None) -> dict:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbool ontbreekt")
    current = watchlist(user)
    if any(w["symbol"] == symbol for w in current):
        return {"ok": True, "already": True}
    if len(current) >= MAX_WATCHLIST:
        raise ValueError(f"maximaal {MAX_WATCHLIST} fondsen in de ticker")

    # Verify before storing, so a typo cannot park a permanently failing symbol
    # in the ticker - the same rule the source list follows.
    quote = _fetch_one(symbol)
    if not quote.get("ok"):
        raise ValueError(f"geen koers gevonden voor '{symbol}'")

    ts = iso(now())
    with tx() as c:
        c.execute("INSERT INTO user_preferences(id,user_id,kind,topic_slug,value,"
                  "weight,created_at,updated_at) VALUES(?,?,'stock',?,?,1.0,?,?)",
                  (new_id("stk_"), user, symbol,
                   name or quote.get("name") or symbol, ts, ts))
    return {"ok": True, "quote": quote}


def remove(user: str, symbol: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM user_preferences WHERE user_id=? AND kind='stock' "
                  "AND topic_slug=?", (user, (symbol or "").strip().upper()))


def ticker_quotes(user: str, force: bool = False) -> list[dict]:
    """Watchlist quotes, keeping the user's own label and ordering."""
    watched = watchlist(user)
    if not watched:
        return []
    live = {q["symbol"].upper(): q for q in quotes([w["symbol"] for w in watched], force)}
    out = []
    for w in watched:
        q = dict(live.get(w["symbol"].upper()) or {"symbol": w["symbol"], "ok": False})
        q["name"] = w["name"]
        q["label"] = short_label(w["name"], w["symbol"])
        out.append(q)

    # Trimming suffixes can collapse two different instruments onto one label -
    # "Heineken N.V." and "Heineken Holding N.V." are separate stocks. Where
    # that happens, fall back to the symbol so the chips stay distinguishable.
    counts: dict[str, int] = {}
    for q in out:
        counts[q["label"]] = counts.get(q["label"], 0) + 1
    for q in out:
        if counts[q["label"]] > 1:
            q["label"] = q["symbol"].split(".")[0]
    return out
