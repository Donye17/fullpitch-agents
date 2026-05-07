"""College Agent — college rugby scores and standings.

Schedule: Every 2 hours.
Sources: CRAA, NCR official sites.
Writes to: /api/v1/ingest/match, /standing
"""

import logging

logger = logging.getLogger(__name__)


def run() -> None:
    logger.info("College agent started")
    # TODO: implement
    #   1. Fetch latest college match results (CRAA D1A, D1AA, Women's)
    #   2. Fetch NCR results where available
    #   3. Update standings
    #   4. Upsert via Fullpitch ingest API
    #   5. Log run to AgentLog
    logger.info("College agent complete")
