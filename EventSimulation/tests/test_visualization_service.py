import sqlite3

from event_simulation.services.network_service import build_interaction_graph
from event_simulation.services.visualization_service import build_visualization_analysis


def _personas():
    return {
        "personas": [
            {"persona_id": "p0", "display_name": "监管方", "role_group": "regulator"},
            {"persona_id": "p1", "display_name": "市场方", "role_group": "futures_company"},
        ]
    }


def _run():
    return {
        "case_id": "case-test",
        "simulation_id": "run-test",
        "rounds": [
            {
                "round": 0,
                "active_agent_ids": [0],
                "actions": [
                    {
                        "agent_id": 0,
                        "persona_id": "p0",
                        "role_group": "regulator",
                        "action_type": "CREATE_POST",
                        "content": "支持人民币外汇期货试点稳妥推进",
                    }
                ],
            },
            {
                "round": 1,
                "active_agent_ids": [0, 1],
                "actions": [
                    {
                        "agent_id": 1,
                        "persona_id": "p1",
                        "role_group": "futures_company",
                        "action_type": "QUOTE_POST",
                        "content": "人民币外汇期货试点有助于企业管理风险",
                        "action_args": {"quoted_id": 1},
                    },
                    {
                        "agent_id": 1,
                        "persona_id": "p1",
                        "role_group": "futures_company",
                        "action_type": "LIKE_POST",
                        "content": "",
                        "action_args": {"post_id": 1},
                    },
                ],
            },
        ],
    }


def _database(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE post (post_id INTEGER, user_id INTEGER)")
    connection.execute("CREATE TABLE comment (comment_id INTEGER, post_id INTEGER)")
    connection.execute("INSERT INTO post VALUES (1, 0)")
    connection.commit()
    connection.close()


def test_visualization_analysis_uses_existing_logs(tmp_path):
    database = tmp_path / "oasis.db"
    _database(database)

    payload = build_visualization_analysis(_run(), _personas(), database)

    assert payload["summary"]["agent_count"] == 2
    assert payload["summary"]["action_count"] == 3
    assert payload["summary"]["interaction_count"] == 2
    assert payload["round_metrics"][1]["actions"]["QUOTE_POST"] == 1
    assert payload["group_metrics"][0]["label"] in {"监管机构", "期货机构"}
    assert any("人民币外汇期货试点" in item["topic"] for item in payload["topic_evolution"])
    assert payload["metric_sources"]["actions"] == "模拟日志直接统计"
    assert payload["keyword_hotspots"]["default_scope"] == "last_round"
    assert payload["keyword_hotspots"]["rounds"][-1]["round"] == 1
    keywords = {item["keyword"] for item in payload["keyword_hotspots"]["all"]}
    assert "外汇期货" in keywords or "期货试点" in keywords or any("外汇期货" in item for item in keywords)


def test_keyword_hotspots_rank_discussion_terms(tmp_path):
    database = tmp_path / "oasis.db"
    _database(database)
    run = _run()
    run["rounds"][0]["actions"][0]["content"] = "宇树科技IPO估值偏高，市盈率超过一百倍。"
    run["rounds"][1]["actions"][0]["content"] = "宇树科技商业化能力才是关键，估值和市盈率都要看落地。"
    run["rounds"].append({
        "round": 2,
        "active_agent_ids": [0, 1],
        "actions": [
            {
                "agent_id": 0,
                "persona_id": "p0",
                "role_group": "regulator",
                "action_type": "CREATE_POST",
                "content": "关注宇树科技商业化进度，而不是只盯着估值泡沫。",
            },
            {
                "agent_id": 1,
                "persona_id": "p1",
                "role_group": "futures_company",
                "action_type": "CREATE_COMMENT",
                "content": "商业化、竞争和风险会取代上市叙事。宇树科技仍是讨论中心。",
            },
        ],
    })

    payload = build_visualization_analysis(run, _personas(), database)
    last_round = payload["keyword_hotspots"]["rounds"][-1]["keywords"]
    names = {item["keyword"] for item in last_round}
    assert "宇树科技" in names
    assert "商业化" in names
    assert last_round[0]["count"] >= last_round[-1]["count"]


def test_interaction_graph_resolves_reposted_id(tmp_path):
    database = tmp_path / "oasis.db"
    _database(database)
    run = _run()
    run["rounds"][1]["actions"] = [
        {
            "agent_id": 1,
            "persona_id": "p1",
            "role_group": "futures_company",
            "action_type": "REPOST",
            "action_args": {"reposted_id": 1},
        }
    ]

    graph = build_interaction_graph(run, _personas(), database)

    assert graph["edges"][0]["source"] == "agent_1"
    assert graph["edges"][0]["target"] == "agent_0"
    assert graph["stats"]["action_types"] == {"REPOST": 1}
