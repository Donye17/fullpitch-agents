"""MLR Agent — Major League Rugby scores, standings, and player stats.

Schedule: Hourly, February through October (MLR season).
Sources: mlrugby.com, official APIs.
Writes to: /api/v1/ingest/match, /standing, /player
"""

import logging

logger = logging.getLogger(__name__)


def run() -> None:
    logger.info("MLR agent started")
    # TODO: implement
    #   1. Fetch latest MLR match results
    #   2. Fetch current standings
    #   3. Fetch player stats updates
    #   4. Upsert via Fullpitch ingest API
    #   5. Log run to AgentLog
    logger.info("MLR agent complete")
