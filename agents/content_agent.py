"""Content Agent — AI-written match reports, recaps, and spotlights.

Schedule: Triggered by other agents after data ingest.
Models: gemini-2.5-pro (match reports, spotlights), gemini-2.5-flash (summaries, recaps).
Writes to: /api/v1/ingest/article
"""

import logging

logger = logging.getLogger(__name__)

PROMPT_DIR = "prompts"

PROMPTS = {
    "match_report_mlr": f"{PROMPT_DIR}/match_report_mlr.txt",
    "match_report_college": f"{PROMPT_DIR}/match_report_college.txt",
    "article_summary": f"{PROMPT_DIR}/article_summary.txt",
    "standings_recap": f"{PROMPT_DIR}/standings_recap.txt",
    "player_spotlight": f"{PROMPT_DIR}/player_spotlight.txt",
}


def load_prompt(name: str) -> str:
    path = PROMPTS.get(name)
    if not path:
        raise ValueError(f"Unknown prompt: {name}")
    with open(path) as f:
        return f.read()


def run() -> None:
    logger.info("Content agent started")
    # TODO: implement
    #   1. Query for matches that need reports (completed, no article)
    #   2. Load appropriate prompt template
    #   3. Generate content with Gemini (pro for MLR, flash for college)
    #   4. Submit as draft article via /api/v1/ingest/article
    #   5. Log run to AgentLog
    logger.info("Content agent complete")
