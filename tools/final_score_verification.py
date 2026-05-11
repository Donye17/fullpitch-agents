"""Verify final MLR scores against NA Rugby DB before reports publish."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from agents.narugbydb import normalize_team_name, parse_fixtures_page
from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.match_report_generator import generate_match_report
from tools.scraper import ScraperError, fetch_html

logger = logging.getLogger(__name__)

NARDB_MLR_FIXTURES_URL = "https://narugbydb.com/calendar/2026-mlr-fixtures-results/"
NARDB_INITIAL_WAIT_SECONDS = 15 * 60
NARDB_RETRY_WAIT_SECONDS = 10 * 60
_verification_threads: set[str] = set()
_verification_lock = threading.Lock()


def _lookup_key(value: str | None) -> str:
    return normalize_team_name(value or "").lower()


def _match_team_name(match: dict[str, Any], side: str) -> str:
    team = match.get(f"{side}Team") or {}
    return team.get("name") or team.get("shortName") or team.get("abbreviation") or ""


def parse_nardb_score(home: str, away: str, fixtures_url: str = NARDB_MLR_FIXTURES_URL) -> tuple[int, int] | None:
    """Fetch and parse the NARDB final score for a match."""
    soup = fetch_html(fixtures_url, timeout=20.0)
    rows = parse_fixtures_page(soup)
    home_key = _lookup_key(home)
    away_key = _lookup_key(away)

    for row in rows:
        row_home = _lookup_key(row.get("home_name"))
        row_away = _lookup_key(row.get("away_name"))
        if row_home == home_key and row_away == away_key and row.get("status") == "final":
            return int(row["home_score"]), int(row["away_score"])
        if row_home == away_key and row_away == home_key and row.get("status") == "final":
            return int(row["away_score"]), int(row["home_score"])

    return None


def verify_final_score(
    match: dict[str, Any],
    mlr_score: tuple[int, int],
    *,
    match_slug: str,
    page_html: str | None = None,
    week: int | None = None,
    venue: str | None = None,
    api: FullpitchAPI | None = None,
    initial_wait_seconds: int = NARDB_INITIAL_WAIT_SECONDS,
    retry_wait_seconds: int = NARDB_RETRY_WAIT_SECONDS,
) -> None:
    """Cross-check the final MLR page score against NARDB, then publish report."""
    api = api or FullpitchAPI()
    home = _match_team_name(match, "home")
    away = _match_team_name(match, "away")
    match_id = match.get("id")

    logger.info("Waiting %d seconds for NARDB to update before verifying %s vs %s", initial_wait_seconds, home, away)
    time.sleep(initial_wait_seconds)

    nardb_score: tuple[int, int] | None = None
    for attempt in (1, 2):
        try:
            nardb_score = parse_nardb_score(home, away)
        except ScraperError as exc:
            logger.warning("NARDB final score fetch failed for %s vs %s: %s", home, away, exc)

        if nardb_score:
            break

        if attempt == 1:
            logger.warning("NARDB not updated yet for %s vs %s, retrying in %d seconds", home, away, retry_wait_seconds)
            time.sleep(retry_wait_seconds)

    if not nardb_score:
        logger.warning("NARDB final score unavailable after retry for %s vs %s; leaving MLR score unverified", home, away)
        return

    mlr_home, mlr_away = mlr_score
    nardb_home, nardb_away = nardb_score

    if (mlr_home, mlr_away) != (nardb_home, nardb_away):
        logger.warning(
            "Score corrected: MLR said %s-%s, NARDB says %s-%s for %s vs %s. Using NARDB.",
            mlr_home,
            mlr_away,
            nardb_home,
            nardb_away,
            home,
            away,
        )
    else:
        logger.info("Score verified: %s-%s for %s vs %s", nardb_home, nardb_away, home, away)

    if match_id:
        try:
            api.update_match(
                match_id,
                {
                    "homeScore": nardb_home,
                    "awayScore": nardb_away,
                    "status": "final",
                    "events": {
                        "sourceUrl": match_slug,
                        "liveScoreSource": "majorleague.rugby",
                        "finalScoreSource": "narugbydb.com",
                        "needs_verification": False,
                        "verifiedScore": {"home": nardb_home, "away": nardb_away},
                    },
                },
            )
        except FullpitchAPIError:
            logger.exception("Failed to write verified NARDB score for match %s", match_id)

    generate_match_report(
        match_slug,
        home,
        away,
        nardb_home,
        nardb_away,
        match.get("league") or "mlr",
        api=api,
        page_html=page_html,
        week=week,
        venue=venue,
        match_id=match_id,
    )


def on_match_final(
    match: dict[str, Any],
    mlr_score: tuple[int, int],
    *,
    match_slug: str,
    page_html: str | None = None,
    week: int | None = None,
    venue: str | None = None,
    api: FullpitchAPI | None = None,
) -> None:
    """Start one non-blocking final-score verification thread per match."""
    match_key = str(match.get("id") or match_slug)
    with _verification_lock:
        if match_key in _verification_threads:
            logger.info("Final score verification already scheduled for %s", match_key)
            return
        _verification_threads.add(match_key)

    def _run() -> None:
        try:
            verify_final_score(
                match,
                mlr_score,
                match_slug=match_slug,
                page_html=page_html,
                week=week,
                venue=venue,
                api=api,
            )
        finally:
            with _verification_lock:
                _verification_threads.discard(match_key)

    threading.Thread(target=_run, name=f"verify-final-score-{match_key}", daemon=True).start()
