"""Write simulation actions to a separate Zep graph, Miro-Fish style.

Agent decisions still do not query Zep. This path only uploads natural-language
activity episodes so a later report can search the simulated world. Real
evidence stays on `<case>:real:<version>`; these episodes go to a simulation
graph_id and are labeled origin=simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Callable


SKIP_ACTIONS = {"DO_NOTHING", ""}


def simulation_graph_id(case_id: str, simulation_id: str) -> str:
    return f"event_simulation_{case_id}_sim_{simulation_id}"


def episode_text_for_action(action: dict[str, Any]) -> str:
    """Mirror Miro-Fish AgentActivity.to_episode_text for Twitter actions."""
    name = str(action.get("agent_name") or f"agent_{action.get('agent_id')}")
    args = action.get("action_args") if isinstance(action.get("action_args"), dict) else {}
    action_type = str(action.get("action_type") or "")
    content = str(action.get("content") or args.get("content") or "")
    post_content = str(args.get("post_content") or args.get("original_content") or "")
    post_author = str(
        args.get("post_author_name")
        or args.get("original_author_name")
        or ""
    )
    quote = str(args.get("quote_content") or content)
    target_user = str(args.get("target_user_name") or "")

    if action_type == "CREATE_POST":
        body = f"发布了一条帖子：「{content}」" if content else "发布了一条帖子"
    elif action_type == "LIKE_POST":
        body = _target_post("点赞", post_author, post_content)
    elif action_type == "DISLIKE_POST":
        body = _target_post("踩", post_author, post_content)
    elif action_type == "REPOST":
        body = _target_post("转发", post_author, post_content)
    elif action_type == "QUOTE_POST":
        base = _target_post("引用", post_author, post_content)
        if quote:
            base += f"，并评论道：「{quote}」"
        body = base
    elif action_type == "FOLLOW":
        body = f"关注了用户「{target_user}」" if target_user else "关注了一个用户"
    elif action_type == "CREATE_COMMENT":
        if content and post_content and post_author:
            body = f"在{post_author}的帖子「{post_content}」下评论道：「{content}」"
        elif content and post_content:
            body = f"在帖子「{post_content}」下评论道：「{content}」"
        elif content:
            body = f"评论道：「{content}」"
        else:
            body = "发表了评论"
    else:
        body = f"执行了{action_type}操作"
    return f"{name}: {body}"


def _target_post(verb: str, author: str, content: str) -> str:
    if content and author:
        return f"{verb}了{author}的帖子：「{content}」"
    if content:
        return f"{verb}了一条帖子：「{content}」"
    if author:
        return f"{verb}了{author}的一条帖子"
    return f"{verb}了一条帖子"


@dataclass
class SimulationZepMemoryUpdater:
    """Batch simulation actions onto a dedicated Zep graph (Miro-Fish updater)."""

    graph_id: str
    api_key: str
    batch_size: int = 5
    add_fn: Callable[..., Any] | None = None
    ensure_graph_fn: Callable[[], None] | None = None
    _buffer: list[str] = field(default_factory=list)
    _sent: int = 0
    _skipped: int = 0
    _failed: int = 0
    _ensured: bool = False

    @classmethod
    def from_env(cls, *, case_id: str, simulation_id: str) -> "SimulationZepMemoryUpdater | None":
        api_key = os.getenv("ZEP_API_KEY") or ""
        if not api_key:
            return None
        return cls(graph_id=simulation_graph_id(case_id, simulation_id), api_key=api_key)

    def add_actions(self, actions: list[dict[str, Any]]) -> None:
        for action in actions:
            action_type = str(action.get("action_type") or "")
            if action_type in SKIP_ACTIONS:
                self._skipped += 1
                continue
            self._buffer.append(episode_text_for_action(action))
            if len(self._buffer) >= self.batch_size:
                self._flush()

    def close(self) -> dict[str, Any]:
        self._flush()
        return {
            "graph_id": self.graph_id,
            "origin": "simulation",
            "items_sent": self._sent,
            "skipped": self._skipped,
            "failed_batches": self._failed,
        }

    def _flush(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        combined = "\n".join(batch)
        try:
            self._ensure_graph()
            self._add(combined)
            self._sent += len(batch)
        except Exception:
            self._failed += 1
            self._buffer = batch + self._buffer

    def _ensure_graph(self) -> None:
        if self._ensured:
            return
        if self.ensure_graph_fn is not None:
            self.ensure_graph_fn()
            self._ensured = True
            return
        from zep_cloud.client import Zep

        client = Zep(api_key=self.api_key)
        try:
            try:
                client.graph.create(
                    graph_id=self.graph_id,
                    name=f"EventSimulation simulation {self.graph_id}",
                    description="Simulation-origin agent activity. Not real evidence.",
                )
            except Exception:
                client.graph.get(self.graph_id)
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
        self._ensured = True

    def _add(self, data: str) -> None:
        if self.add_fn is not None:
            self.add_fn(graph_id=self.graph_id, type="text", data=data)
            return
        from zep_cloud.client import Zep

        client = Zep(api_key=self.api_key)
        try:
            client.graph.add(graph_id=self.graph_id, type="text", data=data)
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
