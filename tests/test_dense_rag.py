import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from My_agent.run_repository import CaseRecord
from My_agent.tools.case_qa import CaseQAService
from My_agent.tools.embedding_service import EmbeddingService
from My_agent.tools.knowledge_indexer import KnowledgeIndexer
from My_agent.tools.reranker_service import RerankerService
from My_agent.tools.vector_retriever import VectorRetriever


def _case(case_id="case-current"):
    return CaseRecord(
        case_id=case_id,
        case_key="k1",
        query="测试问题",
        topic="测试主题",
        status="completed",
        progress="",
        report="# brief",
        report_data={
            "title": "测试主题",
            "executive_summary": ["摘要"],
            "sources": [{"id": "S01", "title": "来源一", "url": "https://example.com/a"}],
        },
        error=None,
        metadata={},
    )


class EmbeddingServiceTests(unittest.TestCase):
    def test_embed_query_and_documents(self):
        service = EmbeddingService(
            api_key="sk-test",
            model="qwen3.7-text-embedding",
            base_url="https://example.invalid/v1",
            dimension=4,
        )
        service.client = SimpleNamespace(
            embeddings=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(data=[
                    SimpleNamespace(index=i, embedding=[0.1, 0.2, 0.3, 0.4])
                    for i, _ in enumerate(kwargs["input"])
                ])
            )
        )
        query = service.embed_query("问题")
        docs = service.embed_documents(["a", "b"])
        self.assertEqual(len(query), 4)
        self.assertEqual(len(docs), 2)
        self.assertEqual(len(docs[0]), 4)


class RerankerServiceTests(unittest.TestCase):
    def test_rerank_orders_by_api_index(self):
        service = RerankerService(
            api_key="sk-test",
            model="qwen3-rerank",
            base_url="https://example.invalid/reranks",
        )
        service.client = SimpleNamespace(
            post=lambda *args, **kwargs: SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                ]},
            )
        )
        ranked = service.rerank("q", [
            {"chunk_id": "a", "content": "A"},
            {"chunk_id": "b", "content": "B"},
        ])
        self.assertEqual([item["chunk_id"] for item in ranked], ["b", "a"])
        self.assertEqual(ranked[0]["rerank_score"], 0.9)

    def test_qa_falls_back_when_rerank_fails(self):
        retriever = MagicMock()
        retriever.search_case.return_value = [
            {"chunk_id": "c1", "case_id": "case-current", "source_id": "S01",
             "content": "当前证据", "title": "t", "url": "https://example.com/a",
             "similarity_score": 0.8, "source_type": "media_insight"},
        ]
        reranker = MagicMock()
        reranker.rerank.side_effect = RuntimeError("boom")
        embedding = MagicMock()
        embedding.embed_query.return_value = [0.1, 0.2]
        llm = MagicMock()
        llm.invoke.return_value = '{"answer":"ok","citations":[{"source_id":"S01","claim":"x"}],"evidence_used":[{"source_id":"S01","quote":"当前证据"}]}'
        service = CaseQAService(
            MagicMock(),
            llm,
            embedding_service=embedding,
            reranker_service=reranker,
            retriever=retriever,
        )
        result = service.answer(_case(), "发生了什么", "analysis")
        self.assertEqual(result.answer, "ok")
        self.assertEqual(result.evidence[0].source_id, "S01")


class VectorRetrievalTests(unittest.TestCase):
    def test_case_scope_does_not_query_other_cases(self):
        repository = MagicMock()
        repository.search_knowledge_chunks.return_value = []
        VectorRetriever(repository).search_case([0.1], "case-a", source_types=["raw_document"], top_k=20)
        kwargs = repository.search_knowledge_chunks.call_args.kwargs
        self.assertEqual(kwargs["case_id"], "case-a")
        self.assertNotIn("exclude_case_id", kwargs)

    def test_global_scope_excludes_current_case(self):
        repository = MagicMock()
        repository.search_knowledge_chunks.return_value = [{"chunk_id": "h1", "case_id": "case-b"}]
        rows = VectorRetriever(repository).search_global([0.1], "case-a", top_k=15)
        kwargs = repository.search_knowledge_chunks.call_args.kwargs
        self.assertEqual(kwargs["exclude_case_id"], "case-a")
        self.assertEqual(rows[0]["case_id"], "case-b")

    def test_search_all_does_not_require_case_filter(self):
        repository = MagicMock()
        repository.search_knowledge_chunks.return_value = [{"chunk_id": "h1", "case_id": "case-b"}]
        VectorRetriever(repository).search_all([0.1], source_types=["raw_document"], top_k=12)
        kwargs = repository.search_knowledge_chunks.call_args.kwargs
        self.assertIsNone(kwargs.get("case_id"))
        self.assertEqual(kwargs["source_types"], ["raw_document"])

    def test_cosine_ranking_order(self):
        rows = [
            {"chunk_id": "low", "similarity_score": 0.1},
            {"chunk_id": "high", "similarity_score": 0.9},
            {"chunk_id": "mid", "similarity_score": 0.5},
        ]
        ranked = sorted(rows, key=lambda item: item["similarity_score"], reverse=True)
        self.assertEqual([item["chunk_id"] for item in ranked], ["high", "mid", "low"])


class KnowledgeIndexerTests(unittest.TestCase):
    def test_same_hash_skips_embedding(self):
        embedding = MagicMock()
        embedding.model = "qwen3.7-text-embedding"
        embedding.embed_documents.return_value = [[0.1, 0.2]]
        repository = MagicMock()
        repository.get_case.return_value = _case()
        repository.list_case_candidates.return_value = []
        repository.aggregate_case_prepared_analysis.return_value = {
            "media_insights": [{
                "title": "洞察",
                "url": "https://example.com/a",
                "reported_facts": ["事实"],
            }],
            "social_insights": [],
        }
        indexer = KnowledgeIndexer(repository, embedding)
        first = indexer.index_case("case-current")
        stored = repository.replace_knowledge_chunks.call_args.args[1]
        self.assertEqual(
            repository.replace_knowledge_chunks.call_args.kwargs["source_types"],
            ["raw_document", "media_insight", "social_insight", "structured_analysis"],
        )
        repository.list_knowledge_chunks.return_value = stored
        embedding.embed_documents.reset_mock()
        second = indexer.index_case("case-current")
        embedding.embed_documents.assert_not_called()
        self.assertEqual(second["embedded"], 0)
        self.assertGreater(first["embedded"], 0)

    def test_simulation_memory_replaces_previous_run_for_the_case(self):
        embedding = MagicMock()
        embedding.model = "qwen3.7-text-embedding"
        embedding.embed_documents.return_value = [[0.3, 0.4]]
        repository = MagicMock()
        repository.get_case.return_value = _case()
        repository.list_knowledge_chunks.return_value = [{
            "source_type": "simulation",
            "source_id": "sim_old_5rounds",
            "chunk_index": 0,
            "content_hash": "old",
            "embedding_model": "qwen3.7-text-embedding",
            "embedding": [0.1, 0.2],
        }]
        indexer = KnowledgeIndexer(repository, embedding)
        result = indexer.index_simulation_run(
            "case-current",
            simulation_id="sim_new_10rounds",
            memory_items=[{
                "round": 1,
                "agent_id": 0,
                "action_type": "CREATE_POST",
                "content": "发行市盈率偏高，需要冷静看待估值。",
                "basis_real_refs": ["claim_1"],
            }],
            personas=[{"display_name": "西南证券研究院", "status": "approved"}],
        )
        kwargs = repository.replace_knowledge_chunks.call_args.kwargs
        records = repository.replace_knowledge_chunks.call_args.args[1]
        self.assertEqual(kwargs["source_types"], ["simulation"])
        self.assertNotIn("source_id", kwargs)
        self.assertEqual(result["embedded"], 1)
        self.assertEqual(records[0]["source_id"], "sim_new_10rounds")
        self.assertEqual(records[0]["source_type"], "simulation")
        self.assertIn("【模拟推演，非真实信息】", records[0]["content"])
        self.assertIn("西南证券研究院", records[0]["content"])

    def test_simulation_report_is_chunked_and_replaces_old_report(self):
        embedding = MagicMock()
        embedding.model = "qwen3.7-text-embedding"
        embedding.embed_documents.return_value = [[0.5, 0.6]]
        repository = MagicMock()
        repository.get_case.return_value = _case()
        repository.list_knowledge_chunks.return_value = []
        indexer = KnowledgeIndexer(repository, embedding)
        result = indexer.index_simulation_report(
            "case-current",
            simulation_id="sim_new_10rounds",
            markdown="# 模拟结果\n\n> 本文只总结模拟生成内容。\n\n估值偏高需要冷静。",
        )
        kwargs = repository.replace_knowledge_chunks.call_args.kwargs
        records = repository.replace_knowledge_chunks.call_args.args[1]
        self.assertEqual(kwargs["source_types"], ["simulation_report"])
        self.assertEqual(result["embedded"], 1)
        self.assertEqual(records[0]["source_type"], "simulation_report")
        self.assertEqual(records[0]["source_id"], "sim_new_10rounds")
        self.assertIn("【模拟分析报告，非真实信息】", records[0]["content"])
        self.assertIn("估值偏高需要冷静", records[0]["content"])


class CaseQAServiceTests(unittest.TestCase):
    def test_fast_uses_report_data(self):
        llm = MagicMock()
        llm.invoke.return_value = '{"answer":"简报回答","citations":[],"evidence_used":[]}'
        result = CaseQAService(MagicMock(), llm).answer(_case(), "总结一下", "fast")
        self.assertEqual(result.retrieval_scope, "brief")
        payload = llm.invoke.call_args.args[1]
        self.assertIn("executive_summary", payload)

    def test_analysis_uses_vector_retrieval_and_rejects_unknown_citation(self):
        retriever = MagicMock()
        retriever.search_case.return_value = [{
            "chunk_id": "c1",
            "case_id": "case-current",
            "source_id": "S01",
            "source_type": "media_insight",
            "title": "来源一",
            "url": "https://example.com/a",
            "content": "媒体洞察",
            "similarity_score": 0.9,
        }]
        embedding = MagicMock()
        embedding.embed_query.return_value = [0.1]
        reranker = MagicMock()
        reranker.rerank.side_effect = lambda question, chunks, top_n=None: chunks
        llm = MagicMock()
        llm.invoke.return_value = '{"answer":"ok","citations":[{"source_id":"S99","claim":"编造"},{"source_id":"S01","claim":"真"}],"evidence_used":[{"source_id":"S01","quote":"媒体洞察"}]}'
        result = CaseQAService(
            MagicMock(),
            llm,
            embedding_service=embedding,
            reranker_service=reranker,
            retriever=retriever,
        ).answer(_case(), "发生了什么", "analysis")
        self.assertEqual(result.retrieval_scope, "case")
        self.assertEqual([item.source_id for item in result.citations], ["S01"])
        retriever.search_global.assert_not_called()

    def test_deep_merges_current_and_historical(self):
        retriever = MagicMock()
        retriever.search_case.return_value = [{
            "chunk_id": "c1",
            "case_id": "case-current",
            "source_id": "S01",
            "source_type": "raw_document",
            "title": "当前",
            "url": "https://example.com/a",
            "content": "当前正文",
            "similarity_score": 0.8,
        }]
        retriever.search_global.return_value = [{
            "chunk_id": "h1",
            "case_id": "case-old",
            "source_id": "S01",
            "source_type": "raw_document",
            "title": "历史",
            "url": "https://example.com/old",
            "content": "历史正文",
            "similarity_score": 0.7,
        }]
        embedding = MagicMock()
        embedding.embed_query.return_value = [0.1]
        reranker = MagicMock()
        reranker.rerank.side_effect = lambda question, chunks, top_n=None: chunks
        llm = MagicMock()
        llm.invoke.return_value = '{"answer":"对照","citations":[{"source_id":"S01","claim":"当前"},{"source_id":"hist:case-old:S01","claim":"历史"}],"evidence_used":[]}'
        result = CaseQAService(
            MagicMock(),
            llm,
            embedding_service=embedding,
            reranker_service=reranker,
            retriever=retriever,
        ).answer(_case(), "和历史比呢", "deep")
        self.assertEqual(result.retrieval_scope, "case+global")
        origins = {item.origin for item in result.evidence}
        self.assertEqual(origins, {"current", "historical"})
        self.assertTrue(any(item.source_id.startswith("hist:") for item in result.citations))
        embedding.embed_query.assert_called_once()

    def test_deep_can_stay_on_current_case_raw_documents(self):
        retriever = MagicMock()
        retriever.search_case.return_value = [{
            "chunk_id": "c1",
            "case_id": "case-current",
            "source_id": "S01",
            "source_type": "raw_document",
            "title": "当前",
            "url": "https://example.com/a",
            "content": "当前正文",
            "similarity_score": 0.8,
        }]
        embedding = MagicMock()
        embedding.embed_query.return_value = [0.1]
        reranker = MagicMock()
        reranker.rerank.side_effect = lambda question, chunks, top_n=None: chunks
        llm = MagicMock()
        llm.invoke.return_value = '{"answer":"原文","citations":[{"source_id":"S01","claim":"当前"}],"evidence_used":[]}'
        result = CaseQAService(
            MagicMock(),
            llm,
            embedding_service=embedding,
            reranker_service=reranker,
            retriever=retriever,
        ).answer(
            _case(),
            "原文怎么说",
            "deep",
            include_historical=False,
            source_types=("raw_document",),
        )
        self.assertEqual(result.retrieval_scope, "case_raw")
        retriever.search_global.assert_not_called()
        self.assertEqual(retriever.search_case.call_args.kwargs["source_types"], ["raw_document"])


if __name__ == "__main__":
    unittest.main()
