"""Shared article URL and title filtering helpers for ingest agents."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from tools.text_utils import clean_text

ARTICLE_DATE_PATH_RE = re.compile(r"/(?:19|20)\d{2}/\d{1,2}/\d{1,2}/")
CATEGORY_PATH_PARTS = {
    "tag",
    "tags",
    "category",
    "categories",
    "author",
    "authors",
    "page",
    "search",
}
NON_ARTICLE_PATH_PARTS = {
    "about",
    "about-us",
    "contact",
    "privacy",
    "terms",
    "login",
    "sign-in",
    "signin",
    "sign-up",
    "signup",
    "subscribe",
    "shop",
    "cart",
    "account",
    "events",
    "calendar",
}
GENERIC_NAV_WORDS = {
    "about",
    "admin",
    "calendar",
    "club",
    "clubs",
    "committee",
    "community",
    "contact",
    "event",
    "events",
    "executive",
    "find",
    "high",
    "home",
    "men",
    "news",
    "post",
    "program",
    "programs",
    "rugby",
    "sanctioning",
    "school",
    "teams",
    "women",
    "youth",
}


def word_count(value: str | None) -> int:
    if not value:
        return 0
    return len(re.findall(r"\b[\w'-]+\b", value))


def is_category_or_tag_url(url: str) -> bool:
    parts = _path_parts(url)
    return any(part in CATEGORY_PATH_PARTS for part in parts)


def looks_like_article_url(url: str) -> bool:
    """Return True for real article URLs, not section/category/nav URLs."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    parts = [part for part in path.strip("/").split("/") if part]

    if not parts or is_category_or_tag_url(url):
        return False
    if any(part in NON_ARTICLE_PATH_PARTS for part in parts):
        return False
    if ARTICLE_DATE_PATH_RE.search(path):
        return True
    if len(parts) > 3 and _is_descriptive_slug(parts[-1]):
        return True

    return False


def is_generic_nav_title(title: str | None) -> bool:
    """Return True for short/generic nav labels likely scraped as links."""
    normalized = clean_text(title or "")
    if not normalized:
        return True

    words = re.findall(r"[a-z0-9]+", normalized.lower())
    if len(words) <= 1:
        return True
    if len(words) <= 3 and all(word in GENERIC_NAV_WORDS for word in words):
        return True

    return False


def is_viable_article_candidate(
    *,
    title: str | None,
    url: str,
    min_title_words: int = 5,
) -> bool:
    if word_count(title) < min_title_words:
        return False
    if is_generic_nav_title(title):
        return False
    return looks_like_article_url(url)


def has_minimum_content(content: str | None, min_chars: int = 100) -> bool:
    return len(clean_text(content or "")) >= min_chars


def _path_parts(url: str) -> list[str]:
    return [part for part in urlparse(url).path.lower().strip("/").split("/") if part]


def _is_descriptive_slug(value: str) -> bool:
    slug = value.strip().lower()
    if not slug or "." in slug:
        return False
    words = [part for part in re.split(r"[-_]+", slug) if part]
    if len(words) < 3:
        return False
    if all(word in GENERIC_NAV_WORDS for word in words):
        return False
    return True
