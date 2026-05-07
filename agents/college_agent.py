"""College Agent — CRAA D1A college rugby scores and standings.

Schedule: Every 2 hours.
Sources: collegiaterugby.com, craa.org, rugbyaffinitysports.com/craa.
Writes to: /api/v1/ingest/match, /api/v1/ingest/standing
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.scraper import ScraperError, fetch_html

logger = logging.getLogger(__name__)

GEMINI_REASONING = "gemini-2.5-flash"

CRAA_BASE_URLS = [
    "https://www.collegiaterugby.com",
    "https://craa.org",
    "https://rugbyaffinitysports.com/craa",
]
CRAA_SCORES_URLS = [
    f"{base}{path}"
    for base in CRAA_BASE_URLS
    for path in ("/scores", "/results", "/schedule", "")
]
CRAA_STANDINGS_URLS = [
    f"{base}{path}"
    for base in CRAA_BASE_URLS
    for path in ("/standings", "/table", "/league-table", "")
]

FETCH_DELAY = 1.0


def _current_season() -> str:
    return str(datetime.now(timezone.utc).year)


def _parse_date(text: str) -> str:
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


# ── Score parsing ─────────────────────────────────────────────────────────────


def _parse_scores_page(soup) -> list[dict[str, Any]]:
    """Extract match results from a CRAA scores page."""
    matches: list[dict[str, Any]] = []

    for card in soup.select(
        ".match-card, .score-card, .game-card, .result, "
        "[class*='match'], [class*='score'], [class*='game']"
    ):
        teams = card.select(".team-name, .team, [class*='team']")
        scores = card.select(".score, .points, [class*='score']")
        date_el = card.select_one(".date, time, [class*='date']")

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
                })

    if not matches:
        for row in soup.select("table tr"):
            cells = row.select("td")
            if len(cells) >= 4:
                home_name = cells[0].get_text(strip=True)
                home_score_text = re.sub(r"[^\d]", "", cells[1].get_text(strip=True))
                away_name = cells[2].get_text(strip=True)
                away_score_text = re.sub(r"[^\d]", "", cells[3].get_text(strip=True))

                if home_name and away_name and (home_score_text or away_score_text):
                    date_text = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    matches.append({
                        "home_name": home_name,
                        "away_name": away_name,
                        "home_score": int(home_score_text) if home_score_text else 0,
                        "away_score": int(away_score_text) if away_score_text else 0,
                        "date_text": date_text,
                    })

    logger.info("Parsed %d college match results", len(matches))
    return matches


# ── Standings parsing ─────────────────────────────────────────────────────────


def _parse_standings_page(soup) -> list[dict[str, Any]]:
    """Extract standings from a CRAA standings page."""
    standings: list[dict[str, Any]] = []

    for table in soup.select("table"):
        headers = [th.get_text(strip=True).lower() for th in table.select("thead th, th")]
        if not headers:
            continue

        col_map: dict[str, int] = {}
        for i, h in enumerate(headers):
            h = h.strip().lower()
            if "team" in h or h in ("club", "name", "school"):
                col_map["team"] = i
            elif h in ("pos", "position", "#", "rank"):
                col_map["position"] = i
            elif h in ("p", "pld", "played", "gp", "mp"):
                col_map["played"] = i
            elif h in ("w", "won", "wins"):
                col_map["won"] = i
            elif h in ("d", "drawn", "draws", "draw", "t"):
                col_map["drawn"] = i
            elif h in ("l", "lost", "losses", "loss"):
                col_map["lost"] = i
            elif h in ("pts", "points", "total"):
                col_map["points"] = i
            elif h in ("pf", "points for", "for"):
                col_map["points_for"] = i
            elif h in ("pa", "points against", "against"):
                col_map["points_against"] = i
            elif h in ("bp", "bonus", "bonus points"):
                col_map["bonus_points"] = i

        if "team" not in col_map:
            continue

        for row in table.select("tbody tr"):
            cells = row.select("td")
            if not cells or col_map["team"] >= len(cells):
                continue

            def cell_int(key: str, default: int = 0) -> int:
                idx = col_map.get(key)
                if idx is None or idx >= len(cells):
                    return default
                text = re.sub(r"[^\d]", "", cells[idx].get_text(strip=True))
                return int(text) if text else default

            team_name = cells[col_map["team"]].get_text(strip=True)
            if not team_name:
                continue

            standings.append({
                "team_name": team_name,
                "position": cell_int("position", len(standings) + 1),
                "played": cell_int("played"),
                "won": cell_int("won"),
                "drawn": cell_int("drawn"),
                "lost": cell_int("lost"),
                "points": cell_int("points"),
                "points_for": cell_int("points_for"),
                "points_against": cell_int("points_against"),
                "bonus_points": cell_int("bonus_points"),
            })

        if standings:
            break

    logger.info("Parsed %d college standing rows", len(standings))
    return standings


# ── Core logic ────────────────────────────────────────────────────────────────


def run_college_agent() -> dict[str, Any]:
    """Fetch CRAA college rugby scores and standings."""
    api = FullpitchAPI()
    season = _current_season()

    summary: dict[str, Any] = {
        "matches_found": 0,
        "added": 0,
        "skipped": 0,
        "standings_updated": 0,
        "errors": [],
    }

    # ── Scores ────────────────────────────────────────────────────────────

    parsed_matches: list[dict[str, Any]] = []
    scores_fetched = False

    for url in CRAA_SCORES_URLS:
        try:
            logger.info("Fetching college scores from %s", url)
            soup = fetch_html(url)
            parsed_matches = _parse_scores_page(soup)
            if parsed_matches:
                scores_fetched = True
                logger.info("College scores URL succeeded: %s", url)
                break
        except ScraperError as exc:
            logger.warning("Failed to fetch %s: %s", url, exc)
        time.sleep(FETCH_DELAY)

    if not scores_fetched and not parsed_matches:
        summary["errors"].append("Could not fetch scores from any CRAA URL")

    summary["matches_found"] = len(parsed_matches)

    existing_matches: list[dict[str, Any]] = []
    try:
        existing_matches = api.get_recent_matches(league="craa-d1a", limit=100)
    except FullpitchAPIError as exc:
        logger.warning("Failed to fetch existing college matches: %s", exc)

    existing_keys: dict[str, dict[str, Any]] = {}
    for m in existing_matches:
        home = m.get("homeTeamId", "")
        away = m.get("awayTeamId", "")
        date_str = m.get("kickoffTime", m.get("matchDate", ""))
        if isinstance(date_str, str):
            date_str = date_str[:10]
        existing_keys[f"{home}|{away}|{date_str}"] = m

    for parsed in parsed_matches:
        home_name = parsed["home_name"]
        away_name = parsed["away_name"]

        home_team = api.get_team(name=home_name)
        away_team = api.get_team(name=away_name)

        if not home_team:
            logger.warning("College team not in DB: '%s'", home_name)
            continue
        if not away_team:
            logger.warning("College team not in DB: '%s'", away_name)
            continue

        match_date = _parse_date(parsed["date_text"])
        dedup_key = _match_key(home_team["id"], away_team["id"], match_date)

        if dedup_key in existing_keys:
            summary["skipped"] += 1
            continue

        try:
            api.upsert_match({
                "homeTeamId": home_team["id"],
                "awayTeamId": away_team["id"],
                "homeScore": parsed["home_score"],
                "awayScore": parsed["away_score"],
                "matchDate": match_date,
                "league": "craa-d1a",
                "season": season,
                "status": "final",
                "agentName": "college-agent",
            })
            summary["added"] += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to upsert college match: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)

    # ── Standings ─────────────────────────────────────────────────────────

    time.sleep(FETCH_DELAY)

    parsed_standings: list[dict[str, Any]] = []
    standings_fetched = False
    for url in CRAA_STANDINGS_URLS:
        try:
            logger.info("Fetching college standings from %s", url)
            soup = fetch_html(url)
            parsed_standings = _parse_standings_page(soup)
            if parsed_standings:
                standings_fetched = True
                logger.info("College standings URL succeeded: %s", url)
                break
        except ScraperError as exc:
            logger.warning("Failed to fetch %s: %s", url, exc)
        time.sleep(FETCH_DELAY)

    if not standings_fetched and not parsed_standings:
        summary["errors"].append("Could not fetch standings from any CRAA URL")

    for row in parsed_standings:
        team_name = row["team_name"]
        team = api.get_team(name=team_name)
        if not team:
            logger.warning("Standings: college team not in DB: '%s'", team_name)
            continue

        try:
            api.upsert_standing({
                "teamId": team["id"],
                "league": "craa-d1a",
                "season": season,
                "position": row["position"],
                "points": row["points"],
                "played": row["played"],
                "won": row["won"],
                "drawn": row["drawn"],
                "lost": row["lost"],
                "pointsFor": row.get("points_for", 0),
                "pointsAgainst": row.get("points_against", 0),
                "bonusPoints": row.get("bonus_points", 0),
                "agentName": "college-agent",
            })
            summary["standings_updated"] += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to upsert college standing for {team_name}: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)

    logger.info(
        "College agent: matches=%d added=%d skipped=%d standings=%d errors=%d",
        summary["matches_found"], summary["added"], summary["skipped"],
        summary["standings_updated"], len(summary["errors"]),
    )
    return summary


def run() -> None:
    """Entry point called by main.py."""
    run_college_agent()
