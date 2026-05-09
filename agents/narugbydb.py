"""NA Rugby DB parsing helpers for MLR and WER agents."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from tools.fullpitch_api import FullpitchAPI
from tools.scraper import fetch_html

logger = logging.getLogger(__name__)

NARUGBYDB_DELAY_SECONDS = 1.0
EASTERN = ZoneInfo("America/New_York")

TEAM_ALIASES = {
    "anthem": "Anthem RC",
    "anthem rc": "Anthem RC",
    "banshees": "Boston Banshees",
    "bay breakers": "Bay Breakers",
    "boston banshees": "Boston Banshees",
    "breakers": "Bay Breakers",
    "california legion": "California Legion",
    "chicago hounds": "Chicago Hounds",
    "chicago tempest": "Chicago Tempest",
    "denver onyx": "Denver Onyx",
    "exiles": "New York Exiles",
    "free jacks": "New England Free Jacks",
    "gemini": "TC Gemini",
    "hounds": "Chicago Hounds",
    "legion": "California Legion",
    "new england free jacks": "New England Free Jacks",
    "new york exiles": "New York Exiles",
    "old glory": "Old Glory DC",
    "old glory dc": "Old Glory DC",
    "onyx": "Denver Onyx",
    "seattle seawolves": "Seattle Seawolves",
    "seawolves": "Seattle Seawolves",
    "tc gemini": "TC Gemini",
    "tempest": "Chicago Tempest",
    "twin cities gemini": "TC Gemini",
}

TEAM_SLUG_ALIASES = {
    "anthem-rc": "Anthem RC",
    "bay-breakers": "Bay Breakers",
    "boston-banshees": "Boston Banshees",
    "california-legion": "California Legion",
    "chicago-hounds": "Chicago Hounds",
    "chicago-tempest": "Chicago Tempest",
    "denver-onyx": "Denver Onyx",
    "new-england-free-jacks": "New England Free Jacks",
    "new-york-exiles": "New York Exiles",
    "old-glory-dc": "Old Glory DC",
    "seattle-seawolves": "Seattle Seawolves",
    "twin-cities-gemini": "TC Gemini",
}


def fetch_narugbydb_html(url: str) -> BeautifulSoup:
    """Fetch an NA Rugby DB page and leave a polite post-request delay."""
    soup = fetch_html(url)
    time.sleep(NARUGBYDB_DELAY_SECONDS)
    return soup


def normalize_team_name(name: str) -> str:
    compact = re.sub(r"\s+", " ", name.replace("\xa0", " ").strip())
    return TEAM_ALIASES.get(compact.lower(), compact)


def _team_name_from_url(url: str) -> str | None:
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    if len(path_parts) < 2 or path_parts[-2] != "team":
        return None

    slug = path_parts[-1].lower()
    if slug in TEAM_SLUG_ALIASES:
        return TEAM_SLUG_ALIASES[slug]

    return normalize_team_name(slug.replace("-", " ").title())


def resolve_team(api: FullpitchAPI, name: str) -> dict[str, Any] | None:
    """Match a scraped team name to a Fullpitch Team record by canonical aliases."""
    canonical = normalize_team_name(name)
    candidates = [canonical]
    if canonical != name:
        candidates.append(name)

    for candidate in candidates:
        team = api.get_team(name=candidate)
        if team:
            return team

    return None


def _int_cell(text: str, default: int = 0) -> int:
    match = re.search(r"-?\d+", text.replace(",", ""))
    return int(match.group(0)) if match else default


def _header_key(header: str) -> str | None:
    value = header.lower().strip()
    if value in {"pos", "pos.", "position", "#", "rank"}:
        return "position"
    if value in {"team", "club", "name"}:
        return "team"
    if value in {"p", "pld", "played", "gp", "mp"}:
        return "played"
    if value in {"w", "won", "wins"}:
        return "won"
    if value in {"l", "lost", "losses"}:
        return "lost"
    if value in {"d", "drawn", "draw", "draws"}:
        return "drawn"
    if value == "pf":
        return "points_for"
    if value == "pa":
        return "points_against"
    if value in {"pd", "+/-", "diff", "points difference"}:
        return "points_difference"
    if value in {"tb", "try bonus"}:
        return "try_bonus"
    if value in {"lb", "losing bonus"}:
        return "losing_bonus"
    if value in {"pts", "points", "total"}:
        return "points"
    return None


def parse_standings_table(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Parse the NA Rugby DB standings table."""
    standings: list[dict[str, Any]] = []

    for table in soup.select("table"):
        headers = [cell.get_text(" ", strip=True) for cell in table.select("tr:first-child th, tr:first-child td")]
        col_map = {
            key: index
            for index, header in enumerate(headers)
            if (key := _header_key(header)) is not None
        }
        required = {"position", "team", "played", "won", "lost", "drawn", "points_difference", "points"}
        if not required.issubset(col_map):
            continue

        for row in table.select("tr")[1:]:
            cells = row.select("td")
            if len(cells) <= max(col_map.values()):
                continue

            team_text = cells[col_map["team"]].get_text(" ", strip=True)
            team_name = normalize_team_name(team_text)
            if not team_name:
                continue

            try_bonus = _int_cell(cells[col_map["try_bonus"]].get_text(" ", strip=True)) if "try_bonus" in col_map else 0
            losing_bonus = _int_cell(cells[col_map["losing_bonus"]].get_text(" ", strip=True)) if "losing_bonus" in col_map else 0

            standings.append(
                {
                    "team_name": team_name,
                    "position": _int_cell(cells[col_map["position"]].get_text(" ", strip=True), len(standings) + 1),
                    "played": _int_cell(cells[col_map["played"]].get_text(" ", strip=True)),
                    "won": _int_cell(cells[col_map["won"]].get_text(" ", strip=True)),
                    "lost": _int_cell(cells[col_map["lost"]].get_text(" ", strip=True)),
                    "drawn": _int_cell(cells[col_map["drawn"]].get_text(" ", strip=True)),
                    "points_difference": _int_cell(cells[col_map["points_difference"]].get_text(" ", strip=True)),
                    "points": _int_cell(cells[col_map["points"]].get_text(" ", strip=True)),
                    "points_for": _int_cell(cells[col_map["points_for"]].get_text(" ", strip=True)) if "points_for" in col_map else 0,
                    "points_against": _int_cell(cells[col_map["points_against"]].get_text(" ", strip=True)) if "points_against" in col_map else 0,
                    "bonus_points": try_bonus + losing_bonus,
                }
            )

        if standings:
            break

    return standings


def _parse_event_title(title: str) -> tuple[str, str] | None:
    if ":" not in title:
        return None

    teams_part = title.split(":", 1)[1].strip()
    if " v " not in teams_part:
        return None

    home_name, away_name = teams_part.split(" v ", 1)
    return normalize_team_name(home_name), normalize_team_name(away_name)


def _parse_event_teams(row) -> tuple[str, str] | None:
    """Extract the left/right team links from an NA Rugby DB event row."""
    team_links = row.select('a[href*="/team/"]')
    if len(team_links) >= 2:
        home_name = _team_name_from_url(team_links[0].get("href", ""))
        away_name = _team_name_from_url(team_links[1].get("href", ""))
        if home_name and away_name:
            return home_name, away_name

    title_el = row.select_one(".sp-event-title")
    if not title_el:
        return None
    return _parse_event_title(title_el.get_text(" ", strip=True))


def _parse_match_datetime(date_text: str, result_text: str) -> str:
    date_text = date_text.strip()
    time_text = result_text.strip() if re.search(r"\b(am|pm)\b", result_text, flags=re.IGNORECASE) else ""
    raw = f"{date_text} {time_text}".strip()

    for fmt in ("%B %d, %Y %I:%M %p", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(raw, fmt).replace(tzinfo=EASTERN)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue

    logger.warning("Could not parse NA Rugby DB date '%s'", raw)
    return datetime.now(timezone.utc).isoformat()


def parse_fixtures_page(
    soup: BeautifulSoup,
    allowed_team_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Parse NA Rugby DB fixture/result rows."""
    allowed_lookup = {normalize_team_name(name).lower() for name in allowed_team_names or set()}

    for table in soup.select("table"):
        matches: list[dict[str, Any]] = []

        for row in table.select("tr"):
            date_el = row.select_one("time.sp-event-date")
            result_el = row.select_one(".sp-event-results")
            title_el = row.select_one(".sp-event-title")
            if not date_el or not result_el or not title_el:
                continue

            teams = _parse_event_teams(row)
            if teams is None:
                continue
            home_name, away_name = teams
            if allowed_lookup and (
                home_name.lower() not in allowed_lookup or away_name.lower() not in allowed_lookup
            ):
                logger.debug(
                    "Skipping NA Rugby DB row outside allowed teams: %s vs %s",
                    home_name,
                    away_name,
                )
                continue

            result_text = result_el.get_text(" ", strip=True)
            score_match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", result_text)
            status = "final" if score_match else "scheduled"

            matches.append(
                {
                    "home_name": home_name,
                    "away_name": away_name,
                    "home_score": int(score_match.group(1)) if score_match else 0,
                    "away_score": int(score_match.group(2)) if score_match else 0,
                    "match_date": _parse_match_datetime(date_el.get_text(" ", strip=True), result_text),
                    "status": status,
                }
            )

        if matches:
            return matches

    return []
