"""Fullpitch ADK Agents — entry point.

Usage:
    python main.py              # run boss agent (orchestrates all)
    python main.py --agent mlr  # run only the MLR agent
    python main.py --agent news # run only the news agent
"""

import argparse
import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fullpitch-agents")

GEMINI_REASONING = "gemini-2.5-flash-lite"
GEMINI_WRITING_MID = "gemini-2.5-flash"
GEMINI_WRITING_PRO = "gemini-2.5-pro"

AGENT_MAP = {
    "boss": "agents.boss_agent",
    "mlr": "agents.mlr_agent",
    "news": "agents.news_agent",
    "video": "agents.video_agent",
    "eagles": "agents.eagles_agent",
    "college": "agents.college_agent",
    "content": "agents.content_agent",
    "wer": "agents.wer_agent",
}


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
    run_agent(args.agent)


if __name__ == "__main__":
    main()
