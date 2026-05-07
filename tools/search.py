"""Web search utility for agents.

Provides Reddit fetching (free, no auth) and general web search helpers.
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

REDDIT_USER_AGENT = "FullpitchBot/1.0"
REDDIT_DELAY_SECONDS = 1.0


def fetch_subreddit_new(subreddit: str, limit: int = 25) -> list[dict]:
    """Fetch newest posts from a subreddit (free, no auth)."""
    url = f"https://www.reddit.com/r/{subreddit}/new.json"
    params = {"limit": limit}
    headers = {"User-Agent": REDDIT_USER_AGENT}

    try:
        response = httpx.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()["data"]["children"]
    except Exception:
        logger.exception("Failed to fetch r/%s", subreddit)
        return []


def search_reddit(subreddit: str, query: str, limit: int = 10) -> list[dict]:
    """Search a subreddit for posts matching a query."""
    url = f"https://www.reddit.com/r/{subreddit}/search.json"
    params = {"q": query, "sort": "new", "limit": limit, "restrict_sr": "true"}
    headers = {"User-Agent": REDDIT_USER_AGENT}

    try:
        response = httpx.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()["data"]["children"]
    except Exception:
        logger.exception("Failed to search r/%s for '%s'", subreddit, query)
        return []


def rate_limit_reddit() -> None:
    """Pause between Reddit requests to stay under 60 req/min."""
    time.sleep(REDDIT_DELAY_SECONDS)
