from event_simulation.services.actor_policy import ActorPolicy
from event_simulation.services.actor_selector import ActorSelector, clamp_max_agents


def test_clamp_max_agents():
    assert clamp_max_agents(None) == 35
    assert clamp_max_agents(1) == 2
    assert clamp_max_agents(99) == 35


def test_regulator_is_passive_and_company_is_active():
    policy = ActorPolicy()
    regulator = policy.classify(
        {"entity_type": "GovernmentAgency", "display_name": "中国证监会", "actor_kind": "organization"},
        {"name": "GovernmentAgency", "display_name_zh": "监管机构", "actor_kind": "organization"},
    )
    company = policy.classify(
        {"entity_type": "ListedCompany", "display_name": "某科技公司", "actor_kind": "organization"},
        {"name": "ListedCompany", "display_name_zh": "上市公司", "actor_kind": "organization"},
    )
    concept = policy.classify(
        {"entity_type": "PolicyTheme", "display_name": "注册制", "actor_kind": "concept"},
        {"name": "PolicyTheme", "display_name_zh": "政策主题", "actor_kind": "concept"},
    )
    investor = policy.classify(
        {"entity_type": "IndividualInvestor", "display_name": "微博用户甲", "actor_kind": "person"},
        {"name": "IndividualInvestor", "display_name_zh": "个人投资者", "actor_kind": "person"},
    )
    assert regulator["simulation_role"] == "passive"
    assert regulator["is_actor"] is True
    assert company["simulation_role"] == "active"
    assert company["actor_pool"] == "entity"
    assert concept["simulation_role"] == "none"
    assert concept["is_actor"] is False
    assert investor["actor_pool"] == "social"


def test_selector_puts_every_typed_actor_on_stage():
    nodes = [
        {
            "id": "co", "display_name": "某科技公司", "name": "某科技公司",
            "is_actor": True, "simulation_role": "active", "actor_pool": "entity",
            "entity_type": "ListedCompany",
        },
        {
            "id": "reg", "display_name": "中国证监会", "name": "中国证监会",
            "is_actor": True, "simulation_role": "passive", "actor_pool": "none",
            "entity_type": "Regulator",
        },
        {
            "id": "acc", "display_name": "微博用户甲", "name": "微博用户甲",
            "is_actor": True, "simulation_role": "active", "actor_pool": "social",
            "entity_type": "SocialMediaAccount",
        },
        {"id": "noise", "display_name": "发行价", "is_actor": False},
    ]
    claims = [
        {"claim_id": "c1", "speaker": "某科技公司", "organization": "", "text": "公司将推进产品落地"},
        {"claim_id": "c2", "speaker": "微博用户甲", "organization": "", "text": "关注产品落地节奏"},
    ]
    edges = [{"source": "co", "target": "reg"}]
    selected = ActorSelector().select(nodes=nodes, edges=edges, claims=claims, max_agents=35)
    assert set(selected["entity_ids"]) == {"co", "reg", "acc"}
    assert selected["starter_count"] == 3


def test_selector_drops_lowest_degree_when_over_cap():
    nodes = [
        {
            "id": f"n{index}", "display_name": f"主体{index:02d}", "name": f"主体{index:02d}",
            "is_actor": True, "entity_type": "Organization",
        }
        for index in range(40)
    ]
    edges = [{"source": "n0", "target": f"n{index}"} for index in range(1, 10)]
    selected = ActorSelector().select(nodes=nodes, edges=edges, claims=[], max_agents=35)
    assert selected["starter_count"] == 35
    assert len(selected["dropped"]) == 5
    assert "n0" in selected["entity_ids"]
