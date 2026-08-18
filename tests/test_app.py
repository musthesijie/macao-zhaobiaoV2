import unittest
from app import GazetteParser, agency_category, clean_summary, normalize_hengqin_articles


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

    def test_normalizes_hengqin_articles(self):
        payload = {"articles": [{"id": 123, "title": "採購公告", "date": "2026-08-17",
            "source": "橫琴部門", "url": "http://www.hengqin.gov.cn/example/123.html",
            "visible_publish_time": 100}]}
        item = normalize_hengqin_articles(payload)[0]
        self.assertEqual(item["agency"], "橫琴部門")
        self.assertEqual(item["detail_url"], "https://www.hengqin.gov.cn/example/123.html")


if __name__ == "__main__":
    unittest.main()
