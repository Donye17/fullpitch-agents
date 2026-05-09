"""Shared YouTube channel allow-list helpers."""

from __future__ import annotations

import re
from typing import Any

APPROVED_YOUTUBE_CHANNELS = (
    "Major League Rugby",
    "USA Rugby",
    "CRAA Rugby",
    "National Collegiate Rugby",
    "The Rugby Review",
    "The Jacks Rangers Show",
    "TJRS",
)


def normalize_channel_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def approved_channel_terms(sources: list[dict[str, Any]] | None = None) -> set[str]:
    terms = {normalize_channel_name(channel) for channel in APPROVED_YOUTUBE_CHANNELS}
    for source in sources or []:
        normalized = normalize_channel_name(source.get("name"))
        if normalized:
            terms.add(normalized)
    terms.discard("")
    return terms


def is_approved_youtube_channel(channel_title: str | None, terms: set[str]) -> bool:
    normalized = normalize_channel_name(channel_title)
    if not normalized:
        return False
    return any(term and (term in normalized or normalized in term) for term in terms)
