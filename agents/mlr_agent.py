"""MLR Agent — Major League Rugby scores, schedule, and standings.

Schedule: Hourly, February through October (MLR season).
Sources: narugbydb.com MLR fixtures/results and standings pages.
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

MLR_STANDINGS_URL = "https://narugbydb.com/2026-season-standings/"
MLR_FIXTURES_URL = "https://narugbydb.com/calendar/2026-mlr-fixtures-results/"
MLR_SEASON = "2026"


def _current_season() -> str:
    now = datetime.now(timezone.utc)
    return MLR_SEASON if now.year <= 2026 else str(now.year)


def _fetch_mlr_fixtures() -> list[dict[str, Any]]:
    soup = fetch_narugbydb_html(MLR_FIXTURES_URL)
    matches = parse_fixtures_page(soup)
    logger.info("Parsed %d MLR fixtures/results from NA Rugby DB", len(matches))
    return matches


def _fetch_mlr_standings() -> list[dict[str, Any]]:
    soup = fetch_narugbydb_html(MLR_STANDINGS_URL)
    standings = parse_standings_table(soup)
    logger.info("Parsed %d MLR standings rows from NA Rugby DB", len(standings))
    return standings


def _ingest_matches(api: FullpitchAPI, season: str, matches: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    summary["matches_found"] = len(matches)

    for parsed in matches:
        home_name = parsed["home_name"]
        away_name = parsed["away_name"]
        home_team = resolve_team(api, home_name)
        away_team = resolve_team(api, away_name)

        if not home_team:
            msg = f"MLR home team not in DB: '{home_name}'"
            logger.warning(msg)
            summary["errors"].append(msg)
            continue
        if not away_team:
            msg = f"MLR away team not in DB: '{away_name}'"
            logger.warning(msg)
            summary["errors"].append(msg)
            continue

        try:
            api.upsert_match(
                {
                    "homeTeamId": home_team["id"],
                    "awayTeamId": away_team["id"],
                    "homeScore": parsed["home_score"],
                    "awayScore": parsed["away_score"],
                    "matchDate": parsed["match_date"],
                    "league": "mlr",
                    "season": season,
                    "status": parsed["status"],
                    "agentName": "mlr-agent",
                    "region": "national",
                }
            )
            summary["matches_added"] += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to upsert MLR match {home_name} vs {away_name}: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)


def _ingest_standings(api: FullpitchAPI, season: str, standings: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    for row in standings:
        team_name = row["team_name"]
        team = resolve_team(api, team_name)
        if not team:
            msg = f"MLR standings team not in DB: '{team_name}'"
            logger.warning(msg)
            summary["errors"].append(msg)
            continue

        try:
            api.upsert_standing(
                {
                    "teamId": team["id"],
                    "league": "mlr",
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
                    "agentName": "mlr-agent",
                }
            )
            summary["standings_updated"] += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to upsert MLR standing for {team_name}: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)


def run_mlr_agent() -> dict[str, Any]:
    """Run the MLR agent against NA Rugby DB."""
    api = FullpitchAPI()
    season = _current_season()
    summary: dict[str, Any] = {
        "matches_found": 0,
        "matches_added": 0,
        "standings_updated": 0,
        "errors": [],
    }

    try:
        _ingest_matches(api, season, _fetch_mlr_fixtures(), summary)
    except ScraperError as exc:
        msg = f"Failed to fetch MLR fixtures from NA Rugby DB: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)

    try:
        _ingest_standings(api, season, _fetch_mlr_standings(), summary)
    except ScraperError as exc:
        msg = f"Failed to fetch MLR standings from NA Rugby DB: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)

    logger.info(
        "MLR agent summary: %d matches found, %d matches upserted, %d standings upserted, %d errors",
        summary["matches_found"],
        summary["matches_added"],
        summary["standings_updated"],
        len(summary["errors"]),
    )
    return summary


def run() -> None:
    """Entry point called by main.py."""
    run_mlr_agent()
