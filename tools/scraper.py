"""HTML and JSON fetch utilities with retry and rate limiting.

Uses httpx for fetching and BeautifulSoup for HTML parsing.
Exponential backoff on 429/503 (up to 3 retries: 2s, 4s, 8s).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from tools.gemini_relevance import GEMINI_FREE_TIER_MODEL
from tools.text_utils import clean_text

logger = logging.getLogger(__name__)

USER_AGENT = "Fullpitch/1.0 (fullpitch.app)"
MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds — retries at 2, 4, 8
DOMAIN_DELAY_SECONDS = 2.0
GEMINI_WRITING_MID = GEMINI_FREE_TIER_MODEL
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


def fetch_text(url: str, timeout: float = 15.0) -> str:
    """Fetch a URL and return raw response text."""
    resp = _request_with_retry(url, timeout=timeout)
    return resp.text


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


def extract_og_image_from_html(html: str, page_url: str) -> str | None:
    """Return an og:image URL from already-fetched HTML, if present."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.select_one('meta[property="og:image"], meta[name="og:image"]')
    image_url = tag.get("content", "").strip() if tag else ""
    return urljoin(page_url, image_url) if image_url else None


def extract_og_image(url: str) -> str | None:
    """Fetch a page and return its og:image URL, if present."""
    try:
        html = fetch_text(url)
    except ScraperError as exc:
        logger.warning("Failed to fetch og:image from %s: %s", url, exc)
        return None

    return extract_og_image_from_html(html, url)


def _parse_publish_date(value: str | None) -> str | None:
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%m/%d/%Y",
        "%d %B %Y",
        "%d %b %Y",
    ):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            continue

    return None


def _jsonld_date_published(value) -> str | None:
    if isinstance(value, dict):
        date_value = value.get("datePublished")
        if isinstance(date_value, str):
            parsed = _parse_publish_date(date_value)
            if parsed:
                return parsed
        for child in value.values():
            parsed = _jsonld_date_published(child)
            if parsed:
                return parsed
    elif isinstance(value, list):
        for item in value:
            parsed = _jsonld_date_published(item)
            if parsed:
                return parsed
    return None


def extract_publish_date(html: str) -> str | None:
    """Return an original source publish date from raw HTML, or None."""
    soup = BeautifulSoup(html, "html.parser")

    selectors = (
        'meta[property="article:published_time"]',
        'meta[name="publishdate"]',
    )
    for selector in selectors:
        tag = soup.select_one(selector)
        parsed = _parse_publish_date(tag.get("content", "") if tag else None)
        if parsed:
            return parsed

    time_tag = soup.select_one("time[datetime]")
    parsed = _parse_publish_date(time_tag.get("datetime", "") if time_tag else None)
    if parsed:
        return parsed

    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        parsed = _jsonld_date_published(data)
        if parsed:
            return parsed

    byline_pattern = re.compile(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b",
        re.IGNORECASE,
    )
    for selector in (
        ".byline",
        ".author",
        ".post-meta",
        ".entry-meta",
        ".published",
        "[class*='byline']",
        "[class*='date']",
        "[class*='meta']",
    ):
        for node in soup.select(selector):
            match = byline_pattern.search(node.get_text(" ", strip=True))
            parsed = _parse_publish_date(match.group(0) if match else None)
            if parsed:
                return parsed

    return None


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


def extract_page_text_from_html(html: str) -> str:
    """Return readable article text from raw HTML."""
    return _page_text(BeautifulSoup(html, "html.parser"))


def gemini_summarize(url: str, text: str | None = None) -> str | None:
    """Generate a 150-200 word Gemini summary for an article."""
    client = _get_genai_client()
    if client is None:
        logger.warning("GOOGLE_API_KEY not set — cannot summarize %s", url)
        return None

    if text is None:
        try:
            soup = fetch_html(url)
            text = _page_text(soup)
        except ScraperError as exc:
            logger.warning("Failed to fetch page text from %s: %s", url, exc)
            return None

    if not text:
        return None

    try:
        response = client.models.generate_content(
            model=GEMINI_WRITING_MID,
            contents=(
                "You are a sports journalist writing for a US rugby news aggregator. "
                "Write a 150-200 word summary of the following article for rugby fans.\n\n"
                "The summary must:\n"
                "- Be 4-6 full sentences, minimum 150 words\n"
                "- Cover: what happened, who was involved, key details, and why it matters to US rugby fans\n"
                "- Include specific names, scores, or statistics mentioned in the article\n"
                "- Be written in an engaging, informative tone\n"
                "- NOT start with 'This article' or 'In this piece'\n"
                "- NOT be a list — write in flowing prose paragraphs\n\n"
                f"Article URL: {url}\n"
                f"Article text: {text[:8000]}\n\n"
                "Write the summary now:"
            ),
        )
        summary = clean_text(response.text.strip())
        return summary or None
    except Exception:
        logger.exception("Gemini summary generation failed for %s", url)
        return None
