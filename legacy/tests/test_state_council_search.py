import unittest

from My_agent.legacy.official.state_council_search import StateCouncilSearch


SEARCH_PAYLOAD = {
    "code": 200,
    "msg": "操作成功",
    "searchVO": {
        "listVO": None,
        "catMap": {
            "gongwen": {
                "catName": "gongwen",
                "listVO": [
                    {
                        "title": "国务院关于新能源汽车税收优惠的通知",
                        "url": "https://www.gov.cn/zhengce/content/202601/content_7000001.htm",
                        "pubtimeStr": "2026.01.10",
                    },
                    {
                        "title": "重复结果",
                        "url": "https://www.gov.cn/zhengce/content/202601/content_7000001.htm?utm_source=x",
                        "pubtimeStr": "2026.01.10",
                    },
                    {
                        "title": "政策栏目",
                        "url": "https://www.gov.cn/zhengce/index.htm",
                    },
                    {
                        "title": "旧版路径",
                        "url": "http://www.gov.cn/zhengce/zhengceku/2015-11/22/content_10336.htm",
                    },
                ],
            },
            "bumenfile": {
                "catName": "bumenfile",
                "listVO": [
                    {
                        "title": "转载",
                        "url": "https://example.com/zhengce/content/202601/content_2.htm",
                    },
                    {
                        "title": "PDF",
                        "url": "https://www.gov.cn/file.pdf",
                    },
                    {
                        "title": "商务部等关于<em>支持</em>实体经济的通知",
                        "url": "https://www.gov.cn/zhengce/zhengceku/202604/content_7064729.htm",
                        "pubtimeStr": "2026.04.06",
                    },
                ],
            },
            "gongbao": {
                "catName": "gongbao",
                "listVO": [
                    {
                        "title": "公报条目",
                        "url": "https://www.gov.cn/gongbao/2026/issue_1/content_1.html",
                    }
                ],
            },
        },
    },
}


class StateCouncilSearchTests(unittest.TestCase):
    def test_filters_and_deduplicates_official_policy_urls(self):
        results = StateCouncilSearch().parse(SEARCH_PAYLOAD)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].source_id, "state_council")
        self.assertEqual(results[0].published_at, "2026-01-10")
        self.assertEqual(
            results[0].url,
            "https://www.gov.cn/zhengce/content/202601/content_7000001.htm",
        )
        self.assertEqual(
            results[1].title,
            "商务部等关于支持实体经济的通知",
        )
        self.assertEqual(
            results[1].url,
            "https://www.gov.cn/zhengce/zhengceku/202604/content_7064729.htm",
        )

    def test_search_merges_multiple_query_results_by_url(self):
        searcher = StateCouncilSearch()
        searcher.fetch_json = lambda query, page_size=50: SEARCH_PAYLOAD
        results = searcher.search(["新能源汽车 税收", "新能源汽车 购置税"], limit=10)
        self.assertEqual(len(results), 2)

    def test_build_search_url_uses_json_api(self):
        url = StateCouncilSearch().build_search_url("金融支持实体经济")
        self.assertIn("search-gov/data", url)
        self.assertIn("q=", url)
        self.assertIn("t=zhengcelibrary_gw_bm_gb", url)
