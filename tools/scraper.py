"""HTML and JSON fetch utilities with retry and rate limiting.

Uses httpx for fetching and BeautifulSoup for HTML parsing.
Exponential backoff on 429/503 (up to 3 retries: 2s, 4s, 8s).
"""

from __future__ import annotations

import logging
import time

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "FullpitchBot/1.0"
MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds — retries at 2, 4, 8


class ScraperError(Exception):
    """Raised after all retries are exhausted."""

    def __init__(self, url: str, status: int, attempts: int) -> None:
        self.url = url
        self.status = status
        super().__init__(
            f"Failed to fetch {url} after {attempts} attempts (last status: {status})"
        )


def _request_with_retry(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> httpx.Response:
    """GET with exponential backoff on 429 and 503."""
    merged_headers = {"User-Agent": USER_AGENT}
    if headers:
        merged_headers.update(headers)

    last_status = 0
    for attempt in range(1, MAX_RETRIES + 1):
        logger.info("Fetching %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
        try:
            resp = httpx.get(
                url,
                headers=merged_headers,
                timeout=timeout,
                follow_redirects=True,
            )
            last_status = resp.status_code

            if resp.status_code in (429, 503) and attempt < MAX_RETRIES:
                delay = BACKOFF_BASE ** attempt
                logger.warning(
                    "%s returned %d — retrying in %ds", url, resp.status_code, delay
                )
                time.sleep(delay)
                continue

            if resp.status_code >= 400:
                raise ScraperError(url, resp.status_code, attempt)

            return resp

        except httpx.HTTPError as exc:
            if attempt < MAX_RETRIES:
                delay = BACKOFF_BASE ** attempt
                logger.warning(
                    "HTTP error fetching %s: %s — retrying in %ds", url, exc, delay
                )
                time.sleep(delay)
            else:
                raise ScraperError(url, last_status or 0, attempt) from exc

    raise ScraperError(url, last_status, MAX_RETRIES)


def fetch_html(url: str, timeout: float = 15.0) -> BeautifulSoup:
    """Fetch a URL and return a parsed BeautifulSoup document.

    Retries up to 3 times with exponential backoff on 429/503.
    Raises ScraperError after max retries.
    """
    resp = _request_with_retry(url, timeout=timeout)
    return BeautifulSoup(resp.text, "html.parser")


def fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> dict:
    """Fetch a URL and return parsed JSON.

    Retries up to 3 times with exponential backoff on 429/503.
    Raises ScraperError after max retries.
    """
    resp = _request_with_retry(url, headers=headers, timeout=timeout)
    return resp.json()
