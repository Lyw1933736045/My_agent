"""Cluster Tavily results into article families without LLM or embeddings."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


TRACKING_PARAMS = {
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
    "from",
    "ref",
    "source",
    "spm",
    "scm",
    "oid",
    "vt",
    "cid",
    "node_id",
    "t",
    "cre",
    "mod",
    "loc",
    "r",
    "rfunc",
    "tj",
    "tr",
}

MOBILE_HOST_PREFIXES = ("m.", "wap.", "mobile.", "h5.")
STRIP_PATH_PREFIXES = ("/m/", "/wap/", "/rain/")
MULTI_PART_TLDS = {("com", "cn"), ("com", "sg"), ("com", "hk"), ("net", "cn"), ("org", "cn"), ("co", "uk")}

SOURCE_PATTERNS = [
    re.compile(r"文章来源[：:]\s*([^\s，,。；;|｜]{2,30})"),
    re.compile(r"转载自[：:]?\s*([^\s，,。；;|｜]{2,30})"),
    re.compile(r"原文[：:]\s*([^\s，,。；;|｜]{2,30})"),
    re.compile(r"(?<![电来])来源[：:]\s*([^\s，,。；;|｜]{2,30})"),
]

SOURCE_DOMAINS = {
    "第一财经": ("yicai.com",),
    "新华社": ("news.cn", "xinhuanet.com"),
    "新华网": ("news.cn", "xinhuanet.com"),
    "证券时报": ("stcn.com",),
    "财新": ("caixin.com",),
    "21世纪经济报道": ("21jingji.com",),
    "21财经": ("21jingji.com",),
    "每日经济新闻": ("nbd.com.cn",),
    "中国证券报": ("cs.com.cn", "cnstock.com"),
    "上海证券报": ("cnstock.com",),
    "新京报": ("bjnews.com.cn",),
    "中新社": ("chinanews.com.cn",),
    "中新网": ("chinanews.com.cn",),
    "中国金融信息网": ("cnfin.com",),
    "联合早报": ("zaobao.com.sg",),
    "文汇报": ("wenweipo.com",),
    "东方财富": ("eastmoney.com",),
}

LOW_TRUST_HINTS = (
    "guba.",
    "sohu.com",
    "qq.com",
    "ifeng.com",
    "sina.cn",
    "jiemian.com",
)

TITLE_SIM_THRESHOLD = 0.85
CONTENT_OVERLAP_THRESHOLD = 0.8
SIMHASH_THRESHOLD = 0.9
MIN_CONTENT_CHARS = 80


def registrable_domain(host: str) -> str:
    parts = [item for item in host.split(".") if item]
    if len(parts) >= 3 and tuple(parts[-2:]) in MULTI_PART_TLDS:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def canonical_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    host = host.removeprefix("www.")
    for prefix in MOBILE_HOST_PREFIXES:
        if host.startswith(prefix) and len(host) > len(prefix):
            host = host[len(prefix) :]
            break
    host = registrable_domain(host)
    path = parsed.path or "/"
    for prefix in STRIP_PATH_PREFIXES:
        if path.startswith(prefix):
            path = "/" + path[len(prefix) :]
            break
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
        ]
    )
    scheme = (parsed.scheme or "https").lower()
    if scheme not in {"http", "https"}:
        scheme = "https"
    return urlunparse((scheme, host, path, "", query, ""))


def domain_of(value: str) -> str:
    host = (urlparse(value or "").hostname or "").lower().rstrip(".")
    host = host.removeprefix("www.")
    for prefix in MOBILE_HOST_PREFIXES:
        if host.startswith(prefix) and len(host) > len(prefix):
            return host[len(prefix) :]
    return host


def normalize_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"\s+", "", text)
    return text


def normalize_title(value: str) -> str:
    text = value or ""
    text = re.split(r"[_｜|]\s*(东方财富|腾讯新闻|新浪财经|搜狐网|凤凰网|第一财经)", text, maxsplit=1)[0]
    text = re.sub(r"[\s\-—_｜|·•,，.。:：!！?？【】\[\]()（）“”\"']+", "", text)
    return text.casefold()


def char_ngrams(text: str, size: int = 3) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def title_similarity(left: str, right: str) -> float:
    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def simhash64(text: str) -> int:
    tokens = char_ngrams(normalize_text(text), 2)
    vector = [0] * 64
    for token in tokens:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        value = int.from_bytes(digest[:8], "big")
        for bit in range(64):
            vector[bit] += 1 if (value >> bit) & 1 else -1
    fingerprint = 0
    for bit in range(64):
        if vector[bit] >= 0:
            fingerprint |= 1 << bit
    return fingerprint


def simhash_similarity(left: int, right: int) -> float:
    return 1.0 - (left ^ right).bit_count() / 64.0


def extract_cited_source(text: str) -> str | None:
    blob = re.sub(r"\s+", " ", text or "")
    for pattern in SOURCE_PATTERNS:
        match = pattern.search(blob)
        if not match:
            continue
        name = match.group(1).strip(" 《》【】[]")
        name = re.split(r"[/／|｜]", name, maxsplit=1)[0].strip()
        if 1 < len(name) <= 20:
            return name
    return None


def source_matches_domain(source_name: str, host: str) -> bool:
    for name, domains in SOURCE_DOMAINS.items():
        if name in source_name or source_name in name:
            return any(host == item or host.endswith("." + item) for item in domains)
    return False


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


def pair_reason(left: dict, right: dict) -> str | None:
    if left["cluster_url"] and left["cluster_url"] == right["cluster_url"]:
        return "canonical_url"
    title_sim = title_similarity(left["title"], right["title"])
    content_left = normalize_text(left.get("content") or "")
    content_right = normalize_text(right.get("content") or "")
    overlap = jaccard(char_ngrams(content_left), char_ngrams(content_right))
    if title_sim >= TITLE_SIM_THRESHOLD and overlap >= CONTENT_OVERLAP_THRESHOLD:
        return "title_and_content"
    if (
        min(len(content_left), len(content_right)) >= MIN_CONTENT_CHARS
        and simhash_similarity(left["simhash"], right["simhash"]) >= SIMHASH_THRESHOLD
    ):
        return "simhash"
    if title_sim >= 0.75:
        left_cited = left.get("cited_source")
        right_cited = right.get("cited_source")
        if left_cited and source_matches_domain(left_cited, right["cluster_domain"]):
            return "syndicated_copy"
        if right_cited and source_matches_domain(right_cited, left["cluster_domain"]):
            return "syndicated_copy"
    return None


def trust_score(item: dict, trusted_domains: list[str]) -> tuple:
    host = item["cluster_domain"]
    url = item.get("url") or ""
    cited = item.get("cited_source") or ""
    mobile = any(
        (urlparse(url).hostname or "").startswith(prefix) for prefix in MOBILE_HOST_PREFIXES
    )
    low_trust = any(hint in host or hint in url for hint in LOW_TRUST_HINTS)
    original_hit = 1 if cited and source_matches_domain(cited, host) else 0
    trusted_index = next(
        (index for index, domain in enumerate(trusted_domains) if host == domain or host.endswith("." + domain)),
        len(trusted_domains) + 1,
    )
    rank = item.get("merged_rank") or item.get("rank") or 999
    score = float(item.get("score") or 0.0)
    return (
        original_hit,
        0 if low_trust else 1,
        0 if mobile else 1,
        -trusted_index,
        score,
        -rank,
    )


def enrich(item: dict) -> dict:
    url = item.get("url") or ""
    content = item.get("content") or ""
    enriched = dict(item)
    enriched["cluster_url"] = canonical_url(url)
    enriched["cluster_domain"] = domain_of(url)
    enriched["cited_source"] = extract_cited_source(content + " " + (item.get("title") or ""))
    enriched["simhash"] = simhash64(content)
    return enriched


def compact_item(item: dict) -> dict:
    return {
        "title": item.get("title"),
        "source": item.get("cluster_domain"),
        "url": item.get("url"),
        "canonical_url": item.get("cluster_url"),
        "rank": item.get("merged_rank") or item.get("rank"),
        "score": item.get("score"),
        "discovered_by": item.get("discovered_by"),
        "cited_source": item.get("cited_source"),
    }


def choose_primary(members: list[dict], trusted_domains: list[str]) -> int:
    cited_names = [item.get("cited_source") for item in members if item.get("cited_source")]
    ranked = sorted(
        range(len(members)),
        key=lambda index: trust_score(members[index], trusted_domains),
        reverse=True,
    )
    for index in ranked:
        host = members[index]["cluster_domain"]
        if any(source_matches_domain(name, host) for name in cited_names):
            return index
    return ranked[0]


def cluster_items(items: list[dict], trusted_domains: list[str], prefix: str) -> list[dict]:
    enriched = [enrich(item) for item in items]
    forest = UnionFind(len(enriched))
    reasons: dict[tuple[int, int], str] = {}
    for left in range(len(enriched)):
        for right in range(left + 1, len(enriched)):
            reason = pair_reason(enriched[left], enriched[right])
            if reason:
                forest.union(left, right)
                reasons[(left, right)] = reason

    buckets: dict[int, list[int]] = {}
    for index in range(len(enriched)):
        buckets.setdefault(forest.find(index), []).append(index)

    clusters = []
    for serial, members_idx in enumerate(sorted(buckets.values(), key=lambda group: min(group)), start=1):
        members = [enriched[index] for index in members_idx]
        primary_index = choose_primary(members, trusted_domains)
        primary = members[primary_index]
        copies = [item for offset, item in enumerate(members) if offset != primary_index]
        pair_reasons = sorted(
            {
                reasons[(left, right)]
                for left in members_idx
                for right in members_idx
                if (left, right) in reasons
            }
        )
        for copy in copies:
            cited = copy.get("cited_source")
            if cited and source_matches_domain(cited, primary["cluster_domain"]):
                pair_reasons.append("syndicated_copy")
                break
        clusters.append(
            {
                "cluster_id": f"{prefix}_{serial:03d}",
                "size": len(members),
                "reasons": sorted(set(pair_reasons)) or ["unique"],
                "primary": compact_item(primary),
                "copies": [compact_item(item) for item in copies],
            }
        )
    return clusters


def cluster_result_file(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    trusted = list((payload.get("experiment") or {}).get("search_params", {}).get("include_domains") or [])
    clustered_groups = {}
    for name, group in (payload.get("groups") or {}).items():
        items = group.get("results") or []
        clusters = cluster_items(items, trusted, name)
        clustered_groups[name] = {
            "query": group.get("query"),
            "input_count": len(items),
            "cluster_count": len(clusters),
            "unique_story_count": len(clusters),
            "clusters": clusters,
        }
    return {
        "source_file": str(path),
        "experiment": payload.get("experiment"),
        "groups": clustered_groups,
    }


def render_cluster_markdown(payload: dict) -> str:
    lines = [
        "# Tavily 稿件簇去重",
        "",
        f"来源：`{payload['source_file']}`",
        "",
        "规则：canonical URL → 标题+正文重合 → SimHash。不上 embedding / LLM。不删除副本，只归簇。",
        "",
    ]
    for name, group in payload["groups"].items():
        lines.extend(
            [
                f"## {name}",
                "",
                f"Query：`{group['query']}`",
                "",
                f"原始 {group['input_count']} 条 → **{group['cluster_count']} 个独立稿件簇**。",
                "",
            ]
        )
        for cluster in group["clusters"]:
            primary = cluster["primary"]
            copies = cluster["copies"]
            lines.append(
                f"### {cluster['cluster_id']}（{cluster['size']} 条，{', '.join(cluster['reasons'])}）"
            )
            lines.append("")
            lines.append(
                f"- primary：{primary['source']}｜{primary['title']}｜{primary['url']}"
            )
            for copy in copies:
                extra = f"；转载自 {copy['cited_source']}" if copy.get("cited_source") else ""
                lines.append(f"- copy：{copy['source']}｜{copy['title']}｜{copy['url']}{extra}")
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="对 Tavily 实验结果做稿件簇去重")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        clustered = cluster_result_file(path)
        stem = path.stem
        json_path = path.with_name(f"{stem}_clustered.json")
        md_path = path.with_name(f"{stem}_clusters.md")
        json_path.write_text(json.dumps(clustered, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(render_cluster_markdown(clustered), encoding="utf-8")
        print(f"{path.name}")
        for name, group in clustered["groups"].items():
            print(f"  {name}: {group['input_count']} → {group['cluster_count']} 簇")
        print(f"  已保存 {json_path.name} / {md_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
