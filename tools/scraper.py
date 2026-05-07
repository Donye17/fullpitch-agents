"""HTML fetch and parse utility with rate limiting.

Uses httpx for fetching and BeautifulSoup for parsing.
"""

import logging
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_DELAY_SECONDS = 2.0
USER_AGENT = "FullpitchBot/1.0 (+https://fullpitch.app)"


def fetch_html(url: str, timeout: float = 15.0) -> str | None:
    """Fetch a URL and return raw HTML, or None on failure."""
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text
    except Exception:
        logger.exception("Failed to fetch %s", url)
        return None


def parse_html(html: str) -> BeautifulSoup:
    """Parse HTML string into a BeautifulSoup document."""
    return BeautifulSoup(html, "html.parser")


def fetch_and_parse(url: str, timeout: float = 15.0) -> BeautifulSoup | None:
    """Fetch a URL and return a parsed BeautifulSoup document."""
    html = fetch_html(url, timeout)
    if html is None:
        return None
    return parse_html(html)


def extract_links(soup: BeautifulSoup, selector: str = "a[href]") -> list[dict[str, Any]]:
    """Extract links from a parsed document."""
    links = []
    for tag in soup.select(selector):
        href = tag.get("href", "")
        text = tag.get_text(strip=True)
        if href:
            links.append({"href": str(href), "text": text})
    return links


def rate_limit(seconds: float = DEFAULT_DELAY_SECONDS) -> None:
    """Pause between requests to be a good citizen."""
    time.sleep(seconds)
