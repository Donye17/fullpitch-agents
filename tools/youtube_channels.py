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
)

BLOCKED_CHANNEL_MARKERS = (
    "redacao conectada",
    "tribunal de justica",
)

# Standalone "TJRS" matches the Brazilian court channel; rugby uses "Jacks Rangers".
RUGBY_TJRS_CHANNEL_MARKER = "jacks rangers"


def normalize_channel_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def is_blocked_youtube_channel(channel_title: str | None) -> bool:
    """Block known spam channels (Brazilian court TJRS, etc.)."""
    normalized = normalize_channel_name(channel_title)
    if not normalized:
        return False

    for marker in BLOCKED_CHANNEL_MARKERS:
        if marker in normalized:
            return True

    if "tribunal" in normalized and "justica" in normalized:
        return True

    if "tjrs" in normalized and RUGBY_TJRS_CHANNEL_MARKER not in normalized:
        return True

    return False


def approved_channel_terms(sources: list[dict[str, Any]] | None = None) -> set[str]:
    terms = {normalize_channel_name(channel) for channel in APPROVED_YOUTUBE_CHANNELS}
    for source in sources or []:
        name = normalize_channel_name(source.get("name"))
        if name and name != "tjrs":
            terms.add(name)
        url = normalize_channel_name(source.get("url"))
        if url and url != "tjrs":
            terms.add(url)
    terms.discard("")
    terms.discard("tjrs")
    return terms


def is_approved_youtube_channel(channel_title: str | None, terms: set[str]) -> bool:
    if is_blocked_youtube_channel(channel_title):
        return False

    normalized = normalize_channel_name(channel_title)
    if not normalized:
        return False

    if RUGBY_TJRS_CHANNEL_MARKER in normalized:
        return True

    return any(
        term
        and term != "tjrs"
        and (term in normalized or normalized in term)
        for term in terms
    )
