"""Fullpitch REST API wrapper — read and write operations.

All writes go through /api/v1/ingest/ endpoints with Bearer auth.
All reads go through /api/v1/ public endpoints.
"""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("FULLPITCH_API_URL", "https://fullpitch.app")
API_KEY = os.getenv("FULLPITCH_API_KEY", "")

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            base_url=BASE_URL,
            timeout=30.0,
            headers={"User-Agent": "FullpitchAgent/1.0"},
        )
    return _client


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


def get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public read from /api/v1/."""
    response = _get_client().get(f"/api/v1/{path}", params=params)
    response.raise_for_status()
    return response.json()


def ingest(endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
    """Authenticated write to /api/v1/ingest/."""
    response = _get_client().post(
        f"/api/v1/ingest/{endpoint}",
        json=data,
        headers=_auth_headers(),
    )
    response.raise_for_status()
    return response.json()


def ingest_match(data: dict[str, Any]) -> dict[str, Any]:
    return ingest("match", data)


def ingest_standing(data: dict[str, Any]) -> dict[str, Any]:
    return ingest("standing", data)


def ingest_article(data: dict[str, Any]) -> dict[str, Any]:
    return ingest("article", data)


def ingest_video(data: dict[str, Any]) -> dict[str, Any]:
    return ingest("video", data)


def ingest_player(data: dict[str, Any]) -> dict[str, Any]:
    return ingest("player", data)


def ingest_conflict(data: dict[str, Any]) -> dict[str, Any]:
    return ingest("conflict", data)
