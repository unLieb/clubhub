// ClubHUB Service Worker - für Web Push und PWA-Installierbarkeit.
// Bewusst kein Offline-Cache: die App zeigt live Aufgabenstatus, Bestände
// usw. - veraltete gecachte Antworten wären hier irreführend statt hilfreich.

self.addEventListener('install', function (event) {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', function (event) {
  // Reiner Passthrough ohne Caching - ein vorhandener fetch-Handler ist bei
  // manchen Browsern (u.a. älteres Chrome/Samsung Internet) Voraussetzung
  // dafür, dass die Seite überhaupt als installierbare PWA erkannt wird.
  event.respondWith(fetch(event.request));
});

self.addEventListener('push', function (event) {
  var data = { title: 'ClubHUB', body: '', url: '/' };
  try {
    if (event.data) data = Object.assign(data, event.data.json());
  } catch (e) {
    if (event.data) data.body = event.data.text();
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/favicon.png',
      badge: '/static/favicon.png',
      data: { url: data.url || '/' },
    })
  );
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clients) {
      for (var i = 0; i < clients.length; i++) {
        var client = clients[i];
        if ('focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
