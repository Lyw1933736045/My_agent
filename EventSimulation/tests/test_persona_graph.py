from event_simulation.services.persona_service import PersonaBuilder


def _manifest():
    return {
        "case_id": "case-test", "status": "complete", "zep_graph_id": "g1", "group_id": "group",
        "episodes": [
            {"item_id": "f1", "status": "processed"},
            {"item_id": "c1", "status": "processed"},
            {"item_id": "c2", "status": "processed"},
        ],
    }


def test_personas_include_regulators_and_companies():
    seed = {
        "case_id": "case-test",
        "facts": [{"fact_id": "f1", "text": "某科技公司发布产品", "source_ids": ["s1"]}],
        "claims": [{
            "claim_id": "c1", "speaker": "某科技公司", "organization": "",
            "text": "我们将推进产品落地", "source_ids": ["s1"], "trace": [],
        }],
    }
    graph = {
        "nodes": [
            {
                "id": "a1", "name": "中国证监会", "display_name": "中国证监会",
                "is_actor": True, "simulation_role": "passive", "actor_pool": "none",
                "entity_type": "GovernmentAgency", "entity_type_zh": "监管机构",
                "actor_kind": "organization", "summary_zh": "负责监管", "attributes": {},
            },
            {
                "id": "c1", "name": "某科技公司", "display_name": "某科技公司",
                "is_actor": True, "simulation_role": "active", "actor_pool": "entity",
                "entity_type": "ListedCompany", "entity_type_zh": "上市公司",
                "actor_kind": "organization", "summary_zh": "事件主体企业", "attributes": {},
            },
            {"id": "n1", "name": "创新", "display_name": "创新", "is_actor": False, "simulation_role": "none"},
        ],
        "edges": [{
            "id": "e1", "source": "c1", "target": "n1", "name_zh": "评论",
            "summary_zh": "公司评论创新", "source_ids": ["s1"],
        }],
    }

    payload = PersonaBuilder().build(seed, _manifest(), graph)

    assert payload["agent_count"] == 2
    names = {item["display_name"] for item in payload["personas"]}
    assert names == {"某科技公司", "中国证监会"}


def test_personas_use_seed_claims_when_document_episodes_are_ingested():
    seed = {
        "case_id": "case-test",
        "facts": [{"fact_id": "f1", "text": "某科技公司发布产品", "source_ids": ["s1"]}],
        "claims": [{
            "claim_id": "c1", "speaker": "某科技公司", "organization": "",
            "text": "我们将推进产品落地", "source_ids": ["s1"], "trace": [],
        }],
    }
    graph = {
        "nodes": [{
            "id": "c1", "name": "某科技公司", "display_name": "某科技公司",
            "is_actor": True, "simulation_role": "active", "actor_pool": "entity",
            "entity_type": "ListedCompany", "entity_type_zh": "上市公司",
            "actor_kind": "organization", "summary_zh": "事件主体企业", "attributes": {},
        }],
        "edges": [],
    }
    manifest = {
        "case_id": "case-test", "status": "complete", "zep_graph_id": "g1", "group_id": "group",
        "episodes": [{"item_id": "document:0", "item_type": "document", "status": "processed"}],
    }

    payload = PersonaBuilder().build(seed, manifest, graph)

    assert payload["personas"][0]["stance_refs"] == ["c1"]

