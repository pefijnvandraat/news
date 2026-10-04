"""Fallback adapter for publishers that expose no structured feed.

Uses Playwright when it is installed (so client-rendered overview pages work)
and falls back to a plain HTTP fetch otherwise. It only reads publicly
reachable listing pages and never attempts to defeat paywalls, logins or
consent walls -- if a page is gated the adapter reports the source as degraded.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx

from .base import FetchResult, RawItem
from .rss import TIMEOUT, USER_AGENT

_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<text>(?:(?!</a>).)*)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAGS = re.compile(r"<[^>]+>")


class HtmlAdapter:
    """kind='html' — listing-page scraper used only as a last resort."""

    kind = "html"

    def fetch(self, source: dict) -> FetchResult:
        url = source["url"]
        html = self._render(url)
        if html is None:
            html = self._plain(url)
        if html is None:
            return FetchResult([], "error", "Pagina niet bereikbaar")
        if self._looks_gated(html):
            return FetchResult([], "error",
                               "Pagina is afgeschermd (consent/login) - bron overgeslagen")
        items = self._extract(html, url, source)
        return FetchResult(items, "ok" if items else "empty",
                           None if items else "Geen artikelen gevonden op de pagina")

    # -- fetching ---------------------------------------------------------
    def _render(self, url: str) -> str | None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=USER_AGENT, locale="nl-NL")
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1200)
                html = page.content()
                browser.close()
                return html
        except Exception:
            return None

    def _plain(self, url: str) -> str | None:
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                              headers={"User-Agent": USER_AGENT}) as client:
                resp = client.get(url)
            return resp.text if resp.status_code < 400 else None
        except Exception:
            return None

    # -- parsing ----------------------------------------------------------
    @staticmethod
    def _looks_gated(html: str) -> bool:
        low = html[:4000].lower()
        return any(m in low for m in ("privacygate", "consent-wall", "cmp-container",
                                      "please enable javascript and cookies"))

    def _extract(self, html: str, base_url: str, source: dict) -> list[RawItem]:
        seen: set[str] = set()
        items: list[RawItem] = []
        for m in _LINK_RE.finditer(html):
            href = urljoin(base_url, m.group("href").strip())
            text = _TAGS.sub(" ", m.group("text"))
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 25 or href in seen:
                continue
            if not re.search(r"/(nieuws|artikel|article|\d{6,})", href):
                continue
            seen.add(href)
            items.append(RawItem(url=href, title=text, guid=href,
                                 category=source.get("category_hint")))
            if len(items) >= 60:
                break
        return items
