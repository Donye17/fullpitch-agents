"""WER Agent — World Rugby Rankings.

Schedule: Daily at 8am UTC.
Sources: World Rugby rankings page / API.
Writes to: Fullpitch API (WorldRankingEntry model).
"""

import logging

logger = logging.getLogger(__name__)


def run() -> None:
    logger.info("WER agent started")
    # TODO: implement
    #   1. Fetch latest World Rugby rankings
    #   2. Parse USA position and points
    #   3. Store/update ranking entries
    #   4. Log run to AgentLog
    logger.info("WER agent complete")
