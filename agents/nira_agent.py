"""NIRA Agent — women's college rugby news and results ingest."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

from tools.article_filter import has_minimum_content, is_viable_article_candidate, looks_like_article_url
from tools.college_leagues import classify_college_league_with_gemini, decode_html
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
NIRA_HOME_URL = "https://nira.rugby"
NIRA_SCHEDULE_URL = "https://nira.rugby/schedule"
SCORE_RE = re.compile(r"(?P<home>.+?)\s+(?P<home_score>\d{1,3})\s*[–—-]\s*(?P<away_score>\d{1,3})\s+(?P<away>.+)")


def _get_genai_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    from google import genai

    return genai.Client(api_key=api_key)


def _sources(api: FullpitchAPI) -> tuple[str, str]:
    sources = [
        source
        for source in api.get_sources(league="college")
        if "nira" in source.get("name", "").lower()
    ]
    news_url = next((s["url"] for s in sources if s.get("type") == "news"), NIRA_HOME_URL)
    schedule_url = next((s["url"] for s in sources if s.get("type") == "data"), NIRA_SCHEDULE_URL)
    return news_url, schedule_url


def _domain(url: str) -> str:
    return urlparse(url).hostname or "nira.rugby"


def _is_relevant(title: str, text: str, client) -> bool:
    if client is None:
        return True
    try:
        response = client.models.generate_content(
            model=GEMINI_REASONING,
            contents=(
                "Is this article about NIRA, NCAA women's college rugby, women's college "
                "rugby teams, results, rankings, players, or postseason? "
                f"Title: {title}\nText: {text[:1000]}\nAnswer YES or NO only."
            ),
        )
        return response.text.strip().upper().startswith("YES")
    except Exception:
        logger.exception("NIRA relevance check failed for %s", title[:80])
        return False


def _extract_articles(soup, base_url: str) -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in soup.select("article a[href], [class*='news'] a[href], [class*='post'] a[href], a[href]"):
        href = link.get("href", "")
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        url = urljoin(base_url, href)
        if url in seen or not looks_like_article_url(url):
            continue
        title = decode_html(clean_text(link.get_text(" ", strip=True)))
        parent = link.find_parent()
        if not title and parent:
            heading = parent.select_one("h1, h2, h3, h4, .title, .headline")
            title = decode_html(clean_text(heading.get_text(" ", strip=True))) if heading else ""
        if not is_viable_article_candidate(title=title, url=url):
            continue
        seen.add(url)
        articles.append({"title": title, "url": url})
    return articles


def _ingest_article(api: FullpitchAPI, article: dict[str, str], client, summary: dict[str, Any]) -> None:
    try:
        html = fetch_text(article["url"])
    except ScraperError as exc:
        logger.warning("Failed to fetch NIRA article %s: %s", article["url"], exc)
        summary["articles_skipped"] += 1
        return

    text = extract_page_text_from_html(html)
    if not has_minimum_content(text, min_chars=100):
        summary["articles_skipped"] += 1
        return
    if not _is_relevant(article["title"], text, client):
        summary["articles_skipped"] += 1
        return

    image_url = extract_og_image_from_html(html, article["url"]) or extract_og_image(article["url"])
    title = decode_html(article["title"])
    league = classify_college_league_with_gemini(title, text, client, default="nira")
    summary_text = decode_html(gemini_summarize(article["url"], text))
    try:
        api.create_article(
            {
                "title": title,
                "url": article["url"],
                "source": _domain(article["url"]),
                "publishedDate": extract_publish_date(html),
                "league": league,
                "gender": "WOMENS",
                "summary": summary_text,
                "content": text[:2000],
                "imageUrl": image_url,
                "agentName": "nira-agent",
                "tags": ["college", "nira", "women", league],
            }
        )
        summary["articles_written"] += 1
    except FullpitchAPIError as exc:
        summary["errors"].append(f"Failed to ingest NIRA article {article['title'][:60]}: {exc}")


def _parse_date(text: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def _extract_results(soup) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for element in soup.select("li, p, div, td"):
        text = clean_text(element.get_text(" ", strip=True))
        match = SCORE_RE.search(text)
        if not match:
            continue
        date_text = ""
        date_el = element.find_previous(["time", "h2", "h3", "strong"])
        if date_el:
            date_text = date_el.get("datetime", "") or date_el.get_text(" ", strip=True)
        match_date = _parse_date(date_text)
        if not match_date:
            continue
        results.append(
            {
                "home_name": clean_text(match.group("home")),
                "away_name": clean_text(match.group("away")),
                "home_score": int(match.group("home_score")),
                "away_score": int(match.group("away_score")),
                "match_date": match_date,
            }
        )
    return results


def _ingest_result(api: FullpitchAPI, result: dict[str, Any], summary: dict[str, Any]) -> None:
    home_team = api.get_team(name=result["home_name"])
    away_team = api.get_team(name=result["away_name"])
    if not home_team or not away_team:
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
                "league": "college",
                "season": str(datetime.now(timezone.utc).year),
                "status": "final",
                "agentName": "nira-agent",
            }
        )
        summary["matches_written"] += 1
    except FullpitchAPIError as exc:
        summary["errors"].append(f"Failed to ingest NIRA result: {exc}")


def run_nira_agent() -> dict[str, Any]:
    api = FullpitchAPI()
    client = _get_genai_client()
    news_url, schedule_url = _sources(api)
    summary: dict[str, Any] = {
        "articles_found": 0,
        "articles_written": 0,
        "articles_skipped": 0,
        "matches_found": 0,
        "matches_written": 0,
        "matches_skipped": 0,
        "errors": [],
    }

    try:
        articles = _extract_articles(fetch_html(news_url), news_url)
        summary["articles_found"] = len(articles)
        for article in articles:
            _ingest_article(api, article, client, summary)
    except ScraperError as exc:
        summary["errors"].append(f"Failed to fetch NIRA news: {exc}")

    try:
        results = _extract_results(fetch_html(schedule_url))
        summary["matches_found"] = len(results)
        for result in results:
            _ingest_result(api, result, summary)
    except ScraperError as exc:
        summary["errors"].append(f"Failed to fetch NIRA schedule: {exc}")

    logger.info("NIRA agent summary: %s", summary)
    return summary


def run() -> None:
    run_nira_agent()
