"""Shared batched Gemini relevance filters."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

GEMINI_FREE_TIER_MODEL = "gemini-2.5-flash"


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
        response = client.models.generate_content(
            model=GEMINI_FREE_TIER_MODEL,
            contents=prompt,
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
