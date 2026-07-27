// Minimal service worker - its only real job is to satisfy the browser's
// PWA installability requirement (a registered SW with a fetch handler).
// Deliberately NOT caching page content: card status/verification must
// always be fetched fresh, never served stale from an old install. The
// cache here exists only to keep the app icons available if a request
// happens to fail offline.
const SHELL_CACHE = 'tut-card-shell-v1';
const SHELL_ASSETS = [
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS))
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});
