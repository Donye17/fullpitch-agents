"""Match status helpers for league agents."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError

logger = logging.getLogger(__name__)


def _parse_match_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _home_name(match: dict[str, Any]) -> str:
    home = match.get("homeTeam") or {}
    return home.get("shortName") or home.get("name") or "Home"


def _away_name(match: dict[str, Any]) -> str:
    away = match.get("awayTeam") or {}
    return away.get("shortName") or away.get("name") or "Away"


def _live_loop_already_finalized(match: dict[str, Any]) -> bool:
    """Skip auto-finalization only when the match is already marked completed."""
    status = str(match.get("status") or "").lower()
    return status in {"completed", "final"}


def mark_past_matches_final(api: FullpitchAPI, league: str) -> int:
    """
    Any match where kickoff is more than 3 hours in the past and status is still
    scheduled/upcoming should be marked final (score may still be missing).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
    marked = 0

    try:
        matches = api.get_matches(league=league, status="upcoming", limit=200)
    except FullpitchAPIError as exc:
        logger.warning("mark_past_matches_final: failed to list %s matches: %s", league, exc)
        return 0

    for match in matches:
        if _live_loop_already_finalized(match):
            logger.info(
                "Skipping mark_past_matches_final for live-owned match: %s vs %s",
                _home_name(match),
                _away_name(match),
            )
            continue

        kickoff_raw = match.get("kickoffTime") or match.get("matchDate")
        kickoff = _parse_match_datetime(kickoff_raw)
        if not kickoff or kickoff >= cutoff:
            continue

        match_id = match.get("id")
        if not match_id:
            continue

        try:
            api.update_match(match_id, {"status": "final"})
            marked += 1
            logger.info(
                "Marked past match as final: %s vs %s %s",
                _home_name(match),
                _away_name(match),
                kickoff_raw,
            )
        except FullpitchAPIError as exc:
            logger.warning(
                "Failed to mark past match final (%s vs %s): %s",
                _home_name(match),
                _away_name(match),
                exc,
            )

    return marked
