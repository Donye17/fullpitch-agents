"""Playwright screenshot + Gemini Vision live score extraction."""

from __future__ import annotations

import ast
import json
import logging
import os
import re
from typing import Any

from playwright.sync_api import sync_playwright

from tools.gemini_relevance import (
    MAX_OUTPUT_TOKENS_CLASSIFICATION,
    generate_gemini_content,
)

logger = logging.getLogger(__name__)

GEMINI_VISION_MODEL = "gemini-2.5-flash-lite"

VISION_PROMPT = """You are reading a live rugby match scoreboard.
Extract the current score and match status from this screenshot. Return JSON only, no other text:
{
  'home_team': 'team name as shown',
  'away_team': 'team name as shown',
  'home_score': 0,
  'away_score': 0,
  'period': '1H|HT|2H|FT|AET',
  'minute': 52
}
If the match has not started or scores are not visible, return: {'status': 'not_available'}"""

PERIOD_TO_STATUS = {
    "1H": "live",
    "2H": "live",
    "HT": "live",
    "FT": "final",
    "AET": "final",
}


def _get_genai_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("GOOGLE_API_KEY not set — screenshot score extraction disabled")
        return None
    from google import genai

    return genai.Client(api_key=api_key)


def _capture_screenshot(url: str, timeout: int) -> bytes | None:
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, timeout=timeout, wait_until="networkidle")
            for selector in (
                "[data-testid*='score']",
                ".score",
                ".match-score",
                ".scores",
                "main",
            ):
                try:
                    page.wait_for_selector(selector, timeout=3000)
                    break
                except Exception:
                    continue
            screenshot = page.screenshot(full_page=True, type="png")
            browser.close()
            return screenshot
    except Exception:
        logger.exception("Screenshot capture failed for %s", url)
        return None


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return str(text).strip()
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        chunks: list[str] = []
        for part in parts:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(str(part_text))
        if chunks:
            return "\n".join(chunks).strip()
    return ""


def _parse_json_payload(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    for parser in (json.loads, ast.literal_eval):
        try:
            payload = parser(cleaned)
            if isinstance(payload, dict):
                return payload
        except (json.JSONDecodeError, SyntaxError, ValueError):
            continue
    return None


def _normalize_result(payload: dict[str, Any]) -> dict[str, Any] | None:
    if str(payload.get("status") or "").lower() == "not_available":
        return None

    period = str(payload.get("period") or "").upper().strip()
    status = PERIOD_TO_STATUS.get(period)
    if not status:
        return None

    try:
        home_score = int(payload.get("home_score"))
        away_score = int(payload.get("away_score"))
    except (TypeError, ValueError):
        return None

    minute_raw = payload.get("minute")
    minute: int | None
    try:
        minute = int(minute_raw) if minute_raw is not None else None
    except (TypeError, ValueError):
        minute = None

    return {
        "home_score": home_score,
        "away_score": away_score,
        "status": status,
        "period": period,
        "minute": minute,
    }


def fetch_live_score_via_screenshot(url: str, timeout: int = 30000) -> dict[str, Any] | None:
    """Capture a match page screenshot and extract live score JSON via Gemini Vision."""
    try:
        screenshot = _capture_screenshot(url, timeout)
        if not screenshot:
            return None

        client = _get_genai_client()
        if client is None:
            return None

        from google.genai import types

        response = generate_gemini_content(
            client,
            GEMINI_VISION_MODEL,
            [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=screenshot, mime_type="image/png"),
                        types.Part.from_text(text=VISION_PROMPT),
                    ],
                )
            ],
            max_output_tokens=MAX_OUTPUT_TOKENS_CLASSIFICATION,
        )
        raw_text = _extract_response_text(response)
        if not raw_text:
            logger.warning("Gemini Vision returned empty response for %s", url)
            return None

        payload = _parse_json_payload(raw_text)
        if payload is None:
            logger.warning("Could not parse Gemini Vision JSON for %s: %r", url, raw_text[:300])
            return None

        return _normalize_result(payload)
    except Exception:
        logger.exception("Screenshot live score extraction failed for %s", url)
        return None
