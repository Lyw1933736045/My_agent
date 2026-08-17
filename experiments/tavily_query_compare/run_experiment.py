"""Compare Tavily recall for three fixed query forms.

Search parameters are loaded from config/media_sources.yaml (depth, days,
targeted domains, extra general search). This script does not create a run
or write PostgreSQL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yaml
from dotenv import load_dotenv
from tavily import TavilyClient


QUERIES = {
    "original_title": "首只离岸国债期货落地香港，资本市场双向开放再升级",
    "concise_description": "首只离岸国债期货落地香港",
    "core_keywords": "香港 离岸国债期货 资本市场 双向开放",
}

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
}


def load_tavily_search_config(project_dir: Path) -> dict:
    config_path = project_dir / "config" / "media_sources.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    tavily = raw.get("tavily") or {}
    depth = str(tavily.get("search_depth", "basic")).strip().lower()
    if depth not in {"basic", "advanced"}:
        depth = "basic"
    trusted = list(tavily.get("trusted_media_domains") or [])
    for domain in list(tavily.get("domestic_finance_domains") or []) + list(
        tavily.get("overseas_finance_domains") or []
    ):
        if domain not in trusted:
            trusted.append(domain)
    days = tavily.get("days")
    return {
        "config_path": str(config_path),
        "topic": "general",
        "search_depth": depth,
        "days": int(days) if days is not None else None,
        "include_answer": False,
        "include_raw_content": False,
        "targeted_search_enabled": bool(tavily.get("targeted_search_enabled", True)),
        "targeted_max_results": max(1, int(tavily.get("targeted_max_results", 10))),
        "max_results_per_query": max(1, int(tavily.get("max_results_per_query", 5))),
        "include_domains": trusted,
    }


def canonical_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.port and parsed.port not in {80, 443}:
        host = f"{host}:{parsed.port}"
    query = urlencode([
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ])
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))


def domain(value: str) -> str:
    host = (urlparse(value or "").hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def clean_cell(value: object, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _normalize_results(response: dict, discovered_by: str) -> list[dict]:
    results = []
    for rank, item in enumerate(response.get("results", []), start=1):
        url = str(item.get("url") or "")
        results.append(
            {
                "rank": rank,
                "title": item.get("title") or "",
                "url": url,
                "canonical_url": canonical_url(url),
                "domain": domain(url),
                "score": item.get("score"),
                "published_date": item.get("published_date"),
                "content": item.get("content") or "",
                "discovered_by": discovered_by,
            }
        )
    return results


def _one_search(
    client: TavilyClient,
    query: str,
    search_config: dict,
    *,
    max_results: int,
    include_domains: list[str] | None,
    retries: int = 2,
) -> dict:
    params = {
        "topic": search_config["topic"],
        "search_depth": search_config["search_depth"],
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
    }
    if search_config.get("days") is not None:
        params["days"] = search_config["days"]
    if include_domains:
        params["include_domains"] = include_domains
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = client.search(query=query, **params)
            return {"status": "ok", "params": params, "raw_response": response}
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    return {"status": "error", "params": params, "raw_response": None, "error": last_error}


def fetch_group(client: TavilyClient, query: str, search_config: dict) -> dict:
    passes = []
    if search_config["targeted_search_enabled"] and search_config["include_domains"]:
        passes.append(
            ("tavily_targeted", search_config["targeted_max_results"], search_config["include_domains"])
        )
    passes.append(("tavily_general", search_config["max_results_per_query"], None))

    pass_records = []
    merged = []
    seen = set()
    errors = []
    for discovered_by, max_results, include_domains in passes:
        record = _one_search(
            client,
            query,
            search_config,
            max_results=max_results,
            include_domains=include_domains,
        )
        results = []
        if record["status"] == "ok":
            results = _normalize_results(record["raw_response"] or {}, discovered_by)
            for item in results:
                key = item.get("canonical_url")
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append({**item, "merged_rank": len(merged) + 1})
        else:
            errors.append(f"{discovered_by}: {record.get('error')}")
        pass_records.append(
            {
                "discovered_by": discovered_by,
                "status": record["status"],
                "params": record["params"],
                "result_count": len(results),
                "results": results,
                "error": record.get("error"),
                "raw_response": record["raw_response"],
            }
        )
    return {
        "query": query,
        "status": "ok" if not errors else ("partial" if merged else "error"),
        "result_count": len(merged),
        "results": merged,
        "passes": pass_records,
        "error": "; ".join(errors) if errors else None,
    }


def overlap(left: dict, right: dict, key: str) -> dict:
    left_set = {item[key] for item in left["results"] if item.get(key)}
    right_set = {item[key] for item in right["results"] if item.get(key)}
    common = sorted(left_set & right_set)
    union = left_set | right_set
    return {
        "common_count": len(common),
        "common_rate_of_top10": round(len(common) / max(len(left_set), len(right_set), 1), 3),
        "jaccard_rate": round(len(common) / len(union), 3) if union else 0.0,
        "common": common,
    }


def unique_results(groups: dict[str, dict]) -> dict[str, list[dict]]:
    output = {}
    for name, group in groups.items():
        other_urls = {
            item.get("canonical_url")
            for other_name, other in groups.items()
            if other_name != name
            for item in other["results"]
            if item.get("canonical_url")
        }
        output[name] = [
            item
            for item in group["results"]
            if item.get("canonical_url") not in other_urls
        ]
    return output


def build_comparison(groups: dict[str, dict]) -> dict:
    names = list(groups)
    pairwise_url = {}
    pairwise_domain = {}
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            pair = f"{left_name}__vs__{right_name}"
            pairwise_url[pair] = overlap(groups[left_name], groups[right_name], "canonical_url")
            pairwise_domain[pair] = overlap(groups[left_name], groups[right_name], "domain")
    common_urls = sorted(
        set.intersection(
            *[
                {item["canonical_url"] for item in group["results"] if item.get("canonical_url")}
                for group in groups.values()
            ]
        )
    )
    common_domains = sorted(
        set.intersection(
            *[
                {item["domain"] for item in group["results"] if item.get("domain")}
                for group in groups.values()
            ]
        )
    )
    return {
        "url_pairwise": pairwise_url,
        "domain_pairwise": pairwise_domain,
        "all_three_common_urls": common_urls,
        "all_three_common_domains": common_domains,
        "unique_results_by_group": unique_results(groups),
    }


def md_escape(value: object) -> str:
    return clean_cell(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(payload: dict) -> str:
    lines = [
        "# Tavily Query 形式召回对比",
        "",
        f"执行时间：`{payload['experiment']['executed_at']}`",
        "",
        "本实验只改变 query；Tavily 参数来自 `config/media_sources.yaml`（定向域名 + 额外全网，以及 depth / days）。不写入正式数据库。",
        "",
        "## 固定参数",
        "",
        "```json",
        json.dumps(payload["experiment"]["search_params"], ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    for name, group in payload["groups"].items():
        lines.extend([
            f"## {name}",
            "",
            f"Query：`{group['query']}`",
            "",
            f"合并去重后 {group['result_count']} 条"
            + (
                "；各轮："
                + "，".join(
                    f"{item['discovered_by']} {item['result_count']} 条"
                    for item in group.get("passes", [])
                )
                if group.get("passes")
                else ""
            )
            + "。",
            "",
            "| 排名 | 轮次 | 标题 | Domain | Score | URL | 内容片段 |",
            "|---:|---|---|---|---:|---|---|",
        ])
        for item in group["results"]:
            lines.append(
                "| {rank} | {discovered_by} | {title} | {domain} | {score} | {url} | {content} |".format(
                    rank=item.get("merged_rank", item["rank"]),
                    discovered_by=md_escape(item.get("discovered_by")),
                    title=md_escape(item["title"]),
                    domain=md_escape(item["domain"]),
                    score=md_escape(item.get("score")),
                    url=md_escape(item["url"]),
                    content=md_escape(item.get("content")),
                )
            )
        if group["status"] != "ok":
            lines.extend(["", f"错误：`{md_escape(group.get('error'))}`"])
        lines.append("")

    lines.extend(["## URL 重合情况", "", "| 查询组 | 共同数量 | 重合率 | Jaccard |", "|---|---:|---:|---:|"])
    for pair, data in payload["comparison"]["url_pairwise"].items():
        lines.append(f"| {pair} | {data['common_count']} | {data['common_rate_of_top10']} | {data['jaccard_rate']} |")
    lines.extend(["", f"三组共同 URL：{len(payload['comparison']['all_three_common_urls'])}", ""])
    lines.extend(["## Domain 重合情况", "", "| 查询组 | 共同数量 | 重合率 | Jaccard |", "|---|---:|---:|---:|"])
    for pair, data in payload["comparison"]["domain_pairwise"].items():
        lines.append(f"| {pair} | {data['common_count']} | {data['common_rate_of_top10']} | {data['jaccard_rate']} |")
    lines.extend(["", f"三组共同 Domain：{len(payload['comparison']['all_three_common_domains'])}", ""])
    lines.extend(["## 各组独有结果", ""])
    for name, items in payload["comparison"]["unique_results_by_group"].items():
        lines.extend([f"### {name}（{len(items)} 条）", ""])
        for item in items:
            lines.append(f"- {item['title']}｜{item['domain']}｜{item['url']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="按 config 比较三种 Tavily query 形式的召回")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parents[2]
    load_dotenv(project_dir / ".env")
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        parser.error(f"未配置 TAVILY_API_KEY，请填写 {project_dir / '.env'}")

    search_config = load_tavily_search_config(project_dir)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    client = TavilyClient(api_key=api_key)
    executed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    groups = {}
    for name, query in QUERIES.items():
        print(f"运行 {name}: {query}")
        groups[name] = fetch_group(client, query, search_config)
        print(f"  {groups[name]['status']}，合并 {groups[name]['result_count']} 条")
        for item in groups[name].get("passes", []):
            print(f"    {item['discovered_by']}: {item['status']}，{item['result_count']} 条")

    report_params = {
        key: value
        for key, value in search_config.items()
        if key != "include_domains"
    }
    report_params["include_domains_count"] = len(search_config["include_domains"])
    report_params["include_domains"] = search_config["include_domains"]

    payload = {
        "experiment": {
            "name": "tavily_query_compare",
            "executed_at": executed_at,
            "queries": QUERIES,
            "search_params": report_params,
            "note": "参数来自 media_sources.yaml：定向域名搜索 + 额外全网搜索。不写入 PostgreSQL。",
        },
        "groups": groups,
    }
    payload["comparison"] = build_comparison(groups)
    json_path = output_dir / "result.json"
    md_path = output_dir / "report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"已保存：{json_path}")
    print(f"已保存：{md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
