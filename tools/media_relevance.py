"""Lightweight local relevance helpers for media candidates."""

from __future__ import annotations

import re
import unicodedata


def normalize_match_text(text: str | None) -> str:
    """Normalize text for simple, punctuation-insensitive substring matching."""
    value = unicodedata.normalize("NFKC", str(text or "")).casefold().strip()
    value = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in value
    )
    return re.sub(r"\s+", " ", value).strip()


def _normalized_terms(terms: list[str]) -> list[str]:
    return list(dict.fromkeys(
        normalized
        for term in terms
        if (normalized := normalize_match_text(term))
    ))


def is_media_candidate_relevant(
    title: str,
    snippet: str | None,
    newsnow_rss_core: list[str],
    newsnow_rss_support: list[str],
    provider: str,
) -> bool:
    """Return whether a NewsNow or RSS candidate merits Stage 1 persistence."""
    normalized_title = normalize_match_text(title)
    normalized_core_terms = _normalized_terms(newsnow_rss_core)
    normalized_support_terms = _normalized_terms(newsnow_rss_support)

    if any(term in normalized_title for term in normalized_core_terms):
        return True
    support_hit_count = sum(
        1 for term in normalized_support_terms if term in normalized_title
    )
    if support_hit_count >= 2:
        return True
    if provider.casefold() == "rss":
        normalized_snippet = normalize_match_text(snippet)
        return any(term in normalized_snippet for term in normalized_core_terms)
    return False


def is_weibo_candidate_relevant(
    content: str,
    core_terms: list[str],
    support_terms: list[str],
) -> bool:
    """Recall-first fallback used only when Weibo LLM review fails."""
    normalized_content = normalize_match_text(content)
    core = _normalized_terms(core_terms)
    support = _normalized_terms(support_terms)
    if any(term in normalized_content for term in core):
        return True
    return sum(1 for term in support if term in normalized_content) >= 2
