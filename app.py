#!/usr/bin/env python3
"""Macao public tender collector and dependency-free web server."""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "tenders.db"
API_URL = os.getenv("TENDER_API_URL", "https://iam.apigateway.data.gov.mo/procurement_tender")
APP_CODE = os.getenv("TENDER_APP_CODE", "09d43a591fba407fb862412970667de4")
REFRESH_SECONDS = max(300, int(os.getenv("REFRESH_INTERVAL", "21600")))
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN", "")
refresh_lock = threading.Lock()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS tenders (
        no TEXT PRIMARY KEY, name_zh TEXT, name_cn TEXT, name_pt TEXT,
        procurement_date TEXT, announcement_date TEXT, contest_date TEXT,
        notice_zh TEXT, notice_cn TEXT, notice_pt TEXT, attachment_zh TEXT,
        attachment_pt TEXT, tender_result TEXT, award_result TEXT,
        other_title_zh TEXT, other_title_cn TEXT, other_title_pt TEXT,
        other_file_zh TEXT, other_file_pt TEXT, raw_json TEXT,
        first_seen TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS metadata
        (key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    db.commit()
    return db


def fetch_remote():
    request = Request(API_URL, headers={
        "Authorization": f"APPCODE {APP_CODE}",
        "Accept": "application/json", "User-Agent": "macao-zhaobiaoV2/1.0"
    })
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise ValueError("上游 API 返回格式不是数组")
    return payload


def refresh():
    if not refresh_lock.acquire(blocking=False):
        return {"ok": False, "message": "刷新任务正在进行中"}
    started = datetime.now(timezone.utc).isoformat()
    try:
        records = fetch_remote()
        db = connect()
        existing = {row[0] for row in db.execute("SELECT no FROM tenders")}
        incoming = set()
        values = []
        for item in records:
            number = str(item.get("no") or "").strip()
            if not number:
                continue
            incoming.add(number)
            values.append((number, item.get("nameZh", ""), item.get("nameCn", ""),
                item.get("namePt", ""), item.get("procurementDate", ""),
                item.get("announcementDate", ""), item.get("contestDate", ""),
                item.get("tenderNoticeZh", ""), item.get("tenderNoticeCn", ""),
                item.get("tenderNoticePt", ""), item.get("tenderAttachmentZh", ""),
                item.get("tenderAttachmentPt", ""), item.get("tenderResult", ""),
                item.get("awardResult", ""), item.get("otherFileTitleZh", ""),
                item.get("otherFileTitleCn", ""), item.get("otherFileTitlePt", ""),
                item.get("otherFileZh", ""), item.get("otherFilePt", ""),
                json.dumps(item, ensure_ascii=False), started, started))
        db.executemany("""INSERT INTO tenders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(no) DO UPDATE SET name_zh=excluded.name_zh,name_cn=excluded.name_cn,
            name_pt=excluded.name_pt,procurement_date=excluded.procurement_date,
            announcement_date=excluded.announcement_date,contest_date=excluded.contest_date,
            notice_zh=excluded.notice_zh,notice_cn=excluded.notice_cn,notice_pt=excluded.notice_pt,
            attachment_zh=excluded.attachment_zh,attachment_pt=excluded.attachment_pt,
            tender_result=excluded.tender_result,award_result=excluded.award_result,
            other_title_zh=excluded.other_title_zh,other_title_cn=excluded.other_title_cn,
            other_title_pt=excluded.other_title_pt,other_file_zh=excluded.other_file_zh,
            other_file_pt=excluded.other_file_pt,raw_json=excluded.raw_json,
            updated_at=excluded.updated_at""", values)
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_refresh', ?)", (started,))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_error', '')")
        db.commit(); db.close()
        return {"ok": True, "count": len(incoming), "new": len(incoming - existing), "at": started}
    except Exception as exc:
        db = connect()
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_error', ?)", (str(exc),))
        db.commit(); db.close()
        return {"ok": False, "message": str(exc), "at": started}
    finally:
        refresh_lock.release()


def scheduler():
    while True:
        refresh()
        time.sleep(REFRESH_SECONDS)


def to_public(row):
    result = dict(row)
    result.pop("raw_json", None)
    return result


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "static"), **kwargs)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def json_response(self, body, status=200):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            return self.json_response({"ok": True})
        if parsed.path == "/api/stats":
            db = connect()
            total = db.execute("SELECT count(*) FROM tenders").fetchone()[0]
            active = db.execute("SELECT count(*) FROM tenders WHERE contest_date >= date('now')").fetchone()[0]
            meta = dict(db.execute("SELECT key,value FROM metadata"))
            db.close()
            return self.json_response({"total": total, "active": active,
                "last_refresh": meta.get("last_refresh"), "last_error": meta.get("last_error")})
        if parsed.path == "/api/tenders":
            q = parse_qs(parsed.query); keyword = q.get("q", [""])[0].strip()
            year = q.get("year", [""])[0].strip(); status = q.get("status", ["all"])[0]
            try: page = max(1, int(q.get("page", ["1"])[0])); size = min(50, max(1, int(q.get("size", ["12"])[0])))
            except ValueError: return self.json_response({"error": "分页参数无效"}, 400)
            where, params = ["1=1"], []
            if keyword:
                where.append("(no LIKE ? OR name_zh LIKE ? OR name_cn LIKE ? OR name_pt LIKE ?)")
                params.extend([f"%{keyword}%"] * 4)
            if year:
                where.append("substr(announcement_date,1,4)=?"); params.append(year)
            if status == "active": where.append("contest_date >= date('now')")
            if status == "closed": where.append("contest_date < date('now')")
            clause = " AND ".join(where); db = connect()
            total = db.execute(f"SELECT count(*) FROM tenders WHERE {clause}", params).fetchone()[0]
            rows = db.execute(f"SELECT * FROM tenders WHERE {clause} ORDER BY announcement_date DESC,no DESC LIMIT ? OFFSET ?", params + [size, (page-1)*size]).fetchall()
            years = [r[0] for r in db.execute("SELECT DISTINCT substr(announcement_date,1,4) y FROM tenders WHERE announcement_date != '' ORDER BY y DESC")]
            db.close()
            return self.json_response({"items": [to_public(r) for r in rows], "total": total, "page": page, "size": size, "years": years})
        if parsed.path.startswith("/api/tenders/"):
            number = parsed.path.removeprefix("/api/tenders/")
            db = connect(); row = db.execute("SELECT * FROM tenders WHERE no=?", (number,)).fetchone(); db.close()
            return self.json_response(to_public(row) if row else {"error": "未找到"}, 200 if row else 404)
        if parsed.path.startswith("/api/"):
            return self.json_response({"error": "接口不存在"}, 404)
        self.path = "/index.html" if parsed.path == "/" else parsed.path
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/api/refresh":
            return self.json_response({"error": "接口不存在"}, 404)
        if REFRESH_TOKEN and self.headers.get("Authorization") != f"Bearer {REFRESH_TOKEN}":
            return self.json_response({"error": "未授权"}, 401)
        result = refresh()
        return self.json_response(result, 200 if result["ok"] else 503)


if __name__ == "__main__":
    connect().close()
    threading.Thread(target=scheduler, daemon=True).start()
    port = int(os.getenv("PORT", "8080"))
    print(f"澳门招标 V2 listening on 0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
