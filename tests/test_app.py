import unittest
from app import GazetteParser


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
        self.assertEqual(parser.rows[0]["summary"], "公告一則，關於供應醫療消耗品的公開招標。")


if __name__ == "__main__":
    unittest.main()
