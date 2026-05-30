"""
KPop Girl Group Tracker — 多來源爬蟲模組（v2）

資料源：
  1. Wikipedia「{year} in South Korean music」發行列表（主來源，涵蓋所有發行，
     含剛出道 / 不知名女團）+ Debuting groups 分節
  2. PTT KoreanPop 板 [情報] 貼文（補強）

→ Claude AI 判斷哪些是「女團」或「前/現任女團成員 solo」，全語言都收，
  不知名的也保留，產生 data/latest.json。

舊版的 Circle Chart / Melon / 寫死 YouTube 頻道清單來源已移除——對方網址全部
失效（404），且寫死清單先天無法涵蓋不知名女團。
"""

import os
import re
import json
import time
import hashlib
import logging
from urllib.parse import quote_plus
from datetime import datetime, timedelta, date

import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ANTHROPIC_CLIENT = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
AI_MODEL = "claude-sonnet-4-6"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept-Language": "en,ko;q=0.9,zh-TW;q=0.8",
}

WIKI_API = "https://en.wikipedia.org/w/api.php"
MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


# ── 1. Wikipedia 發行列表 ──────────────────────────────────────────────────────

def _wiki_sections(page: str) -> list[dict]:
    """取得頁面所有章節（含 index / line）"""
    r = requests.get(WIKI_API, params={
        "action": "parse", "format": "json", "page": page, "prop": "sections",
    }, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json().get("parse", {}).get("sections", [])


def _wiki_section_html(page: str, index: str) -> str:
    """取得某章節的 rendered HTML"""
    r = requests.get(WIKI_API, params={
        "action": "parse", "format": "json", "page": page,
        "prop": "text", "section": index,
    }, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json().get("parse", {}).get("text", {}).get("*", "")


def _parse_release_table(html: str, year: int, month: int) -> list[dict]:
    """解析月份發行表格（Date | Album | Artist(s) | Ref.），處理 rowspan。"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="wikitable")
    if not table:
        return []

    NC = 4  # Date, Album, Artist, Ref
    carry: dict[int, tuple[str, int]] = {}
    out = []
    for tr in table.find_all("tr")[1:]:  # 跳過表頭
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        result = [None] * NC
        # 先填入上一列 rowspan 帶下來的欄
        for c in range(NC):
            if c in carry and carry[c][1] > 0:
                result[c] = carry[c][0]
                carry[c] = (carry[c][0], carry[c][1] - 1)
        # 再依序填入本列的 cell
        ptr = 0
        for c in range(NC):
            if result[c] is None and ptr < len(cells):
                cell = cells[ptr]
                ptr += 1
                txt = cell.get_text(" ", strip=True)
                result[c] = txt
                rs = int(cell.get("rowspan", 1) or 1)
                if rs > 1:
                    carry[c] = (txt, rs - 1)

        day_str, album, artist = result[0], result[1], result[2]
        if not day_str or not artist:
            continue
        m = re.search(r"\d{1,2}", day_str or "")
        if not m:
            continue
        day = int(m.group())
        try:
            full = date(year, month, day)
        except ValueError:
            continue
        out.append({
            "source": "Wikipedia",
            "date": full.strftime("%Y.%m.%d"),
            "_date_obj": full.isoformat(),
            "album": (album or "").strip("'\" "),
            "artist": (artist or "").strip(),
        })
    return out


def fetch_wikipedia_releases(days_back: int = 14) -> list[dict]:
    """抓取近 days_back 天的所有南韓發行（當月＋上月，必要時跨年）。"""
    today = datetime.now().date()
    cutoff = today - timedelta(days=days_back)

    # 收集要查的 (year, month) — 當月與上月
    targets = []
    seen = set()
    for d in (today, cutoff):
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            targets.append(key)

    releases = []
    for year, month in targets:
        page = f"{year} in South Korean music"
        try:
            secs = _wiki_sections(page)
            month_secs = [s for s in secs if s.get("line") == MONTH_NAMES[month]]
            if not month_secs:
                log.warning(f"Wikipedia: {page} 找不到 {MONTH_NAMES[month]} 章節")
                continue
            html = _wiki_section_html(page, month_secs[0]["index"])
            rows = _parse_release_table(html, year, month)
            releases.extend(rows)
            time.sleep(0.4)
        except Exception as e:
            log.warning(f"Wikipedia 抓取失敗 {page}/{MONTH_NAMES[month]}: {e}")

    # 篩近 days_back 天
    recent = [r for r in releases if r["_date_obj"] >= cutoff.isoformat()]
    log.info(f"Wikipedia: 取得 {len(releases)} 筆發行，近 {days_back} 天 {len(recent)} 筆")
    return recent


def fetch_wikipedia_debuts() -> list[str]:
    """抓「Debuting groups」分節的團名清單，協助辨識剛出道 / 不知名女團。"""
    today = datetime.now().date()
    page = f"{today.year} in South Korean music"
    names = []
    try:
        secs = _wiki_sections(page)
        deb = [s for s in secs if s.get("line") == "Debuting groups"]
        if deb:
            html = _wiki_section_html(page, deb[0]["index"])
            soup = BeautifulSoup(html, "html.parser")
            for li in soup.select("li"):
                txt = li.get_text(" ", strip=True)
                if txt:
                    names.append(txt[:120])
        log.info(f"Wikipedia: 取得 {len(names)} 個出道團名")
    except Exception as e:
        log.warning(f"Wikipedia 出道團抓取失敗: {e}")
    return names[:80]


# ── 2. PTT KoreanPop（補強）─────────────────────────────────────────────────────

def fetch_ptt_posts(pages: int = 3) -> list[dict]:
    """抓取 PTT KoreanPop 板最新 [情報] 貼文。"""
    base = "https://www.ptt.cc"
    url = f"{base}/bbs/KoreanPop/index.html"
    posts = []
    for _ in range(pages):
        try:
            r = requests.get(url, headers={**HEADERS, "Cookie": "over18=1"}, timeout=10)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"PTT 抓取失敗: {e}")
            break

        soup = BeautifulSoup(r.text, "html.parser")
        for entry in soup.select(".r-ent"):
            title_el = entry.select_one(".title a")
            if not title_el:
                continue
            title = title_el.text.strip()
            if "[情報]" not in title:
                continue
            posts.append({
                "source": "PTT",
                "title": title,
                "url": base + title_el["href"],
            })

        prev = soup.select_one(".btn-group-paging a:nth-child(2)")
        if not prev or "href" not in prev.attrs:
            break
        url = base + prev["href"]
        time.sleep(0.5)

    log.info(f"PTT: 取得 {len(posts)} 篇情報貼文")
    return posts


# ── 3. AI 分析與整合 ──────────────────────────────────────────────────────────

def _yt_search_url(group: str, title: str) -> str:
    """產生 YouTube 搜尋連結（一鍵開啟該 MV 的搜尋結果）。"""
    q = quote_plus(f"{group} {title} MV".strip())
    return f"https://www.youtube.com/results?search_query={q}"


def ai_analyze(releases: list[dict], debuts: list[str], ptt: list[dict]) -> dict:
    """用 Claude 從發行列表中篩出女團 / 前女團成員 solo，全語言都收。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    releases_min = [{"date": r["date"], "album": r["album"], "artist": r["artist"]}
                    for r in releases]
    ptt_min = [{"title": p["title"]} for p in ptt][:40]

    prompt = f"""你是 KPop 女團情報整理助手。以下是近兩週南韓樂壇的「所有」新發行清單（來自維基百科），以及 PTT 情報貼文標題。請從中篩選出符合條件的項目。

【保留條件（符合任一即保留）】
1. 任何 KPop「女子團體」的新發行——**包含剛出道、冷門、你不熟悉的小團**。寧可多收，不要因為「沒聽過」就排除。
2. 「前任或現任女團成員」的個人 solo 發行。
3. 語言不限：韓文、英文、日文發行都收。

【排除】
- 純男團、男性 solo（非前女團成員）、混聲團體。
- 純 OST（除非是女團整體演唱）。

【判斷輔助：本年度出道團清單】
{json.dumps(debuts, ensure_ascii=False)}

【所有新發行（日期 / 專輯 / 歌手）】
{json.dumps(releases_min, ensure_ascii=False, indent=1)[:12000]}

【PTT 情報標題（補充線索）】
{json.dumps(ptt_min, ensure_ascii=False)[:3000]}

【輸出規則】
- 每個發行一筆（以專輯為單位）。
- title = 主打曲名或專輯名；album = 專輯名。
- 若不確定是不是女團但團名出現在出道清單或像女團，傾向保留並在 note 註明「待確認」。
- 只輸出純 JSON，不要任何其他文字。格式：
{{
  "tracks": [
    {{
      "group": "團名（英文/羅馬拼音）",
      "group_kr": "韓文團名（若知道，否則空字串）",
      "title": "主打曲或專輯名",
      "album": "專輯名",
      "date": "YYYY.MM.DD",
      "is_solo": false,
      "sources": ["Wikipedia"],
      "note": "備註，沒有就空字串"
    }}
  ],
  "summary": "本次整理摘要（一句話，中文）",
  "fetched_at": "{now_str}"
}}"""

    try:
        resp = ANTHROPIC_CLIENT.messages.create(
            model=AI_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            result = json.loads(match.group())
            log.info(f"AI 分析完成：整理出 {len(result.get('tracks', []))} 筆")
            return result
    except Exception as e:
        log.error(f"AI 分析失敗: {e}")

    return {"tracks": [], "summary": "分析失敗", "fetched_at": now_str}


# ── 4. 主執行流程 ─────────────────────────────────────────────────────────────

def run_scraper(days_back: int = 14) -> dict:
    log.info("=== KPop Tracker 爬蟲啟動 (v2) ===")
    releases = fetch_wikipedia_releases(days_back=days_back)
    debuts = fetch_wikipedia_debuts()
    ptt = fetch_ptt_posts(pages=3)

    result = ai_analyze(releases, debuts, ptt)

    cutoff = (datetime.now().date() - timedelta(days=7)).strftime("%Y.%m.%d")
    for t in result.get("tracks", []):
        t["yt_url"] = _yt_search_url(t.get("group", ""), t.get("title", ""))
        t["is_new"] = bool(t.get("date", "") >= cutoff)
        t.setdefault("is_hot", False)        # 目前無人氣來源，保留欄位給前端
        t.setdefault("album", t.get("title", ""))
        raw_id = f"{t.get('group','')}{t.get('title','')}{t.get('date','')}"
        t["id"] = hashlib.md5(raw_id.encode()).hexdigest()[:12]

    return result


if __name__ == "__main__":
    data = run_scraper()
    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "latest.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 完成，共 {len(data.get('tracks', []))} 筆，已存至 data/latest.json")
