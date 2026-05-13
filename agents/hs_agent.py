"""High School Agent — browser diagnostics for JS-rendered tournament scores."""

from __future__ import annotations

import logging
from typing import Any

from tools.browser import fetch_js_page
from tools.fullpitch_api import FullpitchAPI
from tools.schedule import is_tournament_active

logger = logging.getLogger(__name__)

GIRLS_NATIONALS_URL = (
    "https://xplorer.rugby/girls-national-championships"
    "/fixtures-results?team=All"
    "&comp=irMyJcNPJqZyFgfnq&season=All&tab=Results"
)


def run_hs_agent(api: FullpitchAPI | None = None) -> dict[str, Any]:
    """Scrape Girls HS Nationals results from Xplorer on weekends."""
    if not is_tournament_active():
        logger.info("HS agent: skipping, not weekend")
        return {"skipped": True, "score_candidates": 0}

    logger.info("HS agent: fetching Girls Nationals")

    text = fetch_js_page(GIRLS_NATIONALS_URL, timeout=25000)

    if not text or len(text) < 200:
        logger.warning("HS agent: no data returned")
        return {"skipped": False, "score_candidates": 0}

    logger.info("HS agent: %d chars", len(text))
    logger.info("HS agent first 500: %s", text[:500])

    score_candidates = 0
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for i, line in enumerate(lines):
        if any(char.isdigit() for char in line):
            score_candidates += 1
            context = lines[max(0, i - 2) : i + 4]
            logger.info("HS score candidate: %s", context)

    return {"skipped": False, "score_candidates": score_candidates}


def run() -> None:
    """Entry point called by boss_agent.py."""
    run_hs_agent(FullpitchAPI())
