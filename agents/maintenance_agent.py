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
from tools.editorial_ai import normalize_feed_summary, shorten_title
from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.gemini_relevance import GEMINI_FREE_TIER_MODEL
from tools.scraper import (
    ScraperError,
    extract_og_image,
    extract_publish_date,
    fetch_text,
    gemini_summarize,
)
from tools.youtube_channels import approved_channel_terms, is_approved_youtube_channel

logger = logging.getLogger(__name__)

ARTICLE_LIMIT = 200
REQUEST_DELAY_SECONDS = 1
FAST_MAINTENANCE_HOURS = 48
GEMINI_REASONING = GEMINI_FREE_TIER_MODEL
MAX_SUMMARY_REPAIRS_PER_RUN = 10
MAX_TITLE_REPAIRS_PER_RUN = 15
JUNK_TITLES = {
    "create post",
    "find a club",
    "executive committee",
    "community calendar",
    "event sanctioning",
    "find a program",
    "youth & high school",
}
FULL_MODE_JUNK_TITLE_PATTERNS = (
    "student leader of the month",
    "membership dues",
    "coaching mentorship program",
    "broadcast academy",
    "firstpoint usa official",
    "student-leader",
    "powered by gdpr",
)
FAST_MODE_JUNK_TITLE_PATTERNS = (
    "student leader",
    "student-leader",
    "powered by gdpr",
)
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
    "community",
    "world",
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


def _is_full_mode_junk_title(value: str | None) -> bool:
    title = _unescape_text(value).lower()
    return any(pattern in title for pattern in FULL_MODE_JUNK_TITLE_PATTERNS)


def _is_fast_mode_junk_title(value: str | None) -> bool:
    title = _unescape_text(value).lower()
    return any(pattern in title for pattern in FAST_MODE_JUNK_TITLE_PATTERNS)


def _is_spam_video_source(video: dict[str, Any]) -> bool:
    source_names = [
        _unescape_text(video.get("sourceName")),
        _unescape_text(video.get("channelName")),
        _unescape_text(video.get("source")),
    ]
    return any(pattern.search(source_name) for source_name in source_names for pattern in SPAM_VIDEO_SOURCE_PATTERNS)


def _is_unapproved_video_source(video: dict[str, Any], approved_channels: set[str]) -> bool:
    source_names = [
        _unescape_text(video.get("sourceName")),
        _unescape_text(video.get("channelName")),
        _unescape_text(video.get("source")),
    ]
    return not any(is_approved_youtube_channel(source_name, approved_channels) for source_name in source_names)


def _needs_summary_repair(value: Any) -> bool:
    if _is_missing(value):
        return True
    if not isinstance(value, str):
        return False
    stripped = value.lstrip()
    return (
        _word_count(value) > 100
        or "\n\n" in value
        or stripped.lower().startswith(("this article", "in this piece"))
    )


def _needs_title_repair(value: Any) -> bool:
    return isinstance(value, str) and _word_count(_unescape_text(value)) > 7


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
                "- community: US Rugby Foundation-style grassroots stories — scholarships, "
                "grants, youth programs, Hall of Fame, urban rugby, player development, "
                "community milestones (not USA Eagles national team).\n"
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
    allow_summary_repair: bool,
) -> dict[str, int]:
    raw_title = article.get("title") or "Untitled"
    title = _unescape_text(raw_title)
    source_url = article.get("sourceUrl") or article.get("url") or ""
    updates: dict[str, str] = {}
    attempted: set[str] = set()

    if (mode_label == "Fast" and (_is_junk_title(raw_title) or _is_fast_mode_junk_title(raw_title))) or (
        mode_label == "Full" and _is_full_mode_junk_title(raw_title)
    ):
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
    summary_attempted = 0
    if summary_needs_repair and source_url and allow_summary_repair:
        attempted.add("summary")
        summary_attempted = 1
        summary = gemini_summarize(source_url)
        if summary:
            updates["summary"] = normalize_feed_summary(summary)
    elif summary_needs_repair and source_url:
        logger.info("%s maintenance: skipped summary repair for %s because summary budget is exhausted", mode_label, title)

    if _is_missing(article.get("sourceDomain")) and source_url:
        attempted.add("sourceDomain")
        source_domain = _source_domain(source_url)
        if source_domain:
            updates["sourceDomain"] = source_domain

    if not updates:
        logger.info("%s maintenance: complete %s", mode_label, title)
        return {"fixed": 0, "attempted": len(attempted), "deleted": 0, "summary_attempted": summary_attempted}

    api.update_article(article["id"], updates)
    for field in updates:
        logger.info("%s maintenance: fixed %s for %s", mode_label, field, title)

    return {"fixed": len(updates), "attempted": len(attempted), "deleted": 0, "summary_attempted": summary_attempted}


def _run_mode(
    *,
    mode_label: str,
    articles: list[dict[str, Any]],
    api: FullpitchAPI,
    genai_client,
    repair_short_summary: bool,
    summary_budget: dict[str, int],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "mode": mode_label.lower(),
        "articles_checked": 0,
        "fields_fixed": 0,
        "fields_attempted": 0,
        "articles_deleted": 0,
        "summary_repairs_attempted": 0,
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
                summary_budget["remaining"] > 0,
            )
            summary_attempted = result.get("summary_attempted", 0)
            summary_budget["remaining"] = max(summary_budget["remaining"] - summary_attempted, 0)
            summary["articles_checked"] += 1
            summary["fields_fixed"] += result["fixed"]
            summary["fields_attempted"] += result["attempted"]
            summary["articles_deleted"] += result.get("deleted", 0)
            summary["summary_repairs_attempted"] += summary_attempted
        except Exception as exc:
            title = article.get("title") or article.get("id") or "unknown"
            logger.exception("Maintenance failed for %s", title)
            summary["errors"].append(f"{title}: {exc}")

        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("%s maintenance summary: %s", mode_label, summary)
    return summary


def _repair_video(video: dict[str, Any], api: FullpitchAPI, approved_channels: set[str]) -> dict[str, int]:
    if _is_spam_video_source(video) or _is_unapproved_video_source(video, approved_channels):
        api.delete_video(video["id"])
        logger.info(
            "Full maintenance: deleted unapproved video source %s",
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


def _run_fast_title_shortening(api: FullpitchAPI, genai_client) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "articles_checked": 0,
        "titles_attempted": 0,
        "titles_fixed": 0,
        "articles_deleted": 0,
        "errors": [],
    }
    if genai_client is None:
        summary["errors"].append("GOOGLE_API_KEY not set; skipped title shortening")
        return summary

    try:
        articles = _all_articles(api)
    except FullpitchAPIError as exc:
        logger.error("Failed to fetch articles for fast title shortening: %s", exc)
        summary["errors"].append(str(exc))
        return summary

    for article in articles:
        if summary["titles_attempted"] >= MAX_TITLE_REPAIRS_PER_RUN:
            break

        raw_title = article.get("title") or ""
        title = _unescape_text(raw_title)
        summary["articles_checked"] += 1
        if _is_fast_mode_junk_title(title):
            try:
                api.delete_article(article["id"])
                summary["articles_deleted"] += 1
                logger.info("Fast maintenance: deleted junk article %s", title)
            except Exception as exc:
                logger.exception("Junk article deletion failed for %s", title)
                summary["errors"].append(f"{title}: {exc}")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        if not _needs_title_repair(title):
            continue

        try:
            summary["titles_attempted"] += 1
            shortened = shorten_title(title, genai_client)
            if shortened and shortened != title and _word_count(shortened) <= 7:
                api.update_article(article["id"], {"title": shortened})
                summary["titles_fixed"] += 1
                logger.info("Title shortened: %s → %s", title, shortened)
        except Exception as exc:
            logger.exception("Title shortening failed for %s", title)
            summary["errors"].append(f"{title}: {exc}")

        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("Fast title-shortening summary: %s", summary)
    return summary


def run_maintenance_agent() -> dict[str, Any]:
    """Run fast maintenance every cycle and full maintenance at 3 AM UTC."""
    api = FullpitchAPI()
    genai_client = _get_genai_client()
    result: dict[str, Any] = {"fast": None, "fast_title_shortening": None, "full": None}
    summary_budget = {"remaining": MAX_SUMMARY_REPAIRS_PER_RUN}

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
            summary_budget=summary_budget,
        )
        result["fast_title_shortening"] = _run_fast_title_shortening(api, genai_client)

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
                summary_budget=summary_budget,
            )
            try:
                full_videos = _all_videos(api)
                youtube_sources = api.get_sources(type="youtube")
                approved_channels = approved_channel_terms(youtube_sources)
                video_titles_fixed = 0
                videos_deleted = 0
                for video in full_videos:
                    try:
                        video_result = _repair_video(video, api, approved_channels)
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
