#!/usr/bin/env python3
"""Macao Official Gazette public-tender collector and web server."""
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "tenders.db"
SOURCE_URL = os.getenv("TENDER_SOURCE_URL", "https://www.bo.dsaj.gov.mo/cn/news/list/b/?d=13&c=&y=")
REFRESH_SECONDS = max(1800, int(os.getenv("REFRESH_INTERVAL", "43200")))
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN", "")
FETCH_WORKERS = min(8, max(1, int(os.getenv("FETCH_WORKERS", "4"))))
refresh_lock = threading.Lock()
SOURCE_DIRECTORY_PATH = ROOT / "config" / "source-urls.json"

# Categories and current entity names published by the Macao Government Portal.
# Gazette-only or former entity names deliberately fall back to 「其他」.
CATEGORY_AGENCIES = {
    "行政長官直屬機關": {"行政長官辦公室","行政會秘書處","政府總部事務局","政策研究和區域發展局","澳門基金會","新聞局","個人資料保護局","公共資產監督管理局","澳門特別行政區駐北京辦事處","在台灣澳門經濟文化辦事處","澳門駐里斯本經濟貿易辦事處","澳門駐布魯塞爾歐盟經濟貿易辦事處","澳門駐世界貿易組織經濟貿易辦事處","人才發展委員會"},
    "行政法務範疇": {"行政法務司司長辦公室","行政公職局","法務局","身份證明局","市政署","退休基金會","法律及司法培訓中心"},
    "經濟財政範疇": {"經濟財政司司長辦公室","經濟及科技發展局","財政局","旅遊局","博彩監察協調局","勞工事務局","招商投資促進局","澳門金融管理局","科學技術發展基金","統計暨普查局","消費者委員會"},
    "保安範疇": {"保安司司長辦公室","警察總局","中華人民共和國澳門特別行政區海關","治安警察局","司法警察局","消防局","懲教管理局","澳門保安部隊事務局","澳門保安部隊高等學校"},
    "社會文化範疇": {"社會文化司司長辦公室","教育及青年發展局","文化局","體育局","衛生局","藥物監督管理局","離島醫療綜合體北京協和醫院澳門醫學中心","社會工作局","社會保障基金","文化發展基金","澳門大學","澳門理工大學","澳門旅遊大學"},
    "運輸工務範疇": {"運輸工務司司長辦公室","土地工務局","公共建設局","海事及水務局","環境保護局","交通事務局","民航局","郵電局","房屋局","地球物理氣象局"},
    "廉政審計範疇": {"廉政公署","審計署"},
    "立法司法範疇的行政部門": {"終審法院院長辦公室","檢察長辦公室","立法會輔助部門"},
}
AGENCY_ALIASES = {"海關": "中華人民共和國澳門特別行政區海關", "澳門特別行政區海關": "中華人民共和國澳門特別行政區海關"}


@lru_cache(maxsize=1)
def source_directory():
    with SOURCE_DIRECTORY_PATH.open(encoding="utf-8") as source_file:
        return json.load(source_file)


def agency_source(name):
    directory = source_directory()["agencies"]
    candidates = {name, AGENCY_ALIASES.get(name, name)}
    if "海關" in name: candidates.add("澳門特別行政區海關")
    match = next((item for item in directory if item["name"] in candidates), None)
    return match["buttonUrl"] if match else SOURCE_URL


def agency_category(name):
    official_name = AGENCY_ALIASES.get(name, name)
    return next((category for category, agencies in CATEGORY_AGENCIES.items() if official_name in agencies), "其他")


def clean_summary(value):
    return re.sub(r"^(?:通告|公告)一則\s*[，,、：:]?\s*(?:關於)?\s*", "", value).strip()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS bulletin_tenders (
        id TEXT PRIMARY KEY, agency TEXT NOT NULL, summary TEXT NOT NULL,
        published_date TEXT NOT NULL, detail_url TEXT NOT NULL,
        bulletin_url TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_bulletin_agency_date ON bulletin_tenders(agency,published_date DESC,id DESC)")
    db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.commit()
    return db


class GazetteParser(HTMLParser):
    """Extract rows only from the Gazette result table."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_results = False; self.table_depth = 0; self.row = None
        self.td = 0; self.anchor = None; self.anchor_text = []; self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            classes = attrs.get("class", "").lower().split()
            if self.in_results: self.table_depth += 1
            elif "table-striped" in classes: self.in_results = True; self.table_depth = 1
        if not self.in_results: return
        if tag == "tr": self.row = {"first": [], "second": [], "links": []}; self.td = 0
        elif tag == "td" and self.row is not None: self.td += 1
        elif tag == "a" and self.row is not None:
            self.anchor = attrs.get("href", ""); self.anchor_text = []
        elif tag == "br" and self.row is not None: self.row["first" if self.td == 1 else "second"].append(" ")

    def handle_data(self, data):
        if self.row is not None and self.in_results:
            self.row["first" if self.td == 1 else "second"].append(data)
            if self.anchor is not None: self.anchor_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None and self.row is not None:
            self.row["links"].append((self.anchor, "".join(self.anchor_text).strip(), self.td)); self.anchor = None
        elif tag == "tr" and self.row is not None:
            first = " ".join("".join(self.row["first"]).split())
            second = " ".join("".join(self.row["second"]).split())
            agency_link = next(((u,t) for u,t,td in self.row["links"] if td == 1 and "/cn/bo/b/link/" in u), None)
            bulletin = next((u for u,t,td in self.row["links"] if td == 2 and "bo.dsaj.gov.mo/bo/" in u), "")
            date = re.search(r"(20\d{2})/(\d{2})/(\d{2})", second)
            if agency_link and date:
                url, agency = agency_link
                summary = clean_summary(re.sub(rf"^{re.escape(agency)}\s*[,，]\s*", "", first).strip())
                item_id = url.rstrip("/").split("/")[-1]
                self.rows.append({"id": item_id, "agency": agency, "summary": summary,
                    "published_date": "-".join(date.groups()), "detail_url": urljoin(SOURCE_URL, url),
                    "bulletin_url": bulletin})
            self.row = None; self.td = 0
        elif tag == "table" and self.in_results:
            self.table_depth -= 1
            if self.table_depth == 0: self.in_results = False


def fetch_page(page):
    separator = "&" if "?" in SOURCE_URL else "?"
    url = f"{SOURCE_URL}{separator}p={page}"
    request = Request(url, headers={"User-Agent": "macao-zhaobiaoV2/2.0 (+public-data-aggregator)", "Accept": "text/html"})
    with urlopen(request, timeout=45) as response:
        content = response.read().decode("utf-8", "replace")
    parser = GazetteParser(); parser.feed(content)
    if not parser.rows: raise ValueError(f"第 {page} 页没有解析到记录")
    total_pages = max([int(x) for x in re.findall(r"[?&]p=(\d+)[^\"]*d=13", content)] or [page])
    return parser.rows, total_pages


def refresh():
    if not refresh_lock.acquire(blocking=False): return {"ok": False, "message": "刷新任务正在进行中"}
    started = datetime.now(timezone.utc).isoformat()
    try:
        first, pages = fetch_page(1); all_rows = list(first)
        if pages > 1:
            with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
                futures = {pool.submit(fetch_page, p): p for p in range(2, pages + 1)}
                for future in as_completed(futures): all_rows.extend(future.result()[0])
        unique = {row["id"]: row for row in all_rows}
        if len(unique) < 100: raise ValueError(f"完整性检查失败：只取得 {len(unique)} 条记录")
        db = connect(); previous = db.execute("SELECT count(*) FROM bulletin_tenders").fetchone()[0]
        db.execute("BEGIN IMMEDIATE"); db.execute("DELETE FROM bulletin_tenders")
        db.executemany("INSERT INTO bulletin_tenders VALUES (?,?,?,?,?,?,?)", [
            (r["id"],r["agency"],r["summary"],r["published_date"],r["detail_url"],r["bulletin_url"],started) for r in unique.values()])
        for key,value in (("last_refresh",started),("last_error",""),("source_pages",str(pages))):
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",(key,value))
        db.commit(); agencies=db.execute("SELECT count(DISTINCT agency) FROM bulletin_tenders").fetchone()[0]; db.close()
        return {"ok":True,"count":len(unique),"agencies":agencies,"pages":pages,"new":max(0,len(unique)-previous),"at":started}
    except Exception as exc:
        db=connect(); db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_error',?)",(str(exc),)); db.commit(); db.close()
        return {"ok":False,"message":str(exc),"at":started}
    finally: refresh_lock.release()


def public(row): return dict(row)


def dashboard(db):
    agencies=[]
    names=[r[0] for r in db.execute("SELECT DISTINCT agency FROM bulletin_tenders ORDER BY agency")]
    for name in names:
        items=[public(r) for r in db.execute("SELECT * FROM bulletin_tenders WHERE agency=? ORDER BY published_date DESC,id DESC LIMIT 5",(name,))]
        agencies.append({"name":name,"category":agency_category(name),"source_url":agency_source(name),"items":items})
    latest=[public(r) for r in db.execute("SELECT * FROM bulletin_tenders ORDER BY published_date DESC,id DESC LIMIT 10")]
    meta=dict(db.execute("SELECT key,value FROM metadata"))
    category_order = list(CATEGORY_AGENCIES) + ["其他"]
    categories = [{"name": category, "count": sum(a["category"] == category for a in agencies)} for category in category_order]
    return {"agencies":agencies,"categories":categories,"latest":latest,"agency_count":len(agencies),
        "total":db.execute("SELECT count(*) FROM bulletin_tenders").fetchone()[0],
        "last_refresh":meta.get("last_refresh"),"last_error":meta.get("last_error"),"source_url":SOURCE_URL}


def scheduler():
    while True: refresh(); time.sleep(REFRESH_SECONDS)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT/"static"),**kwargs)
    def json_response(self,body,status=200):
        data=json.dumps(body,ensure_ascii=False).encode(); self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path=="/healthz": return self.json_response({"ok":True})
        if parsed.path=="/api/dashboard":
            db=connect(); result=dashboard(db); db.close(); return self.json_response(result)
        if parsed.path=="/api/sources": return self.json_response(source_directory())
        if parsed.path=="/api/tenders":
            q=parse_qs(parsed.query); agency=q.get("agency",[""])[0]; keyword=q.get("q",[""])[0]
            where=["1=1"]; params=[]
            if agency: where.append("agency=?"); params.append(agency)
            if keyword: where.append("(agency LIKE ? OR summary LIKE ?)"); params.extend([f"%{keyword}%"]*2)
            db=connect(); rows=db.execute(f"SELECT * FROM bulletin_tenders WHERE {' AND '.join(where)} ORDER BY published_date DESC,id DESC",params).fetchall(); db.close()
            return self.json_response({"items":[public(r) for r in rows],"total":len(rows)})
        if parsed.path.startswith("/api/"): return self.json_response({"error":"接口不存在"},404)
        self.path="/index.html" if parsed.path=="/" else parsed.path; return super().do_GET()
    def do_POST(self):
        if urlparse(self.path).path!="/api/refresh": return self.json_response({"error":"接口不存在"},404)
        if REFRESH_TOKEN and self.headers.get("Authorization")!=f"Bearer {REFRESH_TOKEN}": return self.json_response({"error":"未授权"},401)
        result=refresh(); return self.json_response(result,200 if result["ok"] else 503)


if __name__=="__main__":
    connect().close(); threading.Thread(target=scheduler,daemon=True).start(); port=int(os.getenv("PORT","8080"))
    print(f"澳门招标 V2 listening on 0.0.0.0:{port}"); ThreadingHTTPServer(("0.0.0.0",port),Handler).serve_forever()
