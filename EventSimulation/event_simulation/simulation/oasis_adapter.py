"""OASIS 0.2.x adapter and database trace normalizer.

The lifecycle mirrors MiroFish's Twitter runner while keeping all database
interpretation inside one integration boundary.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


BUSINESS_ACTIONS = {
    "CREATE_POST",
    "CREATE_COMMENT",
    "LIKE_POST",
    "DISLIKE_POST",
    "REPOST",
    "QUOTE_POST",
    "FOLLOW",
    "DO_NOTHING",
}


def read_trace(database_path: Path, *, after_rowid: int = 0) -> list[dict[str, Any]]:
    """Return enriched OASIS trace rows after a cursor."""
    if not database_path.exists():
        return []
    connection = sqlite3.connect(database_path)
    try:
        post_rows = connection.execute(
            "SELECT post_id, user_id, original_post_id, content, quote_content FROM post"
        ).fetchall()
        posts = {
            int(post_id): {
                "user_id": user_id,
                "original_post_id": original_post_id,
                "content": content or "",
                "quote_content": quote_content or "",
            }
            for post_id, user_id, original_post_id, content, quote_content in post_rows
        }
        comment_rows = connection.execute(
            "SELECT comment_id, post_id, content FROM comment"
        ).fetchall()
        comments = {
            int(comment_id): {"post_id": post_id, "content": content or ""}
            for comment_id, post_id, content in comment_rows
        }
        rows = connection.execute(
            "SELECT rowid, user_id, created_at, action, info "
            "FROM trace WHERE rowid > ? ORDER BY rowid",
            (after_rowid,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()

    result: list[dict[str, Any]] = []
    for rowid, user_id, created_at, raw_action, raw_info in rows:
        try:
            detail = json.loads(raw_info) if isinstance(raw_info, str) else {}
        except json.JSONDecodeError:
            detail = {"raw_info": raw_info}
        if not isinstance(detail, dict):
            detail = {"detail": detail}

        action_type = str(raw_action).upper()
        post_id = detail.get("post_id")
        new_post_id = detail.get("new_post_id")
        quoted_id = detail.get("quoted_id")
        comment_id = detail.get("comment_id")
        produced_post = posts.get(int(new_post_id)) if new_post_id is not None else None
        target_post = posts.get(int(post_id)) if post_id is not None else None
        target_comment = comments.get(int(comment_id)) if comment_id is not None else None

        content = (
            detail.get("content")
            or detail.get("quote_content")
            or detail.get("comment_content")
            or detail.get("text")
            or (produced_post or {}).get("quote_content")
            or (produced_post or {}).get("content")
            or (target_comment or {}).get("content")
            or ""
        )
        parent_post_id = quoted_id
        if parent_post_id is None and action_type in {
            "CREATE_COMMENT", "LIKE_POST", "DISLIKE_POST", "REPOST"
        }:
            parent_post_id = post_id
        if parent_post_id is None and produced_post:
            parent_post_id = produced_post.get("original_post_id")

        if target_post:
            detail.setdefault("post_content", target_post["content"])
            detail.setdefault("post_author_id", target_post["user_id"])
        if produced_post:
            detail.setdefault("generated_content", produced_post["quote_content"] or produced_post["content"])
        result.append({
            "trace_rowid": int(rowid),
            "action_id": f"oasis_trace_{rowid}",
            "agent_id": user_id,
            "created_at": created_at,
            "action_type": action_type,
            "action_args": detail,
            "content": content,
            "parent_action_ids": (
                [f"oasis_post_{parent_post_id}"] if parent_post_id is not None else []
            ),
            "origin": "simulation",
            "validated": True,
        })
    return result


class OasisTwitterAdapter:
    def __init__(
        self,
        *,
        profile_path: Path,
        database_path: Path,
        model: Any,
        action_names: tuple[str, ...],
        semaphore: int = 10,
    ) -> None:
        self.profile_path = profile_path
        self.database_path = database_path
        self.model = model
        self.action_names = action_names
        self.semaphore = semaphore
        self.env: Any = None
        self.agent_graph: Any = None

    async def open(self) -> None:
        try:
            import oasis
            from oasis import ActionType, DefaultPlatformType, generate_twitter_agent_graph
        except ImportError as exc:
            raise RuntimeError("install event-simulation[simulation] first") from exc
        actions = [
            getattr(ActionType, name)
            for name in self.action_names
            if hasattr(ActionType, name)
        ]
        self.agent_graph = await generate_twitter_agent_graph(
            profile_path=str(self.profile_path),
            model=self.model,
            available_actions=actions,
        )
        self.env = oasis.make(
            agent_graph=self.agent_graph,
            platform=DefaultPlatformType.TWITTER,
            database_path=str(self.database_path),
            semaphore=self.semaphore,
        )
        await self.env.reset()

    def agent(self, agent_id: int) -> Any:
        if self.agent_graph is None:
            raise RuntimeError("OASIS adapter is not open")
        return self.agent_graph.get_agent(agent_id)

    async def initial_posts(self, posts: list[dict[str, Any]]) -> None:
        if not posts:
            return
        from oasis import ActionType, ManualAction
        actions = {}
        for post in posts:
            actions[self.agent(int(post["agent_id"]))] = ManualAction(
                action_type=ActionType.CREATE_POST,
                action_args={"content": str(post["content"])},
            )
        await self.env.step(actions)

    async def llm_round(self, agent_ids: list[int]) -> None:
        from oasis import LLMAction
        await self.env.step({self.agent(agent_id): LLMAction() for agent_id in agent_ids})

    async def close(self) -> None:
        if self.env is not None:
            await self.env.close()
            self.env = None
