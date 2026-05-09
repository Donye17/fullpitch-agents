"""WER Agent — Women's Elite Rugby scores, schedule, and standings.

Schedule: Hourly during WER season.
Sources: narugbydb.com WER fixtures/results and standings pages.
Writes to: /api/v1/ingest/match, /api/v1/ingest/standing
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from agents.narugbydb import (
    fetch_narugbydb_html,
    parse_fixtures_page,
    parse_standings_table,
    resolve_team,
)
from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.scraper import ScraperError

logger = logging.getLogger(__name__)

WER_STANDINGS_URL = "https://narugbydb.com/2026-wer-season-standings/"
WER_FIXTURES_URL = "https://narugbydb.com/calendar/2026-wer-fixtures-results/"
WER_SEASON = "2026"
WER_TEAM_NAMES = {
    "Bay Breakers",
    "Boston Banshees",
    "Chicago Tempest",
    "Denver Onyx",
    "New York Exiles",
    "TC Gemini",
}


def _current_season() -> str:
    now = datetime.now(timezone.utc)
    return WER_SEASON if now.year <= 2026 else str(now.year)


def _data_source_urls(api: FullpitchAPI) -> tuple[str, str]:
    sources = api.get_sources(league="wer", type="data")
    fixtures_url = next((s["url"] for s in sources if "fixture" in s.get("name", "").lower()), WER_FIXTURES_URL)
    standings_url = next((s["url"] for s in sources if "standing" in s.get("name", "").lower()), WER_STANDINGS_URL)
    return fixtures_url, standings_url


def _fetch_wer_fixtures(url: str) -> list[dict[str, Any]]:
    soup = fetch_narugbydb_html(url)
    matches = parse_fixtures_page(soup, allowed_team_names=WER_TEAM_NAMES)
    logger.info("Parsed %d WER fixtures/results from NA Rugby DB", len(matches))
    return matches


def _fetch_wer_standings(url: str) -> list[dict[str, Any]]:
    soup = fetch_narugbydb_html(url)
    standings = parse_standings_table(soup)
    logger.info("Parsed %d WER standings rows from NA Rugby DB", len(standings))
    return standings


def _ingest_matches(api: FullpitchAPI, season: str, matches: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    summary["matches_found"] = len(matches)

    for parsed in matches:
        home_name = parsed["home_name"]
        away_name = parsed["away_name"]
        if home_name not in WER_TEAM_NAMES or away_name not in WER_TEAM_NAMES:
            msg = f"Skipping invalid match: {home_name} vs {away_name} - team lookup failed"
            logger.warning(msg)
            summary["errors"].append(msg)
            continue

        home_team = resolve_team(api, home_name)
        away_team = resolve_team(api, away_name)
        home_team_id = home_team.get("id") if home_team else None
        away_team_id = away_team.get("id") if away_team else None

        logger.info(
            "Match: %s (id: %s) vs %s (id: %s)",
            home_name,
            home_team_id or "None",
            away_name,
            away_team_id or "None",
        )

        if not home_team_id or not away_team_id or home_team_id == away_team_id:
            msg = f"Skipping invalid match: {home_name} vs {away_name} - team lookup failed"
            logger.warning(msg)
            summary["errors"].append(msg)
            continue

        try:
            api.upsert_match(
                {
                    "homeTeamId": home_team_id,
                    "awayTeamId": away_team_id,
                    "homeScore": parsed["home_score"],
                    "awayScore": parsed["away_score"],
                    "matchDate": parsed["match_date"],
                    "league": "wer",
                    "season": season,
                    "status": parsed["status"],
                    "agentName": "wer-agent",
                    "region": "national",
                }
            )
            summary["matches_added"] += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to upsert WER match {home_name} vs {away_name}: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)


def _ingest_standings(api: FullpitchAPI, season: str, standings: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    for row in standings:
        team_name = row["team_name"]
        if team_name not in WER_TEAM_NAMES:
            msg = f"Skipping WER standing for non-WER team: '{team_name}'"
            logger.warning(msg)
            summary["errors"].append(msg)
            continue

        team = resolve_team(api, team_name)
        if not team:
            msg = f"WER standings team not in DB: '{team_name}'"
            logger.warning(msg)
            summary["errors"].append(msg)
            continue

        try:
            api.upsert_standing(
                {
                    "teamId": team["id"],
                    "league": "wer",
                    "season": season,
                    "position": row["position"],
                    "points": row["points"],
                    "played": row["played"],
                    "won": row["won"],
                    "drawn": row["drawn"],
                    "lost": row["lost"],
                    "pointsFor": row["points_for"],
                    "pointsAgainst": row["points_against"],
                    "bonusPoints": row["bonus_points"],
                    "agentName": "wer-agent",
                }
            )
            summary["standings_updated"] += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to upsert WER standing for {team_name}: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)


def run_wer_agent() -> dict[str, Any]:
    """Run the WER agent against NA Rugby DB."""
    api = FullpitchAPI()
    season = _current_season()
    fixtures_url, standings_url = _data_source_urls(api)
    summary: dict[str, Any] = {
        "matches_found": 0,
        "matches_added": 0,
        "standings_updated": 0,
        "errors": [],
    }

    try:
        _ingest_matches(api, season, _fetch_wer_fixtures(fixtures_url), summary)
    except ScraperError as exc:
        msg = f"Failed to fetch WER fixtures from NA Rugby DB: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)

    try:
        _ingest_standings(api, season, _fetch_wer_standings(standings_url), summary)
    except ScraperError as exc:
        msg = f"Failed to fetch WER standings from NA Rugby DB: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)

    logger.info(
        "WER agent summary: %d matches found, %d matches upserted, %d standings upserted, %d errors",
        summary["matches_found"],
        summary["matches_added"],
        summary["standings_updated"],
        len(summary["errors"]),
    )
    return summary


def run() -> None:
    """Entry point called by main.py."""
    run_wer_agent()
