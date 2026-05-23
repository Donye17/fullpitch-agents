"""Generate and publish Fullpitch original match reports."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from tools.editorial_ai import normalize_feed_summary
from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.gemini_relevance import (
    GEMINI_WRITING_PRO,
    MAX_OUTPUT_TOKENS_MATCH_REPORT,
    generate_gemini_content,
)
from tools.scraper import ScraperError, extract_og_image_from_html, fetch_text
from tools.text_utils import clean_text

logger = logging.getLogger(__name__)

MLR_MATCH_BASE_URL = "https://www.majorleague.rugby/matches"
FULLPITCH_REPORT_BASE_URL = "https://fullpitch.app/news/match-report"

TEAM_SHORT_NAMES = {
    "New England Free Jacks": "Free Jacks",
    "California Legion": "Legion",
    "Chicago Hounds": "Hounds",
    "Seattle Seawolves": "Seawolves",
    "Old Glory DC": "Old Glory",
    "Anthem RC": "Anthem",
    "Bay Breakers": "Breakers",
    "Boston Banshees": "Banshees",
    "Chicago Tempest": "Tempest",
    "Denver Onyx": "Onyx",
    "New York Exiles": "Exiles",
    "TC Gemini": "Gemini",
}


def _short_team_name(name: str) -> str:
    return TEAM_SHORT_NAMES.get(name, name)


def _build_match_report_title(
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    week: int | None,
) -> str:
    home = _short_team_name(home_team)
    away = _short_team_name(away_team)
    week_part = f"Week {week} " if week else ""
    return f"{home} {home_score}-{away_score} {away} | {week_part}Match Report"


def _get_genai_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None

    from google import genai

    return genai.Client(api_key=api_key)


def _slugify(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _match_slug_from_url_or_slug(match_slug: str) -> str:
    if match_slug.startswith("http://") or match_slug.startswith("https://"):
        parsed = urlparse(match_slug)
        parts = [part for part in parsed.path.split("/") if part]
        return parts[-1] if parts else ""
    return match_slug.strip("/ ")


def _summary_url(match_slug: str) -> str:
    if match_slug.startswith("http://") or match_slug.startswith("https://"):
        return f"{match_slug.split('?')[0]}?tab=summary"
    return f"{MLR_MATCH_BASE_URL}/{_match_slug_from_url_or_slug(match_slug)}?tab=summary"


def _report_source_url(match_slug: str, league: str) -> str:
    slug = _match_slug_from_url_or_slug(match_slug)
    return f"{FULLPITCH_REPORT_BASE_URL}/{league}/{slug}"


def _page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("script, style, noscript, nav, footer, header"):
        element.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def _summary_lines(page_text: str) -> list[str]:
    tokens = (
        "try",
        "conversion",
        "penalty",
        "yellow",
        "red card",
        "possession",
        "territory",
        "carries",
        "metres",
        "meters",
        "tackles",
        "scrum",
        "lineout",
        "player of the match",
    )
    chunks = [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+|\s{2,}", page_text) if chunk.strip()]
    matches = [chunk for chunk in chunks if any(token in chunk.lower() for token in tokens)]
    return matches[:28]


def _format_manual_data(manual_data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, value in manual_data.items():
        if value is None or value == "":
            continue
        label = " ".join(str(key).replace("_", " ").split()).title()
        lines.append(f"{label}: {value}")
    return lines


def _match_data_string(
    *,
    match_slug: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    league: str,
    page_html: str | None = None,
    manual_data: dict[str, Any] | None = None,
    week: int | None = None,
    venue: str | None = None,
) -> str:
    lines = [
        f"Final score: {home_team} {home_score} - {away_score} {away_team}",
        f"Venue: {venue or (manual_data or {}).get('venue') or 'Not listed'}",
        f"Competition: {league.upper()}{f' Week {week}' if week else ''}",
    ]

    if manual_data:
        lines.extend(_format_manual_data(manual_data))

    if page_html:
        extracted = _summary_lines(_page_text(page_html))
        if extracted:
            lines.append("Extracted summary-page facts:")
            lines.extend(extracted)

    if len(lines) <= 3:
        lines.append(f"Match slug: {_match_slug_from_url_or_slug(match_slug)}")

    return "\n".join(lines)


def _prompt(match_data: str) -> str:
    return f"""
Write a professional 300-350 word match report for Fullpitch, the US rugby news platform.

Style: Clean editorial sports journalism. Think The Athletic or BBC Sport.

Match data:
{match_data}

Structure:
- Opening sentence with result and significance
- First half narrative covering early scoring
- Second half narrative and key momentum shifts
- Mention yellow card discipline issues if provided
- Mention kicking contributions if provided
- Mention late tries or closing pressure if provided
- Closing line on what this means for the winner's season

DO NOT invent any statistics not provided.
DO NOT use bullet points.
Write in flowing paragraphs only.
""".strip()


def _call_gemini(prompt: str) -> str | None:
    client = _get_genai_client()
    if client is None:
        logger.warning("GOOGLE_API_KEY not set - using deterministic match report fallback")
        return None

    try:
        response = generate_gemini_content(
            client,
            GEMINI_WRITING_PRO,
            prompt,
            max_output_tokens=MAX_OUTPUT_TOKENS_MATCH_REPORT,
        )
        return clean_text(response.text)
    except Exception:
        logger.exception("Gemini match report generation failed")
        return None


def _fallback_report(
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    match_data: str,
) -> str:
    winner = home_team if home_score >= away_score else away_team
    loser = away_team if home_score >= away_score else home_team
    return clean_text(
        f"{winner} held off {loser} {home_score}-{away_score} in Major League Rugby action, "
        "closing out a tight contest built on early scoring, disciplined game management, and enough "
        "late defensive resistance to protect the result.\n\n"
        "The Fullpitch report was generated from structured match data pulled from the Major League Rugby "
        f"summary page. Key source details included: {match_data}"
    )


def generate_match_report(
    match_slug: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    league: str,
    *,
    api: FullpitchAPI | None = None,
    page_html: str | None = None,
    manual_data: dict[str, Any] | None = None,
    week: int | None = None,
    venue: str | None = None,
    match_id: str | None = None,
) -> dict[str, Any] | None:
    """Generate and publish one Fullpitch original report for a completed match."""
    api = api or FullpitchAPI()
    source_url = _report_source_url(match_slug, league)
    existing = api.get_article_by_source_url(source_url)
    if existing:
        logger.info("Match report already exists: %s", source_url)
        return existing

    summary_url = _summary_url(match_slug)
    if page_html is None:
        try:
            page_html = fetch_text(summary_url, timeout=20.0)
        except ScraperError as exc:
            logger.warning("Failed to fetch MLR summary page %s: %s", summary_url, exc)
            page_html = ""

    image_url = extract_og_image_from_html(page_html, summary_url) if page_html else None
    match_data = _match_data_string(
        match_slug=match_slug,
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
        league=league,
        page_html=page_html,
        manual_data=manual_data,
        week=week,
        venue=venue,
    )
    generated = _call_gemini(_prompt(match_data))
    report = generated or _fallback_report(home_team, away_team, home_score, away_score, match_data)
    title = _build_match_report_title(home_team, away_team, home_score, away_score, week)

    try:
        response = api.create_article(
            {
                "title": title,
                "url": source_url,
                "source": "Fullpitch",
                "publishedDate": datetime.now(timezone.utc).isoformat(),
                "league": league,
                "summary": normalize_feed_summary(report),
                "content": report,
                "imageUrl": image_url,
                "agentName": "match-report-generator",
                "tags": [league, "match-report", "fullpitch-original"],
                "isMatchReport": True,
            }
        )
    except FullpitchAPIError:
        logger.exception("Failed to publish match report: %s", title)
        return None

    article = response.get("data") if isinstance(response, dict) else None
    article_id = article.get("id") if isinstance(article, dict) else None
    if match_id and article_id:
        try:
            api.update_match(match_id, {"summaryArticleId": article_id})
        except FullpitchAPIError:
            logger.exception("Failed to attach match report %s to match %s", article_id, match_id)

    logger.info("Match report published: %s", title)
    return article if isinstance(article, dict) else response
