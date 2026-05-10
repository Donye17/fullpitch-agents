"""Eagles Agent — USA Eagles national team results.

Schedule: Daily at 7am UTC.
Sources: usa.rugby/eagles/results.
Writes to: /api/v1/ingest/match
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.gemini_relevance import GEMINI_FREE_TIER_MODEL
from tools.scraper import ScraperError, fetch_html

logger = logging.getLogger(__name__)

GEMINI_REASONING = GEMINI_FREE_TIER_MODEL

EAGLES_RESULTS_URL = "https://www.usa.rugby/eagles/results"
EAGLES_SCHEDULE_URL = "https://www.usa.rugby/eagles/schedule"

USA_TEAM_NAMES = {"usa", "usa eagles", "united states", "usa men", "usa women"}


def _current_season() -> str:
    return str(datetime.now(timezone.utc).year)


def _parse_date(text: str) -> str:
    """Best-effort date parsing, returns ISO string."""
    text = text.strip()
    if not text:
        return datetime.now(timezone.utc).isoformat()

    for fmt in (
        "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d",
        "%d %B %Y", "%d %b %Y", "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return datetime.now(timezone.utc).isoformat()


def _match_key(home_id: str, away_id: str, date_str: str) -> str:
    return f"{home_id}|{away_id}|{date_str[:10]}"


def _parse_results_page(soup) -> list[dict[str, Any]]:
    """Extract match results from the USA Rugby results page."""
    matches: list[dict[str, Any]] = []

    for card in soup.select(
        ".match-card, .result-card, .game-card, .score-card, "
        "[class*='match'], [class*='result'], [class*='score']"
    ):
        teams = card.select(".team-name, .team, [class*='team-name']")
        scores = card.select(".score, .points, [class*='score']")
        date_el = card.select_one(".date, .match-date, time, [class*='date']")
        venue_el = card.select_one(".venue, .location, [class*='venue']")
        status_el = card.select_one(".status, [class*='status']")

        if len(teams) >= 2 and len(scores) >= 2:
            home_name = teams[0].get_text(strip=True)
            away_name = teams[1].get_text(strip=True)
            home_score_text = re.sub(r"[^\d]", "", scores[0].get_text(strip=True))
            away_score_text = re.sub(r"[^\d]", "", scores[1].get_text(strip=True))

            if home_name and away_name:
                matches.append({
                    "home_name": home_name,
                    "away_name": away_name,
                    "home_score": int(home_score_text) if home_score_text else 0,
                    "away_score": int(away_score_text) if away_score_text else 0,
                    "date_text": (date_el.get("datetime", "") or date_el.get_text(strip=True)) if date_el else "",
                    "venue": venue_el.get_text(strip=True) if venue_el else "",
                    "status": status_el.get_text(strip=True).lower() if status_el else "final",
                })

    if not matches:
        for row in soup.select("table tr"):
            cells = row.select("td")
            if len(cells) >= 4:
                home_name = cells[0].get_text(strip=True)
                home_score_text = re.sub(r"[^\d]", "", cells[1].get_text(strip=True))
                away_name = cells[2].get_text(strip=True)
                away_score_text = re.sub(r"[^\d]", "", cells[3].get_text(strip=True))

                if home_name and away_name:
                    date_text = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    matches.append({
                        "home_name": home_name,
                        "away_name": away_name,
                        "home_score": int(home_score_text) if home_score_text else 0,
                        "away_score": int(away_score_text) if away_score_text else 0,
                        "date_text": date_text,
                        "venue": "",
                        "status": "final",
                    })

    logger.info("Parsed %d Eagles results", len(matches))
    return matches


def _determine_status(raw: str) -> str:
    raw = raw.lower()
    if "upcoming" in raw or "scheduled" in raw or "tbd" in raw:
        return "scheduled"
    if "live" in raw or "in progress" in raw:
        return "live"
    return "final"


def run_eagles_agent() -> dict[str, Any]:
    """Fetch USA Eagles results and upsert to Fullpitch API."""
    api = FullpitchAPI()
    season = _current_season()

    summary: dict[str, Any] = {
        "found": 0,
        "added": 0,
        "skipped": 0,
        "conflicts": 0,
        "errors": [],
    }

    try:
        logger.info("Fetching Eagles results from %s", EAGLES_RESULTS_URL)
        soup = fetch_html(EAGLES_RESULTS_URL)
        parsed = _parse_results_page(soup)
        summary["found"] = len(parsed)
    except ScraperError as exc:
        msg = f"Failed to fetch Eagles results: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)
        parsed = []

    existing_matches: list[dict[str, Any]] = []
    try:
        existing_matches = api.get_recent_matches(league="eagles", limit=100)
    except FullpitchAPIError as exc:
        logger.warning("Failed to fetch existing Eagles matches: %s", exc)

    existing_keys: dict[str, dict[str, Any]] = {}
    for m in existing_matches:
        home = m.get("homeTeamId", "")
        away = m.get("awayTeamId", "")
        date_str = m.get("kickoffTime", m.get("matchDate", ""))[:10] if m.get("kickoffTime") or m.get("matchDate") else ""
        existing_keys[f"{home}|{away}|{date_str}"] = m

    for parsed_match in parsed:
        home_name = parsed_match["home_name"]
        away_name = parsed_match["away_name"]

        home_team = api.get_team(name=home_name)
        away_team = api.get_team(name=away_name)

        if not home_team:
            logger.warning("Home team not in DB: '%s'", home_name)
            summary["errors"].append(f"Team not in DB: {home_name}")
            continue
        if not away_team:
            logger.warning("Away team not in DB: '%s'", away_name)
            summary["errors"].append(f"Team not in DB: {away_name}")
            continue

        match_date = _parse_date(parsed_match["date_text"])
        dedup_key = _match_key(home_team["id"], away_team["id"], match_date)
        status = _determine_status(parsed_match.get("status", "final"))

        if dedup_key in existing_keys:
            existing = existing_keys[dedup_key]
            ex_home = existing.get("homeScore")
            ex_away = existing.get("awayScore")
            if ex_home == parsed_match["home_score"] and ex_away == parsed_match["away_score"]:
                logger.info("Already have %s vs %s — skipping", home_name, away_name)
                summary["skipped"] += 1
                continue
            else:
                try:
                    api.flag_conflict({
                        "model": "Match",
                        "recordId": existing.get("id", ""),
                        "field": "homeScore/awayScore",
                        "existingValue": {"homeScore": ex_home, "awayScore": ex_away},
                        "newValue": {"homeScore": parsed_match["home_score"], "awayScore": parsed_match["away_score"]},
                        "agentName": "eagles-agent",
                    })
                    summary["conflicts"] += 1
                except FullpitchAPIError as exc:
                    summary["errors"].append(str(exc))
                continue

        try:
            api.upsert_match({
                "homeTeamId": home_team["id"],
                "awayTeamId": away_team["id"],
                "homeScore": parsed_match["home_score"],
                "awayScore": parsed_match["away_score"],
                "matchDate": match_date,
                "league": "eagles",
                "season": season,
                "status": status,
                "agentName": "eagles-agent",
            })
            summary["added"] += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to upsert Eagles match: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)

    logger.info(
        "Eagles agent: found=%d added=%d skipped=%d conflicts=%d errors=%d",
        summary["found"], summary["added"], summary["skipped"],
        summary["conflicts"], len(summary["errors"]),
    )
    return summary


def run() -> None:
    """Entry point called by main.py."""
    run_eagles_agent()
