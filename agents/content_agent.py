"""Content Agent — AI-written match reports, recaps, and spotlights.

Schedule: Triggered by other agents after data ingest.
Models: gemini-2.5-flash for the available Gemini account model.
Writes to: /api/v1/ingest/article
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from tools.fullpitch_api import FullpitchAPI, FullpitchAPIError
from tools.gemini_relevance import GEMINI_FREE_TIER_MODEL
from tools.text_utils import clean_text

logger = logging.getLogger(__name__)

GEMINI_WRITING_MID = GEMINI_FREE_TIER_MODEL
GEMINI_WRITING_PRO = GEMINI_FREE_TIER_MODEL

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

PROMPTS = {
    "match_report_mlr": "match_report_mlr.txt",
    "match_report_college": "match_report_college.txt",
    "article_summary": "article_summary.txt",
    "standings_recap": "standings_recap.txt",
    "player_spotlight": "player_spotlight.txt",
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

    use_model = model or GEMINI_WRITING_PRO
    try:
        resp = client.models.generate_content(model=use_model, contents=template)
        return clean_text(resp.text)
    except Exception:
        logger.exception("Content generation failed for prompt '%s'", prompt_name)
        return None


def run() -> None:
    logger.info("Content agent started")
    api = FullpitchAPI()

    # TODO: implement full pipeline
    #   1. Query for matches that need reports (completed, no article)
    #   2. Load appropriate prompt template
    #   3. Generate content with the configured Gemini model
    #   4. Pipe output through clean_text()
    #   5. Submit as draft article via /api/v1/ingest/article
    #   6. Log run to AgentLog
    logger.info("Content agent complete")
