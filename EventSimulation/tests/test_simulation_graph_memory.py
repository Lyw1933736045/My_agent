from event_simulation.services.simulation_graph_memory import (
    SimulationZepMemoryUpdater,
    episode_text_for_action,
    simulation_graph_id,
)


def test_episode_text_matches_mirofish_post_and_like():
    post = {
        "agent_name": "宇树科技",
        "action_type": "CREATE_POST",
        "content": "定价150.8元",
        "action_args": {"content": "定价150.8元"},
    }
    like = {
        "agent_name": "上海证券报",
        "action_type": "LIKE_POST",
        "action_args": {
            "post_author_name": "宇树科技",
            "post_content": "定价150.8元",
        },
    }
    assert episode_text_for_action(post) == "宇树科技: 发布了一条帖子：「定价150.8元」"
    assert episode_text_for_action(like) == "上海证券报: 点赞了宇树科技的帖子：「定价150.8元」"


def test_updater_batches_and_skips_do_nothing():
    sent = []
    updater = SimulationZepMemoryUpdater(
        graph_id="sim-graph",
        api_key="test",
        batch_size=2,
        add_fn=lambda **kwargs: sent.append(kwargs),
        ensure_graph_fn=lambda: None,
    )
    updater.add_actions([
        {"agent_name": "A", "action_type": "DO_NOTHING", "action_args": {}},
        {"agent_name": "A", "action_type": "CREATE_POST", "content": "一", "action_args": {}},
        {"agent_name": "B", "action_type": "FOLLOW", "action_args": {"target_user_name": "A"}},
    ])
    status = updater.close()
    assert status["graph_id"] == "sim-graph"
    assert status["origin"] == "simulation"
    assert status["skipped"] == 1
    assert status["items_sent"] == 2
    assert sent[0]["type"] == "text"
    assert "A: 发布了一条帖子：「一」" in sent[0]["data"]
    assert "B: 关注了用户「A」" in sent[0]["data"]


def test_simulation_graph_id_is_not_the_real_graph():
    assert simulation_graph_id("case-1", "sim_abc") == "event_simulation_case-1_sim_sim_abc"
    assert "_real_" not in simulation_graph_id("case-1", "sim_abc")
