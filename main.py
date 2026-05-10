"""Fullpitch ADK Agents — entry point.

Usage:
    python main.py              # run boss agent (orchestrates all)
    python main.py --agent mlr  # run only the MLR agent
    python main.py --agent news # run only the news agent
"""

import argparse
import logging
import os
import threading
import time

from dotenv import load_dotenv

from tools.fullpitch_api import FullpitchAPI
from tools.gemini_relevance import GEMINI_FREE_TIER_MODEL

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fullpitch-agents")

GEMINI_REASONING = GEMINI_FREE_TIER_MODEL
GEMINI_WRITING_MID = GEMINI_FREE_TIER_MODEL
GEMINI_WRITING_PRO = GEMINI_FREE_TIER_MODEL

AGENT_MAP = {
    "boss": "agents.boss_agent",
    "mlr": "agents.mlr_agent",
    "wer": "agents.wer_agent",
    "world-rankings": "agents.world_rankings_agent",
    "news": "agents.news_agent",
    "video": "agents.video_agent",
    "eagles": "agents.eagles_agent",
    "college": "agents.college_agent",
    "craa": "agents.craa_agent",
    "content": "agents.content_agent",
    "maintenance": "agents.maintenance_agent",
}

BOSS_INTERVAL_SECONDS = 60 * 60
LIVE_ACTIVE_SLEEP_SECONDS = 60
LIVE_IDLE_SLEEP_SECONDS = 300


def init_sentry() -> None:
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        logger.info("SENTRY_DSN not set — Sentry disabled")
        return
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=dsn, traces_sample_rate=0.1)
        logger.info("Sentry initialized")
    except Exception as exc:
        logger.warning("Failed to initialize Sentry: %s", exc)


def is_today_or_live(match: dict) -> bool:
    from agents.mlr_agent import _is_today_or_live

    return _is_today_or_live(match)


def check_and_update_score(match: dict, api: FullpitchAPI | None = None) -> bool:
    from agents.mlr_agent import (
        _build_match_page_url,
        _match_team_name,
        _parse_mlr_match_page,
    )
    from tools.scraper import fetch_text

    api = api or FullpitchAPI()
    url = _build_match_page_url(match)
    if not url:
        logger.warning(
            "Live score tracker could not build MLR URL for %s vs %s",
            _match_team_name(match, "home"),
            _match_team_name(match, "away"),
        )
        return False

    parsed = _parse_mlr_match_page(fetch_text(url, timeout=20.0), match)
    score = parsed["score"]
    status = parsed["status"]
    if not score or status not in {"live", "final"}:
        return False

    home_score, away_score = score
    current_status = str(match.get("status") or "").lower()
    if (
        match.get("homeScore") == home_score
        and match.get("awayScore") == away_score
        and current_status == status
    ):
        return False

    api.update_match(
        match["id"],
        {
            "homeScore": home_score,
            "awayScore": away_score,
            "status": status,
            "events": {"sourceUrl": url, "liveScoreSource": "majorleague.rugby"},
        },
    )
    logger.info(
        "Live score tracker updated %s %s-%s %s (%s)",
        _match_team_name(match, "home"),
        home_score,
        away_score,
        _match_team_name(match, "away"),
        status,
    )
    return True


def live_score_loop() -> None:
    api = FullpitchAPI()
    logger.info("Live score tracker started")

    while True:
        try:
            matches = api.get_matches(league="mlr", status=["scheduled", "live"], limit=100)
            today_matches = [match for match in matches if is_today_or_live(match)]

            if today_matches:
                logger.info("Live score tracker checking %d match(es)", len(today_matches))
                for match in today_matches:
                    try:
                        check_and_update_score(match, api)
                    except Exception as exc:
                        logger.error("Live score error for match %s: %s", match.get("id"), exc)
                time.sleep(LIVE_ACTIVE_SLEEP_SECONDS)
            else:
                time.sleep(LIVE_IDLE_SLEEP_SECONDS)
        except Exception as exc:
            logger.error("Live score error: %s", exc)
            time.sleep(LIVE_ACTIVE_SLEEP_SECONDS)


def run_agent(name: str) -> None:
    module_path = AGENT_MAP.get(name)
    if not module_path:
        logger.error("Unknown agent: %s (available: %s)", name, ", ".join(AGENT_MAP))
        return

    logger.info("Starting agent: %s", name)
    start = time.time()

    try:
        module = __import__(module_path, fromlist=["run"])
        run_fn = getattr(module, "run", None)
        if run_fn is None:
            logger.error("Agent module %s has no run() function", module_path)
            return
        run_fn()
    except Exception:
        logger.exception("Agent %s failed", name)
        raise
    finally:
        elapsed = time.time() - start
        logger.info("Agent %s finished in %.1fs", name, elapsed)


def run_boss_service() -> None:
    live_thread = threading.Thread(target=live_score_loop, daemon=True)
    live_thread.start()

    logger.info("Boss service started — running boss agent every %d seconds", BOSS_INTERVAL_SECONDS)
    while True:
        run_agent("boss")
        logger.info("Boss service sleeping %.0f minutes", BOSS_INTERVAL_SECONDS / 60)
        time.sleep(BOSS_INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fullpitch ADK Agents")
    parser.add_argument(
        "--agent",
        type=str,
        default="boss",
        choices=list(AGENT_MAP.keys()),
        help="Which agent to run (default: boss)",
    )
    args = parser.parse_args()

    init_sentry()
    if args.agent == "boss":
        run_boss_service()
    else:
        run_agent(args.agent)


if __name__ == "__main__":
    main()
