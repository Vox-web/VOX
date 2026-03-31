/**
 * VOX — Service Worker
 *
 * Безопасная стратегия:
 * - HTML / навигация: только сеть, без кеша
 * - API / WebSocket / room / внешние запросы: не трогаем
 * - Статика same-origin (иконки, css, js, шрифты, изображения): stale-while-revalidate
 * - Старые кеши автоматически удаляются
 *
 * Важно:
 * - HTML НЕ кешируется вообще
 * - /host НЕ precache'ится
 * - При обновлении service worker сразу активируется
 */

const CACHE_NAME = 'vox-static-v20';

const PRECACHE = [
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

// Что считаем безопасной статикой для кеширования
function isStaticAsset(request, url) {
  if (request.method !== 'GET') return false;
  if (url.origin !== self.location.origin) return false;

  const pathname = url.pathname.toLowerCase();

  if (
    pathname.startsWith('/api/') ||
    pathname.startsWith('/ws') ||
    pathname.startsWith('/room/')
  ) {
    return false;
  }

  // HTML никогда не кешируем
  const accept = request.headers.get('accept') || '';
  if (request.mode === 'navigate' || accept.includes('text/html')) {
    return false;
  }

  return (
    pathname.endsWith('.js') ||
    pathname.endsWith('.css') ||
    pathname.endsWith('.png') ||
    pathname.endsWith('.jpg') ||
    pathname.endsWith('.jpeg') ||
    pathname.endsWith('.webp') ||
    pathname.endsWith('.svg') ||
    pathname.endsWith('.gif') ||
    pathname.endsWith('.ico') ||
    pathname.endsWith('.woff') ||
    pathname.endsWith('.woff2') ||
    pathname.endsWith('.ttf') ||
    pathname.endsWith('.eot') ||
    pathname.endsWith('.map') ||
    pathname === '/manifest.json'
  );
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys
        .filter(key => key !== CACHE_NAME)
        .map(key => caches.delete(key))
    );
    await self.clients.claim();
  })());
});

// Безопасное обновление кеша в фоне
async function updateCache(request) {
  const response = await fetch(request);

  if (!response || response.status !== 200) {
    return response;
  }

  const url = new URL(request.url);

  // Никогда не кешируем HTML, даже если сюда случайно попали
  const contentType = response.headers.get('content-type') || '';
  if (request.mode === 'navigate' || contentType.includes('text/html')) {
    return response;
  }

  if (isStaticAsset(request, url)) {
    const cache = await caches.open(CACHE_NAME);
    await cache.put(request, response.clone());
  }

  return response;
}

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);

  // Не трогаем не-GET
  if (request.method !== 'GET') {
    return;
  }

  // Не трогаем внешние запросы
  if (url.origin !== self.location.origin) {
    return;
  }

  // Не трогаем WebSocket / API / room
  if (
    url.pathname.startsWith('/ws') ||
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/room/')
  ) {
    return;
  }

  // HTML / навигация — только сеть, без fallback на кеш
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request));
    return;
  }

  // Остальную статику — stale-while-revalidate
  if (isStaticAsset(request, url)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      const cached = await cache.match(request);

      const networkPromise = updateCache(request).catch(() => null);

      if (cached) {
        return cached;
      }

      const fresh = await networkPromise;
      if (fresh) {
        return fresh;
      }

      return new Response('Offline', {
        status: 503,
        statusText: 'Offline',
      });
    })());
    return;
  }

  // Всё прочее same-origin, что не HTML и не API/ws — просто сеть
  event.respondWith(fetch(request));
});