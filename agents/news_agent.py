"""News Agent — official article ingest with US rugby relevance filter.

Schedule: Hourly.
Sources: majorleague.rugby/news, usa.rugby/news, rugbypass.com, ultimaterugby.com.
Writes to: /api/v1/ingest/article
"""

from __future__ import annotations

import logging
import os
import html as html_lib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from tools.article_filter import (
    has_minimum_content,
    is_category_or_tag_url,
    is_viable_article_candidate,
    looks_like_article_url,
)
from tools.college_leagues import VALID_COLLEGE_LEAGUES, classify_college_league
from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
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

GEMINI_REASONING = "gemini-2.5-flash"
GEMINI_WRITING_MID = "gemini-2.5-flash"

MAX_AGE_DAYS = 7

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


def _is_us_rugby(title: str, client) -> bool:
    """Use Gemini to classify whether an article is about US rugby."""
    if client is None:
        logger.warning("No Gemini client — cannot filter, skipping article")
        return False
    try:
        resp = client.models.generate_content(
            model=GEMINI_REASONING,
            contents=(
                "Is this article primarily about US rugby — MLR, college rugby, "
                "USA Eagles, US club rugby, or US players/coaches? "
                f"Article title: '{title}'. Answer YES or NO only."
            ),
        )
        answer = resp.text.strip().upper()
        is_relevant = answer.startswith("YES")
        logger.info("Relevance check: '%s' → %s", title[:80], "YES" if is_relevant else "NO")
        return is_relevant
    except Exception:
        logger.exception("Gemini relevance check failed for '%s'", title[:80])
        return False


def _generate_summary(title: str, content: str, source: str, client) -> str | None:
    """Use Gemini to write a 150-200 word article summary."""
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
        return clean_text(resp.text)
    except Exception:
        logger.exception("Gemini summary generation failed for '%s'", title[:80])
        return None


def _cutoff_date() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)


def _domain(url: str) -> str:
    return urlparse(url).hostname or url


def _clean_entity_text(value: str | None) -> str:
    return clean_text(html_lib.unescape(value or ""))


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
        league = src.get("league")
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

        for art in articles:
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

            if needs_filter:
                if not _is_us_rugby(art["title"], genai_client):
                    summary["skipped_irrelevant"] += 1
                    continue

            article_summary = gemini_summarize(article_url, article_text) or _generate_summary(
                art["title"], article_text, _domain(url), genai_client
            )
            article_summary = _clean_entity_text(article_summary)

            article_league = league or _classify_league(
                art["title"], article_text, genai_client
            )

            try:
                api.create_article({
                    "title": _clean_entity_text(art["title"]),
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
                msg = f"Failed to ingest article '{art['title'][:60]}': {exc}"
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
                "- general: USA Rugby organization news, coaching certifications, policy "
                "updates, referee education, or anything that does not fit the categories above.\n\n"
                f"Title: {title}\n"
                f"Content: {content[:1200]}\n\n"
                "Reply with ONLY the category name, nothing else."
            ),
        )
        category = resp.text.strip().lower()
        valid = {"mlr", "wer", "eagles", "club", "high-school", "general", *VALID_COLLEGE_LEAGUES}
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
