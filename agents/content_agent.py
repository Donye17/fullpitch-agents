"""Content Agent — AI-written match reports, recaps, and spotlights.

Schedule: Triggered by other agents after data ingest.
Models: gemini-2.5-flash for match reports; gemini-2.5-flash-lite for summaries.
Writes to: /api/v1/ingest/article
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.gemini_relevance import (
    GEMINI_FREE_TIER_MODEL,
    GEMINI_WRITING_PRO,
    MAX_OUTPUT_TOKENS_MATCH_REPORT,
    MAX_OUTPUT_TOKENS_SUMMARY,
    generate_gemini_content,
)
from tools.text_utils import clean_text

logger = logging.getLogger(__name__)

GEMINI_WRITING_MID = GEMINI_FREE_TIER_MODEL

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

PROMPTS = {
    "match_report_mlr": "match_report_mlr.txt",
    "match_report_college": "match_report_college.txt",
    "article_summary": "article_summary.txt",
    "standings_recap": "standings_recap.txt",
    "player_spotlight": "player_spotlight.txt",
}

LONG_FORM_PROMPTS = {
    "match_report_mlr",
    "match_report_college",
    "standings_recap",
    "player_spotlight",
}


def load_prompt(name: str) -> str:
    filename = PROMPTS.get(name)
    if not filename:
        raise ValueError(f"Unknown prompt: {name}")
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


def _get_genai_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    from google import genai
    return genai.Client(api_key=api_key)


def generate_content(prompt_name: str, variables: dict[str, str], model: str | None = None) -> str | None:
    """Load a prompt template, fill variables, generate via Gemini, and clean."""
    client = _get_genai_client()
    if client is None:
        logger.warning("No Gemini client — cannot generate content")
        return None

    template = load_prompt(prompt_name)
    for key, value in variables.items():
        template = template.replace(f"{{{key}}}", value)

    use_model = model or (GEMINI_WRITING_PRO if prompt_name in LONG_FORM_PROMPTS else GEMINI_WRITING_MID)
    max_tokens = (
        MAX_OUTPUT_TOKENS_MATCH_REPORT
        if prompt_name in {"match_report_mlr", "match_report_college"}
        else MAX_OUTPUT_TOKENS_SUMMARY
    )
    try:
        resp = generate_gemini_content(
            client,
            use_model,
            template,
            max_output_tokens=max_tokens,
        )
        return clean_text(resp.text)
    except Exception:
        logger.exception("Content generation failed for prompt '%s'", prompt_name)
        return None


def run() -> None:
    logger.info("Content agent started")
    api = FullpitchAPI()

    try:
        matches = api.get_matches(status=["final", "completed"], limit=100)
    except FullpitchAPIError as exc:
        logger.warning("Content agent could not list completed matches: %s", exc)
        matches = []

    for match in matches:
        if match.get("hasReport") or match.get("summaryArticleId"):
            continue
        # TODO: generate report when pipeline is wired to generate_content()

    logger.info("Content agent complete")
