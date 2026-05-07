"""News Agent — article ingest with US rugby relevance filter.

Schedule: Hourly.
Sources: mlrugby.com/news, usa.rugby/news, rugbypass.com, ultimaterugby.com,
         Reddit (r/MLRugby, r/usarugby, r/rugbyunion, r/collegiaterugby).
Writes to: /api/v1/ingest/article
"""

import logging

logger = logging.getLogger(__name__)

WEB_SOURCES = [
    {"url": "mlrugby.com/news", "type": "scrape", "league": "mlr"},
    {"url": "usa.rugby/news", "type": "scrape", "league": "eagles"},
    {"url": "rugbypass.com", "type": "scrape", "filter": True},
    {"url": "ultimaterugby.com", "type": "scrape", "filter": True},
]

REDDIT_SOURCES = [
    {"subreddit": "MLRugby", "type": "reddit", "league": "mlr"},
    {"subreddit": "usarugby", "type": "reddit", "league": "eagles"},
    {"subreddit": "rugbyunion", "type": "reddit", "filter": True},
    {"subreddit": "collegiaterugby", "type": "reddit", "league": "college"},
]


def run() -> None:
    logger.info("News agent started")
    # TODO: implement
    #   1. Scrape web sources for new articles
    #   2. Fetch Reddit posts from monitored subreddits
    #   3. Use Gemini (GEMINI_REASONING) to classify relevance
    #   4. Extract key info, tag with league/level
    #   5. Submit via /api/v1/ingest/article (skip duplicates by URL)
    #   6. Log run to AgentLog
    logger.info("News agent complete")
