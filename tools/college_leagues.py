"""College rugby league tag helpers."""

from __future__ import annotations

import html
import re

from tools.gemini_relevance import GEMINI_FREE_TIER_MODEL

VALID_COLLEGE_LEAGUES = {
    "craa-d1a",
    "craa-d1aa",
    "craa-women",
    "ncr-d1",
    "ncr-d2",
    "ncr-d3",
    "ncr-women",
    "nira",
    "college",
}


def decode_html(value: str | None) -> str:
    return " ".join(html.unescape(value or "").split())


def classify_college_league(title: str, content: str = "", default: str = "college") -> str:
    text = f"{title} {content}".lower()

    if "nira" in text:
        return "nira"

    if "craa" in text:
        if re.search(r"\bd1[\s-]*aa\b|division\s+i[\s-]*aa", text):
            return "craa-d1aa"
        if re.search(r"\bd1[\s-]*a\b|division\s+i[\s-]*a", text):
            return "craa-d1a"
        if re.search(r"\bwomen'?s?\b|\bwoman\b|\bfemale\b", text):
            return "craa-women"

    if "ncr" in text or "national collegiate rugby" in text:
        if re.search(r"\bwomen'?s?\b|\bwoman\b|\bfemale\b", text):
            return "ncr-women"
        if re.search(r"\bdivision\s*iii\b|\bdivision\s*3\b|\bd[\s-]*iii\b|\bd[\s-]*3\b", text):
            return "ncr-d3"
        if re.search(r"\bdivision\s*ii\b|\bdivision\s*2\b|\bd[\s-]*ii\b|\bd[\s-]*2\b", text):
            return "ncr-d2"
        if re.search(r"\bdivision\s*i\b|\bdivision\s*1\b|\bd[\s-]*i\b|\bd[\s-]*1\b", text):
            return "ncr-d1"
        return "college"

    if re.search(r"\bd1[\s-]*aa\b", text):
        return "craa-d1aa"
    if re.search(r"\bd1[\s-]*a\b", text):
        return "craa-d1a"

    if default == "":
        return ""
    return default if default in VALID_COLLEGE_LEAGUES else "college"


def classify_college_league_with_gemini(
    title: str,
    content: str,
    client,
    default: str = "college",
) -> str:
    deterministic = classify_college_league(title, content, default="")
    if deterministic:
        return deterministic

    if client is None:
        return default if default in VALID_COLLEGE_LEAGUES else "college"

    try:
        response = client.models.generate_content(
            model=GEMINI_FREE_TIER_MODEL,
            contents=(
                "Classify this US college rugby content into exactly one league tag.\n\n"
                "Valid tags:\n"
                "- craa-d1a: CRAA Men's D1A. Use for D1A or D1-A.\n"
                "- craa-d1aa: CRAA Men's D1AA. Use for D1AA or D1-AA.\n"
                "- craa-women: CRAA Women's.\n"
                "- ncr-d1: National Collegiate Rugby Men's Division I.\n"
                "- ncr-d2: National Collegiate Rugby Men's Division II.\n"
                "- ncr-d3: National Collegiate Rugby Men's Division III.\n"
                "- ncr-women: National Collegiate Rugby Women's.\n"
                "- nira: NIRA Women's.\n"
                "- college: general college rugby, or unclear division.\n\n"
                "Use the most specific tag possible from title/content. "
                "Reply with ONLY one tag.\n\n"
                f"Title: {title}\n"
                f"Content: {content[:1600]}"
            ),
        )
        league = response.text.strip().lower()
        return league if league in VALID_COLLEGE_LEAGUES else default
    except Exception:
        return default if default in VALID_COLLEGE_LEAGUES else "college"
