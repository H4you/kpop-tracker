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

_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
AI_ENABLED = bool(_API_KEY) and _API_KEY.lower() != "dummy"
ANTHROPIC_CLIENT = Anthropic(api_key=_API_KEY) if AI_ENABLED else None
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

_SECTIONS_CACHE: dict[str, list[dict]] = {}


def _wiki_get(params: dict, retries: int = 4) -> dict:
    """呼叫維基 API，遇 429 / 5xx 以指數退避重試。"""
    delay = 2.0
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=20)
            if r.status_code in (429, 503) or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                wait = float(r.headers.get("Retry-After", delay))
                log.warning(f"Wikipedia {last}，{wait:.0f}s 後重試 ({attempt+1}/{retries})")
                time.sleep(wait)
                delay *= 2
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = str(e)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"Wikipedia API 重試耗盡: {last}")


def _wiki_sections(page: str) -> list[dict]:
    if page in _SECTIONS_CACHE:
        return _SECTIONS_CACHE[page]
    data = _wiki_get({"action": "parse", "format": "json", "page": page, "prop": "sections"})
    secs = data.get("parse", {}).get("sections", [])
    _SECTIONS_CACHE[page] = secs
    return secs


def _wiki_section_html(page: str, index: str) -> str:
    data = _wiki_get({"action": "parse", "format": "json", "page": page,
                      "prop": "text", "section": index})
    return data.get("parse", {}).get("text", {}).get("*", "")


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

    # 只收「已發行」：介於 cutoff ~ 今天之間（含今天）。未來日期屬於發行預告，不進主清單
    today_iso = today.isoformat()
    recent = [r for r in releases
              if cutoff.isoformat() <= r["_date_obj"] <= today_iso]
    log.info(f"Wikipedia: 取得 {len(releases)} 筆發行，近 {days_back} 天已發行 {len(recent)} 筆")
    return recent


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    """列出 start 到 end 之間（含頭尾）的所有 (year, month)，不漏中間月份。"""
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def fetch_wikipedia_upcoming(days_ahead: int = 45) -> list[dict]:
    """抓取未來 days_ahead 天內的南韓發行（涵蓋範圍內所有月份，必要時跨年）。"""
    today = datetime.now().date()
    horizon = today + timedelta(days=days_ahead)
    targets = _months_between(today, horizon)

    rows = []
    for year, month in targets:
        page = f"{year} in South Korean music"
        try:
            secs = _wiki_sections(page)
            month_secs = [s for s in secs if s.get("line") == MONTH_NAMES[month]]
            if not month_secs:
                continue
            html = _wiki_section_html(page, month_secs[0]["index"])
            rows.extend(_parse_release_table(html, year, month))
            time.sleep(0.4)
        except Exception as e:
            log.warning(f"Wikipedia 預告抓取失敗 {page}/{MONTH_NAMES[month]}: {e}")

    upcoming = [r for r in rows
                if today.isoformat() < r["_date_obj"] <= horizon.isoformat()]
    upcoming.sort(key=lambda x: x["_date_obj"])
    log.info(f"Wikipedia: 未來 {days_ahead} 天內 {len(upcoming)} 筆發行")
    return upcoming


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
    """查 namuwiki 判斷是否為女團。回傳 {exists, is_girlgroup, is_boygroup, snippet}。best-effort。
    大小寫不敏感（AI 常給 "Xlov" 但頁面是 "XLOV"）；同時偵測男團避免誤收。"""
    result = {"exists": False, "is_girlgroup": False, "is_boygroup": False, "snippet": ""}
    if not name:
        return result
    name = str(name)
    # 嘗試原樣與全大寫兩種（namuwiki 對團名大小寫敏感）
    tried = []
    for cand in dict.fromkeys([name, name.upper()]):
        try:
            url = "https://namu.wiki/w/" + quote(cand)
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for s in soup(["script", "style"]):
                s.decompose()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            if "해당 문서를 찾을 수 없습니다" in text:  # 找不到頁面
                continue
            head = text[:800]   # 頁面開頭的分類/簡介最能代表該團屬性
            # 男團 / 女團判定：以「開頭出現的先後 + 是否含 보이그룹」為準，
            # 避免「頁面某處提到別的女團」造成誤判（XLOV 是男團卻提到女團）
            gi = head.find("걸그룹")   # 女團
            bi = head.find("보이그룹")  # 男團
            result["exists"] = True
            result["snippet"] = head[:600]
            if bi != -1 and (gi == -1 or bi < gi):
                result["is_boygroup"] = True
                result["is_girlgroup"] = False
            elif gi != -1:
                result["is_girlgroup"] = True
            return result
        except Exception as e:
            tried.append(f"{cand}: {e}")
    if tried:
        log.warning(f"namuwiki 查詢失敗 {name}: {tried}")
    return result


# ── 4. YouTube 官方 MV 驗證 ────────────────────────────────────────────────────

_MV_POS = ["MV", "M/V", "MUSIC VIDEO", "뮤직비디오", "MUSICVIDEO"]
_MV_NEG = ["DANCE PRACTICE", "DANCE VIDEO", "DANCE PERFORMANCE", "PERFORMANCE VIDEO",
           "AUDIO", "LYRIC", "TEASER", "PREVIEW", "TRAILER", "BEHIND", "MAKING",
           "CHALLENGE", "RELAY", "FANCAM", "직캠", "REACTION", "COVER", "LIVE",
           "SHOW!", "MUSIC CORE", "MUSIC BANK", "뮤직뱅크", "쇼!", "인기가요",
           "엠카운트다운", "M COUNTDOWN", "STAGE", "스페셜", "SPECIAL", "PLAYLIST",
           "플레이리스트", "INKIGAYO", "쇼챔피언", "더쇼",
           # 非官方 / 粉絲自製 / 二創
           "FANMADE", "FAN MADE", "FAN-MADE", "CONCEPT", "FANMV", "FAN MV",
           "AI ", "MASHUP", "REMIX", "FMV", "팬메이드", "EDIT", "COMPILATION",
           "MEDLEY", "ALL MV", "PROFILE", "EXPLAINED", "REVIEW", "이론", "분석"]

# 官方頻道線索（出現在頻道名時，可信度高，放寬曲名比對）
_OFFICIAL_CH = ["entertainment", "official", "smtown", "jyp", "hybe", "yg",
                "starship", "kakao", "1thek", "stone music", "label", "records",
                "swing", "blacklabel", "the black label", "pledis", "cube", "rbw",
                "woollim", "fnc", "ador", "ist", "wm ", "mystic", "wakeone",
                "music", "ent.", "에듀", "엔터테인먼트", "오피셜", "레코드", "뮤직"]


def _yt_search_url(group: str, title: str) -> str:
    q = quote_plus(f"{group} {title} MV".strip())
    return f"https://www.youtube.com/results?search_query={q}"


# ── 專輯資料庫（iTunes Search API：封面 + 曲目 + 年份）──────────────────────────

# 種子女團清單（知名團，建立資料庫底；其餘由每日追蹤過的團累積）
SEED_GIRLGROUPS = [
    "aespa", "IVE", "NewJeans", "LE SSERAFIM", "ITZY", "(G)I-DLE", "NMIXX",
    "BLACKPINK", "TWICE", "Red Velvet", "MAMAMOO", "STAYC", "Kep1er", "fromis_9",
    "ILLIT", "BABYMONSTER", "KISS OF LIFE", "tripleS", "VIVIZ", "Billlie",
    "QWER", "Hearts2Hearts", "MEOVV", "izna", "Girls' Generation", "Apink",
    "OH MY GIRL", "WJSN", "Weeekly", "Dreamcatcher", "EVERGLOW", "LIGHTSUM",
    "FIFTY FIFTY", "XG", "UNIS", "ARTMS", "BABYMONSTER", "CSR", "EL7Z UP",
]

_ITUNES = "https://itunes.apple.com"


def _itunes_get(path: str, params: dict, retries: int = 3) -> dict:
    delay = 1.5
    for _ in range(retries):
        try:
            r = requests.get(f"{_ITUNES}{path}", params=params, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 403:   # iTunes 限流
                time.sleep(delay); delay *= 2; continue
            return {}
        except requests.RequestException:
            time.sleep(delay); delay *= 2
    return {}


def _hi_res_art(url: str) -> str:
    # 100x100 → 600x600 高解析封面
    return (url or "").replace("100x100bb", "600x600bb").replace("/100x100", "/600x600")


def itunes_album_tracks(collection_id: int) -> list[str]:
    """抓某張專輯的曲目清單。"""
    d = _itunes_get("/lookup", {"id": collection_id, "entity": "song", "limit": 40})
    return [x.get("trackName") for x in d.get("results", [])
            if x.get("wrapperType") == "track" and x.get("trackName")]


def itunes_group_albums(group: str, limit: int = 12) -> list[dict]:
    """抓某女團的專輯清單（含封面/年份/曲數）；過濾掉藝人名明顯不符者。"""
    d = _itunes_get("/search", {"term": group, "entity": "album",
                                "media": "music", "limit": limit})
    out, seen = [], set()
    gnorm = re.sub(r"[^a-z0-9]", "", group.lower())
    for a in d.get("results", []):
        cid = a.get("collectionId")
        name = a.get("collectionName", "")
        if not cid or cid in seen or not name:
            continue
        seen.add(cid)
        out.append({
            "id": cid,
            "album": name,
            "artist": a.get("artistName", ""),
            "year": (a.get("releaseDate") or "")[:4],
            "track_count": a.get("trackCount"),
            "art": _hi_res_art(a.get("artworkUrl100", "")),
            "itunes_url": a.get("collectionViewUrl", ""),
        })
    return out


def build_album_library(group_names: list[str], data_dir: str,
                        max_albums_per_group: int = 10,
                        max_track_albums: int = 4) -> int:
    """為清單中的女團建立/更新專輯資料庫，合併進 data/albums.json。回傳總團數。
    曲目只抓每團最新數張（max_track_albums），其餘專輯點開時前端再顯示基本資訊。"""
    lib_path = os.path.join(data_dir, "albums.json")
    library = {"groups": {}, "updated_at": ""}
    if os.path.exists(lib_path):
        try:
            with open(lib_path, encoding="utf-8") as f:
                library = json.load(f)
        except Exception as e:
            log.warning(f"albums.json 讀取失敗，將重建: {e}")
            library = {"groups": {}, "updated_at": ""}
    groups = library.get("groups", {})

    for g in group_names:
        try:
            albums = itunes_group_albums(g, limit=max_albums_per_group)
            time.sleep(0.3)
            if not albums:
                continue
            # 依年份新到舊
            albums.sort(key=lambda a: a.get("year", ""), reverse=True)
            # 為最新數張抓曲目
            for a in albums[:max_track_albums]:
                a["tracks"] = itunes_album_tracks(a["id"])
                time.sleep(0.25)
            groups[g] = {"albums": albums,
                         "album_count": len(albums)}
            log.info(f"專輯庫: {g} {len(albums)} 張")
        except Exception as e:
            log.warning(f"專輯庫抓取失敗 {g}: {e}")

    library = {"groups": groups,
               "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    with open(lib_path, "w", encoding="utf-8") as f:
        json.dump(library, f, ensure_ascii=False, indent=2)
    return len(groups)


def youtube_find_mv(group: str, title: str,
                    yt_channel: str = "", title_track: str = "",
                    allow_fallback: bool = True) -> dict | None:
    """在 YouTube 搜尋官方 MV。嚴格驗證：須出自官方頻道 + 曲名相符。
    優先用主打曲名(title_track)搜尋；找不到回傳 None（寧缺勿錯）。
    allow_fallback=False 時關閉 Pass 2 近期後備（用於 AI 判定 MV 尚未上線者）。"""
    song = (title_track or title or "").strip()
    q = f"{group} {song} MV".strip()
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
                vc = vr.get("viewCountText", {}).get("simpleText", "")
                pub = vr.get("publishedTimeText", {}).get("simpleText", "")  # 如 "3 days ago"
                if t and vid:
                    vids.append((t, ch, vid, vc, pub))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)

    def norm(s):
        return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())

    def parse_views(s):
        mm = re.search(r"([\d,]+)", s or "")
        return int(mm.group(1).replace(",", "")) if mm else None

    song_norm = norm(song)

    gtok = norm(group)
    # 團名 token（≥4 字的單字），用於拼法差異大的團（如 "H//PE Princess" 取 "princess"）
    gtokens = [norm(w) for w in re.split(r"[^a-z0-9가-힣]+", group.lower()) if len(norm(w)) >= 4]
    chan_norm = norm(yt_channel)
    # 已知經銷 / 廠牌官方頻道關鍵字（不含單字「official」——假搬運頻道常濫用該字）
    _DISTRIB = ["1thek", "stonemusic", "smtown", "jypentertainment", "hybe",
                "ygentertainment", "starship", "swing", "blacklabel", "pledis",
                "cube", "rbw", "woollim", "fnc", "ador", "wakeone", "kakao",
                "mnetkpop", "genie", "kozent", "mystic", "bluebrown", "records",
                "entertainment", "엔터테인먼트", "레코드"]

    def channel_official(ch_norm: str) -> bool:
        # a) AI 給的官方頻道名相符（雙向子字串）
        if chan_norm and len(chan_norm) >= 3 and (chan_norm in ch_norm or ch_norm in chan_norm):
            return True
        # b) 頻道名以團名開頭（團體自有官方頻道，如 "aespa"、"MEOVV"、"H//PE Princess"）
        if gtok and len(gtok) >= 4 and ch_norm.startswith(gtok[:5]):
            return True
        # c) 頻道名含團名（容忍 "tripleS official"、"FIFTY FIFTY Official"）
        if gtok and len(gtok) >= 4 and gtok[:5] in ch_norm:
            return True
        # d) 頻道名含團名任一較長 token（容忍拼法差異，如 H//PE Princess → "princess"）
        if any(tok in ch_norm for tok in gtokens):
            return True
        # e) 已知經銷 / 廠牌頻道（1theK、Stone Music、HYBE…）
        if any(k in ch_norm for k in _DISTRIB):
            return True
        return False

    # 核心邏輯（簡化）：YouTube 搜尋結果已按相關性排序，
    # 取「第一支出自官方頻道的真正 MV」即為該發行的官方 MV。
    # 排除 teaser/trailer/dance/打歌舞台/二創（_MV_NEG），只認 MV/M\\V（_MV_POS）。
    # 不再用「曲名須對上專輯名」或「舊歌」假設——那會把正確 MV（如 DDI RO RI、Baby Flower）誤擋。
    candidate = None
    for t, ch, vid, vc, pub in vids[:15]:
        up = t.upper()
        if any(n in up for n in _MV_NEG):
            continue
        if not any(p in up for p in _MV_POS):
            continue
        vt = norm(t)
        ch_norm = norm(ch)
        if not channel_official(ch_norm):                # 須官方頻道（擋 SpaceN 等假頻道）
            continue
        # 團名須出現在標題或頻道（容忍 AI 官方頻道吻合 / 拼法差異）
        ai_ch = bool(chan_norm and len(chan_norm) >= 3
                     and (chan_norm in ch_norm or ch_norm in chan_norm))
        hay = vt + ch_norm
        name_ok = (gtok and gtok[:4] in hay) or any(tok in hay for tok in gtokens)
        if not ai_ch and not name_ok:
            continue

        result = {"title": t, "channel": ch, "vid": vid,
                  "url": f"https://www.youtube.com/watch?v={vid}", "views": parse_views(vc)}
        # 若有指定主打曲且該曲名出現在標題 → 最佳匹配，直接回傳
        if song_norm and len(song_norm) >= 3 and song_norm in vt:
            return result
        # 否則記住第一支官方 MV 作為候選（搜尋相關性最高者）
        if candidate is None:
            candidate = result
    return candidate


def ai_resolve_title_tracks(items: list[dict]) -> dict:
    """專注查證：批量問 AI 每個發行的「真正主打曲名」與「官方 YouTube 頻道」。
    回傳 {index: {title_track, yt_channel, has_mv}}。has_mv=False 代表官方 MV 尚未上線/不確定。"""
    if not AI_ENABLED or not items:
        return {}
    listing = [{"i": i, "group": it.get("group"), "album": it.get("album") or it.get("title"),
                "date": it.get("date")} for i, it in enumerate(items)]
    prompt = f"""你是 KPop 資料查證專家。下列是各女團/藝人「本次發行」的清單（index/團名/專輯/日期）。
請逐筆查證該「這次這張發行」的官方主打曲與官方 MV 狀態。

【發行清單】
{json.dumps(listing, ensure_ascii=False)}

逐筆判斷並回答：
- title_track：這張發行的主打曲（title track）正式歌名。注意是「這張、這次」的主打曲，不是該團的舊歌或成名曲。不確定就填 ""。
- yt_channel：該團/藝人的官方 YouTube 頻道名稱（或經銷頻道如 1theK / Stone Music / HYBE LABELS）。不確定填 ""。
- has_mv：你是否確信「這首主打曲的官方 MV 已經公開上線」。確信→true；不確定或可能還沒上→false。
寧可保守：拿不準 title_track 或 has_mv 就填空 / false，不要用該團舊歌硬湊。

只輸出純 JSON：
{{"resolved":[{{"i":0,"title_track":"","yt_channel":"","has_mv":false}}]}}"""
    try:
        resp = ANTHROPIC_CLIENT.messages.create(
            model=AI_MODEL, max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        mt = re.search(r"\{[\s\S]*\}", text)
        if mt:
            arr = json.loads(mt.group()).get("resolved", [])
            out = {}
            for r in arr:
                try:
                    out[int(r["i"])] = {"title_track": (r.get("title_track") or "").strip(),
                                        "yt_channel": (r.get("yt_channel") or "").strip(),
                                        "has_mv": bool(r.get("has_mv"))}
                except Exception:
                    continue
            log.info(f"AI 主打曲查證：{len(out)} 筆")
            return out
    except Exception as e:
        log.error(f"AI 主打曲查證失敗: {e}")
    return {}


# ── 5. AI 分析（pass 1：篩女團 / 前成員 solo 候選）──────────────────────────────

def ai_filter_upcoming(upcoming: list[dict], debuts: list[str]) -> list[dict]:
    """用 Claude 從未來發行清單篩出女團 / 前成員 solo（給「發行預告」用，全語言）。"""
    if not AI_ENABLED or not upcoming:
        return []
    items = [{"date": r["date"], "album": r["album"], "artist": r["artist"]}
             for r in upcoming]
    prompt = f"""以下是未來幾週的南韓發行預告。請篩出「女子團體」或「前/現任女團成員 solo」的項目（全語言，含冷門小團；排除純男團、男性 solo、混聲團體、純 OST）。

【本年度出道團清單（輔助判斷）】
{json.dumps(debuts, ensure_ascii=False)}

【未來發行（日期 / 專輯 / 歌手）】
{json.dumps(items, ensure_ascii=False, indent=1)[:9000]}

只輸出純 JSON：
{{"upcoming":[{{"group":"團名(英文/羅馬拼音)","group_kr":"韓文團名或空字串","title":"主打曲或專輯名","date":"YYYY.MM.DD","is_solo":false}}]}}"""
    try:
        resp = ANTHROPIC_CLIENT.messages.create(
            model=AI_MODEL, max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            data = json.loads(m.group())
            items = data.get("upcoming", [])
            log.info(f"AI 預告篩選：{len(items)} 筆女團/前成員 solo")
            return items
    except Exception as e:
        log.error(f"AI 預告篩選失敗: {e}")
    return []


def ai_weekly_digest(tracks: list[dict], upcoming: list[dict]) -> str:
    """用 Claude 寫一段中文「本週女團懶人包」摘要。失敗回空字串。"""
    if not AI_ENABLED or (not tracks and not upcoming):
        return ""
    t_min = [{"group": t.get("group"), "title": t.get("title"),
              "date": t.get("date"), "is_solo": t.get("is_solo"),
              "views": t.get("yt_views")} for t in tracks]
    u_min = [{"group": u.get("group"), "title": u.get("title"),
              "date": u.get("date"), "days_left": u.get("days_left")} for u in upcoming]
    prompt = f"""你是 KPop 女團情報編輯。請根據以下資料，寫一段「本週女團懶人包」中文摘要，給粉絲快速掌握重點。

【本期新曲（含 MV 觀看數）】
{json.dumps(t_min, ensure_ascii=False)}

【近期發行預告】
{json.dumps(u_min, ensure_ascii=False)}

要求：
- 3～5 句、繁體中文、口語自然，像朋友在分享情報。
- 點出本期亮點（話題作、觀看數高的、前成員 solo、新人團出道）。
- 提一下接下來值得期待的回歸/發行。
- 只輸出摘要文字本身，不要標題、不要 JSON、不要 markdown 符號。"""
    try:
        resp = ANTHROPIC_CLIENT.messages.create(
            model=AI_MODEL, max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "").strip()
        if text:
            log.info("AI 週報已生成")
        return text
    except Exception as e:
        log.error(f"AI 週報生成失敗: {e}")
        return ""


def ai_month_birthdays(group_names: list[str]) -> list[dict]:
    """用 Claude 列出本月過生日的女團成員（限追蹤清單內的團）。失敗回空陣列。"""
    if not AI_ENABLED or not group_names:
        return []
    month = datetime.now().month
    prompt = f"""列出以下 KPop 女團中，「生日在 {month} 月」的現役成員。

【女團清單】
{json.dumps(group_names, ensure_ascii=False)}

要求：
- 只列你「有把握」的成員生日（{month} 月），沒把握就不要列，寧缺勿錯。
- 只輸出純 JSON：
{{"birthdays":[{{"group":"團名","member":"成員名","date":"MM-DD"}}]}}"""
    try:
        resp = ANTHROPIC_CLIENT.messages.create(
            model=AI_MODEL, max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        mt = re.search(r"\{[\s\S]*\}", text)
        if mt:
            items = json.loads(mt.group()).get("birthdays", [])
            today = datetime.now()
            for b in items:
                try:
                    mm, dd = b["date"].split("-")
                    b["is_today"] = (int(mm) == today.month and int(dd) == today.day)
                except Exception:
                    b["is_today"] = False
            items.sort(key=lambda x: x.get("date", ""))
            log.info(f"AI 本月生日：{len(items)} 位成員")
            return items
    except Exception as e:
        log.error(f"AI 生日查詢失敗: {e}")
    return []


def ai_discographies(group_names: list[str]) -> dict:
    """用 Claude 一次生成多個女團的代表作 discography。回傳 {團名: [{year,title,type}...]}。"""
    if not AI_ENABLED or not group_names:
        return {}
    prompt = f"""為以下 KPop 女團/藝人，各列出其「代表性發行作品」的精簡 discography（每團最多 8 筆，由新到舊）。

【清單】
{json.dumps(group_names, ensure_ascii=False)}

要求：
- 只列你「有把握」的作品（正規專輯 EP 單曲），冷門或不確定的團就給空陣列，寧缺勿錯。
- type 用：正規/迷你/單曲/數位單曲 其中之一。
- 只輸出純 JSON：
{{"discographies":{{"團名":[{{"year":"2024","title":"作品名","type":"迷你"}}]}}}}"""
    try:
        resp = ANTHROPIC_CLIENT.messages.create(
            model=AI_MODEL, max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        mt = re.search(r"\{[\s\S]*\}", text)
        if mt:
            d = json.loads(mt.group()).get("discographies", {})
            d = {k: v for k, v in d.items() if v}  # 去掉空的
            log.info(f"AI discography：{len(d)} 團有資料")
            return d
    except Exception as e:
        log.error(f"AI discography 失敗: {e}")
    return {}


def ai_members(group_names: list[str]) -> dict:
    """用 Claude 一次生成多個女團的成員資訊。回傳 {團名: [{name,name_kr,birth,role}...]}。"""
    if not AI_ENABLED or not group_names:
        return {}
    prompt = f"""為以下 KPop 女團，各列出「現役成員」名單。

【清單】
{json.dumps(group_names, ensure_ascii=False)}

要求：
- 只列你「有把握」的現役成員；冷門或不確定的團給空陣列，寧缺勿錯。
- 個人 solo 藝人（非團體）給空陣列。
- name：藝名（英文/羅馬拼音）；name_kr：韓文藝名（不知道填""）。
- birth：生日 MM-DD（只知道月或完全不知就填""）。
- role：隊長 / 忙內 / 主唱 / 主舞 / 隊內Rapper 等，多重用「、」分隔；不確定填""。
- 只輸出純 JSON：
{{"members":{{"團名":[{{"name":"","name_kr":"","birth":"","role":""}}]}}}}"""
    try:
        resp = ANTHROPIC_CLIENT.messages.create(
            model=AI_MODEL, max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        mt = re.search(r"\{[\s\S]*\}", text)
        if mt:
            d = json.loads(mt.group()).get("members", {})
            d = {k: v for k, v in d.items() if v}  # 去掉空的
            log.info(f"AI 成員資訊：{len(d)} 團有資料")
            return d
    except Exception as e:
        log.error(f"AI 成員資訊失敗: {e}")
    return {}


def ai_pick_candidates(releases: list[dict], debuts: list[str], ptt: list[dict]) -> list[dict]:
    if not AI_ENABLED:
        log.warning("未設定 ANTHROPIC_API_KEY，跳過 AI 篩選（候選為空）")
        return []
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
- 每個發行一筆。
- title_track：該張發行的「主打曲 / 先行曲」歌名（不是專輯名）。若你知道主打曲就填，不確定就留空字串 ""。
- title：顯示用標題，優先用主打曲名，否則用專輯名。
- yt_channel：該團/藝人的「官方 YouTube 頻道名稱」（如 "JYP Entertainment"、"SMTOWN"、"THEBLACKLABEL"、"@MEOVV_OFFICIAL"）。不確定就留空字串 ""。
- needs_confirm：若你「不確定」這是不是女團（例如沒聽過、無法判斷），設 true，否則 false。
- 只輸出純 JSON：
{{"candidates":[{{"group":"團名(英文/羅馬拼音)","group_kr":"韓文團名或空字串","title":"顯示標題","title_track":"主打曲名或空","album":"專輯名","date":"YYYY.MM.DD","yt_channel":"官方頻道名或空","is_solo":false,"needs_confirm":false,"note":""}}]}}"""

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
    upcoming_raw = fetch_wikipedia_upcoming(days_ahead=45)

    candidates = ai_pick_candidates(releases, debuts, ptt)

    # 專注查證每筆的「真正主打曲 / 官方頻道 / MV 是否已上」，覆蓋 pass1 的粗略值
    resolved = ai_resolve_title_tracks(candidates)
    for i, c in enumerate(candidates):
        rv = resolved.get(i)
        if not rv:
            continue
        if rv.get("title_track"):
            c["title_track"] = rv["title_track"]
        if rv.get("yt_channel") and not c.get("yt_channel"):
            c["yt_channel"] = rv["yt_channel"]
        # 註：不再用 has_mv 阻擋——改由「官方頻道 + 真正MV」直接判定，避免誤擋已上線 MV

    cutoff7 = (datetime.now().date() - timedelta(days=7)).strftime("%Y.%m.%d")
    tracks = []
    pending_mv = []   # 已確認女團/solo、但官方 MV 尚未上線（MV 即將上線）
    for c in candidates:
        group = c.get("group", "")
        title = c.get("title", "") or c.get("album", "")
        note = c.get("note", "") or ""

        # namuwiki 補強：只對「待確認」且「非 solo」者查
        # （solo 是個人，其 namuwiki 頁面不會標「걸그룹」，不可用女團關鍵字否決）
        if c.get("needs_confirm") and not c.get("is_solo"):
            nm = namu_confirm_girlgroup(group)
            if nm.get("is_boygroup"):
                log.info(f"略過（namuwiki 判定為男團）: {group}")
                continue
            if nm["exists"] and nm["is_girlgroup"]:
                note = (note + "；namuwiki 確認為女團").strip("；")
            elif nm["exists"] and not nm["is_girlgroup"]:
                log.info(f"略過（namuwiki 顯示非女團）: {group}")
                continue
            else:
                note = (note + "；namuwiki 無資料，待確認").strip("；")
            time.sleep(0.4)

        raw_id = f"{group}{title}{c.get('date','')}"
        base = {
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
            "id": hashlib.md5(raw_id.encode()).hexdigest()[:12],
        }

        # YouTube 官方 MV 驗證：取第一支官方頻道的真正 MV（有主打曲名則優先精準匹配）
        mv = youtube_find_mv(group, title,
                             yt_channel=c.get("yt_channel", ""),
                             title_track=c.get("title_track", ""))
        time.sleep(0.3)
        if not mv:
            # 找不到官方 MV → 歸入「MV 即將上線」，附 YouTube 搜尋連結方便手動確認
            log.info(f"無官方 MV，歸入即將上線: {group} - {title}")
            base["yt_search"] = _yt_search_url(group, c.get("title_track") or title)
            pending_mv.append(base)
            continue

        base.update({
            "yt_url": mv["url"],            # 官方 MV 直連
            "yt_id": mv.get("vid", ""),     # YouTube 影片 ID（縮圖 / 內嵌播放用）
            "yt_title": mv["title"],
            "yt_views": mv.get("views"),    # MV 觀看數（int 或 None）
        })
        tracks.append(base)

    # 發行預告：AI 從未來發行清單篩出女團 / 前成員 solo
    upcoming = ai_filter_upcoming(upcoming_raw, debuts)
    today = datetime.now().date()
    for u in upcoming:
        try:
            d = datetime.strptime(u.get("date", ""), "%Y.%m.%d").date()
            u["days_left"] = (d - today).days
        except Exception:
            u["days_left"] = None

    n = len(tracks)
    groups = "、".join(dict.fromkeys(t["group"] for t in tracks))
    summary = (f"本期收錄 {n} 首有官方 MV 的女團／前成員 solo 主打發行"
               + (f"：{groups}。" if groups else "。")
               + (f"（另有 {len(pending_mv)} 筆 MV 即將上線）" if pending_mv else ""))

    digest = ai_weekly_digest(tracks, upcoming)

    # 本月成員生日（限追蹤清單內的女團）
    all_groups = sorted({t["group"] for t in tracks if not t.get("is_solo")}
                        | {u["group"] for u in upcoming if not u.get("is_solo")})
    birthdays = ai_month_birthdays(all_groups)

    # 各團 discography（含 solo 藝人；前端點團名/藝人展開）
    disco_names = sorted({t["group"] for t in tracks} | {u["group"] for u in upcoming})
    discographies = ai_discographies(disco_names)

    # 各團成員資訊（僅團體，前端點團名展開）
    members = ai_members(all_groups)

    log.info(f"完成：收錄 {n} 筆，MV 即將上線 {len(pending_mv)} 筆，預告 {len(upcoming)} 筆")
    return {"tracks": tracks, "pending_mv": pending_mv,
            "upcoming": upcoming, "birthdays": birthdays,
            "discographies": discographies, "members": members,
            "summary": summary, "digest": digest, "fetched_at": now_str}


def update_archive(data_dir: str, tracks: list[dict]) -> int:
    """把本次曲目累積進 data/archive.json（依 id 去重，保留首次出現日期）。回傳總筆數。"""
    arc_path = os.path.join(data_dir, "archive.json")
    archive = {"tracks": [], "updated_at": ""}
    if os.path.exists(arc_path):
        try:
            with open(arc_path, encoding="utf-8") as f:
                archive = json.load(f)
        except Exception as e:
            log.warning(f"archive.json 讀取失敗，將重建: {e}")
            archive = {"tracks": [], "updated_at": ""}

    by_id = {t["id"]: t for t in archive.get("tracks", []) if t.get("id")}
    today = datetime.now().strftime("%Y-%m-%d")
    for t in tracks:
        tid = t.get("id")
        if not tid:
            continue
        if tid in by_id:
            # 已存在：更新欄位但保留 first_seen
            first_seen = by_id[tid].get("first_seen", today)
            by_id[tid] = {**t, "first_seen": first_seen}
        else:
            by_id[tid] = {**t, "first_seen": today}

    merged = sorted(by_id.values(),
                    key=lambda x: (x.get("date", ""), x.get("first_seen", "")),
                    reverse=True)
    archive = {"tracks": merged,
               "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    with open(arc_path, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)
    return len(merged)


def _fmt_views(v) -> str:
    """觀看數整數 → 易讀字串（73061587 → 7306 萬次）。"""
    if not isinstance(v, int):
        return ""
    if v >= 100_000_000:
        return f"{v/100_000_000:.1f} 億次"
    if v >= 10_000:
        return f"{v/10_000:.0f} 萬次"
    return f"{v:,} 次"


def notify_discord(new_tracks: list[dict], site_url: str, digest: str = "") -> None:
    """有新曲時發 Discord webhook 通知。新曲 = 不在上次 archive 的曲目（由呼叫端傳入）。"""
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        log.info("未設定 DISCORD_WEBHOOK_URL，跳過通知")
        return
    if not new_tracks:
        log.info("本次無新曲，不發通知")
        return

    lines = []
    for t in new_tracks[:15]:
        tag = "🎤 solo" if t.get("is_solo") else "👯 女團"
        vstr = _fmt_views(t.get("yt_views"))
        meta = f"（{t.get('date','')}・{tag}" + (f"・▶ {vstr}" if vstr else "") + "）"
        lines.append(f"**{t.get('group','')}** – {t.get('title','')} {meta}\n{t.get('yt_url','')}")
    desc = "\n\n".join(lines)
    if len(new_tracks) > 15:
        desc += f"\n\n…等共 {len(new_tracks)} 首"
    if digest:
        desc = f"📰 **本週懶人包**\n{digest}\n\n" + desc

    payload = {
        "username": "GirlGroup Tracker",
        "embeds": [{
            "title": f"🎀 今日新增 {len(new_tracks)} 首女團新曲",
            "description": desc[:4000],
            "url": site_url,
            "color": 0xE8537C,
            "footer": {"text": "KPop GirlGroup Tracker"},
        }],
    }
    try:
        r = requests.post(webhook, json=payload, timeout=15)
        if r.status_code in (200, 204):
            log.info(f"Discord 通知已送出（{len(new_tracks)} 首新曲）")
        else:
            log.warning(f"Discord 通知失敗 HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"Discord 通知例外: {e}")


if __name__ == "__main__":
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(data_dir, exist_ok=True)

    # 先記錄本次執行前 archive 已有的 id，用來判斷哪些是「真正的新曲」
    arc_path = os.path.join(data_dir, "archive.json")
    prev_ids = set()
    if os.path.exists(arc_path):
        try:
            with open(arc_path, encoding="utf-8") as f:
                prev_ids = {t.get("id") for t in json.load(f).get("tracks", [])}
        except Exception:
            prev_ids = set()

    data = run_scraper()

    out_path = os.path.join(data_dir, "latest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = update_archive(data_dir, data.get("tracks", []))

    # 專輯資料庫：種子女團清單 + 歷來追蹤過的非 solo 團（archive）
    try:
        seen_groups = set()
        if os.path.exists(arc_path):
            with open(arc_path, encoding="utf-8") as f:
                seen_groups = {t.get("group") for t in json.load(f).get("tracks", [])
                               if t.get("group") and not t.get("is_solo")}
        lib_groups = sorted(set(SEED_GIRLGROUPS) | seen_groups)
        lib_total = build_album_library(lib_groups, data_dir)
        log.info(f"專輯資料庫：{lib_total} 團 → albums.json")
    except Exception as e:
        log.warning(f"專輯資料庫建置失敗: {e}")

    # 新曲 = 這次出現、但執行前 archive 沒有的
    new_tracks = [t for t in data.get("tracks", []) if t.get("id") not in prev_ids]
    site_url = os.environ.get("SITE_URL", "").strip() or "https://h4you.github.io/kpop-tracker/"

    digest = data.get("digest", "")
    # 測試模式：手動觸發時勾選，強制把本期曲目當新曲發一次，驗證 webhook
    if os.environ.get("TEST_NOTIFY", "").lower() == "true":
        log.info("TEST_NOTIFY=true：強制發送測試通知")
        notify_discord(data.get("tracks", []) or new_tracks, site_url, digest)
    else:
        notify_discord(new_tracks, site_url, digest)

    print(f"✅ 完成，本期 {len(data.get('tracks', []))} 筆 → latest.json；"
          f"歷史累積 {total} 筆 → archive.json；"
          f"預告 {len(data.get('upcoming', []))} 筆；新曲 {len(new_tracks)} 首")
