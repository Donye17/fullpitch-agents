"""CRAA Agent — college rugby news, postseason results, and rankings.

Schedule: Every 2 hours.
Sources: craa.rugby news/events, LinkHub, and homepage power rankings.
Writes to: /api/v1/ingest/article, /api/v1/ingest/match
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.scraper import (
    ScraperError,
    extract_og_image_from_html,
    extract_publish_date,
    fetch_html,
    fetch_text,
)
from tools.text_utils import clean_text

logger = logging.getLogger(__name__)

GEMINI_REASONING = "gemini-2.5-flash"
GEMINI_WRITING_MID = "gemini-2.5-flash"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

CRAA_HOME_URL = "https://craa.rugby/"
CRAA_NEWS_URL = "https://craa.rugby/news-events/"
CRAA_LINKHUB_URL = "https://craa.rugby/LinkHub/"
CRAA_SOURCE_NAME = "craa.rugby"

SCORE_RE = re.compile(
    r"(?P<home>.+?)\s+(?P<home_score>\d{1,3})\s*[–—-]\s*(?P<away_score>\d{1,3})\s+(?P<away>.+)",
    re.IGNORECASE,
)


def _get_genai_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    from google import genai

    return genai.Client(api_key=api_key)


def _load_summary_prompt() -> str:
    return (PROMPTS_DIR / "article_summary.txt").read_text(encoding="utf-8")


def _current_season() -> str:
    return str(datetime.now(timezone.utc).year)


def _domain(url: str) -> str:
    return urlparse(url).hostname or CRAA_SOURCE_NAME


def _parse_date(text: str | None) -> str | None:
    if not text:
        return None

    value = " ".join(text.split()).strip()
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass

    for fmt in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%d %B %Y",
        "%d %b %Y",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue

    return None


def _classify_article(title: str, snippet: str, client) -> str:
    """Classify CRAA articles as college content or general org/admin news."""
    fallback_general = re.search(
        r"\b(board|committee|meeting|policy|bylaw|membership|registration|announcement|admin)\b",
        f"{title} {snippet}",
        flags=re.IGNORECASE,
    )
    if client is None:
        return "general" if fallback_general else "college"

    try:
        response = client.models.generate_content(
            model=GEMINI_REASONING,
            contents=(
                "Classify this CRAA rugby article as exactly one category.\n\n"
                "- college: CRAA D1A, D1AA, men's college rugby, women's college rugby, "
                "teams, rankings, fixtures, postseason, awards, or results.\n"
                "- general: CRAA organization/admin announcements, governance, policy, "
                "registration, meetings, or non-competition notices.\n\n"
                f"Title: {title}\n"
                f"Summary: {snippet[:1000]}\n\n"
                "Reply with ONLY college or general."
            ),
        )
        category = response.text.strip().lower()
        return category if category in {"college", "general"} else "college"
    except Exception:
        logger.exception("Gemini CRAA article classification failed for %s", title[:80])
        return "general" if fallback_general else "college"


def _generate_summary(title: str, content: str, source: str, client) -> str | None:
    if client is None:
        return clean_text(content)[:500] if content else None
    try:
        template = _load_summary_prompt()
        prompt = template.replace("{title}", title).replace("{source}", source).replace("{content}", content[:4000])
        response = client.models.generate_content(model=GEMINI_WRITING_MID, contents=prompt)
        return clean_text(response.text)
    except Exception:
        logger.exception("Gemini CRAA summary failed for %s", title[:80])
        return clean_text(content)[:500] if content else None


def _article_title_from_link(link) -> str:
    title = link.get_text(" ", strip=True)
    if len(title) >= 10:
        return title
    parent = link.find_parent()
    heading = parent.select_one("h1, h2, h3, h4, .title, .headline") if parent else None
    return heading.get_text(" ", strip=True) if heading else title


def _looks_like_news_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if parsed.netloc and parsed.netloc != "craa.rugby":
        return False
    if path in ("", "/", "/news-events/", "/linkhub/"):
        return False
    skip = ("/tag/", "/category/", "/author/", "/wp-content/", "/privacy", "/contact")
    return not any(item in path for item in skip)


def _extract_news_articles(soup, base_url: str) -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []
    seen: set[str] = set()
    candidates = soup.select("article a[href], .post a[href], .entry-title a[href], [class*='news'] a[href]")
    if not candidates:
        candidates = soup.select("a[href]")

    for link in candidates:
        href = link.get("href", "")
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        url = urljoin(base_url, href)
        if url in seen or not _looks_like_news_url(url):
            continue
        title = _article_title_from_link(link)
        if len(title) < 10:
            continue
        parent = link.find_parent()
        date_el = parent.select_one("time, .date, [class*='date']") if parent else None
        summary_el = parent.select_one("p, .excerpt, .summary, [class*='excerpt']") if parent else None
        articles.append(
            {
                "title": clean_text(title),
                "url": url,
                "date_text": (date_el.get("datetime", "") or date_el.get_text(" ", strip=True)) if date_el else "",
                "summary": clean_text(summary_el.get_text(" ", strip=True)) if summary_el else "",
            }
        )
        seen.add(url)

    logger.info("Parsed %d CRAA news candidates", len(articles))
    return articles


def _extract_power_rankings(soup, base_url: str) -> list[dict[str, str]]:
    rankings: list[dict[str, str]] = []
    for link in soup.select("a[href]"):
        text = clean_text(link.get_text(" ", strip=True))
        if not re.search(r"\b(power rankings?|rankings?)\b", text, re.IGNORECASE):
            continue
        href = link.get("href", "")
        if not href:
            continue
        url = urljoin(base_url, href)
        if not _looks_like_news_url(url):
            continue
        rankings.append({"title": text, "url": url, "date_text": "", "summary": "Latest CRAA D1A power rankings."})
    unique = {item["url"]: item for item in rankings}
    logger.info("Parsed %d CRAA power ranking links", len(unique))
    return list(unique.values())


def _nearby_date_text(element) -> str:
    for node in [element, *list(element.parents)[:4]]:
        date_el = node.select_one("time, .date, [class*='date']") if hasattr(node, "select_one") else None
        if date_el:
            return date_el.get("datetime", "") or date_el.get_text(" ", strip=True)

    previous = element.find_previous(["time", "h2", "h3", "h4", "strong", "p"])
    if previous:
        text = previous.get("datetime", "") or previous.get_text(" ", strip=True)
        if re.search(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|\d{1,2}/\d{1,2}/\d{2,4})", text):
            return text
    return ""


def _clean_team_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -–—\t\r\n")


def _parse_score_line(text: str) -> dict[str, Any] | None:
    value = clean_text(text)
    match = SCORE_RE.search(value)
    if not match:
        return None

    home_name = _clean_team_name(match.group("home"))
    away_name = _clean_team_name(match.group("away"))
    if not home_name or not away_name:
        return None

    return {
        "home_name": home_name,
        "away_name": away_name,
        "home_score": int(match.group("home_score")),
        "away_score": int(match.group("away_score")),
    }


def _extract_results(soup) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for element in soup.select("li, p, div, td"):
        text = element.get_text(" ", strip=True)
        parsed = _parse_score_line(text)
        if parsed is None:
            continue

        date_text = _nearby_date_text(element)
        match_date = _parse_date(date_text)
        if not match_date:
            logger.warning("Skipping CRAA result without parseable date: %s", text[:120])
            continue

        key = f"{parsed['home_name']}|{parsed['away_name']}|{match_date[:10]}"
        if key in seen:
            continue
        seen.add(key)
        parsed["match_date"] = match_date
        results.append(parsed)

    logger.info("Parsed %d CRAA postseason results", len(results))
    return results


def _ingest_article(api: FullpitchAPI, article: dict[str, str], client, summary: dict[str, Any]) -> None:
    try:
        html = fetch_text(article["url"])
    except ScraperError as exc:
        logger.warning("Failed to fetch CRAA article metadata from %s: %s", article["url"], exc)
        html = ""

    image_url = extract_og_image_from_html(html, article["url"]) if html else None
    published_date = (extract_publish_date(html) if html else None) or _parse_date(article.get("date_text"))
    article_summary = article.get("summary") or ""
    league = _classify_article(article["title"], article_summary, client)
    article_summary = _generate_summary(article["title"], article_summary, _domain(article["url"]), client)

    try:
        api.create_article(
            {
                "title": article["title"],
                "url": article["url"],
                "source": CRAA_SOURCE_NAME,
                "publishedDate": published_date,
                "league": league,
                "summary": article_summary,
                "content": article.get("summary") or None,
                "imageUrl": image_url,
                "agentName": "craa-agent",
                "tags": [league, "craa"] if league else ["craa"],
            }
        )
        summary["articles_written"] += 1
    except FullpitchAPIError as exc:
        msg = f"Failed to ingest CRAA article '{article['title'][:60]}': {exc}"
        logger.error(msg)
        summary["errors"].append(msg)


def _ingest_result(api: FullpitchAPI, result: dict[str, Any], season: str, summary: dict[str, Any]) -> None:
    home_team = api.get_team(name=result["home_name"])
    away_team = api.get_team(name=result["away_name"])
    if not home_team or not away_team:
        logger.warning(
            "Skipping CRAA result with unresolved teams: %s vs %s",
            result["home_name"],
            result["away_name"],
        )
        summary["matches_skipped"] += 1
        return

    try:
        api.upsert_match(
            {
                "homeTeamId": home_team["id"],
                "awayTeamId": away_team["id"],
                "homeScore": result["home_score"],
                "awayScore": result["away_score"],
                "matchDate": result["match_date"],
                "league": "craa-d1a",
                "season": season,
                "status": "final",
                "agentName": "craa-agent",
            }
        )
        summary["matches_written"] += 1
    except FullpitchAPIError as exc:
        msg = f"Failed to ingest CRAA result {result['home_name']} vs {result['away_name']}: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)


def run_craa_agent() -> dict[str, Any]:
    api = FullpitchAPI()
    client = _get_genai_client()
    season = _current_season()
    summary: dict[str, Any] = {
        "articles_found": 0,
        "articles_written": 0,
        "rankings_found": 0,
        "matches_found": 0,
        "matches_written": 0,
        "matches_skipped": 0,
        "errors": [],
    }

    existing_urls: set[str] = set()
    try:
        recent_articles = api.get_recent_articles(limit=100)
        existing_urls = {item.get("sourceUrl", item.get("url", "")) for item in recent_articles}
    except FullpitchAPIError as exc:
        logger.warning("Failed to fetch recent articles before CRAA ingest: %s", exc)

    try:
        news_soup = fetch_html(CRAA_NEWS_URL)
        articles = _extract_news_articles(news_soup, CRAA_NEWS_URL)
        summary["articles_found"] = len(articles)
        for article in articles:
            if article["url"] in existing_urls:
                continue
            _ingest_article(api, article, client, summary)
            existing_urls.add(article["url"])
    except ScraperError as exc:
        msg = f"Failed to fetch CRAA news: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)

    try:
        home_soup = fetch_html(CRAA_HOME_URL)
        rankings = _extract_power_rankings(home_soup, CRAA_HOME_URL)
        summary["rankings_found"] = len(rankings)
        for ranking in rankings:
            if ranking["url"] in existing_urls:
                continue
            ranking["summary"] = ranking.get("summary") or "Latest CRAA D1A power rankings."
            _ingest_article(api, ranking, client, summary)
            existing_urls.add(ranking["url"])
    except ScraperError as exc:
        msg = f"Failed to fetch CRAA homepage rankings: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)

    try:
        results_soup = fetch_html(CRAA_LINKHUB_URL)
        results = _extract_results(results_soup)
        summary["matches_found"] = len(results)
        for result in results:
            _ingest_result(api, result, season, summary)
    except ScraperError as exc:
        msg = f"Failed to fetch CRAA LinkHub results: {exc}"
        logger.error(msg)
        summary["errors"].append(msg)

    logger.info("CRAA agent summary: %s", summary)
    return summary


def run() -> None:
    run_craa_agent()
