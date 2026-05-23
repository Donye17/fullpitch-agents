"""Shared Gemini models, token limits, and batched relevance filters."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Cheap model — classification, relevance, structured extraction, summaries
GEMINI_FREE_TIER_MODEL = "gemini-2.5-flash-lite"

# High-quality model — match reports, spotlights, long-form content
GEMINI_WRITING_PRO = "gemini-2.5-flash"

MAX_OUTPUT_TOKENS_CLASSIFICATION = 256
MAX_OUTPUT_TOKENS_SUMMARY = 1024
MAX_OUTPUT_TOKENS_MATCH_REPORT = 4096


def generate_gemini_content(
    client,
    model: str,
    contents: Any,
    *,
    max_output_tokens: int | None = None,
) -> Any:
    """Call Gemini with an optional hard output token cap."""
    from google.genai import types

    config = None
    if max_output_tokens is not None:
        config = types.GenerateContentConfig(max_output_tokens=max_output_tokens)

    return client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )


def parse_relevant_numbers(value: str, count: int) -> set[int]:
    numbers = {int(match.group(0)) for match in re.finditer(r"\b\d+\b", value or "")}
    return {number for number in numbers if 1 <= number <= count}


def batch_relevance_check(
    articles: list[dict[str, Any]],
    client,
    *,
    prompt_intro: str = "You are filtering US rugby news articles.",
    relevant_description: str = "relevant to US rugby",
    default_if_no_client: bool = False,
) -> set[int]:
    """Return zero-based indexes of relevant articles using one Gemini call."""
    if not articles:
        return set()
    if client is None:
        logger.warning("No Gemini client - batch relevance default=%s for %d articles", default_if_no_client, len(articles))
        return set(range(len(articles))) if default_if_no_client else set()

    titles = [f"{i + 1}. {article.get('title', '').strip()}" for i, article in enumerate(articles)]
    prompt = (
        f"{prompt_intro}\n"
        "Return ONLY a comma-separated list of numbers for articles that are "
        f"{relevant_description}.\n\n"
        "Articles:\n"
        f"{chr(10).join(titles)}\n\n"
        "Reply with only numbers like: 1,3,5,7"
    )

    try:
        response = generate_gemini_content(
            client,
            GEMINI_FREE_TIER_MODEL,
            prompt,
            max_output_tokens=MAX_OUTPUT_TOKENS_CLASSIFICATION,
        )
        relevant_numbers = parse_relevant_numbers(response.text, len(articles))
        logger.info(
            "Batch relevance selected %d/%d articles: %s",
            len(relevant_numbers),
            len(articles),
            ",".join(str(number) for number in sorted(relevant_numbers)),
        )
        return {number - 1 for number in relevant_numbers}
    except Exception:
        logger.exception("Batch relevance check failed for %d articles", len(articles))
        return set()
