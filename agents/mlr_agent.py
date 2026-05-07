"""MLR Agent — Major League Rugby scores, standings, and player stats.

Schedule: Hourly, February through October (MLR season).
Sources: mlrugby.com/scores, mlrugby.com/standings.
Writes to: /api/v1/ingest/match, /api/v1/ingest/standing
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.scraper import ScraperError, fetch_html

logger = logging.getLogger(__name__)

GEMINI_REASONING = "gemini-2.5-flash-lite"
GEMINI_WRITING_PRO = "gemini-2.5-pro"

MLR_SCORES_URL = "https://www.mlrugby.com/scores"
MLR_STANDINGS_URL = "https://www.mlrugby.com/standings"

KNOWN_MLR_TEAMS = {
    "chicago hounds",
    "dallas jackals",
    "houston sabercats",
    "miami sharks",
    "new england free jacks",
    "new orleans gold",
    "nola gold",
    "old glory dc",
    "portland breakers",
    "rugby atl",
    "rugby fc la",
    "san diego legion",
    "seattle seawolves",
    "utah warriors",
    "anthem rc",
    "rugby new york",
    "st. louis stars",
    "louisville rugby",
}

_gemini_verified_cache: dict[str, bool] = {}


def _normalize_team_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip())


def _is_known_mlr_team(name: str) -> bool:
    return name.lower().strip() in KNOWN_MLR_TEAMS


def _verify_team_with_gemini(name: str) -> bool:
    """Use Gemini to verify if a name is a valid MLR team."""
    if name.lower().strip() in _gemini_verified_cache:
        return _gemini_verified_cache[name.lower().strip()]

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("GOOGLE_API_KEY not set — skipping Gemini team verification for '%s'", name)
        return False

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_REASONING,
            contents=f"Is '{name}' a valid MLR Major League Rugby team name? Answer YES or NO only.",
        )
        answer = response.text.strip().upper()
        is_valid = answer.startswith("YES")
        _gemini_verified_cache[name.lower().strip()] = is_valid
        logger.info("Gemini team check: '%s' → %s", name, "YES" if is_valid else "NO")
        return is_valid
    except Exception:
        logger.exception("Gemini verification failed for '%s'", name)
        return False


def _current_season() -> str:
    now = datetime.now(timezone.utc)
    return str(now.year)


def _match_key(m: dict[str, Any]) -> str:
    """Build a dedup key from home team + away team + date (YYYY-MM-DD)."""
    home = m.get("homeTeamId", "")
    away = m.get("awayTeamId", "")
    date_str = m.get("kickoffTime", m.get("matchDate", ""))
    if isinstance(date_str, str):
        date_str = date_str[:10]
    return f"{home}|{away}|{date_str}"


# ── HTML Parsers ──────────────────────────────────────────────────────────────
# MLR's website may use JS rendering so these parsers are best-effort.
# Adjust selectors once real HTML structure is inspected.


def _parse_scores_page(soup) -> list[dict[str, Any]]:
    """Extract match data from the MLR scores page."""
    matches: list[dict[str, Any]] = []

    for card in soup.select(".match-card, .score-card, .game-card, [class*='match'], [class*='score']"):
        teams = card.select(".team-name, .team, [class*='team-name']")
        scores = card.select(".score, .points, [class*='score']")
        date_el = card.select_one(".date, .match-date, time, [class*='date']")

        if len(teams) >= 2 and len(scores) >= 2:
            home_name = _normalize_team_name(teams[0].get_text(strip=True))
            away_name = _normalize_team_name(teams[1].get_text(strip=True))
            home_score_text = re.sub(r"[^\d]", "", scores[0].get_text(strip=True))
            away_score_text = re.sub(r"[^\d]", "", scores[1].get_text(strip=True))
            date_text = date_el.get_text(strip=True) if date_el else ""

            if home_name and away_name and home_score_text and away_score_text:
                matches.append({
                    "home_name": home_name,
                    "away_name": away_name,
                    "home_score": int(home_score_text),
                    "away_score": int(away_score_text),
                    "date_text": date_text,
                    "status": "final",
                })

    if not matches:
        rows = soup.select("table tr")
        for row in rows:
            cells = row.select("td")
            if len(cells) >= 4:
                home_name = _normalize_team_name(cells[0].get_text(strip=True))
                home_score_text = re.sub(r"[^\d]", "", cells[1].get_text(strip=True))
                away_name = _normalize_team_name(cells[2].get_text(strip=True))
                away_score_text = re.sub(r"[^\d]", "", cells[3].get_text(strip=True))

                if home_name and away_name and home_score_text and away_score_text:
                    date_text = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    matches.append({
                        "home_name": home_name,
                        "away_name": away_name,
                        "home_score": int(home_score_text),
                        "away_score": int(away_score_text),
                        "date_text": date_text,
                        "status": "final",
                    })

    logger.info("Parsed %d matches from scores page", len(matches))
    return matches


def _parse_standings_page(soup) -> list[dict[str, Any]]:
    """Extract standings data from the MLR standings page."""
    standings: list[dict[str, Any]] = []

    for table in soup.select("table"):
        headers = [th.get_text(strip=True).lower() for th in table.select("thead th, th")]
        if not headers:
            continue

        col_map: dict[str, int] = {}
        for i, h in enumerate(headers):
            h_lower = h.lower().strip()
            if "team" in h_lower or h_lower in ("club", "name"):
                col_map["team"] = i
            elif h_lower in ("pos", "position", "#", "rank"):
                col_map["position"] = i
            elif h_lower in ("p", "pld", "played", "gp", "mp"):
                col_map["played"] = i
            elif h_lower in ("w", "won", "wins"):
                col_map["won"] = i
            elif h_lower in ("d", "drawn", "draws", "draw"):
                col_map["drawn"] = i
            elif h_lower in ("l", "lost", "losses", "loss"):
                col_map["lost"] = i
            elif h_lower in ("pts", "points", "total"):
                col_map["points"] = i
            elif h_lower in ("pf", "points for", "for"):
                col_map["points_for"] = i
            elif h_lower in ("pa", "points against", "against"):
                col_map["points_against"] = i
            elif h_lower in ("bp", "bonus", "bonus points"):
                col_map["bonus_points"] = i

        if "team" not in col_map:
            continue

        for row in table.select("tbody tr"):
            cells = row.select("td")
            if len(cells) <= col_map.get("team", 0):
                continue

            def cell_int(key: str, default: int = 0) -> int:
                idx = col_map.get(key)
                if idx is None or idx >= len(cells):
                    return default
                text = re.sub(r"[^\d]", "", cells[idx].get_text(strip=True))
                return int(text) if text else default

            team_name = _normalize_team_name(cells[col_map["team"]].get_text(strip=True))
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

    logger.info("Parsed %d standing rows from standings page", len(standings))
    return standings


def _parse_date(text: str) -> str:
    """Best-effort date parsing. Returns ISO string or today."""
    text = text.strip()
    if not text:
        return datetime.now(timezone.utc).isoformat()

    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue

    return datetime.now(timezone.utc).isoformat()


# ── Core Logic ────────────────────────────────────────────────────────────────


def run_mlr_agent() -> dict[str, Any]:
    """Run the MLR agent: fetch scores + standings, write to Fullpitch API."""
    api = FullpitchAPI()
    season = _current_season()
    summary: dict[str, Any] = {
        "matches_found": 0,
        "matches_added": 0,
        "matches_skipped": 0,
        "conflicts_flagged": 0,
        "standings_updated": 0,
        "errors": [],
    }

    # ── Step 1–3: Scores ──────────────────────────────────────────────────

    try:
        logger.info("Fetching MLR scores from %s", MLR_SCORES_URL)
        scores_soup = fetch_html(MLR_SCORES_URL)
        parsed_matches = _parse_scores_page(scores_soup)
        summary["matches_found"] = len(parsed_matches)
    except ScraperError as exc:
        msg = f"Failed to fetch scores page: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)
        parsed_matches = []

    existing_matches = []
    try:
        existing_matches = api.get_recent_matches(league="mlr", limit=100)
    except FullpitchAPIError as exc:
        logger.warning("Failed to fetch existing matches: %s", exc)

    existing_keys: dict[str, dict[str, Any]] = {}
    for m in existing_matches:
        key = _match_key(m)
        existing_keys[key] = m

    for parsed in parsed_matches:
        home_name = parsed["home_name"]
        away_name = parsed["away_name"]

        if not _is_known_mlr_team(home_name):
            if not _verify_team_with_gemini(home_name):
                logger.warning("Skipping unknown home team: '%s'", home_name)
                continue
        if not _is_known_mlr_team(away_name):
            if not _verify_team_with_gemini(away_name):
                logger.warning("Skipping unknown away team: '%s'", away_name)
                continue

        home_team = api.get_team(name=home_name)
        away_team = api.get_team(name=away_name)

        if not home_team:
            msg = f"Home team not in DB: '{home_name}'"
            logger.warning(msg)
            summary["errors"].append(msg)
            continue
        if not away_team:
            msg = f"Away team not in DB: '{away_name}'"
            logger.warning(msg)
            summary["errors"].append(msg)
            continue

        match_date = _parse_date(parsed["date_text"])
        dedup_key = f"{home_team['id']}|{away_team['id']}|{match_date[:10]}"

        if dedup_key in existing_keys:
            existing = existing_keys[dedup_key]
            ex_home = existing.get("homeScore")
            ex_away = existing.get("awayScore")
            if ex_home == parsed["home_score"] and ex_away == parsed["away_score"]:
                logger.info("Already have %s vs %s on %s — skipping", home_name, away_name, match_date[:10])
                summary["matches_skipped"] += 1
                continue
            else:
                logger.info(
                    "Score conflict for %s vs %s: DB=%s-%s, parsed=%d-%d",
                    home_name, away_name, ex_home, ex_away,
                    parsed["home_score"], parsed["away_score"],
                )
                try:
                    api.flag_conflict({
                        "model": "Match",
                        "recordId": existing.get("id", ""),
                        "field": "homeScore/awayScore",
                        "existingValue": {"homeScore": ex_home, "awayScore": ex_away},
                        "newValue": {"homeScore": parsed["home_score"], "awayScore": parsed["away_score"]},
                        "agentName": "mlr-agent",
                    })
                    summary["conflicts_flagged"] += 1
                except FullpitchAPIError as exc:
                    logger.error("Failed to flag conflict: %s", exc)
                    summary["errors"].append(str(exc))
                continue

        try:
            api.upsert_match({
                "homeTeamId": home_team["id"],
                "awayTeamId": away_team["id"],
                "homeScore": parsed["home_score"],
                "awayScore": parsed["away_score"],
                "matchDate": match_date,
                "league": "mlr",
                "season": season,
                "status": parsed.get("status", "final"),
                "agentName": "mlr-agent",
            })
            summary["matches_added"] += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to upsert match {home_name} vs {away_name}: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)

    # ── Step 4–6: Standings ───────────────────────────────────────────────

    try:
        logger.info("Fetching MLR standings from %s", MLR_STANDINGS_URL)
        standings_soup = fetch_html(MLR_STANDINGS_URL)
        parsed_standings = _parse_standings_page(standings_soup)
    except ScraperError as exc:
        msg = f"Failed to fetch standings page: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)
        parsed_standings = []

    for row in parsed_standings:
        team_name = row["team_name"]

        if not _is_known_mlr_team(team_name):
            if not _verify_team_with_gemini(team_name):
                logger.warning("Standings: skipping unknown team '%s'", team_name)
                continue

        team = api.get_team(name=team_name)
        if not team:
            logger.warning("Standings: team not in DB: '%s'", team_name)
            continue

        try:
            api.upsert_standing({
                "teamId": team["id"],
                "league": "mlr",
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
                "agentName": "mlr-agent",
            })
            summary["standings_updated"] += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to upsert standing for {team_name}: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)

    # ── Summary ───────────────────────────────────────────────────────────

    logger.info(
        "MLR agent summary: %d found, %d added, %d skipped, %d conflicts, %d standings, %d errors",
        summary["matches_found"],
        summary["matches_added"],
        summary["matches_skipped"],
        summary["conflicts_flagged"],
        summary["standings_updated"],
        len(summary["errors"]),
    )
    return summary


def run() -> None:
    """Entry point called by main.py."""
    run_mlr_agent()
