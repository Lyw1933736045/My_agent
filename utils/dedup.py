"""媒体候选的轻量规范化、相关性匹配与去重。"""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..tools.media_models import MediaCandidate


_TRACKING_PARAMS = {
    "from", "source", "spm", "ref", "refer", "utm_campaign", "utm_content",
    "utm_medium", "utm_source", "utm_term",
}
_TITLE_NOISE = re.compile(r"[\W_]+", re.UNICODE)


def canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.port and parsed.port not in {80, 443}:
        host = f"{host}:{parsed.port}"
    query = urlencode([
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS and not key.lower().startswith("utm_")
    ])
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))


def normalized_title(title: str) -> str:
    return _TITLE_NOISE.sub("", title.casefold())


def match_query(candidate: MediaCandidate, queries: list[str]) -> tuple[int, str | None]:
    text = f"{candidate.title} {candidate.snippet}".casefold()
    best_score = 0
    best_query = candidate.query
    for query in queries:
        normalized = " ".join(query.casefold().split())
        terms = [term for term in normalized.split() if term]
        score = (10 if normalized and normalized in text else 0) + sum(
            3 for term in terms if term in text
        )
        if score > best_score:
            best_score = score
            best_query = query
    return best_score, best_query


def select_candidates(
    candidates: list[MediaCandidate],
    queries: list[str],
    *,
    limit: int,
    max_per_source: int,
) -> tuple[list[MediaCandidate], dict[str, int]]:
    """合并多路重复候选，再按相关性和来源限额选择。"""
    by_url: dict[str, MediaCandidate] = {}
    url_duplicates = 0
    for candidate in candidates:
        key = canonical_url(candidate.url)
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = replace(candidate, url=key)
            continue
        url_duplicates += 1
        methods = tuple(dict.fromkeys(existing.discovered_by + candidate.discovered_by))
        snippet = existing.snippet if len(existing.snippet) >= len(candidate.snippet) else candidate.snippet
        by_url[key] = replace(existing, discovered_by=methods, snippet=snippet)

    by_title: dict[str, MediaCandidate] = {}
    title_duplicates = 0
    for candidate in by_url.values():
        key = normalized_title(candidate.title)
        if key and key in by_title:
            title_duplicates += 1
            existing = by_title[key]
            methods = tuple(dict.fromkeys(existing.discovered_by + candidate.discovered_by))
            by_title[key] = replace(existing, discovered_by=methods)
        else:
            by_title[key or candidate.url] = candidate

    scored = []
    for index, candidate in enumerate(by_title.values()):
        score, query = match_query(candidate, queries)
        if score:
            scored.append((score, index, replace(candidate, query=query)))

    selected = []
    source_counts: dict[str, int] = {}
    for _, _, candidate in sorted(scored, key=lambda item: (-item[0], item[1])):
        if source_counts.get(candidate.source_name, 0) >= max_per_source:
            continue
        selected.append(candidate)
        source_counts[candidate.source_name] = source_counts.get(candidate.source_name, 0) + 1
        if len(selected) >= limit:
            break

    return selected, {
        "url_duplicates": url_duplicates,
        "title_duplicates": title_duplicates,
        "relevant_count": len(scored),
    }
