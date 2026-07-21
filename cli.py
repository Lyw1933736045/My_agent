"""My_agent 官方文件事实提取与候选管理 CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from typing import Sequence


COMMANDS = {
    "brief",
    "discover",
    "extract",
    "list",
    "process",
    "reject",
    "search-web",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in COMMANDS:
        parser = argparse.ArgumentParser(description="管理国务院政策候选文件")
        subparsers = parser.add_subparsers(dest="command", required=True)

        discover_parser = subparsers.add_parser("discover", help="发现最新国务院政策")
        discover_parser.add_argument("--limit", type=int, default=20)

        search_parser = subparsers.add_parser(
            "search-web", help="按自然语言主题检索国务院政策"
        )
        search_parser.add_argument("--query", required=True)
        search_parser.add_argument("--limit", type=int, default=10)

        subparsers.add_parser("list", help="查看候选文件")

        brief_parser = subparsers.add_parser("brief", help="基于多个候选生成事实简报")
        brief_parser.add_argument("--ids", required=True)

        extract_parser = subparsers.add_parser("extract", help="只读验证一条候选")
        extract_parser.add_argument("--id", type=int, required=True, dest="document_id")

        process_parser = subparsers.add_parser("process", help="处理一条候选")
        process_parser.add_argument("--id", type=int, required=True, dest="document_id")

        reject_parser = subparsers.add_parser("reject", help="拒绝一条候选")
        reject_parser.add_argument("--id", type=int, required=True, dest="document_id")
        return parser.parse_args(arguments)

    parser = argparse.ArgumentParser(description="读取一个官方网页并提取金融事件事实")
    parser.set_defaults(command="url")
    parser.add_argument("official_url", nargs="?", help="政府、监管机构或交易所官方文件 URL")
    parser.add_argument("--no-save", action="store_true", help="不保存 state JSON")
    return parser.parse_args(arguments)


def _create_agent():
    from .agent import FinancialResearchAgent
    from .utils.config import Settings

    return FinancialResearchAgent(Settings())


def _create_llm_client():
    from .llms import LLMClient
    from .utils.config import Settings

    settings = Settings()
    return LLMClient(
        api_key=settings.QUERY_ENGINE_API_KEY,
        model_name=settings.QUERY_ENGINE_MODEL_NAME,
        base_url=settings.QUERY_ENGINE_BASE_URL,
        timeout=settings.LLM_REQUEST_TIMEOUT,
    )


def _run_url(args: argparse.Namespace) -> int:
    official_url = (args.official_url or "").strip()
    if not official_url:
        official_url = input("请输入官方文件 URL：").strip()
    if not official_url:
        print("错误：official_url 不能为空。", file=sys.stderr)
        return 2

    try:
        agent = _create_agent()
        verification = agent.verify_official_url(official_url)
        if verification.source_name:
            print(
                f"识别到官方机构：{verification.source_name}"
                f"（{verification.verification_status}）",
                file=sys.stderr,
            )
        else:
            print(
                "未识别为预定义官方来源；将继续读取，但验证状态为 unverified。",
                file=sys.stderr,
            )
        print(verification.verification_message, file=sys.stderr)
        state, state_path = agent.research_official_url(
            official_url,
            save_state=not args.no_save,
        )
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(asdict(state), ensure_ascii=False, indent=2))
    if state_path:
        print(f"\nstate JSON：{state_path}", file=sys.stderr)
    return 0


def _run_discover(args: argparse.Namespace) -> int:
    from .discovery import StateCouncilDiscovery
    from .storage import Database

    try:
        found, inserted = StateCouncilDiscovery(Database()).discover(args.limit)
    except Exception as exc:
        print(f"发现失败：{exc}", file=sys.stderr)
        return 1
    print(f"发现 {found} 条，新增 {inserted} 条，重复 {found - inserted} 条。")
    return 0


def _run_list() -> int:
    from .storage import Database

    documents = Database().list_documents()
    if not documents:
        print("暂无候选文件。")
        return 0
    print("ID\t发布日期\t标题\t状态\tURL")
    for document in documents:
        print(
            f"{document.id}\t{document.published_at or '-'}\t{document.title}\t"
            f"{document.status}\t{document.url}"
        )
    return 0


def _run_search_web(args: argparse.Namespace) -> int:
    from .nodes import QueryPlanNode
    from .storage import Database
    from .tools import StateCouncilSearch
    from .utils.config import Settings

    try:
        settings = Settings()
        plan = QueryPlanNode(_create_llm_client()).run({"query": args.query})
        searcher = StateCouncilSearch(
            timeout=settings.WEB_REQUEST_TIMEOUT,
            user_agent=settings.WEB_USER_AGENT,
        )
        candidates = searcher.search(plan["search_queries"], limit=args.limit)
        database = Database()
    except Exception as exc:
        print(f"主题检索失败：{exc}", file=sys.stderr)
        return 1

    print(f"主题：{plan['topic']}")
    print("检索词：")
    for index, query in enumerate(plan["search_queries"], 1):
        print(f"{index}. {query}")
    if not candidates:
        print("未发现通过官方来源校验的政策详情页。")
        return 0

    print("ID\t标题\t来源\tURL")
    for candidate in candidates:
        document_id, _ = database.upsert_document(
            source_id=candidate.source_id,
            title=candidate.title,
            url=candidate.url,
            published_at=candidate.published_at,
        )
        print(
            f"{document_id}\t{candidate.title}\t"
            f"{candidate.source_name}\t{candidate.url}"
        )
    return 0


def _parse_document_ids(raw_ids: str) -> list[int]:
    result = []
    for item in raw_ids.split(","):
        value = item.strip()
        if not value or not value.isdigit() or int(value) < 1:
            raise ValueError("--ids 必须是逗号分隔的正整数")
        document_id = int(value)
        if document_id not in result:
            result.append(document_id)
    if not result:
        raise ValueError("--ids 不能为空")
    return result


def _run_brief(args: argparse.Namespace) -> int:
    from .agent import PROJECT_ROOT
    from .nodes import BriefNode
    from .storage import Database

    try:
        document_ids = _parse_document_ids(args.ids)
        documents = Database().get_documents(document_ids)
        agent = _create_agent()
    except Exception as exc:
        print(f"简报准备失败：{exc}", file=sys.stderr)
        return 1

    successful = []
    for document in documents:
        try:
            state, _ = agent.research_official_url(document.url, save_state=True)
            if state.event_fact is None:
                raise ValueError("事实提取未返回 EventFact")
            official_url = (
                state.source_document.final_url
                if state.source_document
                else document.url
            )
            successful.append(
                {
                    "document_id": document.id,
                    "official_url": official_url,
                    "event_fact": asdict(state.event_fact),
                }
            )
            print(f"ID {document.id}：事实提取成功")
        except Exception as exc:
            print(f"ID {document.id}：跳过，原因：{exc}", file=sys.stderr)

    if not successful:
        print("简报生成失败：没有可用的已验证事实。", file=sys.stderr)
        return 1

    try:
        brief = BriefNode(agent.llm_client).run(
            {
                "topic": "所选国务院政策文件综合主题",
                "documents": successful,
            }
        )
        output_dir = PROJECT_ROOT / "reports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / (
            f"policy_brief_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        )
        output_path.write_text(brief.strip() + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"简报生成失败：{exc}", file=sys.stderr)
        return 1

    print(brief)
    print(f"\n简报文件：{output_path}")
    return 0


def _run_process(args: argparse.Namespace) -> int:
    from .storage import Database

    database = Database()
    document = database.get_document(args.document_id)
    if document is None:
        print(f"处理失败：未找到候选文件 ID：{args.document_id}", file=sys.stderr)
        return 1
    if document.status == "rejected":
        print("处理失败：该候选已被拒绝。", file=sys.stderr)
        return 1

    try:
        agent = _create_agent()
        state, state_path = agent.research_official_url(document.url, save_state=True)
        if state.event_fact is None:
            raise ValueError("事实提取未返回 EventFact")
        fact_id = database.save_event_fact(document.id, state.event_fact)
        database.update_status(document.id, "processed", None)
    except Exception as exc:
        database.update_status(document.id, "failed", str(exc))
        print(f"处理失败：{exc}", file=sys.stderr)
        return 1

    print(f"候选 {document.id} 已处理，event_fact ID：{fact_id}")
    if state_path:
        print(f"state JSON：{state_path}")
    return 0


def _run_extract(args: argparse.Namespace) -> int:
    """按候选 ID 调用现有事实链路，不写数据库或改变候选状态。"""
    from .storage import Database

    document = Database().get_document(args.document_id)
    if document is None:
        print(f"提取失败：未找到候选文件 ID：{args.document_id}", file=sys.stderr)
        return 1

    print(f"候选 ID：{document.id}")
    print(f"候选标题：{document.title}")
    print(f"候选状态：{document.status}")
    print(f"候选 URL：{document.url}")
    if document.status == "rejected":
        print(
            "注意：该候选当前状态为 rejected，本次仅执行验证，不修改状态。",
            file=sys.stderr,
        )

    try:
        agent = _create_agent()
        state, state_path = agent.research_official_url(
            document.url,
            save_state=True,
        )
        if state.event_fact is None:
            raise ValueError("事实提取未返回 EventFact")
    except Exception as exc:
        print(f"提取失败：{exc}", file=sys.stderr)
        return 1

    source = state.source_document
    if source:
        print(f"来源验证：{source.verification_status}")
        print(f"最终 URL：{source.final_url}")
        print(f"是否重定向：{'是' if source.redirected else '否'}")
        print(f"内容类型：{source.content_type}")
        print(f"正文长度：{len(source.content)}")
    print("LLM 调用：成功")
    print("提取结果：")
    print(json.dumps(asdict(state.event_fact), ensure_ascii=False, indent=2))
    if state_path:
        print(f"state JSON：{state_path}")
    return 0


def _run_reject(args: argparse.Namespace) -> int:
    from .storage import Database

    try:
        Database().update_status(args.document_id, "rejected", None)
    except Exception as exc:
        print(f"拒绝失败：{exc}", file=sys.stderr)
        return 1
    print(f"候选 {args.document_id} 已标记为 rejected。")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "discover":
        return _run_discover(args)
    if args.command == "search-web":
        return _run_search_web(args)
    if args.command == "list":
        return _run_list()
    if args.command == "brief":
        return _run_brief(args)
    if args.command == "extract":
        return _run_extract(args)
    if args.command == "process":
        return _run_process(args)
    if args.command == "reject":
        return _run_reject(args)
    return _run_url(args)


if __name__ == "__main__":
    raise SystemExit(main())
