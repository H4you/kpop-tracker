"""
KPop Girl Group Tracker — 多來源爬蟲模組
資料源：PTT koreanpop、Circle Chart、Melon、YouTube RSS
"""

import os
import re
import json
import time
import hashlib
import logging
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ANTHROPIC_CLIENT = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

# 已知 KPop 女團 YouTube 頻道 RSS（可自行擴充）
GIRLGROUP_YT_CHANNELS = {
    "aespa":          "UCjjSn4er5-PHtFKyRJaa1jA",
    "IVE":            "UCkLGbCMaYnuuHpqkFX1cEAg",
    "ILLIT":          "UCPbDt-hoLDn1HH4qHbvlqNQ",
    "NMIXX":          "UCrYqVzGBzMxMC2JFpHOVB5Q",
    "NewJeans":       "UCF9F_4OvLHN2d8FCqJPYdkA",
    "LE SSERAFIM":    "UC6bHOCTHxgERpRqFRoGnFwQ",
    "tripleS":        "UCu27Wj8BbGvgRMKCKlI1SQg",
    "Hearts2Hearts":  "UCpSRZFREflJjWUiRa3WOVWQ",
    "BLACKPINK":      "UCOmHUn--16B90oW2L6FRR3A",
    "TWICE":          "UCaO6TYkqD-7HuHv0-HXN-_g",
    "Red Velvet":     "UCo6bbAEoKMpHxj6TRRRKoEw",
    "MAMAMOO":        "UCOr5YnFyKhKHHVSRAvV4hVg",
    "ITZY":           "UCRxBFBDJDxSRoJKGSZKT4KQ",
    "Kep1er":         "UC2VFn-uIqUiCPKpgCqiVPeA",
    "fromis_9":       "UCX0K8RSxBuCh0nO7bPLFDFQ",
    "STAYC":          "UChmFYKxODTOmC-5bPXFXdBw",
    "XG":             "UCdTzVsLo9GYBzCXRNkW1hzg",
    "VIVIZ":          "UCnIbIfSQZZBMNYAYpXpPKUA",
    "(G)I-DLE":       "UCoAEBualAjAdS0m4LxpKNRg",
    "EXID":           "UCGHb8-6SKqnGQ-hRWr1atqA",
    "f(x)":           "UCStJSKnS3kY3AxDxFQkY8PQ",
}


# ── 1. PTT koreanpop ──────────────────────────────────────────────────────────

def fetch_ptt_posts(pages: int = 3) -> list[dict]:
    """抓取 PTT koreanpop 板最新 [情報] 貼文"""
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
            href = title_el["href"]
            date_el = entry.select_one(".date")
            date_str = date_el.text.strip() if date_el else ""
            posts.append({
                "source": "PTT",
                "title": title,
                "url": base + href,
                "raw_date": date_str,
            })

        # 上一頁
        prev = soup.select_one(".btn-group-paging a:nth-child(2)")
        if not prev or "href" not in prev.attrs:
            break
        url = base + prev["href"]
        time.sleep(0.5)

    log.info(f"PTT: 取得 {len(posts)} 篇情報貼文")
    return posts


def fetch_ptt_post_detail(url: str) -> str:
    """抓取單篇 PTT 貼文內文"""
    try:
        r = requests.get(url, headers={**HEADERS, "Cookie": "over18=1"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one("#main-content")
        if not content:
            return ""
        # 移除推文區
        for el in content.select(".push"):
            el.decompose()
        return content.get_text("\n", strip=True)[:2000]
    except Exception as e:
        log.warning(f"PTT 內文抓取失敗 {url}: {e}")
        return ""


# ── 2. Circle Chart (舊 Gaon) ─────────────────────────────────────────────────

def fetch_circle_chart() -> list[dict]:
    """從 Circle Chart 抓本週數位週榜前 100，標記女團歌曲"""
    url = "https://circlechart.kr/page_chart/onDown.circle"
    # Circle Chart 提供公開 JSON API
    params = {"termGbn": "week", "hitYear": datetime.now().year,
              "hitMonth": datetime.now().month, "nationGbn": "T"}
    tracks = []
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = r.json()
        for item in data.get("list", [])[:100]:
            tracks.append({
                "source": "Circle",
                "rank": item.get("rank"),
                "artist": item.get("artist"),
                "title": item.get("song"),
                "album": item.get("album"),
            })
        log.info(f"Circle Chart: 取得 {len(tracks)} 首")
    except Exception as e:
        log.warning(f"Circle Chart 抓取失敗: {e}")
    return tracks


# ── 3. YouTube 頻道 RSS ───────────────────────────────────────────────────────

def fetch_youtube_rss(days_back: int = 7) -> list[dict]:
    """透過 YouTube RSS 偵測女團頻道新上傳（不需 API key）"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    new_videos = []

    for group, channel_id in GIRLGROUP_YT_CHANNELS.items():
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:5]:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if published < cutoff:
                    continue
                title = entry.title
                # 只取可能是 MV 的影片（過濾 LIVE、Shorts 以外）
                if any(kw in title.upper() for kw in ["MV", "M/V", "MUSIC VIDEO",
                                                        "OFFICIAL", "LYRIC", "AUDIO"]):
                    new_videos.append({
                        "source": "YouTube",
                        "group": group,
                        "title": title,
                        "url": entry.link,
                        "published": published.strftime("%Y.%m.%d"),
                    })
            time.sleep(0.3)
        except Exception as e:
            log.warning(f"YouTube RSS 失敗 {group}: {e}")

    log.info(f"YouTube RSS: 取得 {len(new_videos)} 部新影片")
    return new_videos


# ── 4. Melon 新曲 ─────────────────────────────────────────────────────────────

def fetch_melon_new() -> list[dict]:
    """抓 Melon 新曲頁面（公開頁面）"""
    url = "https://www.melon.com/chart/new/index.htm"
    tracks = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.select("tr.lst50, tr.lst100")[:50]:
            artist = row.select_one(".ellipsis.rank02 a")
            song = row.select_one(".ellipsis.rank01 a")
            if artist and song:
                tracks.append({
                    "source": "Melon",
                    "artist": artist.text.strip(),
                    "title": song.text.strip(),
                })
        log.info(f"Melon: 取得 {len(tracks)} 首新曲")
    except Exception as e:
        log.warning(f"Melon 抓取失敗: {e}")
    return tracks


# ── 5. AI 分析與整合 ──────────────────────────────────────────────────────────

GIRLGROUP_KEYWORDS = [
    "aespa", "IVE", "ILLIT", "NMIXX", "NewJeans", "LE SSERAFIM", "Hearts2Hearts",
    "BLACKPINK", "TWICE", "Red Velvet", "MAMAMOO", "ITZY", "Kep1er", "fromis_9",
    "STAYC", "XG", "VIVIZ", "(G)I-DLE", "tripleS", "KISS OF LIFE", "Billlie",
    "QWER", "BABYMONSTER", "UNIS", "MEENOI", "Apink", "Girl's Day", "miss A",
    "4MINUTE", "SISTAR", "2NE1", "f(x)", "SNSD", "소녀시대", "트와이스", "에스파",
    "아이브", "뉴진스", "르세라핌", "블랙핑크", "레드벨벳", "마마무", "여자아이들",
    "fromis", "stayc", "키스오브라이프", "빌리", "유니스", "베이비몬스터",
]


def is_girlgroup_related(text: str) -> bool:
    """快速預篩：是否和女團相關"""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in GIRLGROUP_KEYWORDS)


def ai_analyze(raw_data: dict) -> list[dict]:
    """用 Claude 分析並結構化所有來源的資料"""
    # 先做快速預篩，減少 token 用量
    ptt_filtered = [p for p in raw_data["ptt"] if is_girlgroup_related(p["title"])]
    yt_filtered = raw_data["youtube"]  # YouTube 已按頻道過濾
    circle_filtered = [c for c in raw_data["circle"] if is_girlgroup_related(c.get("artist", ""))]
    melon_filtered = [m for m in raw_data["melon"] if is_girlgroup_related(m.get("artist", ""))]

    # 抓 PTT 貼文詳細內文（最多 10 篇）
    ptt_details = []
    for post in ptt_filtered[:10]:
        detail = fetch_ptt_post_detail(post["url"])
        ptt_details.append({**post, "content": detail})
        time.sleep(0.3)

    prompt = f"""你是 KPop 女團情報整理助手。以下是從多個來源收集到的原始資料，請整理出所有「KPop 女團」的最新單曲/專輯發行資訊。

【PTT 貼文（含內文）】
{json.dumps(ptt_details, ensure_ascii=False, indent=2)[:3000]}

【Circle Chart 本週榜單（女團部分）】
{json.dumps(circle_filtered[:30], ensure_ascii=False, indent=2)}

【YouTube 新上傳影片】
{json.dumps(yt_filtered, ensure_ascii=False, indent=2)}

【Melon 新曲（女團部分）】
{json.dumps(melon_filtered[:20], ensure_ascii=False, indent=2)}

整理規則：
1. 只保留現役女團或曾為女團成員的個人出道作品
2. 不收 OST（除非是女團整體參與）、男團、混合團體
3. 去除重複（同一首歌可能出現在多個來源）
4. 每筆資料必填：group、title、date（YYYY.MM.DD）、sources（資料來源列表）
5. 選填：album、yt_url（YouTube MV 直連）、ptt_url、circle_rank、is_new（7天內=true）、note

只輸出純 JSON，格式：
{{
  "tracks": [
    {{
      "group": "女團名（英文）",
      "group_kr": "韓文名（若有）",
      "title": "歌曲名",
      "date": "YYYY.MM.DD",
      "album": "專輯名",
      "yt_url": "YouTube MV 網址",
      "ptt_url": "PTT 貼文網址",
      "circle_rank": null,
      "is_new": true,
      "sources": ["PTT", "YouTube"],
      "note": "備註"
    }}
  ],
  "summary": "本次整理摘要（一句話）",
  "fetched_at": "{datetime.now().strftime('%Y-%m-%d %H:%M')}"
}}"""

    try:
        resp = ANTHROPIC_CLIENT.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            result = json.loads(match.group())
            log.info(f"AI 分析完成：整理出 {len(result.get('tracks', []))} 首")
            return result
    except Exception as e:
        log.error(f"AI 分析失敗: {e}")

    return {"tracks": [], "summary": "分析失敗", "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ── 6. 主執行流程 ─────────────────────────────────────────────────────────────

def run_scraper() -> dict:
    log.info("=== KPop Tracker 爬蟲啟動 ===")
    raw = {
        "ptt":     fetch_ptt_posts(pages=3),
        "circle":  fetch_circle_chart(),
        "youtube": fetch_youtube_rss(days_back=7),
        "melon":   fetch_melon_new(),
    }
    result = ai_analyze(raw)

    # 加入穩定 ID（用 group+title hash）
    for t in result.get("tracks", []):
        raw_id = f"{t.get('group','')}{t.get('title','')}{t.get('date','')}"
        t["id"] = hashlib.md5(raw_id.encode()).hexdigest()[:12]

    return result


if __name__ == "__main__":
    data = run_scraper()
    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "latest.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 完成，共 {len(data.get('tracks', []))} 首，已存至 data/latest.json")
