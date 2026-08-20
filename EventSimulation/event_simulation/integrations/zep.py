"""Zep Cloud graph integration for EventSimulation.

Zep owns graph extraction, search, and embeddings. EventSimulation still keeps
its strict Seed and provenance manifest locally so a hosted graph cannot become
the source of truth for real evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from ..services.ontology_service import (
    DocumentComposer,
    GraphChineseLocalizer,
    OntologyGenerator,
    split_text,
    validate_ontology,
)
from ..services.actor_policy import ActorPolicy


ACCEPTANCE_QUERIES = (
    "人民币外汇期货",
    "中国证监会",
    "中国人民银行",
    "中金所",
    "企业汇率避险",
)


class ZepGraphNotReady(RuntimeError):
    """Raised when the real Zep graph has not extracted nodes and edges yet."""


@dataclass(frozen=True)
class ZepConfig:
    api_key: str
    graph_version: str = "v1"


def _uuid(value: Any) -> str:
    return str(getattr(value, "uuid_", None) or getattr(value, "uuid", "") or "")


def _seed_digest(seed: dict[str, Any]) -> str:
    payload = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_line(item: dict[str, Any]) -> str:
    parts = []
    for trace in item.get("trace") or []:
        title = str(trace.get("title") or trace.get("publisher") or "").strip()
        url = str(trace.get("url") or "").strip()
        label = " ".join(part for part in (title, url) if part)
        if label and label not in parts:
            parts.append(label)
        if len(parts) >= 3:
            break
    return "；".join(parts)


def _episode_text(item_type: str, item: dict[str, Any]) -> str:
    text = str(item.get("text") or "").strip()
    speaker = str(item.get("speaker") or "").strip()
    organization = str(item.get("organization") or "").strip()
    who = " ".join(part for part in (speaker, organization) if part)
    if item_type == "claim" and who:
        body = f"言论：{who}表示：{text}"
    elif item_type == "claim":
        body = f"言论：{text}"
    else:
        body = f"事实：{text}"
    source = _source_line(item)
    if source:
        body += f" 来源：{source}"
    return body


class ZepGraphWriter:
    _PAGE_SIZE = 100
    _MAX_PAGES = 1000

    def __init__(
        self,
        config: ZepConfig,
        *,
        ontology_generator: OntologyGenerator | None = None,
        document_composer: DocumentComposer | None = None,
        localizer: GraphChineseLocalizer | None = None,
    ) -> None:
        self.config = config
        self.ontology_generator = ontology_generator or OntologyGenerator()
        self.document_composer = document_composer or DocumentComposer()
        self.localizer = localizer or GraphChineseLocalizer()

    @staticmethod
    def _graph_id(case_id: str, version: str) -> str:
        return f"event_simulation_{case_id}_real_{version}"

    @staticmethod
    def _client(api_key: str):
        try:
            from zep_cloud.client import Zep
        except ImportError as exc:
            raise RuntimeError("install event-simulation[graph] for Zep Cloud") from exc
        return Zep(api_key=api_key)

    def _list_graph_items(self, getter, graph_id: str) -> list[Any]:
        """Read every cursor page returned by Zep, without assuming page length.

        Zep may cap a response below the requested limit, so a short page is not
        necessarily the last page. Stop only on an empty page or when the cursor
        no longer advances.
        """
        items: list[Any] = []
        seen: set[str] = set()
        cursor: str | None = None
        for _ in range(self._MAX_PAGES):
            kwargs: dict[str, Any] = {"limit": self._PAGE_SIZE}
            if cursor:
                kwargs["uuid_cursor"] = cursor
            page = list(getter(graph_id, **kwargs) or [])
            if not page:
                break
            new_items = []
            for item in page:
                item_uuid = _uuid(item)
                if item_uuid and item_uuid not in seen:
                    seen.add(item_uuid)
                    new_items.append(item)
            if not new_items:
                break
            items.extend(new_items)
            next_cursor = _uuid(page[-1])
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        else:
            raise RuntimeError(f"Zep graph pagination exceeded {self._MAX_PAGES} pages")
        return items

    def _list_nodes(self, client, graph_id: str) -> list[Any]:
        return self._list_graph_items(client.graph.node.get_by_graph_id, graph_id)

    def _list_edges(self, client, graph_id: str) -> list[Any]:
        return self._list_graph_items(client.graph.edge.get_by_graph_id, graph_id)

    @staticmethod
    def _episode_uuid(item: dict[str, Any]) -> str:
        return str(item.get("episode_uuid") or "")

    def _episode_progress(self, client, manifest: dict[str, Any]) -> tuple[int, int]:
        episodes = manifest.get("episodes") or []
        completed = 0
        for item in episodes:
            episode_uuid = self._episode_uuid(item)
            if not episode_uuid:
                item["status"] = "missing_uuid"
                continue
            try:
                episode = client.graph.episode.get(uuid_=episode_uuid)
                processed = bool(getattr(episode, "processed", False))
            except Exception:
                processed = False
            item["status"] = "processed" if processed else "processing"
            completed += int(processed)
        return completed, len(episodes)

    def inspect(self, manifest: dict[str, Any]) -> dict[str, Any]:
        graph_id = str(manifest.get("zep_graph_id") or "")
        if not graph_id:
            raise ZepGraphNotReady("缺少 zep_graph_id，请先运行 graph-init")
        client = self._client(self.config.api_key)
        try:
            try:
                client.graph.get(graph_id)
            except Exception as exc:
                raise ZepGraphNotReady(f"Zep 图不存在或无法读取：{graph_id}（{exc}）") from exc
            episodes = manifest.get("episodes") or []
            cached_nodes = int(manifest.get("zep_nodes") or 0)
            cached_edges = int(manifest.get("zep_edges") or 0)
            cached_complete = (
                bool(manifest.get("ontology"))
                and bool(episodes)
                and all(item.get("status") == "processed" for item in episodes)
                and cached_nodes > 0
                and cached_edges > 0
            )
            if cached_complete:
                return {
                    "ready": True,
                    "graph_id": graph_id,
                    "status": "complete",
                    "nodes": cached_nodes,
                    "edges": cached_edges,
                    "episodes_total": len(episodes),
                    "episodes_processed": len(episodes),
                    "origin": "real",
                    "detail": None,
                }
            completed_episodes, total_episodes = self._episode_progress(client, manifest)
            nodes = self._list_nodes(client, graph_id)
            edges = self._list_edges(client, graph_id)
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
        ontology_ready = bool(manifest.get("ontology"))
        episodes_ready = total_episodes > 0 and completed_episodes == total_episodes
        ready = ontology_ready and episodes_ready and bool(nodes) and bool(edges)
        return {
            "ready": ready,
            "graph_id": graph_id,
            "status": "complete" if ready else "uploaded",
            "nodes": len(nodes),
            "edges": len(edges),
            "episodes_total": total_episodes,
            "episodes_processed": completed_episodes,
            "origin": "real",
            "detail": (
                None
                if ready
                else (
                    "Zep 图谱尚未完成本体化抽取："
                    f"文本块 {completed_episodes}/{total_episodes}，"
                    f"节点 {len(nodes)}，关系 {len(edges)}。禁止进入人设或推演。"
                )
            ),
        }

    def require_ready(self, manifest: dict[str, Any]) -> dict[str, Any]:
        status = self.inspect(manifest)
        if not status["ready"]:
            raise ZepGraphNotReady(
                status["detail"]
                or f"Zep 图谱未就绪（节点 {status['nodes']}，边 {status['edges']}）"
            )
        return status

    def _write_manifest(self, path: Path, manifest: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _artifact_path(manifest_path: Path, prefix: str, version: str) -> Path:
        safe_version = "".join(char if char.isalnum() or char in "_.-" else "_" for char in version)
        return manifest_path.with_name(f"{prefix}_{safe_version}.json")

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _set_ontology(client, graph_id: str, ontology: dict[str, Any]) -> None:
        """Register MiroFish-style dynamic entity and edge models before ingestion."""
        from typing import Optional

        from pydantic import Field
        from zep_cloud import EntityEdgeSourceTarget
        from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel

        entity_types = {}
        for definition in ontology.get("entity_types") or []:
            annotations = {}
            attrs: dict[str, Any] = {"__doc__": definition.get("description") or definition["name"]}
            for attribute in definition.get("attributes") or []:
                name = attribute["name"]
                annotations[name] = Optional[EntityText]
                attrs[name] = Field(
                    default=None,
                    description=attribute.get("description") or name,
                )
            attrs["__annotations__"] = annotations
            model = type(definition["name"], (EntityModel,), attrs)
            model.__doc__ = attrs["__doc__"]
            entity_types[definition["name"]] = model

        edge_types = {}
        for definition in ontology.get("edge_types") or []:
            class_name = "".join(word.capitalize() for word in definition["name"].split("_"))
            edge_model = type(
                class_name,
                (EdgeModel,),
                {"__doc__": definition.get("description") or definition["name"], "__annotations__": {}},
            )
            pairs = [
                EntityEdgeSourceTarget(source=item["source"], target=item["target"])
                for item in definition.get("source_targets") or []
            ]
            edge_types[definition["name"]] = (edge_model, pairs)

        client.graph.set_ontology(
            graph_ids=[graph_id],
            entities=entity_types,
            edges=edge_types,
        )

    def _wait_until_ready(
        self, manifest: dict[str, Any], manifest_path: Path, wait_seconds: int
    ) -> dict[str, Any]:
        deadline = time.time() + max(0, wait_seconds)
        status = self.inspect(manifest)
        while not status["ready"] and time.time() < deadline:
            self._write_manifest(manifest_path, manifest)
            time.sleep(min(3, max(0.25, deadline - time.time())))
            status = self.inspect(manifest)
        manifest["status"] = "complete" if status["ready"] else "uploaded"
        manifest["zep_nodes"] = status["nodes"]
        manifest["zep_edges"] = status["edges"]
        manifest["episodes_processed"] = status["episodes_processed"]
        manifest["episodes_total"] = status["episodes_total"]
        self._write_manifest(manifest_path, manifest)
        if not status["ready"]:
            raise ZepGraphNotReady(
                "Zep 图谱未在期限内完成全部文本块处理"
                f"（文本块 {status['episodes_processed']}/{status['episodes_total']}，"
                f"节点 {status['nodes']}，关系 {status['edges']}）。"
                "已终止，未进入人设或推演。"
            )
        return status

    def _localize_graph(self, manifest: dict[str, Any], manifest_path: Path) -> None:
        localization_path = self._artifact_path(
            manifest_path, "graph_localization", self.config.graph_version
        )
        if localization_path.is_file():
            manifest["localization_path"] = str(localization_path)
            self._write_manifest(manifest_path, manifest)
            return
        graph_id = str(manifest["zep_graph_id"])
        client = self._client(self.config.api_key)
        try:
            nodes = self._list_nodes(client, graph_id)
            edges = self._list_edges(client, graph_id)
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
        node_records = [
            {
                "id": _uuid(node),
                "name_zh": getattr(node, "name", "") or _uuid(node),
                "summary_zh": getattr(node, "summary", "") or getattr(node, "name", ""),
            }
            for node in nodes
        ]
        edge_records = [
            {
                "id": _uuid(edge),
                "name_zh": getattr(edge, "name", "") or "相关",
                "fact_zh": getattr(edge, "fact", "") or "",
            }
            for edge in edges
        ]
        payload = {
            "localization_version": "zh-cn-v1",
            "graph_id": graph_id,
            "nodes": self.localizer.translate(node_records),
            "edges": self.localizer.translate(edge_records),
        }
        self._write_json(localization_path, payload)
        manifest["localization_path"] = str(localization_path)
        self._write_manifest(manifest_path, manifest)

    def initialize(
        self,
        seed: dict[str, Any],
        manifest_path: Path,
        *,
        wait_seconds: int = 120,
    ) -> dict[str, Any]:
        from zep_cloud import EpisodeData

        if seed.get("seed_version") != "1.1":
            raise ValueError("real graph requires strict seed_version 1.1")
        graph_id = self._graph_id(str(seed["case_id"]), self.config.graph_version)
        seed_digest = _seed_digest(seed)
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            same = (
                existing.get("case_id") == seed.get("case_id")
                and existing.get("seed_version") == seed.get("seed_version")
                and existing.get("zep_graph_id") == graph_id
                and existing.get("seed_digest") == seed_digest
            )
            if not same:
                raise ValueError(
                    f"manifest already exists for {manifest_path} with different or legacy seed content; "
                    "choose a new --real-version instead of appending duplicate episodes"
                )
            if not existing.get("ontology"):
                raise ValueError(
                    "现有图谱未使用MiroFish本体链路，请选择新的 --real-version 重建图谱"
                )
            if any(not self._episode_uuid(item) for item in existing.get("episodes") or []):
                raise ValueError(
                    "现有图谱曾在文本块上传阶段中断；为避免重复证据，请选择新的 --real-version 重建"
                )
            self._wait_until_ready(existing, manifest_path, wait_seconds)
            self._localize_graph(existing, manifest_path)
            return json.loads(manifest_path.read_text(encoding="utf-8"))

        document = self.document_composer.compose_from_seed(seed)
        document_path = self._artifact_path(manifest_path, "graph_document", self.config.graph_version)
        document_path = document_path.with_suffix(".txt")
        document_path.write_text(document + "\n", encoding="utf-8")
        ontology = validate_ontology(
            self.ontology_generator.generate({**seed, "graph_document": document})
        )
        ontology_path = self._artifact_path(manifest_path, "ontology", self.config.graph_version)
        self._write_json(ontology_path, ontology)

        client = self._client(self.config.api_key)
        try:
            try:
                client.graph.create(
                    graph_id=graph_id,
                    name=f"EventSimulation {seed['case_id']} real graph",
                    description="MiroFish-style document graph for EventSimulation",
                )
            except Exception:
                try:
                    client.graph.get(graph_id)
                except Exception as exc:
                    raise RuntimeError(f"could not create or resume Zep graph {graph_id}: {exc}") from exc
            self._set_ontology(client, graph_id, ontology)
            episodes = []
            manifest_episodes = []
            chunks = split_text(document, chunk_size=500, overlap=50)
            if not chunks:
                raise ValueError("图谱输入正文为空，无法上传")
            for chunk_index, chunk in enumerate(chunks):
                episodes.append(EpisodeData(
                    data=chunk,
                    type="text",
                    source_description=f"document:chunk:{chunk_index}",
                ))
                manifest_episodes.append({
                    "item_id": f"document:{chunk_index}",
                    "item_type": "document",
                    "chunk_index": chunk_index,
                    "source_ids": [],
                    "trace": [],
                    "status": "pending",
                })
            manifest = {
                "manifest_version": "2.0",
                "case_id": seed["case_id"],
                "seed_version": seed["seed_version"],
                "group_id": f"{seed['case_id']}:real:{self.config.graph_version}",
                "zep_graph_id": graph_id,
                "seed_digest": seed_digest,
                "ontology": ontology,
                "ontology_path": str(ontology_path),
                "graph_document_path": str(document_path),
                "origin": "real",
                "status": "uploading",
                "episodes": manifest_episodes,
                "quarantined_graph_outputs": [],
                "embedding_provider": "Zep Cloud managed",
            }
            self._write_manifest(manifest_path, manifest)
            returned = []
            for offset in range(0, len(episodes), 3):
                batch_result = list(
                    client.graph.add_batch(
                        graph_id=graph_id, episodes=episodes[offset : offset + 3]
                    ) or []
                )
                returned.extend(batch_result)
                for local_index, episode in enumerate(batch_result):
                    index = offset + local_index
                    episode_uuid = _uuid(episode)
                    if not episode_uuid:
                        raise RuntimeError(f"第 {index + 1} 个文本块缺少Zep标识，已终止")
                    manifest_episodes[index]["episode_uuid"] = episode_uuid
                    manifest_episodes[index]["status"] = "uploaded"
                self._write_manifest(manifest_path, manifest)
                if offset + 3 < len(episodes):
                    time.sleep(1)
            if len(returned) != len(episodes):
                raise RuntimeError(
                    f"Zep只返回了 {len(returned)}/{len(episodes)} 个文本块标识，已终止"
                )
            manifest["status"] = "uploaded"
            self._write_manifest(manifest_path, manifest)
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
        self._wait_until_ready(manifest, manifest_path, wait_seconds)
        self._localize_graph(manifest, manifest_path)
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def export_evidence_graph(
        self, manifest: dict[str, Any], seed: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.require_ready(manifest)
        graph_id = str(manifest["zep_graph_id"])
        client = self._client(self.config.api_key)
        try:
            raw_nodes = self._list_nodes(client, graph_id)
            raw_edges = self._list_edges(client, graph_id)
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
        episode_sources: dict[str, list[str]] = {}
        for item in manifest.get("episodes") or []:
            episode_sources[str(item.get("episode_uuid") or "")] = [
                str(value) for value in item.get("source_ids") or []
            ]
        ontology = manifest.get("ontology") or {}
        entity_definitions = {
            str(item.get("name")): item for item in ontology.get("entity_types") or []
        }
        edge_definitions = {
            str(item.get("name")): item for item in ontology.get("edge_types") or []
        }
        entity_type_names = set(entity_definitions)
        localization = {"nodes": {}, "edges": {}}
        localization_path = manifest.get("localization_path")
        if localization_path:
            path = Path(str(localization_path))
            if path.is_file():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    localization = loaded
        nodes = []
        for node in raw_nodes:
            labels = [str(label) for label in (getattr(node, "labels", None) or []) if label]
            custom_labels = [label for label in labels if label not in {"Entity", "Node"}]
            entity_type = next((label for label in custom_labels if label in entity_type_names), None)
            definition = entity_definitions.get(str(entity_type)) or {}
            localized = (localization.get("nodes") or {}).get(_uuid(node), {})
            display_name = localized.get("name_zh") or getattr(node, "name", "") or _uuid(node)
            nodes.append({
                "id": _uuid(node),
                "name": getattr(node, "name", "") or _uuid(node),
                "name_zh": display_name,
                "display_name": display_name,
                "node_type": "concept",
                "is_actor": False,
                "entity_type": entity_type,
                "entity_type_zh": definition.get("display_name_zh") or "未分类知识节点",
                "actor_kind": definition.get("actor_kind"),
                "summary": getattr(node, "summary", "") or getattr(node, "name", ""),
                "summary_zh": localized.get("summary_zh") or getattr(node, "summary", "") or getattr(node, "name", ""),
                "labels": labels,
                "attributes": getattr(node, "attributes", None) or {},
                "created_at": str(getattr(node, "created_at", "") or "") or None,
                "origin": "real",
            })
        nodes = ActorPolicy().apply(nodes, ontology)
        node_ids = {node["id"] for node in nodes}
        edges = []
        omitted_edges = 0
        for edge in raw_edges:
            source = str(getattr(edge, "source_node_uuid", "") or "")
            target = str(getattr(edge, "target_node_uuid", "") or "")
            if source not in node_ids or target not in node_ids:
                omitted_edges += 1
                continue
            source_ids: list[str] = []
            for episode_uuid in getattr(edge, "episodes", None) or []:
                source_ids.extend(episode_sources.get(str(episode_uuid), []))
            localized = (localization.get("edges") or {}).get(_uuid(edge), {})
            episodes = [str(value) for value in getattr(edge, "episodes", None) or []]
            raw_edge_name = getattr(edge, "name", "") or "相关"
            edge_definition = edge_definitions.get(str(raw_edge_name)) or {}
            edges.append({
                "id": _uuid(edge),
                "source": source,
                "target": target,
                "name": raw_edge_name,
                "name_zh": edge_definition.get("display_name_zh") or localized.get("name_zh") or raw_edge_name,
                "summary": getattr(edge, "fact", "") or "",
                "summary_zh": localized.get("fact_zh") or getattr(edge, "fact", "") or "",
                "fact_type": getattr(edge, "fact_type", None) or getattr(edge, "name", "") or "相关",
                "attributes": getattr(edge, "attributes", None) or {},
                "created_at": str(getattr(edge, "created_at", "") or "") or None,
                "valid_at": str(getattr(edge, "valid_at", "") or "") or None,
                "invalid_at": str(getattr(edge, "invalid_at", "") or "") or None,
                "expired_at": str(getattr(edge, "expired_at", "") or "") or None,
                "episodes": episodes,
                "source_ids": list(dict.fromkeys(source_ids)),
                "origin": "real",
            })
        actor_count = sum(1 for node in nodes if node["is_actor"])
        return {
            "graph_version": "zep-ontology-2.0",
            "case_id": (seed or {}).get("case_id") or manifest.get("case_id"),
            "mode": "evidence",
            "origin": "real",
            "ontology": ontology,
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "nodes": len(nodes),
                "edges": len(edges),
                "omitted_edges": omitted_edges,
                "actor_nodes": actor_count,
                "knowledge_nodes": len(nodes) - actor_count,
                "entity_types": len({node["entity_type"] for node in nodes if node["entity_type"]}),
            },
            "notice": (
                "节点和关系来自经过时间与证据审核的材料。"
                "完整知识图谱展示全部实体与概念；推演起点只显示被选入模拟的可发声角色。"
            ),
        }

    def acceptance_queries(self, manifest: dict[str, Any], output_path: Path) -> dict[str, Any]:
        self.require_ready(manifest)
        graph_id = manifest.get("zep_graph_id")
        client = self._client(self.config.api_key)
        report = {"graph_id": graph_id, "queries": [], "origin": "real"}
        try:
            for query in ACCEPTANCE_QUERIES:
                result = client.graph.search(
                    graph_id=graph_id,
                    query=query,
                    limit=20,
                    scope="edges",
                    reranker="cross_encoder",
                )
                rows = []
                for edge in getattr(result, "edges", []) or []:
                    rows.append({
                        "uuid": _uuid(edge),
                        "name": getattr(edge, "name", ""),
                        "fact": getattr(edge, "fact", ""),
                    })
                report["queries"].append({"query": query, "results": rows})
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            return report
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
