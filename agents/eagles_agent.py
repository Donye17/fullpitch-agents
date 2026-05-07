"""Eagles Agent — USA Eagles national team results.

Schedule: Daily at 7am UTC.
Sources: usa.rugby, World Rugby API.
Writes to: /api/v1/ingest/match
"""

import logging

logger = logging.getLogger(__name__)


def run() -> None:
    logger.info("Eagles agent started")
    # TODO: implement
    #   1. Fetch latest USA Eagles match results
    #   2. Check for upcoming fixtures
    #   3. Upsert via Fullpitch ingest API
    #   4. Log run to AgentLog
    logger.info("Eagles agent complete")
