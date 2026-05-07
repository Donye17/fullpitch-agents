"""Video Agent — YouTube content discovery.

Schedule: Daily at 6am UTC.
Sources: YouTube Data API v3.
Writes to: /api/v1/ingest/video
"""

import logging

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "MLR rugby highlights",
    "Major League Rugby",
    "USA rugby",
    "college rugby highlights",
    "USA Eagles rugby",
]


def run() -> None:
    logger.info("Video agent started")
    # TODO: implement
    #   1. Search YouTube for US rugby content
    #   2. Filter for relevance and recency
    #   3. Tag with league/level
    #   4. Submit via /api/v1/ingest/video (skip duplicates by videoId)
    #   5. Log run to AgentLog
    logger.info("Video agent complete")
