"""Probe Qwen embedding and rerank APIs. Does not write to the database."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
ENV = dotenv_values(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return str(ENV.get(name) or default).strip()


def _key(*names: str) -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return ""


def _mask(value: str) -> str:
    if len(value) < 8:
        return "(empty)"
    return f"{value[:4]}...{value[-4:]} (len={len(value)})"


def _print_result(title: str, ok: bool, detail: str) -> None:
    status = "OK" if ok else "FAIL"
    print(f"\n[{status}] {title}")
    print(detail.rstrip())


def test_embedding(client: httpx.Client, api_key: str) -> tuple[bool, int | None]:
    base = _env("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    model = _env("EMBEDDING_MODEL", "qwen3.7-text-embedding")
    dimension = int(_env("EMBEDDING_DIMENSION", "1024") or "1024")
    url = f"{base}/embeddings"
    payload = {
        "model": model,
        "input": ["国债期货落地香港，资本市场双向开放"],
        "dimensions": dimension,
    }
    response = client.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:800]}
    if response.status_code >= 400:
        _print_result(
            "Embedding",
            False,
            f"url={url}\nmodel={model}\nhttp={response.status_code}\nbody={json.dumps(body, ensure_ascii=False)[:800]}",
        )
        return False, None
    data = body.get("data") or []
    vector = (data[0] or {}).get("embedding") if data else None
    actual_dim = len(vector) if isinstance(vector, list) else None
    ok = isinstance(vector, list) and actual_dim == dimension
    _print_result(
        "Embedding",
        ok,
        f"url={url}\nmodel={model}\nrequested_dim={dimension}\nactual_dim={actual_dim}\nhttp={response.status_code}",
    )
    return ok, actual_dim


def test_rerank(client: httpx.Client, api_key: str) -> bool:
    url = _env("RERANKER_BASE_URL", "https://dashscope.aliyuncs.com/compatible-api/v1/reranks")
    model = _env("RERANKER_MODEL", "qwen3-rerank")
    payload = {
        "model": model,
        "query": "国债期货何时在香港落地？",
        "documents": [
            "5年期人民币国债期货8月3日在香港正式落地。",
            "量子计算是计算科学的一个前沿领域。",
            "预训练语言模型的发展给文本排序模型带来了新的进展。",
        ],
        "top_n": 2,
    }
    response = client.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:800]}
    results = body.get("results") or (body.get("output") or {}).get("results") or []
    ok = response.status_code < 400 and bool(results)
    preview = []
    for item in results[:3]:
        if isinstance(item, dict):
            preview.append({
                "index": item.get("index"),
                "relevance_score": item.get("relevance_score"),
            })
    _print_result(
        "Rerank",
        ok,
        f"url={url}\nmodel={model}\nhttp={response.status_code}\nresults={json.dumps(preview, ensure_ascii=False)}\nbody={json.dumps(body, ensure_ascii=False)[:800]}",
    )
    return ok


def main() -> int:
    api_key = _key("DASHSCOPE_API_KEY", "EMBEDDING_API_KEY", "RERANKER_API_KEY")
    print("Qwen RAG model probe")
    print(f"env_file={ROOT / '.env'}")
    print(f"DASHSCOPE_API_KEY={_mask(api_key)}")
    if not api_key:
        print("\n[FAIL] 缺少 DASHSCOPE_API_KEY（或 EMBEDDING_API_KEY / RERANKER_API_KEY）")
        print("请在 My_agent/.env 写入百炼 Key 后再运行：")
        print("  python -m My_agent.scripts.test_qwen_rag_models")
        return 2

    # Clash/local proxies on this machine break DashScope TLS; call the API directly.
    with httpx.Client(trust_env=False) as client:
        embed_ok, _dim = test_embedding(client, api_key)
        rerank_ok = test_rerank(client, api_key)
    if embed_ok and rerank_ok:
        print("\nBoth models are reachable.")
        return 0
    print("\nOne or both models failed. Check Key, model name, and Base URL.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
