import unittest
from app import GazetteParser, agency_category, agency_source, clean_summary, source_directory


class GazetteParserTests(unittest.TestCase):
    def test_extracts_only_official_result_table(self):
        markup = '''<table><tr><td><a href="/cn/bo/b/link/1">忽略</a></td></tr></table>
        <table class="table table-striped"><tr>
        <td><a href="/cn/bo/b/link/99622">衛生局</a>, 公告一則，關於供應醫療消耗品的公開招標。</td>
        <td><a href="https://bo.dsaj.gov.mo/bo/ii/2026/32/bo32_cn.asp">《公報》第32期,<br>2026/08/12</a></td>
        </tr></table>'''
        parser = GazetteParser(); parser.feed(markup)
        self.assertEqual(len(parser.rows), 1)
        self.assertEqual(parser.rows[0]["agency"], "衛生局")
        self.assertEqual(parser.rows[0]["id"], "99622")
        self.assertEqual(parser.rows[0]["published_date"], "2026-08-12")
        self.assertEqual(parser.rows[0]["summary"], "供應醫療消耗品的公開招標。")

    def test_summary_prefixes_are_removed(self):
        self.assertEqual(clean_summary("通告一則，關於樓宇管理服務的公開招標。"), "樓宇管理服務的公開招標。")
        self.assertEqual(clean_summary("公告一則，供應設備。"), "供應設備。")

    def test_agencies_follow_government_portal_categories(self):
        self.assertEqual(agency_category("市政署"), "行政法務範疇")
        self.assertEqual(agency_category("房屋局"), "運輸工務範疇")
        self.assertEqual(agency_category("未列出的歷史機構"), "其他")

    def test_source_directory_comes_from_original_dashboard(self):
        directory = source_directory()
        self.assertEqual(directory["count"], 61)
        self.assertEqual(directory["source"], "https://github.com/musthesijie/macao-zhaobiao")
        self.assertEqual({item["name"] for item in directory["agencies"] if item.get("needsReview")}, {"樓宇維修基金", "審計署"})
        self.assertEqual(agency_source("新聞局"), "https://www.gcs.gov.mo/news/tender/zh-hant")
        self.assertEqual(agency_source("市政署"), "https://www.iam.gov.mo/c/tender/itemlist")
        self.assertEqual(agency_source("未列出的歷史機構"), "https://www.bo.dsaj.gov.mo/cn/news/list/b/?d=13&c=&y=")


if __name__ == "__main__":
    unittest.main()
