#!/usr/bin/env python3
"""Macao Official Gazette public-tender collector and web server."""
import json
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "tenders.db"
SOURCE_URL = os.getenv("TENDER_SOURCE_URL", "https://www.bo.dsaj.gov.mo/cn/news/list/b/?d=13&c=&y=")
HENGQIN_API_URL = os.getenv("HENGQIN_API_URL", "https://www.hengqin.gov.cn/postmeta/i/22178.json")
PCP_API_URL = os.getenv("PCP_API_URL", "https://www.pcp.gov.mo/gpcms/web/front/v2/info/selectInfoMoreChannel")
PCP_SITE_URL = "https://www.pcp.gov.mo/gpcms-center-web/#/infoDetail"
REFRESH_SECONDS = max(1800, int(os.getenv("REFRESH_INTERVAL", "43200")))
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN", "")
LEDGER_PASSWORD = os.getenv("LEDGER_PASSWORD", "28700847")
LEDGER_SESSION_SECRET = os.getenv("LEDGER_SESSION_SECRET", secrets.token_hex(32))
FETCH_WORKERS = min(8, max(1, int(os.getenv("FETCH_WORKERS", "4"))))
refresh_lock = threading.Lock()

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
    db.execute("""CREATE TABLE IF NOT EXISTS hengqin_tenders (
        id TEXT PRIMARY KEY, agency TEXT NOT NULL, title TEXT NOT NULL,
        published_date TEXT NOT NULL, detail_url TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS pcp_tenders (
        id TEXT PRIMARY KEY, agency TEXT NOT NULL, summary TEXT NOT NULL,
        published_date TEXT NOT NULL, detail_url TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS tender_decisions (
        source TEXT NOT NULL, tender_id TEXT NOT NULL, decision TEXT NOT NULL,
        decided_at TEXT NOT NULL, PRIMARY KEY(source,tender_id))""")
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


def normalize_hengqin_articles(payload):
    articles = payload.get("articles", []) if isinstance(payload, dict) else []
    rows = []
    for item in articles:
        item_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        published = str(item.get("date") or "").strip()
        url = str(item.get("url") or "").strip().replace("http://www.hengqin.gov.cn/", "https://www.hengqin.gov.cn/", 1)
        if item_id and title and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", published) and url.startswith("https://"):
            rows.append({"id": item_id, "agency": str(item.get("source") or "橫琴粵澳深度合作區").strip(),
                "title": title, "published_date": published, "detail_url": url,
                "sort_time": int(item.get("visible_publish_time") or item.get("publish_time") or 0)})
    return sorted(rows, key=lambda row: (row["sort_time"], row["id"]), reverse=True)[:20]


def fetch_hengqin():
    request = Request(HENGQIN_API_URL, headers={"User-Agent": "macao-zhaobiaoV2/2.0 (+public-data-aggregator)", "Accept": "application/json"})
    with urlopen(request, timeout=45) as response:
        rows = normalize_hengqin_articles(json.load(response))
    if len(rows) < 20: raise ValueError(f"横琴完整性检查失败：只取得 {len(rows)} 条记录")
    return rows


def normalize_pcp_tenders(payload):
    """Convert Public Procurement Platform API records to local tender rows."""
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("rows", []) if isinstance(data, dict) else []
    rows = []
    for item in items:
        item_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or item.get("projectName") or "").strip()
        agency = str(item.get("publishDepartment") or "公共採購平台").strip()
        date_match = re.match(r"(20\d{2}-\d{2}-\d{2})", str(item.get("noticeTime") or ""))
        if not (item_id and title and date_match):
            continue
        query = urlencode({"id": item_id, "publishNumber": item.get("publishNumber") or "",
            "cgType": item.get("cgType") or "", "parentPath": ""})
        rows.append({"id": item_id, "agency": agency, "summary": title,
            "published_date": date_match.group(1), "detail_url": f"{PCP_SITE_URL}?{query}"})
    return rows


def fetch_pcp():
    params = urlencode({"siteId": "94c965cc-c55d-4f92-8469-d5875c68bd04",
        "channel": "3ca455ef3f421810f1e36a46ed220b61", "pageNumber": 1, "pageSize": 20})
    request = Request(f"{PCP_API_URL}?{params}", headers={
        "User-Agent": "macao-zhaobiaoV2/2.0 (+public-data-aggregator)", "Accept": "application/json"})
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if str(payload.get("code")) != "200":
        raise ValueError(f"公共採購平台接口異常：{payload.get('msg') or '未知錯誤'}")
    return normalize_pcp_tenders(payload)


def refresh():
    if not refresh_lock.acquire(blocking=False): return {"ok": False, "message": "刷新任务正在进行中"}
    started = datetime.now(timezone.utc).isoformat()
    try:
        first, pages = fetch_page(1); all_rows = list(first)
        if pages > 1:
            with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
                futures = {pool.submit(fetch_page, p): p for p in range(2, pages + 1)}
                for future in as_completed(futures): all_rows.extend(future.result()[0])
        unique = {row["id"]: row for row in all_rows}; hengqin_rows = fetch_hengqin(); pcp_rows = fetch_pcp()
        if len(unique) < 100: raise ValueError(f"完整性检查失败：只取得 {len(unique)} 条记录")
        db = connect(); previous = db.execute("SELECT count(*) FROM bulletin_tenders").fetchone()[0]
        db.execute("BEGIN IMMEDIATE"); db.execute("DELETE FROM bulletin_tenders")
        db.executemany("INSERT INTO bulletin_tenders VALUES (?,?,?,?,?,?,?)", [
            (r["id"],r["agency"],r["summary"],r["published_date"],r["detail_url"],r["bulletin_url"],started) for r in unique.values()])
        db.execute("DELETE FROM hengqin_tenders")
        db.executemany("INSERT INTO hengqin_tenders VALUES (?,?,?,?,?,?)", [
            (r["id"],r["agency"],r["title"],r["published_date"],r["detail_url"],started) for r in hengqin_rows])
        db.execute("DELETE FROM pcp_tenders")
        db.executemany("INSERT INTO pcp_tenders VALUES (?,?,?,?,?,?)", [
            (r["id"],r["agency"],r["summary"],r["published_date"],r["detail_url"],started) for r in pcp_rows])
        for key,value in (("last_refresh",started),("last_error",""),("source_pages",str(pages))):
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",(key,value))
        db.commit(); agencies=db.execute("SELECT count(DISTINCT agency) FROM bulletin_tenders").fetchone()[0]; db.close()
        return {"ok":True,"count":len(unique),"agencies":agencies,"pages":pages,"hengqin":len(hengqin_rows),
            "pcp":len(pcp_rows),"new":max(0,len(unique)-previous),"at":started}
    except Exception as exc:
        db=connect(); db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_error',?)",(str(exc),)); db.commit(); db.close()
        return {"ok":False,"message":str(exc),"at":started}
    finally: refresh_lock.release()


def public(row): return dict(row)


def ledger_session_token():
    """Return an opaque server-verifiable token without exposing the password."""
    return hmac.new(LEDGER_SESSION_SECRET.encode(), b"tender-ledger", hashlib.sha256).hexdigest()


def valid_month(value):
    return bool(re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", value or ""))


def month_index(value):
    year, month = map(int, value.split("-"))
    return year * 12 + month - 1


def ledger(db, start_month, end_month=None):
    """Build a register for one month or an inclusive range of months."""
    end_month = end_month or start_month
    start = f"{start_month}-01"
    end_index = month_index(end_month) + 1
    end = f"{end_index // 12:04d}-{end_index % 12 + 1:02d}-01"
    rows = db.execute("""SELECT t.*,d.decision,d.decided_at FROM (
        SELECT 'gazette' source_key,id tender_id,published_date,summary title,agency,detail_url,'公報原文' source FROM bulletin_tenders
        UNION ALL SELECT 'pcp',id,published_date,summary,agency,detail_url,'採購平台' FROM pcp_tenders
        UNION ALL SELECT 'hengqin',id,published_date,title,agency,detail_url,'橫琴原文' FROM hengqin_tenders
    ) t LEFT JOIN tender_decisions d ON d.source=t.source_key AND d.tender_id=t.tender_id
    WHERE published_date>=? AND published_date<? ORDER BY published_date DESC,tender_id DESC""", (start, end)).fetchall()
    return [public(row) for row in rows]


def dashboard(db):
    agencies=[]
    names=[r[0] for r in db.execute("SELECT DISTINCT agency FROM bulletin_tenders ORDER BY agency")]
    for name in names:
        items=[public(r) for r in db.execute("SELECT * FROM bulletin_tenders WHERE agency=? ORDER BY published_date DESC,id DESC LIMIT 5",(name,))]
        agencies.append({"name":name,"category":agency_category(name),"items":items})
    latest=[public(r) for r in db.execute("""SELECT id,agency,summary,published_date,detail_url,'公報原文' AS source
        FROM bulletin_tenders ORDER BY published_date DESC,id DESC LIMIT 20""")]
    pcp_latest=[public(r) for r in db.execute("""SELECT id,agency,summary,published_date,detail_url,'採購平台' AS source
        FROM pcp_tenders ORDER BY published_date DESC,id DESC LIMIT 20""")]
    hengqin_latest=[public(r) for r in db.execute("SELECT * FROM hengqin_tenders ORDER BY published_date DESC,id DESC LIMIT 20")]
    meta=dict(db.execute("SELECT key,value FROM metadata"))
    category_order = list(CATEGORY_AGENCIES) + ["其他"]
    categories = [{"name": category, "count": sum(a["category"] == category for a in agencies)} for category in category_order]
    return {"agencies":agencies,"categories":categories,"latest":latest,"pcp_latest":pcp_latest,
        "hengqin_latest":hengqin_latest,"agency_count":len(agencies),
        "total":db.execute("SELECT count(*) FROM bulletin_tenders").fetchone()[0],
        "last_refresh":meta.get("last_refresh"),"last_error":meta.get("last_error"),"source_url":SOURCE_URL}


def scheduler(interval=REFRESH_SECONDS, stop_event=None, refresh_fn=refresh):
    """Refresh immediately and then repeatedly at the configured interval.

    Event.wait makes the loop straightforward to stop in tests and during a
    graceful shutdown.  Keeping the exception guard here ensures that an
    unexpected collector error cannot silently kill all future scheduled runs.
    """
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        try:
            refresh_fn()
        except Exception as exc:
            # refresh() normally records its own errors, but retain the
            # scheduler even if an unforeseen exception escapes it.
            print(f"Scheduled refresh failed: {exc}", flush=True)
        stop_event.wait(interval)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT/"static"),**kwargs)
    def end_headers(self):
        if not urlparse(self.path).path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()
    def json_response(self,body,status=200):
        data=json.dumps(body,ensure_ascii=False).encode(); self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data)
    def read_json(self):
        try:
            length=int(self.headers.get("Content-Length","0"))
            return json.loads(self.rfile.read(length)) if length else {}
        except (ValueError, json.JSONDecodeError):
            return None
    def ledger_authorized(self):
        cookies={part.strip().partition("=")[0]:part.strip().partition("=")[2] for part in self.headers.get("Cookie","").split(";") if "=" in part}
        return hmac.compare_digest(cookies.get("ledger_session",""),ledger_session_token())
    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path=="/healthz": return self.json_response({"ok":True})
        if parsed.path=="/api/dashboard":
            db=connect(); result=dashboard(db); db.close(); return self.json_response(result)
        if parsed.path=="/api/tenders":
            q=parse_qs(parsed.query); agency=q.get("agency",[""])[0]; keyword=q.get("q",[""])[0]
            where=["1=1"]; params=[]
            if agency: where.append("agency=?"); params.append(agency)
            if keyword: where.append("(agency LIKE ? OR summary LIKE ?)"); params.extend([f"%{keyword}%"]*2)
            db=connect(); rows=db.execute(f"SELECT * FROM bulletin_tenders WHERE {' AND '.join(where)} ORDER BY published_date DESC,id DESC",params).fetchall(); db.close()
            return self.json_response({"items":[public(r) for r in rows],"total":len(rows)})
        if parsed.path=="/api/ledger/session": return self.json_response({"authenticated":self.ledger_authorized()})
        if parsed.path=="/api/ledger":
            if not self.ledger_authorized(): return self.json_response({"error":"請先輸入口令"},401)
            query=parse_qs(parsed.query); month=query.get("month",[""])[0]
            start_month=query.get("start_month",[month or datetime.now().strftime("%Y-%m")])[0]
            end_month=query.get("end_month",[month or start_month])[0]
            if not valid_month(start_month) or not valid_month(end_month): return self.json_response({"error":"月份格式不正確"},400)
            span=month_index(end_month)-month_index(start_month)+1
            if span < 1: return self.json_response({"error":"結束月份不可早於開始月份"},400)
            if span > 24: return self.json_response({"error":"每次最多匯總連續 24 個月"},400)
            db=connect(); items=ledger(db,start_month,end_month); db.close()
            counts={key:sum(row["decision"]==key for row in items) for key in ("participate","decline")}
            counts["pending"]=sum(not row["decision"] for row in items)
            return self.json_response({"start_month":start_month,"end_month":end_month,"months":span,
                "items":items,"total":len(items),"counts":counts})
        if parsed.path.startswith("/api/"): return self.json_response({"error":"接口不存在"},404)
        self.path="/index.html" if parsed.path=="/" else parsed.path; return super().do_GET()
    def do_POST(self):
        path=urlparse(self.path).path
        if path=="/api/ledger/login":
            body=self.read_json()
            if body is None: return self.json_response({"error":"請求格式不正確"},400)
            if not hmac.compare_digest(str(body.get("password","")),LEDGER_PASSWORD): return self.json_response({"error":"口令不正確"},401)
            token=ledger_session_token(); data=json.dumps({"ok":True},ensure_ascii=False).encode(); self.send_response(200)
            self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(data)))
            self.send_header("Set-Cookie",f"ledger_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800")
            self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data); return
        if path=="/api/ledger/decision":
            if not self.ledger_authorized(): return self.json_response({"error":"登入已失效，請重新輸入口令"},401)
            body=self.read_json()
            if body is None: return self.json_response({"error":"請求格式不正確"},400)
            source=str(body.get("source_key","")); tender_id=str(body.get("tender_id","")); decision=body.get("decision")
            if source not in {"gazette","pcp","hengqin"} or not tender_id or decision not in {"participate","decline"}: return self.json_response({"error":"決策資料不正確"},400)
            db=connect(); exists=db.execute("""SELECT 1 FROM (
                SELECT 'gazette' source,id FROM bulletin_tenders UNION ALL SELECT 'pcp',id FROM pcp_tenders UNION ALL SELECT 'hengqin',id FROM hengqin_tenders
                ) WHERE source=? AND id=?""",(source,tender_id)).fetchone()
            if not exists: db.close(); return self.json_response({"error":"招標記錄不存在"},404)
            decided_at=datetime.now(timezone.utc).isoformat()
            db.execute("INSERT OR REPLACE INTO tender_decisions VALUES (?,?,?,?)",(source,tender_id,decision,decided_at)); db.commit(); db.close()
            return self.json_response({"ok":True,"decision":decision,"decided_at":decided_at})
        if path!="/api/refresh": return self.json_response({"error":"接口不存在"},404)
        if REFRESH_TOKEN and self.headers.get("Authorization")!=f"Bearer {REFRESH_TOKEN}": return self.json_response({"error":"未授权"},401)
        result=refresh(); return self.json_response(result,200 if result["ok"] else 503)


if __name__=="__main__":
    connect().close(); threading.Thread(target=scheduler,name="tender-refresh",daemon=True).start(); port=int(os.getenv("PORT","8080"))
    print(f"澳门招标 V2 listening on 0.0.0.0:{port}"); ThreadingHTTPServer(("0.0.0.0",port),Handler).serve_forever()
