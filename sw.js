/* KPop Girl Group Tracker — Service Worker
   App shell 走 cache-first（離線可開、載入更快）；
   資料 JSON 走 network-first（永遠先抓最新，離線才回快取）。 */
const CACHE = 'kpop-tracker-v1';
const SHELL = ['./', './index.html', './library.html', './manifest.json'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;   // 第三方（字型 / YouTube 縮圖）不攔截

  // 資料 JSON + HTML 頁面：network-first（線上永遠拿最新，離線才回快取）
  // —— 避免每次改版後使用者看到舊畫面
  const isHTML = req.mode === 'navigate' || url.pathname.endsWith('.html') ||
                 url.pathname === '/' || url.pathname.endsWith('/');
  if (url.pathname.endsWith('.json') || isHTML) {
    e.respondWith(
      fetch(req)
        .then((res) => { const cp = res.clone(); caches.open(CACHE).then((c) => c.put(req, cp)); return res; })
        .catch(() => caches.match(req).then((m) => m || caches.match('./index.html')))
    );
    return;
  }

  // 靜態資源（圖示 / manifest）：cache-first，背景補抓更新
  e.respondWith(
    caches.match(req).then((cached) =>
      cached ||
      fetch(req).then((res) => { const cp = res.clone(); caches.open(CACHE).then((c) => c.put(req, cp)); return res; })
                .catch(() => cached)
    )
  );
});
