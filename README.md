# KPop Girl Group Tracker

每天自動從多個來源抓取 KPop 女團最新單曲，AI 篩選整理後呈現在網頁儀表板。
你自己決定要不要把歌加進 YouTube 播放清單（系統不會自動加）。

## 資料來源

- Wikipedia「{年} in South Korean music」發行列表：當期所有南韓發行（主來源，涵蓋剛出道 / 不知名女團）
- PTT koreanpop：[情報] 新曲貼文（補強）
- namuwiki（나무위키）：維基/AI 無法確認時，補強辨識是否為女團
- YouTube：驗證該發行是否有官方 MV，並取得 MV 直連

AI 篩出「女團」與「前/現任女團成員 solo」，全語言都收。**只收錄能找到官方 MV 的主打發行**（符合「只加主打歌 / 有 MV 的曲」習慣），每筆附官方 MV 直連，一鍵開啟。

舊資料來源（已停用）：

## 運作方式

每天台灣時間 09:00，GitHub Actions 自動執行爬蟲，把整理好的女團新曲
存成 data/latest.json，並部署到 GitHub Pages。你打開網站就能看到最新清單。

在網站上：
- 點 YouTube 圖示 → 開啟 YouTube，你自己手動加進播放清單
- 點 ✓ 圖示 → 標記「已看過/已加入」（純個人記錄，存在瀏覽器）

---

## 部署步驟

> 建議直接用 Claude Code 協助部署：在專案資料夾開啟 Claude Code，
> 它會讀取 CLAUDE.md 自動了解整個專案，照著做即可。

### 手動部署

1. 建立 GitHub repo，把這些檔案推上去
2. 確認 data/latest.json 存在（空檔也行，內容：{"tracks":[],"summary":"","fetched_at":""}）
3. 設定 GitHub Secret：
   Settings → Secrets and variables → Actions → New repository secret
   名稱 ANTHROPIC_API_KEY，值是你的 Anthropic API key
4. 開啟 Actions 寫入權限：
   Settings → Actions → General → Workflow permissions → Read and write permissions
5. 設定 GitHub Pages：
   Settings → Pages → Source 選 gh-pages 分支
6. 手動觸發測試：Actions → KPop Daily Tracker → Run workflow
7. 網站網址：https://你的帳號.github.io/kpop-tracker/

---

## 費用估算

- GitHub Actions：免費（每月 2000 分鐘免費額度）
- Anthropic API：約 USD 0.01–0.05 / 天
- GitHub Pages：免費

---

## 自訂

- 調整抓取範圍：改 src/scraper.py 的 run_scraper(days_back=14) 天數（不需再維護女團白名單，AI 自動判斷）
- 改執行時間：編輯 .github/workflows/daily.yml 的 cron
- 改外觀：編輯 index.html 的 :root CSS 變數
