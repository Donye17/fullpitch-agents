"""Video Agent — YouTube content discovery for US rugby.

Schedule: Daily at 6am UTC.
Sources: YouTube Data API v3 (preferred), HTML scraping fallback.
Writes to: /api/v1/ingest/video
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import quote_plus

import httpx

from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.scraper import ScraperError, fetch_html

logger = logging.getLogger(__name__)

GEMINI_REASONING = "gemini-2.5-flash-lite"

MAX_AGE_DAYS = 30
QUERY_DELAY_SECONDS = 0.5

SEARCH_QUERIES = [
    "MLR Major League Rugby highlights 2026",
    "USA Eagles rugby 2026",
    "college rugby highlights 2026",
    "US club rugby 2026",
    "Major League Rugby 2026",
]

MLR_TEAM_KEYWORDS = [
    "chicago hounds", "dallas jackals", "houston sabercats",
    "miami sharks", "new england free jacks", "new orleans gold",
    "nola gold", "old glory dc", "portland breakers", "rugby atl",
    "rugby fc la", "san diego legion", "seattle seawolves",
    "utah warriors", "anthem rc", "rugby new york",
]

YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/search"


def _get_genai_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    from google import genai
    return genai.Client(api_key=api_key)


def _cutoff_date() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)


def _parse_iso_date(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# ── YouTube Data API v3 ──────────────────────────────────────────────────────


def _search_youtube_api(query: str, api_key: str) -> list[dict[str, Any]]:
    """Search YouTube via the Data API v3."""
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": 10,
        "order": "date",
        "key": api_key,
    }
    try:
        resp = httpx.get(YOUTUBE_API_URL, params=params, timeout=15)
        if resp.status_code != 200:
            logger.error("YouTube API returned %d: %s", resp.status_code, resp.text[:300])
            return []
        data = resp.json()
    except Exception:
        logger.exception("YouTube API request failed for query '%s'", query)
        return []

    videos: list[dict[str, Any]] = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        vid_id = item.get("id", {}).get("videoId")
        if not vid_id:
            continue
        videos.append({
            "videoId": vid_id,
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "channelName": snippet.get("channelTitle", ""),
            "channelId": snippet.get("channelId", ""),
            "publishedAt": snippet.get("publishedAt", ""),
            "thumbnailUrl": snippet.get("thumbnails", {}).get("high", {}).get("url")
                or snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
        })
    return videos


# ── HTML scraping fallback ────────────────────────────────────────────────────


def _search_youtube_scrape(query: str) -> list[dict[str, Any]]:
    """Fallback: scrape youtube.com/results for video cards."""
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    try:
        soup = fetch_html(url, timeout=20)
    except ScraperError as exc:
        logger.error("YouTube scrape failed for '%s': %s", query, exc)
        return []

    videos: list[dict[str, Any]] = []

    for script in soup.select("script"):
        text = script.string or ""
        if "var ytInitialData" not in text:
            continue

        for match in re.finditer(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"', text):
            vid_id = match.group(1)
            if any(v["videoId"] == vid_id for v in videos):
                continue

            title_match = re.search(
                rf'"videoId"\s*:\s*"{vid_id}".*?"title"\s*:\s*\{{"runs"\s*:\s*\[\{{"text"\s*:\s*"([^"]+)"',
                text,
            )
            title = title_match.group(1) if title_match else ""

            videos.append({
                "videoId": vid_id,
                "title": title,
                "description": "",
                "channelName": "",
                "channelId": "",
                "publishedAt": "",
                "thumbnailUrl": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
            })

            if len(videos) >= 10:
                break
        break

    logger.info("Scraped %d videos from YouTube for '%s'", len(videos), query)
    return videos


# ── Classification ────────────────────────────────────────────────────────────


def _is_us_rugby_video(title: str, channel: str, client) -> bool:
    """Use Gemini to check if a video is about US rugby."""
    if client is None:
        logger.warning("No Gemini client, cannot filter video '%s'", title[:60])
        return False
    try:
        resp = client.models.generate_content(
            model=GEMINI_REASONING,
            contents=(
                "Is this YouTube video about US rugby, MLR, USA Eagles, "
                "college rugby, or US club rugby? "
                f"Title: '{title}'. Channel: '{channel}'. "
                "Answer YES or NO only."
            ),
        )
        answer = resp.text.strip().upper()
        is_relevant = answer.startswith("YES")
        logger.info("Video relevance: '%s' → %s", title[:60], "YES" if is_relevant else "NO")
        return is_relevant
    except Exception:
        logger.exception("Gemini video classification failed for '%s'", title[:60])
        return False


def _classify_league(title: str, channel: str) -> str:
    """Determine league tag from title and channel name."""
    combined = f"{title} {channel}".lower()

    if any(kw in combined for kw in ("eagles", "usa rugby", "usa men", "usa women", "test match")):
        return "eagles"

    if any(kw in combined for kw in ("college", "collegiate", "craa", "ncr", "university", "d1a")):
        return "craa-d1a"

    if any(kw in combined for kw in ("club rugby", "us club", "usa club")):
        return "club"

    if any(kw in combined for kw in MLR_TEAM_KEYWORDS):
        return "mlr"
    if any(kw in combined for kw in ("mlr", "major league rugby")):
        return "mlr"

    return "mlr"


# ── Core logic ────────────────────────────────────────────────────────────────


def run_video_agent() -> dict[str, Any]:
    """Run the video agent: search YouTube, filter, classify, ingest."""
    api = FullpitchAPI()
    genai_client = _get_genai_client()
    yt_api_key = os.getenv("YOUTUBE_DATA_API_KEY", "")
    cutoff = _cutoff_date()

    summary: dict[str, Any] = {
        "queries_run": 0,
        "videos_found": 0,
        "skipped_duplicate": 0,
        "skipped_irrelevant": 0,
        "skipped_old": 0,
        "written": 0,
        "errors": [],
    }

    existing_video_ids: set[str] = set()
    try:
        recent = api.get_recent_videos(limit=100)
        existing_video_ids = {v.get("videoId", "") for v in recent}
    except FullpitchAPIError as exc:
        logger.warning("Failed to fetch existing videos: %s", exc)

    use_api = bool(yt_api_key)
    logger.info("YouTube mode: %s", "Data API v3" if use_api else "HTML scrape fallback")

    all_videos: list[dict[str, Any]] = []

    for query in SEARCH_QUERIES:
        logger.info("Searching YouTube: '%s'", query)
        if use_api:
            results = _search_youtube_api(query, yt_api_key)
        else:
            results = _search_youtube_scrape(query)

        summary["queries_run"] += 1
        summary["videos_found"] += len(results)
        all_videos.extend(results)

        time.sleep(QUERY_DELAY_SECONDS)

    seen_ids: set[str] = set()
    for video in all_videos:
        vid_id = video.get("videoId", "")
        if not vid_id or vid_id in seen_ids:
            continue
        seen_ids.add(vid_id)

        if vid_id in existing_video_ids:
            summary["skipped_duplicate"] += 1
            continue

        pub_date = _parse_iso_date(video.get("publishedAt", ""))
        if pub_date and pub_date < cutoff:
            summary["skipped_old"] += 1
            continue

        title = video.get("title", "")
        channel = video.get("channelName", "")

        if not _is_us_rugby_video(title, channel, genai_client):
            summary["skipped_irrelevant"] += 1
            continue

        league = _classify_league(title, channel)
        published_date = (pub_date or datetime.now(timezone.utc)).isoformat()

        try:
            api.create_video({
                "videoId": vid_id,
                "title": title,
                "description": video.get("description", "")[:500],
                "thumbnailUrl": video.get("thumbnailUrl", ""),
                "channelName": channel,
                "channelId": video.get("channelId", ""),
                "publishedDate": published_date,
                "league": league,
                "agentName": "video-agent",
                "tags": [league],
            })
            existing_video_ids.add(vid_id)
            summary["written"] += 1
        except FullpitchAPIError as exc:
            msg = f"Failed to ingest video '{title[:60]}': {exc}"
            logger.error(msg)
            summary["errors"].append(msg)

    logger.info(
        "Video agent summary: queries=%d found=%d written=%d dup=%d old=%d irrelevant=%d errors=%d",
        summary["queries_run"],
        summary["videos_found"],
        summary["written"],
        summary["skipped_duplicate"],
        summary["skipped_old"],
        summary["skipped_irrelevant"],
        len(summary["errors"]),
    )
    return summary


def run() -> None:
    """Entry point called by main.py."""
    run_video_agent()
