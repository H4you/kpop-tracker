# KPop Girl Group Tracker 🎀

每天自動從多個來源抓取 KPop 女團最新單曲，AI 篩選整理後呈現在網頁儀表板。
你自己決定要不要把歌加進 YouTube 播放清單（系統**不會**自動加，也不碰你的 YouTube 帳號）。

線上網站：https://h4you.github.io/kpop-tracker/

---

## 功能總覽

**核心**
- 每日自動抓取 → AI 篩女團/前成員 solo（全語言、含冷門新團）→ 驗證官方 MV → 產生儀表板
- 只收錄「找到官方 MV」的主打發行；找不到 MV 的歸到「⏳ MV 即將上線」
- MV 縮圖 + 頁內嵌彈窗播放；搜尋、來源篩選、時間篩選（全部 / 近7天 / 近3天）
- 進階篩選（女團 / solo / 有試聽）；排序（日期 / 熱度 / 成長 / 按讚 / 粉絲）
- 分頁：全部 / ♥最愛 / 未標記 / 已標記 / 本週新 / 歷史

**個人化（存在瀏覽器 localStorage）**
- ♥ 追蹤最愛團（可一鍵只看最愛）
- ✓ 標記已看過/已加入
- ⭐ 我的評分（每首打星）→「統計」彈窗有「我的年度最愛」排行
- ✨ 新曲未讀提示（自上次造訪新增 N 首 + 🆕 標記）
- 🎵 設定你的 YouTube 播放清單網址 → 點 YouTube 鈕直接帶出清單情境

**資料維度**
- ▶ 觀看數、👍 按讚數、🆙 MV 上線天數、📺 官方頻道訂閱數（YouTube 官方 API）
- 📈 觀看數成長榜 + 卡片迷你趨勢圖（每日快照算成長）
- 🏆 觀看數里程碑（距千萬/億還差多少、估計天數）
- 🔗 一鍵分享卡（Canvas 生成漸層分享圖，可 Web Share 或下載 PNG）
- 🖼️ 藝人照片、🔊 30 秒試聽、👥 粉絲數（Deezer）
- 🏢 經紀公司 + 出道年份（Wikidata）
- 🎵 每團熱門曲（可試聽，Deezer）
- 🪪 藝人小檔案：橫幅 + 類型 + 成立年 + **繁中簡介**（優先繁中維基，否則英文經 MyMemory 翻譯）
- 👯 成員名單（人工修正檔 + MusicBrainz）
- 🔔 發行預告 + 回歸倒數 + 📅 .ics 行事曆訂閱
- 🎂 本月成員生日、🌱 今年新出道女團、💿 專輯資料庫（library.html）
- 📊 年度回顧/統計（每月發行分布、最常出現團、個人統計）

**其他**
- 📱 PWA：可「加到主畫面」像 App 一樣開、離線可看
- 🔔 Discord 通知（有新曲才發）
- 💰 程式內每日 API 花費上限（預設 $0.30/日，超過自動停 AI 呼叫）

---

## 資料來源

| 來源 | 用途 | 金鑰 |
|---|---|---|
| Wikipedia「{年} in South Korean music」 | 發行清單（主來源）+ 出道團 | 免 |
| PTT KoreanPop | [情報] 新曲線索 | 免 |
| YouTube 搜尋發掘 | 近期女團 comeback/MV 線索（補維基沒收錄的） | YouTube 金鑰 |
| YouTube 官方 API | 觀看/按讚/上線日/頻道訂閱、找不到 MV 時官方搜尋 | YouTube 金鑰 |
| namuwiki | 判斷是否女團（本機可用，CI 常被擋） | 免 |
| Deezer | 藝人照片、30 秒試聽、粉絲數、熱門曲 | 免 |
| iTunes Search | 試聽備援、專輯資料庫 | 免 |
| Wikidata | 經紀公司、出道年份 | 免 |
| TheAudioDB + 繁中/英文維基 | 藝人小檔案（橫幅/類型/繁中簡介） | 免 |
| MyMemory | 英文簡介翻繁中（備援，免金鑰） | 免 |
| MusicBrainz | 成員名單補強 | 免 |

> 已停用：Reddit（官方政策需 OAuth App + 公開端點封雲端 IP）；Spotify（2025 起非 Premium 開發者帳號 Web API 一律 403）。兩者程式碼保留，設定金鑰即可重新啟用。

---

## 運作方式

每天台灣時間 09:00，GitHub Actions（`.github/workflows/daily.yml`）執行 `src/scraper.py`，
產生 `data/*.json` 與 `data/upcoming.ics`，commit 回 repo 並部署到 GitHub Pages。

網站上：點 YouTube 圖示 → 開 YouTube（自己手動加歌）；點 ✓ → 個人標記。

---

## 需要的 GitHub Secrets

| Secret | 必要 | 用途 |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ 必要 | Claude AI 篩選女團 / 查證主打曲 |
| `YOUTUBE_API_KEY` | 建議 | 穩定觀看數/按讚/訂閱、官方 MV 搜尋、發掘來源 |
| `DISCORD_WEBHOOK_URL` | 選用 | 有新曲時發 Discord 通知 |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | 選用 | 啟用 Reddit r/kpop 來源（需自建 script App）|
| `DAILY_USD_LIMIT`（env，非 secret） | 選用 | 每日 AI 花費上限，預設 `0.30` |

（Spotify 金鑰已不需要——免費帳號被 Spotify 擋，改用 Deezer。）

---

## 部署步驟

> 建議用 Claude Code 協助：在專案資料夾開啟 Claude Code，它會讀 CLAUDE.md 自動了解專案。

1. 建立 GitHub repo，推上所有檔案
2. 確認 `data/latest.json` 存在（空檔也行）
3. 設定 Secret `ANTHROPIC_API_KEY`（其餘選用）
4. Settings → Actions → General → Workflow permissions → **Read and write**
5. Settings → Pages → Source 選 **gh-pages** 分支
6. Actions → KPop Daily Tracker → Run workflow（首次手動觸發）
7. 開 `https://<帳號>.github.io/kpop-tracker/`

---

## 費用估算

- GitHub Actions / Pages：免費額度內
- Anthropic API：約 USD 0.03–0.08 / 天（有 $0.30/日 上限保護）
- YouTube / Deezer / Wikidata / MusicBrainz / TheAudioDB：免費

---

## 自訂

- 抓取天數：`src/scraper.py` 的 `run_scraper(days_back=14)`
- 執行時間：`.github/workflows/daily.yml` 的 cron（`0 1 * * *` = 台灣 09:00）
- 每日花費上限：workflow env `DAILY_USD_LIMIT`
- 外觀：`index.html` 的 `:root` CSS 變數（暗色霓虹玻璃風，色彩/字體/圓角集中於此）
- 成員修正：`data/members_override.json`（AI/MusicBrainz 出錯時人工覆蓋）
