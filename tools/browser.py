"""Headless browser utilities for JavaScript-rendered source pages."""

from __future__ import annotations

import logging

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


def fetch_js_page(url: str, wait_for: str | None = None, timeout: int = 15000) -> str:
    """
    Fetch a JS-rendered page and return body text.
    Blocks images and fonts to reduce memory usage.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.route(
                "**/*.{png,jpg,jpeg,gif,svg,woff,woff2}",
                lambda route: route.abort(),
            )

            page.goto(url, timeout=timeout, wait_until="networkidle")

            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=5000)
                except Exception:
                    pass

            text = page.inner_text("body")
            browser.close()
            logger.info("Browser fetched %s (%d chars)", url, len(text))
            return text

    except Exception as exc:
        logger.error("Browser fetch failed %s: %s", url, exc)
        return ""
