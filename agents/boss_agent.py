"""Boss Agent — orchestrates all sub-agents.

Runs on Railway cron. Delegates to sub-agents based on schedule and priority.
"""

import logging

logger = logging.getLogger(__name__)


def run() -> None:
    logger.info("Boss agent started — delegating to sub-agents")
    # TODO: implement orchestration logic
    #   1. Check schedule — which agents should run now?
    #   2. Run each sub-agent in sequence or parallel
    #   3. Collect results and log summary
    logger.info("Boss agent complete")
