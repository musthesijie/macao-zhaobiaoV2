import unittest
import threading
import tempfile
from pathlib import Path
from unittest.mock import patch

from app import (REFRESH_SECONDS, GazetteParser, agency_category, clean_summary,
    connect, dashboard, ledger, ledger_session_token, month_index, normalize_hengqin_articles,
    normalize_pcp_tenders, scheduler, valid_month)


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

    def test_hengqin_latest_keeps_twenty_articles(self):
        payload = {"articles": [{"id": i, "title": f"採購公告 {i}", "date": "2026-08-17",
            "url": f"https://www.hengqin.gov.cn/example/{i}.html", "publish_time": i} for i in range(25)]}
        self.assertEqual(len(normalize_hengqin_articles(payload)), 20)
        self.assertEqual(normalize_hengqin_articles(payload)[0]["id"], "24")

    def test_normalizes_public_procurement_platform_tenders(self):
        payload = {"code": "200", "data": {"rows": [{"id": "notice-1", "title": "供應NAS網絡存儲硬盤",
            "noticeTime": "2026-09-15 12:09:55", "publishDepartment": "協和澳門醫學中心",
            "publishNumber": "6012/PP/2026", "cgType": "4"}]}}
        item = normalize_pcp_tenders(payload)[0]
        self.assertEqual(item["agency"], "協和澳門醫學中心")
        self.assertEqual(item["published_date"], "2026-09-15")
        self.assertIn("id=notice-1", item["detail_url"])
        self.assertIn("publishNumber=6012%2FPP%2F2026", item["detail_url"])


class SchedulerTests(unittest.TestCase):
    def test_default_refresh_interval_is_twelve_hours(self):
        self.assertEqual(REFRESH_SECONDS, 12 * 60 * 60)

    def test_scheduler_keeps_running_after_a_failed_refresh(self):
        stopped = threading.Event()
        calls = []

        def refresh_once():
            calls.append(len(calls))
            if len(calls) == 1:
                raise RuntimeError("temporary source failure")
            stopped.set()

        scheduler(interval=0.001, stop_event=stopped, refresh_fn=refresh_once)
        self.assertEqual(calls, [0, 1])


class LedgerTests(unittest.TestCase):
    def test_validates_month_and_creates_stable_opaque_session(self):
        self.assertTrue(valid_month("2026-09"))
        self.assertFalse(valid_month("2026-13"))
        self.assertFalse(valid_month("September"))
        self.assertLess(month_index("2026-12"), month_index("2027-01"))
        self.assertEqual(ledger_session_token(), ledger_session_token())
        self.assertNotIn("28700847", ledger_session_token())

    def test_monthly_ledger_merges_sources_and_decisions(self):
        with tempfile.TemporaryDirectory() as directory, patch("app.DATA_DIR", Path(directory)), patch("app.DB_PATH", Path(directory) / "test.db"):
            db = connect()
            db.execute("INSERT INTO bulletin_tenders VALUES (?,?,?,?,?,?,?)",
                ("g1", "衛生局", "醫療設備", "2026-09-05", "https://example.test/g1", "https://example.test/b", "now"))
            db.execute("INSERT INTO hengqin_tenders VALUES (?,?,?,?,?,?)",
                ("h1", "合作區", "辦公用品", "2026-09-06", "https://example.test/h1", "now"))
            db.execute("INSERT INTO pcp_tenders VALUES (?,?,?,?,?,?)",
                ("p1", "公共採購平台", "資訊設備", "2026-10-02", "https://example.test/p1", "now"))
            db.execute("INSERT INTO tender_decisions VALUES (?,?,?,?)",
                ("gazette", "g1", "participate", "2026-09-07T00:00:00+00:00"))
            db.commit()
            items = ledger(db, "2026-09")
            range_items = ledger(db, "2026-09", "2026-10")
            db.close()
        self.assertEqual([item["tender_id"] for item in items], ["h1", "g1"])
        self.assertEqual([item["tender_id"] for item in range_items], ["p1", "h1", "g1"])
        self.assertIsNone(items[0]["decision"])
        self.assertEqual(items[1]["decision"], "participate")


class DashboardTests(unittest.TestCase):
    def test_latest_sections_keep_gazette_and_procurement_separate(self):
        with tempfile.TemporaryDirectory() as directory, patch("app.DATA_DIR", Path(directory)), patch("app.DB_PATH", Path(directory) / "test.db"):
            db = connect()
            for number in range(25):
                date = f"2026-09-{number + 1:02d}"
                db.execute("INSERT INTO bulletin_tenders VALUES (?,?,?,?,?,?,?)",
                    (f"g{number:02d}", "衛生局", f"公報項目 {number}", date,
                     f"https://example.test/g{number}", "https://example.test/b", "now"))
                db.execute("INSERT INTO pcp_tenders VALUES (?,?,?,?,?,?)",
                    (f"p{number:02d}", "採購部門", f"平台項目 {number}", date,
                     f"https://example.test/p{number}", "now"))
            db.commit()
            result = dashboard(db)
            db.close()
        self.assertEqual(len(result["latest"]), 20)
        self.assertEqual(len(result["pcp_latest"]), 20)
        self.assertTrue(all(item["id"].startswith("g") and item["source"] == "公報原文" for item in result["latest"]))
        self.assertTrue(all(item["id"].startswith("p") and item["source"] == "採購平台" for item in result["pcp_latest"]))


if __name__ == "__main__":
    unittest.main()
