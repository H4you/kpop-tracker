"""
KPop Girl Group Tracker — 多來源爬蟲模組（v3）

資料源：
  1. Wikipedia「{year} in South Korean music」發行列表（主來源，涵蓋所有發行）
     + Debuting groups 分節
  2. PTT KoreanPop 板 [情報] 貼文（補強）
  3. namuwiki（나무위키）：當維基/AI 無法確認是否為女團時的補充查證
  4. YouTube 搜尋：驗證該發行是否有「官方 MV」，並取得 MV 直連

流程：
  收集發行 → Claude AI 篩出女團 / 前女團成員 solo 候選（全語言）
  → 對「待確認」者查 namuwiki 補強辨識
  → 對每個候選用 YouTube 驗證官方 MV：找到才保留（嚴格模式），並附 MV 直連
  → 產生 data/latest.json

說明：使用者只把「專輯主打歌 / 有正式 MV 的曲目」加進播放清單，因此採嚴格模式：
找不到官方 MV 的發行不收錄。
"""

import os
import re
import json
import time
import hashlib
import logging
from urllib.parse import quote_plus, quote
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
    r = requests.get(WIKI_API, params={
        "action": "parse", "format": "json", "page": page, "prop": "sections",
    }, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json().get("parse", {}).get("sections", [])


def _wiki_section_html(page: str, index: str) -> str:
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

    NC = 4
    carry: dict[int, tuple[str, int]] = {}
    out = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        result = [None] * NC
        for c in range(NC):
            if c in carry and carry[c][1] > 0:
                result[c] = carry[c][0]
                carry[c] = (carry[c][0], carry[c][1] - 1)
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
        try:
            full = date(year, month, int(m.group()))
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
    today = datetime.now().date()
    cutoff = today - timedelta(days=days_back)
    targets, seen = [], set()
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
            releases.extend(_parse_release_table(html, year, month))
            time.sleep(0.4)
        except Exception as e:
            log.warning(f"Wikipedia 抓取失敗 {page}/{MONTH_NAMES[month]}: {e}")

    recent = [r for r in releases if r["_date_obj"] >= cutoff.isoformat()]
    log.info(f"Wikipedia: 取得 {len(releases)} 筆發行，近 {days_back} 天 {len(recent)} 筆")
    return recent


def fetch_wikipedia_debuts() -> list[str]:
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
            posts.append({"source": "PTT", "title": title, "url": base + title_el["href"]})
        prev = soup.select_one(".btn-group-paging a:nth-child(2)")
        if not prev or "href" not in prev.attrs:
            break
        url = base + prev["href"]
        time.sleep(0.5)
    log.info(f"PTT: 取得 {len(posts)} 篇情報貼文")
    return posts


# ── 3. namuwiki 補充辨識 ───────────────────────────────────────────────────────

def namu_confirm_girlgroup(name: str) -> dict:
    """查 namuwiki 判斷是否為女團。回傳 {exists, is_girlgroup, snippet}。best-effort。"""
    result = {"exists": False, "is_girlgroup": False, "snippet": ""}
    if not name:
        return result
    try:
        url = "https://namu.wiki/w/" + quote(name)
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return result
        soup = BeautifulSoup(r.text, "html.parser")
        for s in soup(["script", "style"]):
            s.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        if "해당 문서를 찾을 수 없습니다" in text:  # 找不到頁面
            return result
        result["exists"] = True
        result["is_girlgroup"] = ("걸그룹" in text)  # 韓文「女團」
        result["snippet"] = text[:600]
    except Exception as e:
        log.warning(f"namuwiki 查詢失敗 {name}: {e}")
    return result


# ── 4. YouTube 官方 MV 驗證 ────────────────────────────────────────────────────

_MV_POS = ["MV", "M/V", "MUSIC VIDEO", "뮤직비디오", "MUSICVIDEO"]
_MV_NEG = ["DANCE PRACTICE", "DANCE VIDEO", "DANCE PERFORMANCE", "PERFORMANCE VIDEO",
           "AUDIO", "LYRIC", "TEASER", "PREVIEW", "TRAILER", "BEHIND", "MAKING",
           "CHALLENGE", "RELAY", "FANCAM", "직캠", "REACTION", "COVER", "LIVE",
           "SHOW!", "MUSIC CORE", "MUSIC BANK", "뮤직뱅크", "쇼!", "인기가요",
           "엠카운트다운", "M COUNTDOWN", "STAGE", "스페셜", "SPECIAL", "PLAYLIST",
           "플레이리스트", "INKIGAYO", "쇼챔피언", "더쇼"]


def _yt_search_url(group: str, title: str) -> str:
    q = quote_plus(f"{group} {title} MV".strip())
    return f"https://www.youtube.com/results?search_query={q}"


def youtube_find_mv(group: str, title: str) -> dict | None:
    """在 YouTube 搜尋官方 MV。找到回傳 {title, channel, url}；找不到回傳 None。"""
    q = f"{group} {title} MV".strip()
    url = "https://www.youtube.com/results?search_query=" + quote(q)
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
    except Exception as e:
        log.warning(f"YouTube 搜尋失敗 {q}: {e}")
        return None

    m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None

    vids = []

    def walk(o):
        if isinstance(o, dict):
            if "videoRenderer" in o:
                vr = o["videoRenderer"]
                t = vr.get("title", {}).get("runs", [{}])[0].get("text", "")
                ch = (vr.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
                      or vr.get("longBylineText", {}).get("runs", [{}])[0].get("text", ""))
                vid = vr.get("videoId", "")
                if t and vid:
                    vids.append((t, ch, vid))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)

    def norm(s):
        return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())

    gtok = norm(group)
    ttok = norm(title)
    for t, ch, vid in vids[:15]:
        up = t.upper()
        if any(n in up for n in _MV_NEG):
            continue
        if not any(p in up for p in _MV_POS):
            continue
        hay = norm(t) + norm(ch)
        rel = (gtok and gtok[:4] in hay) or (ttok and len(ttok) >= 3 and ttok[:4] in norm(t))
        if not rel:
            continue
        return {"title": t, "channel": ch, "url": f"https://www.youtube.com/watch?v={vid}"}
    return None


# ── 5. AI 分析（pass 1：篩女團 / 前成員 solo 候選）──────────────────────────────

def ai_pick_candidates(releases: list[dict], debuts: list[str], ptt: list[dict]) -> list[dict]:
    releases_min = [{"date": r["date"], "album": r["album"], "artist": r["artist"]}
                    for r in releases]
    ptt_min = [{"title": p["title"]} for p in ptt][:40]

    prompt = f"""你是 KPop 女團情報整理助手。以下是近兩週南韓樂壇「所有」新發行清單（維基百科），以及 PTT 情報標題。請篩出符合條件者。

【保留條件（符合任一）】
1. 任何 KPop「女子團體」的新發行——包含剛出道、冷門、你不熟悉的小團。寧可多收。
2. 「前任或現任女團成員」的個人 solo 發行。
3. 語言不限（韓/英/日）。

【排除】純男團、男性 solo（非前女團成員）、混聲團體、純 OST。

【判斷輔助：本年度出道團清單】
{json.dumps(debuts, ensure_ascii=False)}

【所有新發行（日期 / 專輯 / 歌手）】
{json.dumps(releases_min, ensure_ascii=False, indent=1)[:12000]}

【PTT 情報標題（補充線索）】
{json.dumps(ptt_min, ensure_ascii=False)[:3000]}

【輸出規則】
- 每個發行一筆；title 用主打曲名（若不確定主打曲，用專輯名）。
- needs_confirm：若你「不確定」這是不是女團（例如沒聽過、無法判斷），設 true，否則 false。
- 只輸出純 JSON：
{{"candidates":[{{"group":"團名(英文/羅馬拼音)","group_kr":"韓文團名或空字串","title":"主打曲或專輯名","album":"專輯名","date":"YYYY.MM.DD","is_solo":false,"needs_confirm":false,"note":""}}]}}"""

    try:
        resp = ANTHROPIC_CLIENT.messages.create(
            model=AI_MODEL, max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            data = json.loads(m.group())
            cands = data.get("candidates", [])
            log.info(f"AI pass1：候選 {len(cands)} 筆")
            return cands
    except Exception as e:
        log.error(f"AI pass1 失敗: {e}")
    return []


# ── 6. 主執行流程 ─────────────────────────────────────────────────────────────

def run_scraper(days_back: int = 14) -> dict:
    log.info("=== KPop Tracker 爬蟲啟動 (v3) ===")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    releases = fetch_wikipedia_releases(days_back=days_back)
    debuts = fetch_wikipedia_debuts()
    ptt = fetch_ptt_posts(pages=3)

    candidates = ai_pick_candidates(releases, debuts, ptt)

    cutoff7 = (datetime.now().date() - timedelta(days=7)).strftime("%Y.%m.%d")
    tracks = []
    dropped_no_mv = 0
    for c in candidates:
        group = c.get("group", "")
        title = c.get("title", "") or c.get("album", "")
        note = c.get("note", "") or ""

        # namuwiki 補強：只對「待確認」且「非 solo」者查
        # （solo 是個人，其 namuwiki 頁面不會標「걸그룹」，不可用女團關鍵字否決）
        if c.get("needs_confirm") and not c.get("is_solo"):
            nm = namu_confirm_girlgroup(group)
            if nm["exists"] and nm["is_girlgroup"]:
                note = (note + "；namuwiki 確認為女團").strip("；")
            elif nm["exists"] and not nm["is_girlgroup"]:
                log.info(f"略過（namuwiki 判定非女團）: {group}")
                continue
            else:
                note = (note + "；namuwiki 無資料，待確認").strip("；")
            time.sleep(0.4)

        # YouTube 官方 MV 驗證（嚴格模式：找不到就不收）
        mv = youtube_find_mv(group, title)
        if not mv:
            dropped_no_mv += 1
            log.info(f"略過（查無官方 MV）: {group} - {title}")
            time.sleep(0.3)
            continue
        time.sleep(0.3)

        raw_id = f"{group}{title}{c.get('date','')}"
        tracks.append({
            "group": group,
            "group_kr": c.get("group_kr", ""),
            "title": title,
            "album": c.get("album", title),
            "date": c.get("date", ""),
            "is_solo": bool(c.get("is_solo")),
            "is_new": bool(c.get("date", "") >= cutoff7),
            "is_hot": False,
            "sources": ["Wikipedia"],
            "note": note,
            "yt_url": mv["url"],            # 官方 MV 直連
            "yt_title": mv["title"],
            "id": hashlib.md5(raw_id.encode()).hexdigest()[:12],
        })

    n = len(tracks)
    groups = "、".join(dict.fromkeys(t["group"] for t in tracks))
    summary = (f"本期收錄 {n} 首有官方 MV 的女團／前成員 solo 主打發行"
               + (f"：{groups}。" if groups else "。")
               + (f"（另有 {dropped_no_mv} 筆查無官方 MV 未收錄）" if dropped_no_mv else ""))

    log.info(f"完成：收錄 {n} 筆，略過無 MV {dropped_no_mv} 筆")
    return {"tracks": tracks, "summary": summary, "fetched_at": now_str}


if __name__ == "__main__":
    data = run_scraper()
    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "latest.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 完成，共 {len(data.get('tracks', []))} 筆，已存至 data/latest.json")
