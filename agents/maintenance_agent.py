"""Maintenance Agent — daily audit and repair for article metadata.

Schedule: Daily, after content ingestion agents.
Reads from: /api/v1/articles?limit=200
Writes to: PATCH /api/v1/articles/[id]
"""

from __future__ import annotations

import logging
import os
import re
import time
import html as html_lib
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from tools.college_leagues import classify_college_league
from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.scraper import (
    ScraperError,
    extract_og_image,
    extract_publish_date,
    fetch_text,
    gemini_summarize,
)

logger = logging.getLogger(__name__)

ARTICLE_LIMIT = 200
REQUEST_DELAY_SECONDS = 1
FAST_MAINTENANCE_HOURS = 48
GEMINI_REASONING = "gemini-2.5-flash"
MIN_SUMMARY_WORDS = 150
JUNK_TITLES = {
    "create post",
    "find a club",
    "executive committee",
    "community calendar",
    "event sanctioning",
    "find a program",
    "youth & high school",
}
SPAM_VIDEO_SOURCE_PATTERNS = (
    re.compile(r"\bhdq\b", re.I),
    re.compile(r"\bgaming\b", re.I),
    re.compile(r"\bjornada\b", re.I),
    re.compile(r"\blive\s*c1\b", re.I),
    re.compile(r"\bh&n\b", re.I),
)

TITLE_LEAGUE_RULES = (
    (re.compile(r"\b(craa|ncr|nira|d1a|d1-aa|d1aa|division\s+i|division\s+ii|division\s+iii)\b", re.I), "college"),
    (re.compile(r"\b(high school|high-school)\b", re.I), "high-school"),
    (re.compile(r"\b(coaching|certification|certifications)\b", re.I), "general"),
)

VALID_LEAGUES = {
    "mlr",
    "wer",
    "craa-d1a",
    "craa-d1aa",
    "craa-women",
    "ncr-d1",
    "ncr-d2",
    "ncr-d3",
    "ncr-women",
    "nira",
    "college",
    "eagles",
    "club",
    "high-school",
    "general",
}


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def _source_domain(source_url: str) -> str | None:
    return urlparse(source_url).hostname


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _word_count(value: str | None) -> int:
    if not value:
        return 0
    return len(re.findall(r"\b\w+\b", value))


def _unescape_text(value: str | None) -> str:
    return " ".join(html_lib.unescape(value or "").split())


def _is_junk_title(value: str | None) -> bool:
    return _unescape_text(value).lower() in JUNK_TITLES


def _is_spam_video_source(video: dict[str, Any]) -> bool:
    source_name = _unescape_text(
        video.get("sourceName") or video.get("channelName") or video.get("source") or ""
    )
    return any(pattern.search(source_name) for pattern in SPAM_VIDEO_SOURCE_PATTERNS)


def _needs_summary_repair(value: Any) -> bool:
    if _is_missing(value):
        return True
    return isinstance(value, str) and _word_count(value) < MIN_SUMMARY_WORDS


def _get_genai_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None

    from google import genai

    return genai.Client(api_key=api_key)


def _keyword_league(title: str) -> str | None:
    for pattern, league in TITLE_LEAGUE_RULES:
        if pattern.search(title):
            return classify_college_league(title, default="college") if league == "college" else league
    return None


def _classify_league_with_gemini(title: str, content: str, client) -> str | None:
    if client is None:
        return None

    try:
        response = client.models.generate_content(
            model=GEMINI_REASONING,
            contents=(
                "Reclassify this rugby article into exactly one league category based on "
                "title and content, not source domain.\n\n"
                "Categories:\n"
                "- mlr: Major League Rugby, MLR teams or MLR players.\n"
                "- wer: Women's Elite Rugby, WER teams or WER players.\n"
                "- craa-d1a: CRAA Men's D1A, D1A, or D1-A.\n"
                "- craa-d1aa: CRAA Men's D1AA, D1AA, or D1-AA.\n"
                "- craa-women: CRAA Women's.\n"
                "- ncr-d1: NCR Men's Division I.\n"
                "- ncr-d2: NCR Men's Division II.\n"
                "- ncr-d3: NCR Men's Division III.\n"
                "- ncr-women: NCR Women's.\n"
                "- nira: NIRA Women's.\n"
                "- college: general college rugby when the specific division is unclear.\n"
                "- eagles: USA Eagles national team ONLY, men's or women's national team "
                "competing internationally.\n"
                "- club: club rugby or territorial unions.\n"
                "- high-school: high school rugby programs.\n"
                "- general: USA Rugby organization news, coaching certifications, policy "
                "updates, referee education, or anything that does not fit the categories above.\n\n"
                f"Title: {title}\n"
                f"Content: {content[:1200]}\n\n"
                "Reply with ONLY the category name."
            ),
        )
        league = response.text.strip().lower()
        return league if league in VALID_LEAGUES else None
    except Exception:
        logger.exception("Gemini league reclassification failed for %s", title[:80])
        return None


def is_full_maintenance_window() -> bool:
    """Return True during the daily 3 AM UTC full-maintenance window."""
    return datetime.now(timezone.utc).hour == 3


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _correct_league(article: dict[str, Any], client) -> str | None:
    current_league = (article.get("league") or "").lower()
    title = article.get("title") or ""
    keyword_league = _keyword_league(title)
    if keyword_league and keyword_league != current_league:
        return keyword_league

    content = article.get("summary") or article.get("content") or ""
    gemini_league = _classify_league_with_gemini(title, content, client)
    if gemini_league and gemini_league != current_league:
        return gemini_league

    return None


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


def _all_videos(api: FullpitchAPI) -> list[dict[str, Any]]:
    videos: list[dict[str, Any]] = []
    page = 1

    while True:
        envelope = api.get_videos(limit=ARTICLE_LIMIT, page=page)
        batch = envelope.get("data", [])
        meta = envelope.get("meta", {})
        videos.extend(batch)

        total = int(meta.get("total") or len(videos))
        returned_limit = int(meta.get("limit") or ARTICLE_LIMIT)
        if not batch or len(videos) >= total:
            break

        page += 1
        if page * returned_limit > total + returned_limit:
            break

    return videos


def _recent_articles(api: FullpitchAPI) -> list[dict[str, Any]]:
    envelope = api.get_articles(limit=ARTICLE_LIMIT, page=1)
    articles = envelope.get("data", [])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=FAST_MAINTENANCE_HOURS)
    recent_articles = [
        article
        for article in articles
        if (created_at := _parse_datetime(article.get("createdAt"))) is not None
        and created_at >= cutoff
    ]
    return sorted(
        recent_articles,
        key=lambda article: 0 if _is_missing(article.get("imageUrl")) else 1,
    )


def _repair_article(
    article: dict[str, Any],
    api: FullpitchAPI,
    genai_client,
    mode_label: str,
    repair_short_summary: bool,
) -> dict[str, int]:
    raw_title = article.get("title") or "Untitled"
    title = _unescape_text(raw_title)
    source_url = article.get("sourceUrl") or article.get("url") or ""
    updates: dict[str, str] = {}
    attempted: set[str] = set()

    if mode_label == "Fast" and _is_junk_title(raw_title):
        api.delete_article(article["id"])
        logger.info("%s maintenance: deleted junk article %s", mode_label, title)
        return {"fixed": 0, "attempted": 1, "deleted": 1}

    if title != raw_title:
        attempted.add("title")
        updates["title"] = title

    corrected_league = _correct_league(article, genai_client)
    if corrected_league:
        attempted.add("league")
        updates["league"] = corrected_league

    if _is_missing(article.get("imageUrl")) and source_url:
        attempted.add("imageUrl")
        image_url = extract_og_image(source_url)
        if image_url:
            updates["imageUrl"] = image_url

    if mode_label == "Fast" and _is_missing(article.get("publishedDate")) and source_url:
        attempted.add("publishedDate")
        try:
            published_date = extract_publish_date(fetch_text(source_url))
        except ScraperError as exc:
            logger.warning("Failed to fetch publish date from %s: %s", source_url, exc)
            published_date = None
        if published_date:
            updates["publishedDate"] = published_date

    if _is_missing(article.get("slug")) and title:
        attempted.add("slug")
        slug = _slugify(title)
        if slug:
            updates["slug"] = slug

    summary_needs_repair = (
        _needs_summary_repair(article.get("summary"))
        if repair_short_summary
        else _is_missing(article.get("summary"))
    )
    if summary_needs_repair and source_url:
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
        logger.info("%s maintenance: complete %s", mode_label, title)
        return {"fixed": 0, "attempted": len(attempted), "deleted": 0}

    api.update_article(article["id"], updates)
    for field in updates:
        logger.info("%s maintenance: fixed %s for %s", mode_label, field, title)

    return {"fixed": len(updates), "attempted": len(attempted), "deleted": 0}


def _run_mode(
    *,
    mode_label: str,
    articles: list[dict[str, Any]],
    api: FullpitchAPI,
    genai_client,
    repair_short_summary: bool,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "mode": mode_label.lower(),
        "articles_checked": 0,
        "fields_fixed": 0,
        "fields_attempted": 0,
        "articles_deleted": 0,
        "errors": [],
    }

    for article in articles:
        try:
            result = _repair_article(
                article,
                api,
                genai_client,
                mode_label,
                repair_short_summary,
            )
            summary["articles_checked"] += 1
            summary["fields_fixed"] += result["fixed"]
            summary["fields_attempted"] += result["attempted"]
            summary["articles_deleted"] += result.get("deleted", 0)
        except Exception as exc:
            title = article.get("title") or article.get("id") or "unknown"
            logger.exception("Maintenance failed for %s", title)
            summary["errors"].append(f"{title}: {exc}")

        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("%s maintenance summary: %s", mode_label, summary)
    return summary


def _repair_video(video: dict[str, Any], api: FullpitchAPI) -> dict[str, int]:
    if _is_spam_video_source(video):
        api.delete_video(video["id"])
        logger.info(
            "Full maintenance: deleted spam video source %s",
            video.get("sourceName") or video.get("channelName") or video.get("source") or video.get("id"),
        )
        return {"fixed": 0, "deleted": 1}

    raw_title = video.get("title") or ""
    title = _unescape_text(raw_title)
    if not title or title == raw_title:
        return {"fixed": 0, "deleted": 0}

    api.update_video(video["id"], {"title": title})
    logger.info("Full maintenance: fixed title for video %s", title)
    return {"fixed": 1, "deleted": 0}


def run_maintenance_agent() -> dict[str, Any]:
    """Run fast maintenance every cycle and full maintenance at 3 AM UTC."""
    api = FullpitchAPI()
    genai_client = _get_genai_client()
    result: dict[str, Any] = {"fast": None, "full": None}

    try:
        fast_articles = _recent_articles(api)
    except FullpitchAPIError as exc:
        logger.error("Failed to fetch recent articles for fast maintenance: %s", exc)
        result["fast"] = {"errors": [str(exc)]}
    else:
        result["fast"] = _run_mode(
            mode_label="Fast",
            articles=fast_articles,
            api=api,
            genai_client=genai_client,
            repair_short_summary=True,
        )

    if is_full_maintenance_window():
        try:
            full_articles = _all_articles(api)
        except FullpitchAPIError as exc:
            logger.error("Failed to fetch all articles for full maintenance: %s", exc)
            result["full"] = {"errors": [str(exc)]}
        else:
            result["full"] = _run_mode(
                mode_label="Full",
                articles=full_articles,
                api=api,
                genai_client=genai_client,
                repair_short_summary=True,
            )
            try:
                full_videos = _all_videos(api)
                video_titles_fixed = 0
                videos_deleted = 0
                for video in full_videos:
                    try:
                        video_result = _repair_video(video, api)
                        video_titles_fixed += video_result["fixed"]
                        videos_deleted += video_result["deleted"]
                    except Exception as exc:
                        title = video.get("title") or video.get("id") or "unknown"
                        logger.exception("Video title maintenance failed for %s", title)
                        result["full"]["errors"].append(f"video {title}: {exc}")
                    time.sleep(REQUEST_DELAY_SECONDS)
                result["full"]["video_titles_fixed"] = video_titles_fixed
                result["full"]["videos_deleted"] = videos_deleted
            except FullpitchAPIError as exc:
                logger.error("Failed to fetch all videos for full maintenance: %s", exc)
                result["full"]["errors"].append(str(exc))
    else:
        logger.info("Full maintenance skipped: outside 3 AM UTC window")

    logger.info("Maintenance summary: %s", result)
    return result


def run() -> None:
    """Entry point called by main.py."""
    run_maintenance_agent()
