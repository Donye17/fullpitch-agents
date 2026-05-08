"""HTML and JSON fetch utilities with retry and rate limiting.

Uses httpx for fetching and BeautifulSoup for HTML parsing.
Exponential backoff on 429/503 (up to 3 retries: 2s, 4s, 8s).
"""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "Fullpitch/1.0 (fullpitch.app)"
MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds — retries at 2, 4, 8
DOMAIN_DELAY_SECONDS = 2.0
_domain_last_request: dict[str, float] = {}
_domain_lock = threading.Lock()


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
    merged_headers = dict(headers or {})
    merged_headers["User-Agent"] = USER_AGENT

    last_status = 0
    for attempt in range(1, MAX_RETRIES + 1):
        logger.info("Fetching %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
        try:
            _pause_for_domain(url)
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


def _pause_for_domain(url: str) -> None:
    """Throttle consecutive requests to the same domain."""
    domain = urlparse(url).netloc.lower()
    if not domain:
        return

    with _domain_lock:
        now = time.monotonic()
        last_request = _domain_last_request.get(domain)
        if last_request is not None:
            wait = DOMAIN_DELAY_SECONDS - (now - last_request)
            if wait > 0:
                logger.debug("Waiting %.2fs before next request to %s", wait, domain)
                time.sleep(wait)
        _domain_last_request[domain] = time.monotonic()


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
