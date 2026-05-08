"""HTML and JSON fetch utilities with retry and rate limiting.

Uses httpx for fetching and BeautifulSoup for HTML parsing.
Exponential backoff on 429/503 (up to 3 retries: 2s, 4s, 8s).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from urllib.parse import urljoin
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from tools.text_utils import clean_text

logger = logging.getLogger(__name__)

USER_AGENT = "Fullpitch/1.0 (fullpitch.app)"
MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds — retries at 2, 4, 8
DOMAIN_DELAY_SECONDS = 2.0
GEMINI_WRITING_MID = "gemini-2.5-flash"
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


def extract_og_image(url: str) -> str | None:
    """Fetch a page and return its og:image URL, if present."""
    try:
        soup = fetch_html(url)
    except ScraperError as exc:
        logger.warning("Failed to fetch og:image from %s: %s", url, exc)
        return None

    tag = soup.select_one('meta[property="og:image"], meta[name="og:image"]')
    image_url = tag.get("content", "").strip() if tag else ""
    return urljoin(url, image_url) if image_url else None


def _get_genai_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None

    from google import genai

    return genai.Client(api_key=api_key)


def _page_text(soup: BeautifulSoup) -> str:
    for element in soup.select("script, style, noscript, nav, footer, header"):
        element.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def gemini_summarize(url: str) -> str | None:
    """Fetch a page and generate a concise Gemini TLDR."""
    client = _get_genai_client()
    if client is None:
        logger.warning("GOOGLE_API_KEY not set — cannot summarize %s", url)
        return None

    try:
        soup = fetch_html(url)
    except ScraperError as exc:
        logger.warning("Failed to fetch page text from %s: %s", url, exc)
        return None

    text = _page_text(soup)
    if not text:
        return None

    try:
        response = client.models.generate_content(
            model=GEMINI_WRITING_MID,
            contents=(
                "Write a neutral 4-6 sentence TLDR, around 100 words, for US rugby fans. "
                "Cover what happened, who is involved, and why it matters to US rugby fans. "
                'Do not start with "This article" or "In this piece." '
                f"Source URL: {url}\n\nPage text:\n{text[:6000]}"
            ),
        )
        summary = clean_text(response.text.strip())
        return summary or None
    except Exception:
        logger.exception("Gemini summary generation failed for %s", url)
        return None
