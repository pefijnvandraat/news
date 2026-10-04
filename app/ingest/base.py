"""Source adapter contract.

Every adapter turns one configured source into a list of RawItem dicts. The
rest of the pipeline only knows about RawItem, so new publisher mechanisms
(RSS, JSON API, Playwright-rendered HTML) plug in without touching anything
downstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class RawItem:
    url: str
    title: str
    guid: str | None = None
    description: str | None = None
    image_url: str | None = None
    author: str | None = None
    category: str | None = None
    language: str | None = None
    published_at: object | None = None
    updated_at: object | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    items: list[RawItem]
    status: str = "ok"          # ok | error | empty
    error: str | None = None
    http_status: int | None = None


class SourceAdapter(Protocol):
    kind: str

    def fetch(self, source: dict) -> FetchResult: ...
