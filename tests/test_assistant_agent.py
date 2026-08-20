import json
import unittest
from unittest.mock import MagicMock

from My_agent.assistant.agent import CaseAssistantAgent
from My_agent.assistant.memory import SessionMemory, SessionMemoryStore
from My_agent.assistant.tools import AssistantToolbox
from My_agent.llms import LLMToolCall, LLMTurn
from My_agent.run_repository import CaseRecord


def _case():
    return CaseRecord(
        case_id="case-current",
        case_key="k1",
        query="测试问题",
        topic="测试主题",
        status="completed",
        progress="",
        report="# brief",
        report_data={"title": "测试主题", "executive_summary": ["摘要"]},
        error=None,
        metadata={},
    )


class SessionMemoryTests(unittest.TestCase):
    def test_keeps_recent_turns_and_case_id(self):
        store = SessionMemoryStore()
        memory = store.get_or_create("s1", "case-a")
        for index in range(12):
            memory.add_message("user", f"问{index}")
            memory.add_message("assistant", f"答{index}")
        self.assertEqual(len(memory.messages), 16)
        self.assertEqual(memory.messages[0]["content"], "问4")
        again = store.get_or_create("s1")
        self.assertEqual(again.case_id, "case-a")

    def test_bind_job_and_refresh_status(self):
        memory = SessionMemory("s1")
        memory.bind_job(run_id="run-1", status="running", topic="试点", case_id="case-n")
        self.assertEqual(memory.run_id, "run-1")
        self.assertEqual(memory.run_status, "running")
        record = MagicMock(status="completed", topic="试点", parent_event_id="case-n")
        repository = MagicMock()
        repository.get.return_value = record
        repository.get_case.return_value = None
        llm = MagicMock()
        llm.invoke_messages.return_value = LLMTurn(content="已经生成完成。")
        agent = CaseAssistantAgent(repository, llm, toolbox=MagicMock())
        held = agent.memory_store.get_or_create("sess-held", "case-n")
        held.bind_job(run_id="run-1", status="running", topic="试点")
        agent.chat("刚才那个好了吗", session_id="sess-held")
        self.assertEqual(held.run_status, "completed")
        prompt = llm.invoke_messages.call_args.args[0][0]["content"]
        self.assertIn("run-1", prompt)
        self.assertIn("completed", prompt)
        self.assertIn("pending_topic", prompt)

    def test_generate_sets_started_job_only_that_turn(self):
        llm = MagicMock()
        llm.invoke_messages.side_effect = [
            LLMTurn(
                content="",
                tool_calls=[LLMToolCall(
                    id="1",
                    name="report_manager",
                    arguments='{"action":"generate","topic":"香港国债期货"}',
                )],
                raw_assistant_message={"role": "assistant", "tool_calls": []},
            ),
            LLMTurn(content="已经开始生成，可以问进度。"),
            LLMTurn(content="还在抓取。"),
        ]
        toolbox = MagicMock()
        toolbox.execute.return_value = {
            "ok": True,
            "started": True,
            "case_id": "case-new",
            "run_id": "run-new",
            "topic": "香港国债期货",
            "status": "running",
        }
        repository = MagicMock()
        repository.get.return_value = MagicMock(
            status="running",
            progress="正在聚合来源",
            topic="香港国债期货",
            parent_event_id="case-new",
        )
        repository.get_case.return_value = None
        agent = CaseAssistantAgent(repository, llm, toolbox=toolbox)
        first = agent.chat("需要生成", session_id="job-sess")
        self.assertEqual(first.started_job["run_id"], "run-new")
        self.assertEqual(first.job["run_id"], "run-new")
        self.assertIsNone(first.open_case_id)
        toolbox.execute.assert_called_once()
        self.assertEqual(toolbox.execute.call_args.args[0], "report_manager")

        second = agent.chat("进度呢", session_id="job-sess")
        self.assertIsNone(second.started_job)
        self.assertEqual(second.job["status"], "running")
        prompt = llm.invoke_messages.call_args.args[0][0]["content"]
        self.assertIn("正在聚合来源", prompt)
        self.assertEqual(toolbox.execute.call_count, 1)

    def test_case_report_get_sets_open_case_id(self):
        llm = MagicMock()
        llm.invoke_messages.side_effect = [
            LLMTurn(
                content="",
                tool_calls=[LLMToolCall(
                    id="1",
                    name="case_report",
                    arguments='{"action":"get","case_id":"case-hist"}',
                )],
                raw_assistant_message={"role": "assistant", "tool_calls": []},
            ),
            LLMTurn(content="已经打开那份简报。"),
        ]
        toolbox = MagicMock()
        toolbox.execute.return_value = {
            "ok": True,
            "has_report": True,
            "case_id": "case-hist",
            "title": "历史简报",
        }
        result = CaseAssistantAgent(MagicMock(), llm, toolbox=toolbox).chat(
            "打开那份",
            session_id="open-sess",
        )
        self.assertEqual(result.open_case_id, "case-hist")
        self.assertIsNone(result.started_job)



class AssistantToolTests(unittest.TestCase):
    def test_case_report_query_deep_uses_current_case_raw_only(self):
        qa = MagicMock()
        qa.answer.return_value = MagicMock(
            case_id="case-current",
            mode="deep",
            answer="原文如此",
            citations=[],
            evidence=[],
            retrieved_count=1,
            retrieval_scope="case_raw",
        )
        repository = MagicMock()
        repository.get_case.return_value = _case()
        box = AssistantToolbox(repository, MagicMock(), qa_service=qa)
        result = box.case_report(
            {"action": "query", "question": "定价权是什么意思", "mode": "deep"},
            SessionMemory("s1", case_id="case-current"),
        )
        self.assertTrue(result["ok"])
        kwargs = qa.answer.call_args.kwargs
        self.assertEqual(kwargs["include_historical"], False)
        self.assertEqual(kwargs["source_types"], ("raw_document",))

    def test_generate_starts_full_research_not_old_brief_rewrite(self):
        start_research = MagicMock(return_value={
            "ok": True,
            "started": True,
            "case_id": "case-new",
            "run_id": "run-new",
            "topic": "人民币外汇期货试点",
        })
        repository = MagicMock()
        box = AssistantToolbox(
            repository,
            MagicMock(),
            qa_service=MagicMock(),
            start_research=start_research,
        )
        memory = SessionMemory("s1", case_id="case-yushu")
        result = box.report_manager(
            {"action": "generate", "topic": "人民币外汇期货试点"},
            memory,
        )
        start_research.assert_called_once_with("人民币外汇期货试点")
        repository.begin_case_brief.assert_not_called()
        self.assertTrue(result["started"])
        self.assertEqual(memory.case_id, "case-new")
        self.assertEqual(memory.run_id, "run-new")
        self.assertEqual(memory.run_status, "running")

    def test_report_search_does_not_generate(self):
        repository = MagicMock()
        repository.find_cases_for_lookup.return_value = []
        box = AssistantToolbox(repository, MagicMock(), qa_service=MagicMock())
        result = box.report_manager(
            {"action": "search", "topic": "人民币外汇期货试点"},
            SessionMemory("s1"),
        )
        self.assertFalse(result["found"])
        repository.begin_case_brief.assert_not_called()
        self.assertEqual(result["message"], "当前没有找到该主题的已有简报，是否为你生成一份新的？")

    def test_generate_uses_pending_topic(self):
        start_research = MagicMock(return_value={
            "ok": True,
            "started": True,
            "case_id": "case-new",
            "run_id": "run-new",
            "topic": "香港国债期货",
        })
        box = AssistantToolbox(
            MagicMock(),
            MagicMock(),
            qa_service=MagicMock(),
            start_research=start_research,
        )
        memory = SessionMemory("s1")
        memory.pending_topic = "香港国债期货"
        result = box.report_manager({"action": "generate"}, memory)
        start_research.assert_called_once_with("香港国债期货")
        self.assertTrue(result["started"])
        self.assertIsNone(memory.pending_topic)

    def test_search_knowledge_queries_whole_raw_corpus(self):
        retriever = MagicMock()
        retriever.search_all.return_value = [{
            "case_id": "old",
            "title": "历史报道",
            "url": "https://example.com/old",
            "content": "人民币外汇期货",
            "published_at": "2025-01-01",
            "similarity_score": 0.8,
            "chunk_id": "c1",
        }]
        embedding = MagicMock()
        embedding.embed_query.return_value = [0.1]
        reranker = MagicMock()
        reranker.rerank.side_effect = lambda query, chunks, top_n=None: chunks
        box = AssistantToolbox(
            MagicMock(),
            MagicMock(),
            qa_service=MagicMock(),
            embedding_service=embedding,
            reranker_service=reranker,
            retriever=retriever,
        )
        result = box.search_knowledge({"query": "人民币外汇期货"}, SessionMemory("s1"))
        self.assertEqual(result["count"], 1)
        self.assertEqual(retriever.search_all.call_args.kwargs["source_types"], ["raw_document"])


class CaseAssistantAgentTests(unittest.TestCase):
    def test_tool_call_then_final_answer_uses_session_history(self):
        llm = MagicMock()
        llm.invoke_messages.side_effect = [
            LLMTurn(
                content="",
                tool_calls=[LLMToolCall(
                    id="call-1",
                    name="case_report",
                    arguments=json.dumps({
                        "action": "query",
                        "question": "那企业呢",
                        "mode": "analysis",
                    }),
                )],
                raw_assistant_message={"role": "assistant", "content": None, "tool_calls": []},
            ),
            LLMTurn(content="结合上一轮简报，对企业更有利于套保。"),
        ]
        toolbox = MagicMock()
        toolbox.execute.return_value = {
            "ok": True,
            "answer": "有利于企业套保",
            "mode": "analysis",
            "citations": [{"source_id": "S01", "claim": "套保", "title": "t", "source_name": "", "url": "https://example.com"}],
            "evidence": [{"source_id": "S01", "quote": "证据"}],
            "retrieved_count": 1,
            "retrieval_scope": "brief",
        }
        agent = CaseAssistantAgent(
            MagicMock(),
            llm,
            memory_store=SessionMemoryStore(),
            toolbox=toolbox,
        )
        first_memory = agent.memory_store.get_or_create("sess", "case-current")
        first_memory.add_message("user", "这份简报主要怎么看这个政策？")
        first_memory.add_message("assistant", "政策总体偏利好。")
        result = agent.chat("那企业呢", session_id="sess", case_id="case-current")
        self.assertIn("企业", result.answer)
        toolbox.execute.assert_called_once()
        self.assertEqual(toolbox.execute.call_args.args[0], "case_report")
        self.assertEqual(result.retrieval_scope, "brief")
        prompt_messages = llm.invoke_messages.call_args_list[0].args[0]
        self.assertTrue(any("这份简报主要怎么看这个政策" in str(item.get("content")) for item in prompt_messages))
        self.assertIsNotNone(llm.invoke_messages.call_args_list[0].kwargs.get("tools"))

    def test_stops_after_max_tool_steps(self):
        llm = MagicMock()
        looping = LLMTurn(
            content="",
            tool_calls=[LLMToolCall(id="x", name="search_knowledge", arguments='{"query":"人民币"}')],
            raw_assistant_message={"role": "assistant", "tool_calls": []},
        )
        llm.invoke_messages.side_effect = [looping, looping, looping, LLMTurn(content="到此为止")]
        toolbox = MagicMock()
        toolbox.execute.return_value = {"ok": True, "count": 0, "results": []}
        result = CaseAssistantAgent(
            MagicMock(),
            llm,
            toolbox=toolbox,
        ).chat("过去有没有报道", session_id="s2")
        self.assertEqual(result.answer, "到此为止")
        self.assertEqual(toolbox.execute.call_count, 3)



if __name__ == "__main__":
    unittest.main()
