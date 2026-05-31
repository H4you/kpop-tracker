# CLAUDE.md — 專案說明書

> 這個檔案是給 Claude Code 讀的。它描述專案的目標、架構、現況，以及待辦事項。

## 專案目標

幫使用者追蹤 KPop 女團最新單曲。每天自動從多個來源抓取女團新曲情報，AI 篩選整理後呈現在網頁儀表板上。使用者在儀表板上瀏覽，**自行決定**要不要把某首歌加進自己的 YouTube 播放清單（系統不會自動加，只提供一鍵開啟 YouTube 的連結 + 個人「已看過」標記）。

使用者目前的播放清單：`https://www.youtube.com/playlist?list=PL4CW32I7lZVkaKBJ6CnorcF6LIkSKXhcP`

## 系統架構

```
每天 09:00（台灣時間）
  └─ GitHub Actions 自動觸發 (.github/workflows/daily.yml)
       ├─ 1. 執行 src/scraper.py
       │      爬 Wikipedia「{年} in South Korean music」發行列表 + PTT koreanpop
       │      → Claude AI 篩選女團 / 前女團成員 solo（全語言、含不知名團）
       │      → 待確認團查 namuwiki 補強辨識
       │      → YouTube 驗證官方 MV（嚴格：無 MV 不收），取得 MV 直連
       │      → 產生 data/latest.json（yt_url 為官方 MV 直連）
       ├─ 2. 把 latest.json commit 回 repo
       └─ 3. 部署 GitHub Pages（index.html + latest.json）

使用者打開網頁 (index.html)
  └─ 自動讀取 data/latest.json 顯示清單
       ├─ 點 YouTube 圖示 → 開 YouTube（使用者自己手動加入播放清單）
       └─ 點 ✓ 圖示 → 個人標記「已看過/已加入」（存在瀏覽器 localStorage）
```

重點：**系統不碰使用者的 YouTube 帳號**。加歌是手動的，這是使用者明確要求的設計。

## 檔案結構

```
kpop-tracker/
├── CLAUDE.md              # 本檔，專案說明書
├── README.md             # 給人看的設定教學
├── index.html            # 網頁儀表板（單檔，含所有 CSS/JS）
├── requirements.txt      # Python 依賴
├── .gitignore
├── src/
│   └── scraper.py        # 多來源爬蟲 + Claude AI 分析，產生 data/latest.json
├── data/
│   └── latest.json       # 爬蟲輸出（GitHub Actions 自動更新，初次需手動建立空檔）
└── .github/
    └── workflows/
        └── daily.yml     # 每日定時任務 + GitHub Pages 部署
```

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

只需要一個：

| Secret 名稱 | 用途 |
|------------|------|
| `ANTHROPIC_API_KEY` | 爬蟲呼叫 Claude API 做篩選 |

（已不需要 YouTube 相關 Secret，因為改成手動加歌。）

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

- **新增追蹤女團**：編輯 `src/scraper.py` 的 `GIRLGROUP_YT_CHANNELS` 字典（加頻道 ID）與 `GIRLGROUP_KEYWORDS` 清單
- **改執行時間**：改 `.github/workflows/daily.yml` 的 cron（目前 `0 1 * * *` = UTC 01:00 = 台灣 09:00）
- **改前端外觀**：`index.html` 的 `<style>` 區塊，CSS 變數集中在 `:root`

## 注意事項

- `gh` CLI（GitHub CLI）若使用者已安裝，許多步驟可自動化；若無，請給出網頁點擊步驟
- 部署前確認 `data/latest.json` 存在，否則前端 fetch 會失敗（已在待辦 #2 處理）
- 爬蟲依賴 Wikipedia 發行表格、PTT、namuwiki、YouTube 搜尋結果的網頁結構，若失效需更新解析邏輯
- 嚴格模式：只收「能在 YouTube 找到官方 MV」的發行（使用者只把主打歌 / 有 MV 的曲加進清單）。要放寬就改 `run_scraper` 內 YouTube 驗證的處理
- 舊版的 Circle Chart / Melon / 寫死 YouTube 頻道來源已於 2026-05 移除（對方網址全部 404）
