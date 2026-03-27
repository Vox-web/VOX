/**
 * VOX — Service Worker
 * 
 * Стратегия: Network-first с fallback на кеш.
 * Кешируем только статические ресурсы (HTML, шрифты, иконки).
 * WebSocket и API-запросы не кешируются — всегда идут в сеть.
 */

const CACHE_NAME = 'vox-v3';

// Что кешируем при установке
const PRECACHE = [
    '/host',
    '/manifest.json',
    '/icons/icon-192.png',
    '/icons/icon-512.png',
];

// ─── Install: кешируем базовые ресурсы ───────────────────────────────────────
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(PRECACHE))
            .then(() => self.skipWaiting())
    );
});

// ─── Activate: удаляем старые кеши ───────────────────────────────────────────
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
            ))
            .then(() => self.clients.claim())
    );
});

// ─── Fetch: Network-first ─────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    // WebSocket, API, и внешние запросы — всегда в сеть
    if (
        event.request.url.includes('ws://') ||
        event.request.url.includes('wss://') ||
        url.pathname.startsWith('/ws') ||
        url.pathname.startsWith('/api/') ||
        url.pathname.startsWith('/room/') ||
        !url.origin.startsWith(self.location.origin)
    ) {
        return; // браузер обрабатывает сам
    }

    // Для HTML страниц: Network-first, fallback на кеш
    event.respondWith(
        fetch(event.request)
            .then(response => {
                // Кешируем успешные ответы на GET запросы
                if (event.request.method === 'GET' && response.status === 200) {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
                }
                return response;
            })
            .catch(() => caches.match(event.request))
    );
});

