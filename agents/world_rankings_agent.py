"""World Rankings Agent — World Rugby Rankings for USA Eagles.

Schedule: Daily at 8am UTC.
Sources: world.rugby/rankings/mru (men), world.rugby/rankings/wru (women).
Writes to: /api/v1/ingest/standing
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

RANKINGS_URLS = {
    "men": "https://www.world.rugby/rankings/mru",
    "women": "https://www.world.rugby/rankings/wru",
}

USA_NAMES = {"united states", "usa", "usa eagles", "united states of america"}
FETCH_DELAY = 1.0


def _current_season() -> str:
    return str(datetime.now(timezone.utc).year)


def _find_usa_in_rankings(soup, gender: str) -> dict[str, Any] | None:
    """Find the USA row in a World Rugby rankings page."""

    for table in soup.select("table"):
        headers = [th.get_text(strip=True).lower() for th in table.select("thead th, th")]
        if not headers:
            continue

        col_map: dict[str, int] = {}
        for i, h in enumerate(headers):
            h = h.strip().lower()
            if h in ("pos", "position", "#", "rank"):
                col_map["position"] = i
            elif h in ("team", "country", "union", "name"):
                col_map["team"] = i
            elif h in ("pts", "points", "rating", "lr"):
                col_map["points"] = i
            elif "prev" in h or "move" in h or "+/-" in h:
                col_map["movement"] = i

        if "team" not in col_map:
            continue

        for row in table.select("tbody tr, tr"):
            cells = row.select("td")
            if not cells or col_map["team"] >= len(cells):
                continue

            team_text = cells[col_map["team"]].get_text(strip=True).lower()
            if team_text not in USA_NAMES:
                continue

            def cell_val(key: str) -> str:
                idx = col_map.get(key)
                if idx is None or idx >= len(cells):
                    return ""
                return cells[idx].get_text(strip=True)

            pos_text = re.sub(r"[^\d]", "", cell_val("position"))
            pts_text = re.sub(r"[^\d.]", "", cell_val("points"))

            return {
                "position": int(pos_text) if pos_text else 0,
                "points": float(pts_text) if pts_text else 0.0,
                "movement": cell_val("movement"),
                "gender": gender,
            }

    for el in soup.select("[class*='rank'], [class*='team'], [class*='country']"):
        text = el.get_text(strip=True).lower()
        if text in USA_NAMES:
            parent = el.find_parent("tr") or el.find_parent("div") or el.find_parent("li")
            if parent:
                numbers = re.findall(r"[\d.]+", parent.get_text())
                if len(numbers) >= 2:
                    return {
                        "position": int(float(numbers[0])),
                        "points": float(numbers[1]),
                        "movement": "",
                        "gender": gender,
                    }

    logger.warning("Could not find USA in %s rankings page", gender)
    return None


def run_world_rankings_agent() -> dict[str, Any]:
    """Fetch World Rugby Rankings and upsert USA Eagles position."""
    api = FullpitchAPI()
    season = _current_season()

    summary: dict[str, Any] = {
        "mens_rank": None,
        "womens_rank": None,
        "errors": [],
    }

    for gender, url in RANKINGS_URLS.items():
        logger.info("Fetching %s rankings from %s", gender, url)
        try:
            soup = fetch_html(url)
        except ScraperError as exc:
            msg = f"Failed to fetch {gender} rankings: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)
            time.sleep(FETCH_DELAY)
            continue

        result = _find_usa_in_rankings(soup, gender)
        if not result:
            summary["errors"].append(f"USA not found in {gender} rankings")
            time.sleep(FETCH_DELAY)
            continue

        team_label = f"USA Eagles {'Men' if gender == 'men' else 'Women'}"
        rank_key = f"{gender}s_rank"
        summary[rank_key] = result["position"]

        logger.info(
            "%s: position=%d, rating=%.2f, movement=%s",
            team_label, result["position"], result["points"], result["movement"] or "n/a",
        )

        team = api.get_team(name=team_label)
        if not team:
            team = api.get_team(name="USA Eagles")
        if not team:
            team = api.get_team(name="United States")

        if not team:
            msg = f"Team '{team_label}' not found in DB — cannot upsert standing"
            logger.warning(msg)
            summary["errors"].append(msg)
            time.sleep(FETCH_DELAY)
            continue

        try:
            api.upsert_standing({
                "teamId": team["id"],
                "league": "world",
                "season": season,
                "position": result["position"],
                "points": int(result["points"]),
                "played": 0,
                "won": 0,
                "drawn": 0,
                "lost": 0,
                "agentName": "world-rankings-agent",
            })
            logger.info("Upserted %s standing: #%d", team_label, result["position"])
        except FullpitchAPIError as exc:
            msg = f"Failed to upsert {team_label} standing: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)

        time.sleep(FETCH_DELAY)

    logger.info(
        "World Rankings agent: men=#%s women=#%s errors=%d",
        summary["mens_rank"] or "?",
        summary["womens_rank"] or "?",
        len(summary["errors"]),
    )
    return summary


def run() -> None:
    """Entry point called by main.py."""
    run_world_rankings_agent()
