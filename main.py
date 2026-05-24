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

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fullpitch-agents")

GEMINI_REASONING = "gemini-2.5-flash-lite"
GEMINI_WRITING_MID = "gemini-2.5-flash-lite"
GEMINI_WRITING_PRO = "gemini-2.5-flash"

AGENT_MAP = {
    "boss": "agents.boss_agent",
    "mlr": "agents.mlr_agent",
    "wer": "agents.wer_agent",
    "world-rankings": "agents.world_rankings_agent",
    "news": "agents.news_agent",
    "video": "agents.video_agent",
    "eagles": "agents.eagles_agent",
    "college": "agents.college_agent",
    "hs": "agents.hs_agent",
    "craa": "agents.craa_agent",
    "content": "agents.content_agent",
    "maintenance": "agents.maintenance_agent",
}

BOSS_INTERVAL_SECONDS = 60 * 60
LIVE_ACTIVE_SLEEP_SECONDS = 60
LIVE_HT_SLEEP_SECONDS = 300
LIVE_IDLE_SLEEP_SECONDS = 300


def _build_live_match_url(match: dict) -> str | None:
    league = str(match.get("league") or "").lower()
    if league == "wer":
        from agents.wer_agent import _build_wer_match_page_url

        return _build_wer_match_page_url(match)
    from agents.mlr_agent import _build_match_page_url

    return _build_match_page_url(match)


def _live_score_source(match: dict) -> str:
    league = str(match.get("league") or "").lower()
    if league == "wer":
        return "womenseliterugby.us"
    return "majorleague.rugby"


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


def check_and_update_score(match: dict, api: FullpitchAPI | None = None) -> str | None:
    from agents.mlr_agent import _match_team_name, _week_from_match
    from tools.final_score_verification import on_match_final
    from tools.screenshot_scores import fetch_live_score_via_screenshot

    api = api or FullpitchAPI()
    url = _build_live_match_url(match)
    if not url:
        logger.warning(
            "Live score tracker could not build URL for %s vs %s (league=%s)",
            _match_team_name(match, "home"),
            _match_team_name(match, "away"),
            match.get("league"),
        )
        return None

    current_status = str(match.get("status") or "").lower()
    if current_status == "completed":
        return None

    parsed = fetch_live_score_via_screenshot(url)
    if parsed is None:
        return None

    home_score = parsed["home_score"]
    away_score = parsed["away_score"]
    period = str(parsed.get("period") or "").upper() or None
    is_final_period = period in {"FT", "AET"}
    status = "completed" if is_final_period or parsed["status"] == "final" else "live"
    if status not in {"live", "completed"}:
        return period

    if (
        match.get("homeScore") == home_score
        and match.get("awayScore") == away_score
        and current_status == status
    ):
        return period

    league = str(match.get("league") or "mlr").lower()
    events: dict[str, object] = {
        "sourceUrl": url,
        "liveScoreSource": _live_score_source(match),
        "needs_verification": status == "completed",
    }
    if period:
        events["period"] = period

    if status == "completed":
        payload: dict[str, object] = {
            "homeTeamId": match["homeTeamId"],
            "awayTeamId": match["awayTeamId"],
            "homeScore": home_score,
            "awayScore": away_score,
            "matchDate": match["kickoffTime"],
            "league": league,
            "season": match.get("season"),
            "status": "completed",
            "agentName": "live-score-loop",
            "region": "national",
            "events": events,
        }
        if league == "mlr":
            week = _week_from_match(match)
            if week:
                payload["round"] = str(week)
        api.upsert_match(payload)
    else:
        api.update_match(
            match["id"],
            {
                "homeScore": home_score,
                "awayScore": away_score,
                "status": status,
                "events": events,
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
    if status == "completed" and current_status != "completed":
        on_match_final(
            match,
            (home_score, away_score),
            match_slug=url,
            api=api,
            week=_week_from_match(match) if league == "mlr" else None,
            venue=match.get("venue"),
        )
    return period


def live_score_loop() -> None:
    api = FullpitchAPI()
    logger.info("Live score tracker started")

    while True:
        try:
            matches = api.get_matches(league=["mlr", "wer"], status=["scheduled", "live"], limit=100)
            today_matches = [match for match in matches if is_today_or_live(match)]

            if today_matches:
                logger.info("Live score tracker checking %d match(es)", len(today_matches))
                periods: list[str] = []
                for match in today_matches:
                    try:
                        period = check_and_update_score(match, api)
                        if period:
                            periods.append(period)
                    except Exception as exc:
                        logger.error("Live score error for match %s: %s", match.get("id"), exc)

                if any(period == "HT" for period in periods):
                    time.sleep(LIVE_HT_SLEEP_SECONDS)
                elif any(period in {"1H", "2H"} for period in periods):
                    time.sleep(LIVE_ACTIVE_SLEEP_SECONDS)
                else:
                    time.sleep(LIVE_IDLE_SLEEP_SECONDS)
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
