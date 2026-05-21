"""Fullpitch REST API wrapper — read and write operations.

All writes go through /api/v1/ingest/ endpoints with Bearer auth.
All reads go through /api/v1/ public endpoints.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _lookup_key(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


class FullpitchAPIError(Exception):
    """Raised when the Fullpitch API returns a non-200 response."""

    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        self.status = status
        super().__init__(f"{method} {url} returned {status}: {body}")


class FullpitchAPI:
    """Sync client for the Fullpitch REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("FULLPITCH_API_URL", "https://fullpitch.app")).rstrip("/")
        self.api_key = api_key or os.getenv("FULLPITCH_API_KEY", "")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=30.0,
            headers={"User-Agent": "FullpitchAgent/1.0"},
            follow_redirects=True,
        )

    # ── internal helpers ──────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path if path.startswith("/api/") else f"/api/v1/{path.lstrip('/')}"
        logger.info("GET %s%s %s", self.base_url, url, params or "")
        resp = self._client.get(url, params=params)
        if resp.status_code != 200:
            raise FullpitchAPIError("GET", f"{self.base_url}{url}", resp.status_code, resp.text[:500])
        return resp.json()

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        url = f"/api/v1/ingest/{path}"
        logger.info("POST %s%s", self.base_url, url)
        resp = self._client.post(url, json=data, headers=self._auth_headers())
        if resp.status_code not in (200, 201):
            raise FullpitchAPIError("POST", f"{self.base_url}{url}", resp.status_code, resp.text[:500])
        return resp.json()

    def _patch(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        url = f"/api/v1/{path}"
        logger.info("PATCH %s%s", self.base_url, url)
        resp = self._client.patch(url, json=data, headers=self._auth_headers())
        if resp.status_code != 200:
            raise FullpitchAPIError("PATCH", f"{self.base_url}{url}", resp.status_code, resp.text[:500])
        return resp.json()

    def _delete(self, path: str) -> dict[str, Any]:
        url = f"/api/v1/{path}"
        logger.info("DELETE %s%s", self.base_url, url)
        resp = self._client.delete(url, headers=self._auth_headers())
        if resp.status_code != 200:
            raise FullpitchAPIError("DELETE", f"{self.base_url}{url}", resp.status_code, resp.text[:500])
        return resp.json()

    # ── READ methods ──────────────────────────────────────────────────────

    def get_recent_matches(
        self, league: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """GET /api/v1/matches — recent matches, optionally filtered by league."""
        params: dict[str, Any] = {"limit": limit}
        if league:
            params["league"] = league
        envelope = self._get("matches", params)
        return envelope.get("data", [])

    def get_matches(
        self,
        league: str | None = None,
        season: str | None = None,
        status: str | list[str] | tuple[str, ...] | None = None,
        limit: int = 100,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """GET /api/v1/matches — match list with common filters."""
        if isinstance(status, (list, tuple)):
            matches: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in status:
                for match in self.get_matches(league=league, season=season, status=item, limit=limit, page=page):
                    match_id = match.get("id")
                    if match_id and match_id in seen:
                        continue
                    if match_id:
                        seen.add(match_id)
                    matches.append(match)
            return matches

        params: dict[str, Any] = {"limit": limit, "page": page}
        if league:
            params["league"] = league
        if season:
            params["season"] = season
        if status:
            params["status"] = status
        envelope = self._get("matches", params)
        return envelope.get("data", [])

    def get_team(
        self, name: str | None = None, id: str | None = None
    ) -> dict[str, Any] | None:
        """GET /api/v1/teams/[id] or search by name via /api/v1/teams?name=."""
        if id:
            try:
                envelope = self._get(f"teams/{id}")
                return envelope.get("data")
            except FullpitchAPIError as exc:
                if exc.status == 404:
                    return None
                raise
        if name:
            # The public teams endpoint does not currently implement a name filter,
            # so fetch a bounded list and match locally instead of accepting item 0.
            envelope = self._get("teams", {"limit": 200})
            items = envelope.get("data", [])
            needle = _lookup_key(name)

            for item in items:
                values = [
                    item.get("name"),
                    item.get("shortName"),
                    item.get("abbreviation"),
                    item.get("slug"),
                ]
                if any(_lookup_key(value) == needle for value in values):
                    return item

            return None
        return None

    def get_player(
        self, name: str | None = None, id: str | None = None
    ) -> dict[str, Any] | None:
        """GET /api/v1/players/[id] or search by name via /api/v1/players."""
        if id:
            try:
                envelope = self._get(f"players/{id}")
                return envelope.get("data")
            except FullpitchAPIError as exc:
                if exc.status == 404:
                    return None
                raise
        if name:
            envelope = self._get("players", {"name": name, "limit": 1})
            items = envelope.get("data", [])
            return items[0] if items else None
        return None

    def get_standings(
        self, league: str | None = None, season: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /api/v1/standings — filtered by league and/or season."""
        params: dict[str, Any] = {}
        if league:
            params["league"] = league
        if season:
            params["season"] = season
        envelope = self._get("standings", params or None)
        return envelope.get("data", [])

    def get_sources(
        self, league: str | None = None, type: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /api/v1/sources — active source records, optionally filtered."""
        params: dict[str, Any] = {}
        if league:
            params["league"] = league
        if type:
            params["type"] = type
        envelope = self._get("/api/v1/sources", params or None)
        return envelope.get("data", [])

    def get_recent_articles(self, limit: int = 20) -> list[dict[str, Any]]:
        """GET /api/v1/articles — most recent articles."""
        envelope = self._get("articles", {"limit": limit})
        return envelope.get("data", [])

    def get_articles(self, limit: int = 200, page: int = 1) -> dict[str, Any]:
        """GET /api/v1/articles — article envelope with pagination metadata."""
        return self._get("articles", {"limit": limit, "page": page})

    def get_article_by_source_url(self, source_url: str) -> dict[str, Any] | None:
        """GET /api/v1/articles?sourceUrl=... — find a published article by source URL."""
        envelope = self._get("articles", {"sourceUrl": source_url, "limit": 1})
        items = envelope.get("data", [])
        return items[0] if items else None

    def get_recent_videos(self, limit: int = 20) -> list[dict[str, Any]]:
        """GET /api/v1/videos — most recent videos."""
        envelope = self._get("videos", {"limit": limit})
        return envelope.get("data", [])

    def get_videos(self, limit: int = 200, page: int = 1) -> dict[str, Any]:
        """GET /api/v1/videos — video envelope with pagination metadata."""
        return self._get("videos", {"limit": limit, "page": page})

    # ── WRITE methods ─────────────────────────────────────────────────────

    def _normalize_match_upsert_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        """Align with app ingest: season + round, or same calendar day (not exact kickoff)."""
        payload = dict(data)
        round_val = payload.get("round") or payload.get("week")
        if round_val is not None and str(round_val).strip():
            payload["round"] = str(round_val).strip()
            payload.pop("week", None)
            return payload

        payload.pop("round", None)
        payload.pop("week", None)
        match_date = payload.get("matchDate")
        if not match_date:
            return payload

        try:
            raw = str(match_date).strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
            noon = datetime(
                parsed.year,
                parsed.month,
                parsed.day,
                12,
                0,
                0,
                tzinfo=timezone.utc,
            )
            payload["matchDate"] = noon.isoformat()
        except ValueError:
            logger.warning("Could not normalize matchDate for upsert: %s", match_date)
        return payload

    def upsert_match(self, data: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/ingest/match — idempotent match upsert."""
        payload = self._normalize_match_upsert_payload(data)
        logger.info("Upserting match: %s vs %s", payload.get("homeTeamId", "?"), payload.get("awayTeamId", "?"))
        return self._post("match", payload)

    def update_match(self, match_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """PATCH /api/v1/matches/[id] — protected partial match update."""
        logger.info("Updating match %s: %s", match_id, ", ".join(data.keys()))
        return self._patch(f"matches/{match_id}", data)

    def upsert_standing(self, data: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/ingest/standing — idempotent standing upsert."""
        logger.info("Upserting standing: team=%s league=%s", data.get("teamId", "?"), data.get("league", "?"))
        return self._post("standing", data)

    def create_article(self, data: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/ingest/article — create article (skips duplicates by URL)."""
        logger.info("Creating article: %s", data.get("title", "?")[:80])
        return self._post("article", data)

    def update_article(self, article_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """PATCH /api/v1/articles/[id] — protected partial article update."""
        logger.info("Updating article %s: %s", article_id, ", ".join(data.keys()))
        return self._patch(f"articles/{article_id}", data)

    def delete_article(self, article_id: str) -> dict[str, Any]:
        """DELETE /api/v1/articles/[id] — protected article cleanup."""
        logger.info("Deleting article %s", article_id)
        return self._delete(f"articles/{article_id}")

    def create_video(self, data: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/ingest/video — create video (skips duplicates by videoId)."""
        logger.info("Creating video: %s", data.get("title", "?")[:80])
        return self._post("video", data)

    def update_video(self, video_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """PATCH /api/v1/videos/[id] — protected partial video update."""
        logger.info("Updating video %s: %s", video_id, ", ".join(data.keys()))
        return self._patch(f"videos/{video_id}", data)

    def delete_video(self, video_id: str) -> dict[str, Any]:
        """DELETE /api/v1/videos/[id] — protected video cleanup."""
        logger.info("Deleting video %s", video_id)
        return self._delete(f"videos/{video_id}")

    def upsert_player(self, data: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/ingest/player — idempotent player upsert."""
        logger.info("Upserting player: %s", data.get("name", "?"))
        return self._post("player", data)

    def flag_conflict(self, data: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/ingest/conflict — flag a data conflict for admin review."""
        logger.info("Flagging conflict: %s.%s", data.get("model", "?"), data.get("field", "?"))
        return self._post("conflict", data)
