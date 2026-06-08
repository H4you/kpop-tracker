# CLAUDE.md — 專案說明書

> 這個檔案是給 Claude Code 讀的。它描述專案的目標、架構、現況，以及待辦事項。

## 專案目標

幫使用者追蹤 KPop 女團最新單曲。每天自動從多個來源抓取女團新曲情報，AI 篩選整理後呈現在網頁儀表板上。使用者在儀表板上瀏覽，**自行決定**要不要把某首歌加進自己的 YouTube 播放清單（系統不會自動加，只提供一鍵開啟 YouTube 的連結 + 個人「已看過」標記）。

使用者目前的播放清單：`https://www.youtube.com/playlist?list=PL4CW32I7lZVkaKBJ6CnorcF6LIkSKXhcP`

## 系統架構

```
每天 09:00（台灣時間）
  └─ GitHub Actions (.github/workflows/daily.yml) → 執行 src/scraper.py
       1. 發掘：Wikipedia 發行列表 + PTT + YouTube 搜尋發掘（合併成 AI 線索）
       2. AI 篩選女團 / 前女團成員 solo（全語言、含不知名團；ai_pick_candidates）
          → 待確認團查 namuwiki 補強；AI 查證主打曲（ai_resolve_title_tracks）
       3. MV：youtube_find_mv 爬蟲 + youtube_api_search_mv 官方 API fallback
          （嚴格：排除 medley/teaser/非官方；無 MV → pending_mv）
       4. 強化：YouTube 官方 API（觀看/讚/上線日/頻道訂閱）、Deezer（照片/試聽/
          粉絲/熱門曲）、Wikidata（公司/出道年）、TheAudioDB+Wikipedia（小檔案）、
          MusicBrainz（成員）
       5. 附加：發行預告+倒數、本月生日、discography、新出道女團（皆 AI，受花費上限）
       6. 產出 data/*.json + upcoming.ics → commit → 部署 GitHub Pages

使用者打開網頁 (index.html)
  └─ 自動讀 data/latest.json（+ archive/views_history）顯示
       ├─ 點 YouTube 圖示 → 開 YouTube（使用者自己手動加入播放清單）
       ├─ 點縮圖 → 頁內嵌彈窗播放 MV
       └─ ♥追蹤 / ✓標記 / ⭐評分 / 🔊試聽（個人化，存 localStorage）
```

重點：**系統不碰使用者的 YouTube 帳號**。加歌是手動的，這是使用者明確要求的設計。
**成本保護**：scraper 內建每日 AI 花費上限（`DAILY_USD_LIMIT`，預設 $0.30），所有
AI 呼叫經 `ai_create()`，累計成本超限即停止後續呼叫（回 None，各功能 graceful 處理）。
所有外部資料源（YouTube/Deezer/Wikidata/MusicBrainz/TheAudioDB）皆免費、不算 AI。

## 檔案結構

```
kpop-tracker/
├── CLAUDE.md / README.md
├── index.html            # 主儀表板（單檔，暗色霓虹玻璃風，含所有 CSS/JS）
├── library.html          # 女團專輯資料庫頁
├── manifest.json, sw.js  # PWA（可安裝 + 離線快取；HTML/JSON 走 network-first）
├── icons/                # PWA 圖示（192/512/maskable/180）
├── requirements.txt
├── src/scraper.py        # 多來源爬蟲 + AI + 各 API 強化，產生 data/*.json
├── data/
│   ├── latest.json       # 本期輸出（自動更新；初次需手動建空檔）
│   ├── archive.json      # 歷史累積（依 id 去重；「歷史」分頁讀）
│   ├── albums.json       # 專輯資料庫（library.html 讀）
│   ├── views_history.json# 每日觀看數快照（成長榜算成長用，每 id 留 30 天）
│   ├── upcoming.ics       # 發行預告行事曆（可訂閱到手機日曆）
│   └── members_override.json  # 人工成員修正檔（AI/MusicBrainz 出錯時覆蓋）
└── .github/workflows/daily.yml
```

前端：MV 縮圖（`i.ytimg.com`）+ 內嵌彈窗播放（`youtube.com/embed`）；分頁
全部/♥最愛/未標記/已標記/本週新/歷史；搜尋、來源/時間/進階篩選（女團/solo/有試聽）；
排序 日期/熱度/成長/按讚/粉絲；觀看數里程碑、一鍵分享卡（Canvas，純文字避免跨域汙染）。
藝人小檔案簡介為繁中（`wikipedia_summary(lang="zh")` 優先，英文經 `translate_to_zh` MyMemory 翻譯）。
歌詞 `t.lyrics` 由 `lrclib_lyrics()`（LRCLIB 免金鑰）在爬蟲端抓好存入（避免瀏覽器 CORS）。
發行預告可切「清單 / 月曆視圖」（`renderUpcomingCalendar`，純前端）。
個人化存 localStorage：`kpop_added_v3`(標記)、`kpop_fav_groups`(最愛)、
`kpop_ratings_v1`(評分)、`kpop_seen_ids`(未讀)、`kpop_playlist_id`(播放清單)。

latest.json 每筆 track 欄位：`id, group, group_kr, title, album, date, is_solo,
is_new, sources, note, yt_url, yt_id, yt_title, yt_views, yt_likes, yt_published,
yt_channel_title, yt_channel_subs, artist_img, preview_url, fans, agency, debut_year, lyrics`。
頂層另有：`pending_mv`(MV即將上線)、`upcoming`(預告+days_left)、`birthdays`、
`discographies`、`members`、`debut_girlgroups`、`top_tracks`(每團熱門曲)、
`profiles`(藝人小檔案)、`summary`、`fetched_at`。

通知：`notify_discord()` 用 `DISCORD_WEBHOOK_URL` secret，有新曲才發（含觀看數+懶人包）。手動觸發 workflow 勾 `test_notify` 可強制測試。LINE Notify 已於 2025 停用，若要 LINE 需改用 Messaging API。

## 技術細節

- **語言**：Python 3.12（爬蟲）+ 純 HTML/CSS/JS（前端，無框架）
- **AI**：Anthropic Claude API（`claude-sonnet-4-6`），用於篩選與結構化爬蟲資料
- **部署**：GitHub Actions（免費額度內）+ GitHub Pages
- **資料格式**：`data/latest.json` 結構如下
  ```json
  {
    "tracks": [
      {
        "id": "...",
        "group": "aespa",
        "group_kr": "에스파",
        "title": "歌曲名",
        "date": "2026.05.20",
        "album": "專輯名",
        "yt_url": "https://youtu.be/...",
        "ptt_url": "https://www.ptt.cc/...",
        "circle_rank": 3,
        "is_new": true,
        "is_hot": false,
        "sources": ["PTT", "Circle"]
      }
    ],
    "summary": "本次整理摘要",
    "fetched_at": "2026-05-31 09:00"
  }
  ```

## 需要的 GitHub Secrets

| Secret | 必要 | 用途 |
|------------|------|------|
| `ANTHROPIC_API_KEY` | ✅ | Claude AI 篩女團 / 查證主打曲 |
| `YOUTUBE_API_KEY` | 建議 | 觀看/讚/訂閱/上線日、官方 MV 搜尋、YouTube 發掘來源 |
| `DISCORD_WEBHOOK_URL` | 選用 | 有新曲發 Discord 通知 |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | 選用 | 啟用 Reddit r/kpop 來源（需自建 script App；未設則略過）|

env（在 daily.yml，非 secret）：`DAILY_USD_LIMIT`（每日 AI 花費上限，預設 `0.30`）、
`SITE_URL`、`TEST_NOTIFY`。

已停用 / 不需要：
- **Spotify**（`SPOTIFY_CLIENT_ID/SECRET`）：2025 起非 Premium 開發者帳號 Web API 一律 403，
  改用 Deezer（免金鑰）。程式碼保留但不呼叫。
- **Reddit 公開端點**：雲端 IP 被擋；未設 OAuth 金鑰時 `fetch_reddit_posts()` 直接略過。

## 部署待辦清單（Claude Code 可協助執行）

使用者要把這個專案部署上線。請依序協助：

1. **初始化 Git repo**（若尚未）並建立 GitHub repo
2. **建立空的 `data/latest.json`**（內容 `{"tracks":[],"summary":"","fetched_at":""}`），否則網頁初次載入會 404
3. **設定 GitHub Secret** `ANTHROPIC_API_KEY`（指導使用者去 repo Settings 加，或用 `gh secret set`）
4. **開啟 GitHub Actions 寫入權限**：Settings → Actions → General → Workflow permissions → Read and write permissions
5. **設定 GitHub Pages**：Settings → Pages → Source 選 `Deploy from a branch` → 選 `gh-pages` 分支
6. **首次手動觸發** workflow 測試：`gh workflow run "KPop Daily Tracker"` 或在 Actions 頁面點 Run workflow
7. 確認網站上線：`https://<username>.github.io/kpop-tracker/`

## 常見調整需求

- **女團判斷不需白名單**：靠 AI（`ai_pick_candidates`）+ namuwiki + `SEED_GIRLGROUPS`/
  `NOT_GIRLGROUPS` 黑名單。誤收非女團 → 加進 `NOT_GIRLGROUPS`；誤排女團 → 加進 `SEED_GIRLGROUPS`
- **成員名單錯了**：編輯 `data/members_override.json`（大小寫不敏感覆蓋 AI/MusicBrainz）
- **MV 抓錯（非官方/medley/teaser）**：調整 `_MV_NEG`/`_MV_POS` 關鍵字，或 `youtube_find_mv`
  的評分（`strong_official` 加權）/ `youtube_api_search_mv` 的過濾
- **每日花費上限**：改 daily.yml env `DAILY_USD_LIMIT`
- **改執行時間**：daily.yml 的 cron（`0 1 * * *` = 台灣 09:00）
- **改外觀**：`index.html` 的 `:root` CSS 變數（暗色霓虹玻璃風）

## 注意事項

- `gh` CLI 已安裝可自動化；爬蟲在 cp950 終端會因韓文無法 print，先寫檔再 Read
- 部署前確認 `data/latest.json` 存在，否則前端 fetch 會失敗
- 空資料保護：本次抓到 0 筆時 `sys.exit(0)` 不覆蓋舊 latest.json（避免網站變空白）
- AI 解析統一用 `_extract_json()`（raw_decode 容忍 AI 多回的說明文字）
- service worker 對 HTML/JSON 走 network-first（改版後不會卡舊畫面）；本機用 Claude Preview
  測試時，SW 會讓截圖卡住 → 先在 console `unregister()` SW + 清 caches 再測，或用 DOM 斷言驗證
- 已移除/停用：Circle/Melon/寫死頻道（404）、Spotify（403 需 Premium）、Reddit 公開端點（雲端被擋）
