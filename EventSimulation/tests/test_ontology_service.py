import pytest

from event_simulation.services.ontology_service import (
    is_speakable_actor_name,
    split_text,
    strip_report_links,
    validate_ontology,
)


def _ontology():
    entities = []
    for index in range(8):
        entities.append({
            "name": f"ActorType{index}",
            "display_name_zh": f"主体类型{index}",
            "actor_kind": "organization",
            "description": "A speakable organization.",
            "attributes": [],
        })
    entities.extend([
        {
            "name": "Person", "display_name_zh": "个人", "actor_kind": "person",
            "description": "A person.", "attributes": [],
        },
        {
            "name": "Organization", "display_name_zh": "组织", "actor_kind": "organization",
            "description": "An organization.", "attributes": [],
        },
    ])
    edges = [
        {
            "name": f"RELATES_TO_{index}",
            "display_name_zh": f"关系{index}",
            "description": "A relation.",
            "source_targets": [{"source": "Person", "target": "Organization"}],
            "attributes": [],
        }
        for index in range(6)
    ]
    return {"entity_types": entities, "edge_types": edges, "analysis_summary": "测试"}


def test_validate_ontology_requires_exact_actor_contract():
    ontology = validate_ontology(_ontology())

    assert len(ontology["entity_types"]) == 10
    assert ontology["entity_types"][-2]["name"] == "Person"
    assert ontology["entity_types"][-1]["name"] == "Organization"
    assert len(ontology["edge_types"]) == 6


def test_validate_ontology_rejects_concept_types():
    payload = _ontology()
    payload["entity_types"][0]["actor_kind"] = "concept"

    with pytest.raises(ValueError, match="person或organization"):
        validate_ontology(payload)


def test_strip_report_links_keeps_source_ids():
    text = "发行价150.80元 [S01](https://www.cls.cn/detail/2455292) 微博用户甲表示看好。"
    stripped = strip_report_links(text)
    assert "https://" not in stripped
    assert "（S01）" in stripped
    assert "微博用户甲" in stripped


def test_validate_ontology_rejects_missing_type():
    payload = _ontology()
    payload["entity_types"].pop(0)

    with pytest.raises(ValueError, match="10"):
        validate_ontology(payload)


def test_split_text_uses_overlap_and_sentence_boundary():
    text = "第一句。" * 120
    chunks = split_text(text, chunk_size=80, overlap=10)

    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert all(chunk.endswith("。") for chunk in chunks[:-1])


def test_speakable_actor_name_rejects_noise():
    assert is_speakable_actor_name("中国证监会")
    assert is_speakable_actor_name("吴清")
    assert not is_speakable_actor_name("创新")
    assert not is_speakable_actor_name("今年以来")
    assert not is_speakable_actor_name("稳、严、优、新")
    assert not is_speakable_actor_name("进一步健全投融资协调机制")
    assert not is_speakable_actor_name("中小企业")
    assert not is_speakable_actor_name("符合条件的港股上市公司")
    assert not is_speakable_actor_name("私募投资基金行业")
    assert not is_speakable_actor_name("A股")
    assert is_speakable_actor_name("恒泰期货")
