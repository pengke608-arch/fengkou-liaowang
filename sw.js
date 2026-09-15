// 风口瞭望 Service Worker - 离线缓存
const CACHE_NAME = 'fengkou-v3';
const ASSETS = [
  './',
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-512.png'
];

// 数据文件（运行时缓存）
const DATA_FILES = [
  './data/trends.json',
  './data/policies.json',
  './data/global.json',
  './data/cities.json',
  './data/remote-jobs.json',
  './data/remote-platforms.json',
  './data/videos.json',
  './data/last-update.json'
];

// 安装时缓存核心资源
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS)).catch(()=>{})
  );
  self.skipWaiting();
});

// 激活时清理旧缓存
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// 请求拦截：网络优先，失败回退缓存
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // 只缓存同源请求，跨域请求直接放行
  if (url.origin !== location.origin) return;
  e.respondWith(
    fetch(e.request)
      .then(res => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(e.request, copy)).catch(()=>{});
        return res;
      })
      .catch(() => caches.match(e.request).then(r => r || caches.match('./index.html')))
  );
});
