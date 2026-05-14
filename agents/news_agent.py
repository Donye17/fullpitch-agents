"""News Agent — official article ingest with US rugby relevance filter.

Schedule: Hourly.
Sources: majorleague.rugby/news, usa.rugby/news, rugbypass.com, ultimaterugby.com,
        usrugbyfoundation.org/news (league=community, max 3/run).
Writes to: /api/v1/ingest/article
"""

from __future__ import annotations

import logging
import os
import re
import html as html_lib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from tools.article_filter import (
    has_minimum_content,
    is_category_or_tag_url,
    is_viable_article_candidate,
    looks_like_article_url,
)
from tools.college_leagues import VALID_COLLEGE_LEAGUES, classify_college_league
from tools.editorial_ai import normalize_feed_summary, shorten_title
from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.gemini_relevance import GEMINI_FREE_TIER_MODEL, batch_relevance_check
from tools.scraper import (
    ScraperError,
    extract_og_image,
    extract_og_image_from_html,
    extract_page_text_from_html,
    extract_publish_date,
    fetch_html,
    fetch_text,
    gemini_summarize,
)
from tools.text_utils import clean_text

logger = logging.getLogger(__name__)

GEMINI_REASONING = GEMINI_FREE_TIER_MODEL
GEMINI_WRITING_MID = GEMINI_FREE_TIER_MODEL

MAX_AGE_DAYS = 7
HIGH_SCHOOL_KEYWORDS = (
    "high school",
    "girls nationals",
    "boys nationals",
    "youth",
    "yhs",
    "girls rugby national",
)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_summary_prompt() -> str:
    path = PROMPTS_DIR / "article_summary.txt"
    return path.read_text(encoding="utf-8")


def _get_genai_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    from google import genai
    return genai.Client(api_key=api_key)


def _generate_summary(title: str, content: str, source: str, client) -> str | None:
    """Use Gemini to write a short one-paragraph article summary."""
    if client is None:
        return None
    try:
        template = _load_summary_prompt()
        prompt = template.replace("{title}", title).replace(
            "{source}", source
        ).replace("{content}", content[:4000])
        resp = client.models.generate_content(
            model=GEMINI_WRITING_MID,
            contents=prompt,
        )
        return normalize_feed_summary(resp.text)
    except Exception:
        logger.exception("Gemini summary generation failed for '%s'", title[:80])
        return None


def _cutoff_date() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)


def _domain(url: str) -> str:
    return urlparse(url).hostname or url


def _clean_entity_text(value: str | None) -> str:
    return clean_text(html_lib.unescape(value or ""))


def _is_high_school_article(title: str, content: str = "") -> bool:
    text = f"{title} {content}".lower()
    return any(keyword in text for keyword in HIGH_SCHOOL_KEYWORDS)


# ── HTML parsing helpers ──────────────────────────────────────────────────────


def _extract_articles_from_html(soup, base_url: str) -> list[dict[str, Any]]:
    """Best-effort extraction of article links/titles from a news page."""
    articles: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    selectors = [
        "article a",
        ".news-item a",
        ".post-item a",
        ".article-card a",
        "[class*='news'] a",
        "[class*='article'] a",
        ".content-list a",
        ".story a",
    ]

    candidates: list = []
    for sel in selectors:
        candidates.extend(soup.select(sel))

    if not candidates:
        candidates = soup.select("a[href]")

    for tag in candidates:
        href = tag.get("href", "")
        if not href or href.startswith("#") or href.startswith("javascript"):
            continue

        full_url = urljoin(base_url, href)

        if not _looks_like_article_url(full_url, base_url):
            continue
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        title = _clean_entity_text(tag.get_text(" ", strip=True))
        if not title or len(title) < 10:
            heading = tag.select_one("h1, h2, h3, h4, .title, .headline")
            if heading:
                title = _clean_entity_text(heading.get_text(" ", strip=True))
        if not title or len(title) < 10:
            continue
        if not is_viable_article_candidate(title=title, url=full_url):
            continue

        date_text = ""
        time_el = tag.find_parent().select_one("time, .date, [class*='date']") if tag.find_parent() else None
        if time_el:
            date_text = time_el.get("datetime", "") or time_el.get_text(strip=True)

        snippet = ""
        desc_el = tag.find_parent().select_one("p, .summary, .excerpt, [class*='desc']") if tag.find_parent() else None
        if desc_el:
            snippet = _clean_entity_text(desc_el.get_text(" ", strip=True))[:500]

        articles.append({
            "title": title,
            "url": full_url,
            "date_text": date_text,
            "snippet": snippet,
        })

    logger.info("Extracted %d article candidates from %s", len(articles), base_url)
    return articles


def _looks_like_article_url(url: str, base_url: str) -> bool:
    """Heuristic: does this URL look like a news article?"""
    if not looks_like_article_url(url):
        return False

    parsed = urlparse(url)
    path = parsed.path.lower()

    skip_patterns = (
        "/tag/", "/category/", "/author/", "/page/", "/login",
        "/signup", "/subscribe", "/contact", "/about", "/privacy",
        "/terms", "/search", "/cart", "/shop",
    )
    if any(p in path for p in skip_patterns) or is_category_or_tag_url(url):
        return False

    if path in ("/", "/news", "/news/", ""):
        return False

    return True


def _parse_date(text: str) -> datetime | None:
    """Best-effort date parse. Returns None if unparseable."""
    text = text.strip()
    if not text:
        return None

    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%m/%d/%Y",
        "%d %B %Y",
        "%d %b %Y",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


COMMUNITY_INGEST_MIN = datetime(2026, 5, 1, tzinfo=timezone.utc)
USRF_SKIP_TITLE_SUBSTRINGS = (
    "donate",
    "giving tuesday",
    "make a donation",
    "scrumble",
    "sold out",
    "register today",
)


def _should_skip_community_fundraising_title(title: str) -> bool:
    t = title.lower()
    return any(s in t for s in USRF_SKIP_TITLE_SUBSTRINGS)


def _parse_iso_to_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _extract_usrf_article_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Collect /news/[slug] article URLs from the US Rugby Foundation news index."""
    host = urlparse(base_url).netloc.lower()
    if "usrugbyfoundation.org" not in host:
        logger.warning("Community news listing host not supported: %s", host)
        return []

    seen: set[str] = set()
    out: list[str] = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        full = urljoin(base_url, href).split("#")[0].rstrip("/")
        parsed = urlparse(full)
        if parsed.netloc.lower() != host:
            continue
        path = parsed.path.rstrip("/")
        if not re.match(r"^/news/[^/]+$", path, re.I):
            continue
        if full in seen:
            continue
        seen.add(full)
        out.append(full)

    logger.info("USRF listing: %d article URLs", len(out))
    return out


def _process_community_news_sources(
    src: dict[str, Any],
    soup: BeautifulSoup,
    api: FullpitchAPI,
    genai_client,
    existing_urls: set[str],
    summary: dict[str, Any],
) -> int:
    """Ingest up to 3 US Rugby Foundation articles per run (published on or after 2026-05-01)."""
    base_url = src["url"]
    article_urls = _extract_usrf_article_urls(soup, base_url)
    written_here = 0
    max_per_run = 3

    for article_url in article_urls:
        if written_here >= max_per_run:
            break
        if article_url in existing_urls:
            summary["skipped_duplicate"] += 1
            continue

        try:
            article_html = fetch_text(article_url)
        except ScraperError as exc:
            logger.warning("Failed to fetch community article %s: %s", article_url, exc)
            summary["errors"].append(str(exc))
            continue

        page_soup = BeautifulSoup(article_html, "html.parser")
        h1 = page_soup.find("h1")
        title = _clean_entity_text(h1.get_text(" ", strip=True) if h1 else "")
        if not title or len(title) < 10:
            og_title = page_soup.select_one('meta[property="og:title"]')
            title = _clean_entity_text((og_title.get("content") or "").strip() if og_title else "")
        if not title or len(title) < 10:
            summary["skipped_irrelevant"] += 1
            continue
        if _should_skip_community_fundraising_title(title):
            logger.info("Skipping community article (title filter): %s", title[:80])
            summary["skipped_irrelevant"] += 1
            continue

        published_iso = extract_publish_date(article_html)
        pub_dt = _parse_iso_to_utc(published_iso)
        if pub_dt is None:
            logger.info("Skipping community article (no parseable date): %s", title[:80])
            summary["skipped_irrelevant"] += 1
            continue
        if pub_dt < COMMUNITY_INGEST_MIN:
            logger.info("Skipping community article (before 2026-05-01): %s", title[:80])
            continue

        image_url = extract_og_image_from_html(article_html, article_url)
        if not image_url:
            image_url = extract_og_image(article_url)

        article_text = extract_page_text_from_html(article_html) or ""
        if not has_minimum_content(article_text, min_chars=100):
            summary["skipped_irrelevant"] += 1
            continue

        article_summary = _generate_summary(
            title, article_text, "US Rugby Foundation", genai_client
        )
        if not article_summary:
            article_summary = gemini_summarize(article_url, article_text) or ""
        article_summary = normalize_feed_summary(_clean_entity_text(article_summary))
        if not article_summary:
            summary["skipped_irrelevant"] += 1
            continue

        title = shorten_title(title, genai_client)
        try:
            api.create_article(
                {
                    "title": title,
                    "url": article_url,
                    "source": "US Rugby Foundation",
                    "publishedDate": published_iso,
                    "league": "community",
                    "summary": article_summary,
                    "content": article_text[:2000] if article_text else None,
                    "imageUrl": image_url,
                    "agentName": "news-agent",
                    "tags": ["community"],
                }
            )
            existing_urls.add(article_url)
            summary["written"] += 1
            written_here += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to ingest community article '{title[:60]}': {exc}"
            logger.error(msg)
            summary["errors"].append(msg)

    return len(article_urls)


# ── Core logic ────────────────────────────────────────────────────────────────


def run_news_agent() -> dict[str, Any]:
    """Run the news agent: scrape official web sources, filter, summarize, ingest."""
    api = FullpitchAPI()
    genai_client = _get_genai_client()
    cutoff = _cutoff_date()

    summary: dict[str, Any] = {
        "web_found": 0,
        "skipped_duplicate": 0,
        "skipped_irrelevant": 0,
        "written": 0,
        "errors": [],
    }

    existing_urls: set[str] = set()
    try:
        recent = api.get_recent_articles(limit=50)
        existing_urls = {a.get("sourceUrl", a.get("url", "")) for a in recent}
    except FullpitchAPIError as exc:
        logger.warning("Failed to fetch existing articles: %s", exc)

    try:
        web_sources = api.get_sources(type="news")
    except FullpitchAPIError as exc:
        logger.error("Failed to fetch news sources from Fullpitch API: %s", exc)
        summary["errors"].append(str(exc))
        return summary

    # ── Web sources ───────────────────────────────────────────────────────

    for src in web_sources:
        url = src["url"]
        league = (src.get("league") or "").strip().lower()

        if league == "community":
            try:
                logger.info("Fetching web source: %s", url)
                soup = fetch_html(url)
            except ScraperError as exc:
                msg = f"Failed to fetch {url}: {exc}"
                logger.error(msg)
                summary["errors"].append(msg)
                continue
            n_listed = _process_community_news_sources(
                src, soup, api, genai_client, existing_urls, summary
            )
            summary["web_found"] += n_listed
            continue

        needs_filter = league == "general"

        try:
            logger.info("Fetching web source: %s", url)
            soup = fetch_html(url)
            articles = _extract_articles_from_html(soup, url)
            summary["web_found"] += len(articles)
        except ScraperError as exc:
            msg = f"Failed to fetch {url}: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)
            continue

        relevant_indexes = (
            batch_relevance_check(
                articles,
                genai_client,
                prompt_intro="You are filtering US rugby news articles.",
                relevant_description="primarily about US rugby, MLR, college rugby, USA Eagles, US club rugby, or US players/coaches",
            )
            if needs_filter
            else set(range(len(articles)))
        )

        for index, art in enumerate(articles):
            article_url = art["url"]

            if article_url in existing_urls:
                summary["skipped_duplicate"] += 1
                continue
            if not is_viable_article_candidate(title=art["title"], url=article_url):
                summary["skipped_irrelevant"] += 1
                continue

            pub_date = _parse_date(art["date_text"])
            if pub_date and pub_date < cutoff:
                continue

            if index not in relevant_indexes:
                summary["skipped_irrelevant"] += 1
                continue

            image_url = None
            published_date = None
            article_text = art.get("snippet", "")
            try:
                article_html = fetch_text(article_url)
                image_url = extract_og_image_from_html(article_html, article_url)
                published_date = extract_publish_date(article_html)
                article_text = extract_page_text_from_html(article_html) or article_text
            except ScraperError as exc:
                logger.warning("Failed to fetch article metadata from %s: %s", article_url, exc)

            if not image_url:
                image_url = extract_og_image(article_url)

            if not has_minimum_content(article_text, min_chars=100):
                logger.info("Skipping article with too little extracted content: %s", art["title"][:80])
                summary["skipped_irrelevant"] += 1
                continue

            title = _clean_entity_text(art["title"])
            is_high_school = _is_high_school_article(title, article_text)
            if league == "high-school" and not is_high_school:
                logger.info("Skipping non-high-school article from high-school source: %s", title[:80])
                summary["skipped_irrelevant"] += 1
                continue

            article_summary = art.get("snippet") or ""
            if not article_summary:
                article_summary = gemini_summarize(article_url, article_text) or _generate_summary(
                    title, article_text, _domain(url), genai_client
                )
            article_summary = normalize_feed_summary(_clean_entity_text(article_summary))

            article_league = (
                "high-school"
                if is_high_school
                else league or _classify_league(title, article_text, genai_client)
            )
            title = shorten_title(title, genai_client)

            try:
                api.create_article({
                    "title": title,
                    "url": article_url,
                    "source": _domain(url),
                    "publishedDate": published_date,
                    "league": article_league,
                    "summary": article_summary,
                    "content": article_text[:2000] if article_text else art.get("snippet"),
                    "imageUrl": image_url,
                    "agentName": "news-agent",
                    "tags": [article_league] if article_league else [],
                })
                existing_urls.add(article_url)
                summary["written"] += 1
            except FullpitchAPIError as exc:
                msg = f"Failed to ingest article '{title[:60]}': {exc}"
                logger.error(msg)
                summary["errors"].append(msg)

    # ── Summary ───────────────────────────────────────────────────────────

    logger.info(
        "News agent summary: web=%d written=%d dup=%d irrelevant=%d errors=%d",
        summary["web_found"],
        summary["written"],
        summary["skipped_duplicate"],
        summary["skipped_irrelevant"],
        len(summary["errors"]),
    )
    return summary


def _classify_league(title: str, content: str, client) -> str:
    """Attempt to classify which league an article belongs to via Gemini."""
    if _is_high_school_article(title, content):
        return "high-school"

    if client is None:
        return "general"
    try:
        resp = client.models.generate_content(
            model=GEMINI_REASONING,
            contents=(
                "Classify this rugby article into exactly one league category based on the "
                "article title and content, not the source domain. Source domain alone does "
                "not determine league.\n\n"
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
                "- community: US Rugby Foundation-style grassroots US rugby — scholarships, grants, "
                "youth programs, Hall of Fame, urban rugby, community milestones (not Eagles national team).\n"
                "- general: USA Rugby organization news, coaching certifications, policy "
                "updates, referee education, or anything that does not fit the categories above.\n\n"
                f"Title: {title}\n"
                f"Content: {content[:1200]}\n\n"
                "Reply with ONLY the category name, nothing else."
            ),
        )
        category = resp.text.strip().lower()
        valid = {
            "mlr",
            "wer",
            "eagles",
            "club",
            "high-school",
            "general",
            "community",
            *VALID_COLLEGE_LEAGUES,
        }
        if category == "college":
            return classify_college_league(title, content, default="college")
        if category in valid:
            return category
        if "college" in f"{title} {content}".lower() or "craa" in f"{title} {content}".lower() or "ncr" in f"{title} {content}".lower():
            return classify_college_league(title, content, default="college")
        return "general"
    except Exception:
        logger.exception("Gemini league classification failed")
        return "general"


def run() -> None:
    """Entry point called by main.py."""
    run_news_agent()
