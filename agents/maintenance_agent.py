"""Maintenance Agent — daily audit and repair for article metadata.

Schedule: Daily, after content ingestion agents.
Reads from: /api/v1/articles?limit=200
Writes to: PATCH /api/v1/articles/[id]
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.scraper import extract_og_image, gemini_summarize

logger = logging.getLogger(__name__)

ARTICLE_LIMIT = 200
REQUEST_DELAY_SECONDS = 1


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def _source_domain(source_url: str) -> str | None:
    return urlparse(source_url).hostname


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _all_articles(api: FullpitchAPI) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    page = 1

    while True:
        envelope = api.get_articles(limit=ARTICLE_LIMIT, page=page)
        batch = envelope.get("data", [])
        meta = envelope.get("meta", {})
        articles.extend(batch)

        total = int(meta.get("total") or len(articles))
        returned_limit = int(meta.get("limit") or ARTICLE_LIMIT)
        if not batch or len(articles) >= total:
            break

        page += 1
        if page * returned_limit > total + returned_limit:
            break

    return articles


def _repair_article(article: dict[str, Any], api: FullpitchAPI) -> dict[str, int]:
    title = article.get("title") or "Untitled"
    source_url = article.get("sourceUrl") or article.get("url") or ""
    updates: dict[str, str] = {}
    attempted: set[str] = set()

    if _is_missing(article.get("imageUrl")) and source_url:
        attempted.add("imageUrl")
        image_url = extract_og_image(source_url)
        if image_url:
            updates["imageUrl"] = image_url

    if _is_missing(article.get("slug")) and title:
        attempted.add("slug")
        slug = _slugify(title)
        if slug:
            updates["slug"] = slug

    if _is_missing(article.get("summary")) and source_url:
        attempted.add("summary")
        summary = gemini_summarize(source_url)
        if summary:
            updates["summary"] = summary

    if _is_missing(article.get("sourceDomain")) and source_url:
        attempted.add("sourceDomain")
        source_domain = _source_domain(source_url)
        if source_domain:
            updates["sourceDomain"] = source_domain

    if not updates:
        logger.info("Complete: %s", title)
        return {"fixed": 0, "attempted": len(attempted)}

    api.update_article(article["id"], updates)
    for field in updates:
        logger.info("Fixed %s: %s", field, title)

    return {"fixed": len(updates), "attempted": len(attempted)}


def run_maintenance_agent() -> dict[str, Any]:
    """Audit every article and fill missing metadata where possible."""
    api = FullpitchAPI()
    summary: dict[str, Any] = {
        "articles_checked": 0,
        "fields_fixed": 0,
        "fields_attempted": 0,
        "errors": [],
    }

    try:
        articles = _all_articles(api)
    except FullpitchAPIError as exc:
        logger.error("Failed to fetch articles for maintenance: %s", exc)
        summary["errors"].append(str(exc))
        return summary

    for article in articles:
        try:
            result = _repair_article(article, api)
            summary["articles_checked"] += 1
            summary["fields_fixed"] += result["fixed"]
            summary["fields_attempted"] += result["attempted"]
        except Exception as exc:
            title = article.get("title") or article.get("id") or "unknown"
            logger.exception("Maintenance failed for %s", title)
            summary["errors"].append(f"{title}: {exc}")

        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("Maintenance summary: %s", summary)
    return summary


def run() -> None:
    """Entry point called by main.py."""
    run_maintenance_agent()
