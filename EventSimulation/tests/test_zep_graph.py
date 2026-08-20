from types import SimpleNamespace

from event_simulation.integrations.zep import ZepConfig, ZepGraphWriter
from tests.test_ontology_service import _ontology


def _item(uuid: str, **values):
    return SimpleNamespace(uuid_=uuid, **values)


def test_list_graph_items_reads_short_cursor_pages():
    calls = []
    pages = {
        None: [_item("node-3"), _item("node-2")],
        "node-2": [_item("node-1")],
        "node-1": [],
    }

    def getter(graph_id, *, limit, uuid_cursor=None):
        calls.append((graph_id, limit, uuid_cursor))
        return pages[uuid_cursor]

    writer = ZepGraphWriter(ZepConfig(api_key="test"))

    assert [item.uuid_ for item in writer._list_graph_items(getter, "graph-1")] == [
        "node-3",
        "node-2",
        "node-1",
    ]
    assert calls == [
        ("graph-1", 100, None),
        ("graph-1", 100, "node-2"),
        ("graph-1", 100, "node-1"),
    ]


def test_export_evidence_graph_omits_dangling_edges(monkeypatch):
    writer = ZepGraphWriter(ZepConfig(api_key="test"))
    nodes = [_item("node-1", name="一", labels=[], summary=""), _item("node-2", name="二", labels=[], summary="")]
    edges = [
        _item("edge-1", source_node_uuid="node-1", target_node_uuid="node-2", name="连接", fact="", episodes=[]),
        _item("edge-2", source_node_uuid="node-1", target_node_uuid="missing", name="坏边", fact="", episodes=[]),
    ]
    fake_client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(writer, "require_ready", lambda manifest: {"ready": True})
    monkeypatch.setattr(writer, "_client", lambda api_key: fake_client)
    monkeypatch.setattr(writer, "_list_nodes", lambda client, graph_id: nodes)
    monkeypatch.setattr(writer, "_list_edges", lambda client, graph_id: edges)

    payload = writer.export_evidence_graph({"zep_graph_id": "graph-1", "episodes": []})

    assert [edge["id"] for edge in payload["edges"]] == ["edge-1"]
    assert payload["stats"] == {
        "nodes": 2,
        "edges": 1,
        "omitted_edges": 1,
        "actor_nodes": 0,
        "knowledge_nodes": 2,
        "entity_types": 0,
    }


def test_export_marks_only_custom_ontology_labels_as_actors(monkeypatch):
    writer = ZepGraphWriter(ZepConfig(api_key="test"))
    nodes = [
        _item("actor-1", name="中国证监会", labels=["Entity", "GovernmentAgency"], summary="监管机构"),
        _item("concept-1", name="资本市场", labels=["Entity"], summary="概念"),
    ]
    edges = [
        _item(
            "edge-1", source_node_uuid="actor-1", target_node_uuid="concept-1",
            name="COMMENTS_ON", fact="评论资本市场", episodes=[],
        )
    ]
    fake_client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(writer, "require_ready", lambda manifest: {"ready": True})
    monkeypatch.setattr(writer, "_client", lambda api_key: fake_client)
    monkeypatch.setattr(writer, "_list_nodes", lambda client, graph_id: nodes)
    monkeypatch.setattr(writer, "_list_edges", lambda client, graph_id: edges)
    manifest = {
        "zep_graph_id": "graph-1",
        "episodes": [],
        "ontology": {
            "entity_types": [{
                "name": "GovernmentAgency", "display_name_zh": "监管机构",
                "actor_kind": "organization",
            }],
            "edge_types": [{"name": "COMMENTS_ON", "display_name_zh": "评论"}],
        },
    }

    payload = writer.export_evidence_graph(manifest)

    assert payload["nodes"][0]["is_actor"] is True
    assert payload["nodes"][0]["simulation_role"] == "passive"
    assert payload["nodes"][0]["entity_type_zh"] == "监管机构"
    assert payload["nodes"][1]["node_type"] == "concept"
    assert payload["edges"][0]["name_zh"] == "评论"
    assert payload["stats"]["actor_nodes"] == 1


def test_initialize_registers_ontology_before_upload(monkeypatch, tmp_path):
    calls = []

    class Generator:
        def generate(self, seed):
            calls.append("generate_ontology")
            return _ontology()

    graph_api = SimpleNamespace(
        create=lambda **kwargs: calls.append("create_graph"),
        add_batch=lambda **kwargs: (
            calls.append("upload_episodes")
            or [_item(f"episode-{index}") for index, _ in enumerate(kwargs["episodes"])]
        ),
    )
    fake_client = SimpleNamespace(graph=graph_api, close=lambda: None)
    class Composer:
        def compose_from_seed(self, seed):
            calls.append("compose_document")
            return str(seed.get("source_report") or seed["facts"][0]["text"])

    writer = ZepGraphWriter(
        ZepConfig(api_key="test", graph_version="v-test"),
        ontology_generator=Generator(),
        document_composer=Composer(),
    )
    monkeypatch.setattr(writer, "_client", lambda api_key: fake_client)
    monkeypatch.setattr(
        writer,
        "_set_ontology",
        lambda client, graph_id, ontology: calls.append("set_ontology"),
    )

    def mark_ready(manifest, manifest_path, wait_seconds):
        for episode in manifest["episodes"]:
            episode["status"] = "processed"
        manifest["status"] = "complete"
        manifest["zep_nodes"] = 1
        manifest["zep_edges"] = 1
        writer._write_manifest(manifest_path, manifest)
        return {"ready": True}

    monkeypatch.setattr(writer, "_wait_until_ready", mark_ready)
    monkeypatch.setattr(writer, "_localize_graph", lambda manifest, path: calls.append("localize"))
    seed = {
        "seed_version": "1.1", "case_id": "case-test", "scenario": {"question": "测试什么？"},
        "source_report": "监管机构甲发布信息。媒体乙报道发行价150.80元。微博用户甲表示关注估值。",
        "facts": [{"fact_id": "f1", "text": "监管机构甲发布信息", "source_ids": ["s1"]}],
        "claims": [],
    }

    writer.initialize(seed, tmp_path / "graph_manifest.json")

    assert calls.index("compose_document") < calls.index("generate_ontology")
    assert calls.index("set_ontology") < calls.index("upload_episodes")
    manifest = __import__("json").loads((tmp_path / "graph_manifest.json").read_text())
    assert manifest["manifest_version"] == "2.0"
    assert manifest["episodes"][0]["item_type"] == "document"
    assert manifest["episodes"][0]["status"] == "processed"
    assert (tmp_path / "graph_document_v-test.txt").read_text(encoding="utf-8").startswith("监管机构甲")
