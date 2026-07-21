"""独立金融研究 Agent 的顺序编排。"""

import re
from datetime import datetime
from pathlib import Path
from loguru import logger

from .llms import LLMClient
from .nodes import (
    FactNode,
    FirstSearchNode,
    FirstSummaryNode,
    ReflectionNode,
    ReflectionSummaryNode,
    ReportFormattingNode,
    ReportStructureNode,
)
from .state import Paragraph, SourceDocument, State
from .tools import TavilySearchAgency, WebReader
from .utils import Settings, format_search_results_for_prompt
from .utils.official_sources import OfficialSourcesRegistry, SourceVerification


PROJECT_ROOT = Path(__file__).resolve().parent


def resolve_output_dir(configured_path: str) -> Path:
    """将输出目录限制在 My_agent 项目根目录内。"""
    candidate = Path(configured_path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError(
            f"OUTPUT_DIR 必须位于 My_agent 目录内：{resolved}"
        ) from exc
    return resolved


class FinancialResearchAgent:
    def __init__(self, config: Settings):
        self.config = config
        self.llm_client = LLMClient(
            api_key=config.QUERY_ENGINE_API_KEY,
            model_name=config.QUERY_ENGINE_MODEL_NAME,
            base_url=config.QUERY_ENGINE_BASE_URL,
            timeout=config.LLM_REQUEST_TIMEOUT,
        )
        self.search_agency = (
            TavilySearchAgency(config.TAVILY_API_KEY)
            if config.TAVILY_API_KEY
            else None
        )
        self.structure_node = ReportStructureNode(self.llm_client)
        self.first_search_node = FirstSearchNode(self.llm_client)
        self.first_summary_node = FirstSummaryNode(self.llm_client)
        self.reflection_node = ReflectionNode(self.llm_client)
        self.reflection_summary_node = ReflectionSummaryNode(self.llm_client)
        self.formatting_node = ReportFormattingNode(self.llm_client)
        self.fact_node = FactNode(self.llm_client)
        self.official_sources = OfficialSourcesRegistry()
        self.state = State()

    def verify_official_url(self, official_url: str) -> SourceVerification:
        """校验 URL 并返回可供 CLI 展示的来源识别结果。"""
        return self.official_sources.match_url(official_url)

    @staticmethod
    def _combine_source_verification(
        requested: SourceVerification,
        final: SourceVerification,
        redirected: bool,
    ) -> SourceVerification:
        if not redirected:
            return requested

        same_source = (
            requested.source_id is not None
            and requested.source_id == final.source_id
        )
        verified = (
            requested.verification_status == "verified"
            and final.verification_status == "verified"
            and same_source
        )
        selected = final if final.source_id else requested
        if verified:
            message = (
                f"请求 URL 校验通过并发生重定向；最终 URL 仍匹配 "
                f"{selected.source_name}。请求校验：{requested.verification_message}；"
                f"最终校验：{final.verification_message}"
            )
        else:
            message = (
                "请求 URL 与重定向后的最终 URL 未能同时通过同一官方来源校验；"
                f"请求校验：{requested.verification_message}；"
                f"最终校验：{final.verification_message}"
            )
        return SourceVerification(
            url=final.url,
            source_id=selected.source_id,
            source_name=selected.source_name,
            source_type=selected.source_type,
            trust_level=selected.trust_level,
            source_priority=selected.source_priority,
            domain_verified=requested.domain_verified and final.domain_verified and same_source,
            path_verified=requested.path_verified and final.path_verified and same_source,
            verification_status="verified" if verified else "unverified",
            verification_message=message,
        )

    def research_official_url(
        self, official_url: str, save_state: bool = True
    ) -> tuple[State, Path | None]:
        """读取一个官方 URL，提取事实并可选保存结构化状态。"""
        self.state = State(query=official_url.strip())
        requested_verification = self.verify_official_url(official_url)
        reader = WebReader(
            timeout=self.config.WEB_REQUEST_TIMEOUT,
            max_content_bytes=self.config.WEB_MAX_CONTENT_BYTES,
            max_text_length=self.config.WEB_MAX_TEXT_LENGTH,
            user_agent=self.config.WEB_USER_AGENT,
        )
        read_result = reader.read(official_url)
        redirected = read_result.requested_url != read_result.final_url
        final_verification = self.verify_official_url(read_result.final_url)
        verification = self._combine_source_verification(
            requested_verification,
            final_verification,
            redirected,
        )
        self.state.source_document = SourceDocument(
            official_url=read_result.requested_url,
            requested_url=read_result.requested_url,
            final_url=read_result.final_url,
            redirected=redirected,
            fetched_at=read_result.fetched_at,
            content_type=read_result.content_type,
            content=read_result.content,
            source_id=verification.source_id,
            source_name=verification.source_name,
            source_type=verification.source_type,
            trust_level=verification.trust_level,
            source_priority=verification.source_priority,
            domain_verified=verification.domain_verified,
            path_verified=verification.path_verified,
            verification_status=verification.verification_status,
            verification_message=verification.verification_message,
        )
        self.state.event_fact = self.fact_node.run(self.state.source_document)
        self.state.is_completed = True
        self.state.touch()

        state_path = self._save_fact_state() if save_state else None
        return self.state, state_path

    def research(self, query: str, save_report: bool = True) -> str:
        query = query.strip()
        if not query:
            raise ValueError("研究事件不能为空")
        self.state = State(
            query=query,
            data_cutoff=datetime.now().astimezone().isoformat(timespec="minutes"),
        )
        self._generate_report_structure()
        for index in range(len(self.state.paragraphs)):
            self._initial_search_and_summary(index)
            self._reflection_loop(index)
            self.state.paragraphs[index].research.is_completed = True
        report = self._generate_final_report()
        if save_report:
            self._save_report(report)
        return report

    def _generate_report_structure(self) -> None:
        plan = self.structure_node.run(
            {"query": self.state.query, "max_paragraphs": self.config.MAX_PARAGRAPHS}
        )
        self.state.report_title = plan["report_title"]
        self.state.paragraphs = [
            Paragraph(title=item["title"], content=item["content"])
            for item in plan["paragraphs"]
        ]
        self.state.touch()

    def _search(self, search_plan: dict) -> tuple[str, list[dict]]:
        if self.search_agency is None:
            raise ValueError("搜索流程需要配置 TAVILY_API_KEY")
        query = search_plan["search_query"]
        response = self.search_agency.search(
            query=query,
            max_results=self.config.MAX_SEARCH_RESULTS,
            search_depth=search_plan.get("search_depth", "basic"),
            days=search_plan.get("days"),
        )
        results = [
            {
                "title": result.title,
                "url": result.url,
                "published_date": result.published_date,
                "source": result.source,
                "content": result.content,
                "score": result.score,
            }
            for result in response.results
        ]
        return query, results

    def _initial_search_and_summary(self, paragraph_index: int) -> None:
        paragraph = self.state.paragraphs[paragraph_index]
        plan = self.first_search_node.run(
            {
                "research_question": self.state.query,
                "title": paragraph.title,
                "content": paragraph.content,
            }
        )
        query, results = self._search(plan)
        paragraph.research.add_search_results(query, results)
        paragraph.research.latest_summary = self.first_summary_node.run(
            {
                "research_question": self.state.query,
                "title": paragraph.title,
                "content": paragraph.content,
                "search_query": query,
                "search_results": format_search_results_for_prompt(
                    results, self.config.SEARCH_CONTENT_MAX_LENGTH
                ),
            }
        )
        self.state.touch()

    def _reflection_loop(self, paragraph_index: int) -> None:
        paragraph = self.state.paragraphs[paragraph_index]
        for _ in range(self.config.MAX_REFLECTIONS):
            plan = self.reflection_node.run(
                {
                    "research_question": self.state.query,
                    "title": paragraph.title,
                    "content": paragraph.content,
                    "paragraph_latest_state": paragraph.research.latest_summary,
                }
            )
            query, results = self._search(plan)
            paragraph.research.add_search_results(query, results)
            paragraph.research.latest_summary = self.reflection_summary_node.run(
                {
                    "research_question": self.state.query,
                    "title": paragraph.title,
                    "content": paragraph.content,
                    "search_query": query,
                    "search_results": format_search_results_for_prompt(
                        results, self.config.SEARCH_CONTENT_MAX_LENGTH
                    ),
                    "paragraph_latest_state": paragraph.research.latest_summary,
                }
            )
            paragraph.research.reflection_iteration += 1
            self.state.touch()

    def _report_input(self) -> dict:
        return {
            "report_title": self.state.report_title,
            "research_question": self.state.query,
            "data_cutoff": self.state.data_cutoff,
            "paragraphs": [
                {"title": item.title, "content": item.research.latest_summary}
                for item in self.state.paragraphs
            ],
            "sources": [
                {
                    "title": source.title,
                    "source": source.source,
                    "published_date": source.published_date,
                    "url": source.url,
                }
                for source in self.state.source_list()
            ],
        }

    def _generate_final_report(self) -> str:
        report_input = self._report_input()
        try:
            body = self.formatting_node.run(report_input)
        except Exception as exc:
            logger.warning("最终格式化失败，使用确定性模板：{}", exc)
            body = self.formatting_node.format_manually(report_input)
        report = self._ensure_required_sections(body, report_input)
        self.state.final_report = report
        self.state.is_completed = True
        self.state.touch()
        return report

    @staticmethod
    def _ensure_required_sections(body: str, report_input: dict) -> str:
        lines = [body.strip()]
        if "数据截止时间" not in body:
            lines.extend(["", f"> 数据截止时间：{report_input['data_cutoff']}"])
        if "## 来源列表" not in body:
            lines.extend(["", "## 来源列表", ""])
            if report_input["sources"]:
                for index, source in enumerate(report_input["sources"], 1):
                    date = source["published_date"] or "发布日期未知"
                    origin = source["source"] or "来源未知"
                    lines.append(
                        f"{index}. [{source['title'] or origin}]"
                        f"({source['url']}) — {origin}，{date}"
                    )
            else:
                lines.append("- 本次搜索未返回可用来源。")
        if "## 风险与局限" not in body:
            lines.extend(
                [
                    "",
                    "## 风险与局限",
                    "",
                    "本报告仅基于数据截止时间前可检索的公开网页资料，"
                    "未接入实时行情或结构化金融数据库，信息可能存在遗漏或时滞。",
                ]
            )
        if "## 免责声明" not in body:
            lines.extend(
                ["", "## 免责声明", "", "本报告仅供信息研究，不构成任何投资建议。"]
            )
        return "\n".join(lines).strip() + "\n"

    def _save_report(self, report: str) -> Path:
        output_dir = resolve_output_dir(self.config.OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_query = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", self.state.query)[:40]
        report_path = output_dir / f"financial_report_{safe_query}_{timestamp}.md"
        report_path.write_text(report, encoding="utf-8")
        if self.config.SAVE_INTERMEDIATE_STATES:
            self.state.save_to_file(output_dir / f"state_{safe_query}_{timestamp}.json")
        logger.info("报告已保存：{}", report_path)
        return report_path

    def _save_fact_state(self) -> Path:
        output_dir = resolve_output_dir(self.config.OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        state_path = output_dir / f"fact_state_{timestamp}.json"
        self.state.save_to_file(state_path)
        logger.info("事实状态已保存：{}", state_path)
        return state_path
