"""MLR Agent — Major League Rugby scores, schedule, and standings.

Schedule: Hourly, February through October (MLR season).
Sources: narugbydb.com MLR fixtures/results and standings pages.
Writes to: /api/v1/ingest/match, /api/v1/ingest/standing
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from agents.narugbydb import (
    fetch_narugbydb_html,
    parse_fixtures_page,
    parse_standings_table,
    resolve_team,
)
from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.scraper import ScraperError, fetch_text

logger = logging.getLogger(__name__)

MLR_STANDINGS_URL = "https://narugbydb.com/2026-season-standings/"
MLR_FIXTURES_URL = "https://narugbydb.com/calendar/2026-mlr-fixtures-results/"
MLR_MATCH_BASE_URL = "https://www.majorleague.rugby/matches"
MLR_SEASON = "2026"
KNOWN_MLR_MATCH_URLS = {
    ("new england free jacks", "california legion"):
        f"{MLR_MATCH_BASE_URL}/season-2026-major-league-rugby-7-new-england-free-jacks-vs-california-legion",
}
KNOWN_WEEK_ANCHOR_DATE = date(2026, 5, 9)
KNOWN_WEEK_ANCHOR_NUMBER = 7


def _current_season() -> str:
    now = datetime.now(timezone.utc)
    return MLR_SEASON if now.year <= 2026 else str(now.year)


def _data_source_urls(api: FullpitchAPI) -> tuple[str, str]:
    sources = api.get_sources(league="mlr", type="data")
    fixtures_url = next((s["url"] for s in sources if "fixture" in s.get("name", "").lower()), MLR_FIXTURES_URL)
    standings_url = next((s["url"] for s in sources if "standing" in s.get("name", "").lower()), MLR_STANDINGS_URL)
    return fixtures_url, standings_url


def _fetch_mlr_fixtures(url: str) -> list[dict[str, Any]]:
    soup = fetch_narugbydb_html(url)
    matches = parse_fixtures_page(soup)
    logger.info("Parsed %d MLR fixtures/results from NA Rugby DB", len(matches))
    return matches


def _fetch_mlr_standings(url: str) -> list[dict[str, Any]]:
    soup = fetch_narugbydb_html(url)
    standings = parse_standings_table(soup)
    logger.info("Parsed %d MLR standings rows from NA Rugby DB", len(standings))
    return standings


def _lookup_key(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()


def _slugify(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _lookup_key(value)).strip("-")


def _parse_match_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _match_team_name(match: dict[str, Any], side: str) -> str:
    team = match.get(f"{side}Team") or {}
    return team.get("name") or team.get("shortName") or team.get("abbreviation") or ""


def _match_team_slug(match: dict[str, Any], side: str) -> str:
    team = match.get(f"{side}Team") or {}
    return team.get("slug") or _slugify(team.get("name") or team.get("shortName") or team.get("abbreviation"))


def _match_team_abbr(match: dict[str, Any], side: str) -> str:
    team = match.get(f"{side}Team") or {}
    value = team.get("abbreviation") or team.get("shortName") or team.get("name") or ""
    if len(value) <= 4:
        return value.upper()
    return "".join(word[0] for word in re.findall(r"[A-Za-z]+", value)[:3]).upper()


def _week_from_match(match: dict[str, Any]) -> int | None:
    raw_round = str(match.get("round") or "")
    round_match = re.search(r"\b(?:week|round)?\s*(\d{1,2})\b", raw_round, flags=re.IGNORECASE)
    if round_match:
        return int(round_match.group(1))

    kickoff = _parse_match_datetime(match.get("kickoffTime"))
    if not kickoff:
        return None

    delta_days = (kickoff.date() - KNOWN_WEEK_ANCHOR_DATE).days
    week = KNOWN_WEEK_ANCHOR_NUMBER + round(delta_days / 7)
    return week if week > 0 else None


def _build_match_page_url(match: dict[str, Any]) -> str | None:
    home_name = _match_team_name(match, "home")
    away_name = _match_team_name(match, "away")
    known_url = KNOWN_MLR_MATCH_URLS.get((_lookup_key(home_name), _lookup_key(away_name)))
    if known_url:
        return known_url

    week = _week_from_match(match)
    home_slug = _match_team_slug(match, "home")
    away_slug = _match_team_slug(match, "away")
    if not week or not home_slug or not away_slug:
        return None

    return f"{MLR_MATCH_BASE_URL}/season-2026-major-league-rugby-{week}-{home_slug}-vs-{away_slug}"


def _is_today_or_live(match: dict[str, Any], now: datetime | None = None) -> bool:
    status = str(match.get("status") or "").lower()
    if status == "live":
        return True
    if status != "scheduled":
        return False

    kickoff = _parse_match_datetime(match.get("kickoffTime"))
    if not kickoff:
        return False
    today = (now or datetime.now(timezone.utc)).date()
    return kickoff.date() == today


def _parse_live_status(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(live|in progress)\b", lowered):
        return "live"
    if re.search(r"\b(ft|full time|final)\b", lowered):
        return "final"
    return "scheduled"


def _parse_live_score(text: str, home_abbr: str, away_abbr: str) -> tuple[int, int] | None:
    home = re.escape(home_abbr)
    away = re.escape(away_abbr)
    if home_abbr and away_abbr:
        abbr_patterns = (
            rf"\b{home}\s+(\d{{1,3}})\s+{away}\s+(\d{{1,3}})\b",
            rf"\b{home}\s+(\d{{1,3}})\s+(\d{{1,3}})\s+{away}\b",
            rf"\b(\d{{1,3}})\s+{home}\s+(\d{{1,3}})\s+{away}\b",
        )
        for pattern in abbr_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return int(match.group(1)), int(match.group(2))

    score_match = re.search(r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\b", text)
    if score_match:
        return int(score_match.group(1)), int(score_match.group(2))

    return None


def _pre_history_lines(lines: list[str]) -> list[str]:
    stop_patterns = (
        r"\brecent meetings\b",
        r"\bhead to head\b",
        r"\bteam trend\b",
        r"\bseason stats\b",
    )
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(re.search(pattern, lowered) for pattern in stop_patterns):
            return lines[:index]
    return lines


def _clock_score_context(lines: list[str]) -> str:
    for index, line in enumerate(lines[:120]):
        if re.fullmatch(r"(?:1T|2T|HT|FT|\d{1,2}:\d{2})", line, flags=re.IGNORECASE):
            start = max(index - 12, 0)
            end = min(index + 13, len(lines))
            return " ".join(lines[start:end])
    return ""


def _team_order_score(lines: list[str], match: dict[str, Any]) -> tuple[int, int] | None:
    home_terms = {
        _lookup_key(_match_team_name(match, "home")),
        _lookup_key(_match_team_abbr(match, "home")),
    }
    away_terms = {
        _lookup_key(_match_team_name(match, "away")),
        _lookup_key(_match_team_abbr(match, "away")),
    }
    home_terms.discard("")
    away_terms.discard("")

    def matches_any(key: str, terms: set[str]) -> bool:
        return any(term and (key == term or term in key or key in term) for term in terms)

    for index in range(min(len(lines), 120)):
        first_key = _lookup_key(lines[index])
        if matches_any(first_key, home_terms):
            first_side = "home"
            second_terms = away_terms
        elif matches_any(first_key, away_terms):
            first_side = "away"
            second_terms = home_terms
        else:
            continue
        window = lines[index : min(index + 10, len(lines))]
        numbers: list[int] = []
        second_team_seen = False
        for item in window[1:]:
            key = _lookup_key(item)
            if re.fullmatch(r"\d{1,3}", item):
                numbers.append(int(item))
                continue
            if matches_any(key, second_terms):
                second_team_seen = True
                break
        if second_team_seen and len(numbers) >= 2:
            if first_side == "home":
                return numbers[0], numbers[1]
            return numbers[1], numbers[0]
    return None


def _team_score_context(lines: list[str], match: dict[str, Any]) -> str:
    home_terms = {
        _lookup_key(_match_team_name(match, "home")),
        _lookup_key(_match_team_abbr(match, "home")),
    }
    away_terms = {
        _lookup_key(_match_team_name(match, "away")),
        _lookup_key(_match_team_abbr(match, "away")),
    }
    home_terms.discard("")
    away_terms.discard("")

    home_indexes: list[int] = []
    away_indexes: list[int] = []
    for index, line in enumerate(lines[:160]):
        key = _lookup_key(line)
        if any(term and (key == term or term in key or key in term) for term in home_terms):
            home_indexes.append(index)
        if any(term and (key == term or term in key or key in term) for term in away_terms):
            away_indexes.append(index)

    if not home_indexes or not away_indexes:
        return ""

    start = max(min(home_indexes[0], away_indexes[0]) - 12, 0)
    end = min(max(home_indexes[-1], away_indexes[-1]) + 13, len(lines))
    return " ".join(lines[start:end])


def _extract_current_score(lines: list[str], match: dict[str, Any]) -> tuple[tuple[int, int] | None, str, str]:
    home_abbr = _match_team_abbr(match, "home")
    away_abbr = _match_team_abbr(match, "away")
    live_lines = _pre_history_lines(lines)
    live_text = " ".join(live_lines)

    clock_context = _clock_score_context(live_lines)
    if clock_context:
        score = _parse_live_score(clock_context, home_abbr, away_abbr)
        if score:
            return score, "clock-section", clock_context

    team_order_score = _team_order_score(live_lines, match)
    if team_order_score:
        return team_order_score, "team-name-score-order", _team_score_context(live_lines, match)

    team_context = _team_score_context(live_lines, match)
    if team_context:
        score = _parse_live_score(team_context, home_abbr, away_abbr)
        if score:
            return score, "team-nearby-section", team_context

    score = _parse_live_score(live_text, home_abbr, away_abbr)
    if score:
        score_match = re.search(r"\b\d{1,3}\s*[-–]\s*\d{1,3}\b", live_text)
        context = live_text[max((score_match.start() if score_match else 0) - 160, 0) : (score_match.end() if score_match else 0) + 160]
        return score, "first-pre-history-dash-score", context

    return None, "none", ""


def _text_lines(soup: BeautifulSoup) -> list[str]:
    lines: list[str] = []
    for line in soup.get_text("\n", strip=True).splitlines():
        clean = " ".join(line.split())
        if clean and clean not in lines:
            lines.append(clean)
    return lines


def _looks_like_player_name(value: str) -> bool:
    if len(value) < 4 or len(value) > 70:
        return False
    lowered = value.lower()
    blocked = {
        "lineup",
        "lineups",
        "roster",
        "substitutes",
        "reserves",
        "major league rugby",
        "new england free jacks",
        "california legion",
    }
    if lowered in blocked or any(token in lowered for token in ("tickets", "match", "score", "season")):
        return False
    return bool(re.search(r"[A-Za-z]", value)) and not value.isupper()


def _parse_lineups(soup: BeautifulSoup, match: dict[str, Any]) -> list[dict[str, Any]]:
    home_name = _match_team_name(match, "home")
    away_name = _match_team_name(match, "away")
    home_abbr = _match_team_abbr(match, "home")
    away_abbr = _match_team_abbr(match, "away")
    home_lookup = _lookup_key(home_name)
    away_lookup = _lookup_key(away_name)
    home_team_id = match.get("homeTeamId")
    away_team_id = match.get("awayTeamId")

    lines = _text_lines(soup)
    try:
        start = next(i for i, line in enumerate(lines) if line.lower() == "lineups")
    except StopIteration:
        return []

    stop = next(
        (i for i, line in enumerate(lines[start + 1 :], start + 1) if line.lower() in {"head to head", "team trend", "season stats"}),
        len(lines),
    )
    lineup_lines = lines[start + 1 : stop]

    current_team_id: str | None = None
    parsed_any_for_current_team = False
    pending_number: int | None = None
    players: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, int]] = set()

    for line in lineup_lines:
        key = _lookup_key(line)
        if key == _lookup_key(home_abbr) or key == home_lookup:
            current_team_id = home_team_id
            parsed_any_for_current_team = False
            pending_number = None
            continue
        if key == _lookup_key(away_abbr) or key == away_lookup:
            if current_team_id == home_team_id and not parsed_any_for_current_team:
                # The MLR page prints both team tab labels before the active lineup.
                continue
            current_team_id = away_team_id
            parsed_any_for_current_team = False
            pending_number = None
            continue

        if current_team_id is None:
            continue

        if re.fullmatch(r"\d{1,2}", line):
            pending_number = int(line)
            continue

        if line.lower() == "captain":
            continue

        player_match = re.match(r"^(?:#\s*)?(\d{1,2})\s+([A-Z][A-Za-z .'\-]{2,70})$", line)
        if not player_match:
            player_match = re.match(r"^([A-Z][A-Za-z .'\-]{2,70})\s+#?\s*(\d{1,2})$", line)
            if player_match:
                name = player_match.group(1).strip()
                number = int(player_match.group(2))
            elif pending_number is not None:
                number = pending_number
                name = line.strip()
            else:
                continue
        else:
            number = int(player_match.group(1))
            name = player_match.group(2).strip()
        pending_number = None

        if not _looks_like_player_name(name):
            continue

        dedupe_key = (_lookup_key(name), current_team_id, number)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        players.append({"name": name, "teamId": current_team_id, "jerseyNumber": number})
        parsed_any_for_current_team = True

    return players


def _parse_mlr_match_page(html: str, match: dict[str, Any]) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    lines = _text_lines(soup)
    text = " ".join(lines)
    logger.info(
        "MLR match page text length=%d first500=%r",
        len(text),
        text[:500],
    )
    score, score_source, score_context = _extract_current_score(lines, match)
    logger.info(
        "MLR live score extracted score=%s source=%s context=%r",
        score,
        score_source,
        score_context[:500],
    )
    status = _parse_live_status(text)
    kickoff = _parse_match_datetime(match.get("kickoffTime"))
    if score and status == "scheduled" and kickoff and kickoff <= datetime.now(timezone.utc):
        status = "live"
    return {
        "status": status,
        "score": score,
        "lineups": _parse_lineups(soup, match),
    }


def _check_live_match_pages(api: FullpitchAPI, season: str, summary: dict[str, Any]) -> None:
    matches = api.get_matches(league="mlr", season=season, limit=100)
    targets = [match for match in matches if _is_today_or_live(match)]
    summary["live_matches_checked"] = len(targets)

    for match in targets:
        url = _build_match_page_url(match)
        if not url:
            logger.warning("Could not build MLR match URL for %s vs %s", _match_team_name(match, "home"), _match_team_name(match, "away"))
            continue

        try:
            parsed = _parse_mlr_match_page(fetch_text(url, timeout=20.0), match)
        except ScraperError as exc:
            msg = f"Failed to fetch MLR match page {url}: {exc}"
            logger.warning(msg)
            summary["errors"].append(msg)
            continue

        score = parsed["score"]
        status = parsed["status"]
        if score and status in {"live", "final"}:
            home_score, away_score = score
            api.upsert_match(
                {
                    "homeTeamId": match["homeTeamId"],
                    "awayTeamId": match["awayTeamId"],
                    "homeScore": home_score,
                    "awayScore": away_score,
                    "matchDate": match["kickoffTime"],
                    "league": "mlr",
                    "season": season,
                    "status": status,
                    "agentName": "mlr-agent",
                    "region": "national",
                    "events": {"sourceUrl": url, "liveScoreSource": "majorleague.rugby"},
                }
            )
            summary["live_matches_updated"] += 1
            logger.info("Updated MLR live page score: %s %s-%s %s (%s)", _match_team_name(match, "home"), home_score, away_score, _match_team_name(match, "away"), status)

        for player in parsed["lineups"]:
            try:
                api.upsert_player(
                    {
                        "name": player["name"],
                        "league": "mlr",
                        "teamId": player["teamId"],
                        "season": season,
                        "jerseyNumber": player["jerseyNumber"],
                        "position": str(player["jerseyNumber"]),
                        "agentName": "mlr-agent",
                    }
                )
                summary["lineup_players_upserted"] += 1
            except FullpitchAPIError as exc:
                msg = f"Failed to upsert MLR lineup player {player['name']}: {exc}"
                logger.warning(msg)
                summary["errors"].append(msg)


def _ingest_matches(api: FullpitchAPI, season: str, matches: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    summary["matches_found"] = len(matches)

    for parsed in matches:
        home_name = parsed["home_name"]
        away_name = parsed["away_name"]
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
    fixtures_url, standings_url = _data_source_urls(api)
    summary: dict[str, Any] = {
        "matches_found": 0,
        "matches_added": 0,
        "live_matches_checked": 0,
        "live_matches_updated": 0,
        "lineup_players_upserted": 0,
        "standings_updated": 0,
        "errors": [],
    }

    try:
        _ingest_matches(api, season, _fetch_mlr_fixtures(fixtures_url), summary)
    except ScraperError as exc:
        msg = f"Failed to fetch MLR fixtures from NA Rugby DB: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)

    try:
        _check_live_match_pages(api, season, summary)
    except FullpitchAPIError as exc:
        msg = f"Failed to check MLR live match pages: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)

    try:
        _ingest_standings(api, season, _fetch_mlr_standings(standings_url), summary)
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
