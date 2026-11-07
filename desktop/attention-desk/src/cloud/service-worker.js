/* Cache the public application shell, never account or learning data. */
const { cache: CACHE, files: FILES, shell: SHELL, entry: ENTRY } = ERIC_SW;
const PREFIX = 'eric-os-shell-';
const NAVIGATION_TIMEOUT = 4000;
const authKeys = ['code', 'access_token', 'refresh_token', 'token_hash', 'error', 'error_description'];

function appUrl(url) {
  return ['/', '/index.html'].includes(url.pathname) &&
    !authKeys.some(key => url.searchParams.has(key)) &&
    !/access_token=|refresh_token=/.test(url.hash);
}

function staticUrl(url) {
  return url.origin === self.location.origin && (
    /^\/assets\/[A-Za-z0-9_.-]+\.(js|mjs|css|png|webp|svg|jpg|jpeg|avif)$/.test(url.pathname) ||
    /^\/pdfjs-[\d.]+\//.test(url.pathname) ||
    /^\/icon(?:-[A-Za-z0-9-]+)?\.(png|svg)$/.test(url.pathname) ||
    url.pathname === '/manifest.webmanifest'
  );
}

function safeAsset(response) {
  return response && response.status === 200 && !response.redirected &&
    !['opaque', 'opaqueredirect'].includes(response.type) &&
    !response.headers.get('Content-Type')?.includes('text/html');
}

function cleanResponse(response, body = response.body) {
  const headers = new Headers(response.headers);
  for (const name of ['Content-Encoding', 'Content-Length', 'Location', 'Refresh']) {
    headers.delete(name);
  }
  // Reconstruct the response so Safari does not inherit a redirect URL list.
  return new Response(body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function shellVersion(html) {
  return /<meta name="eric-build" content="([^"]+)"/.exec(html)?.[1] || null;
}

async function readPublicShell(requireCurrent, signal) {
  const response = await fetch(new Request(new URL('/', self.location.origin), {
    cache: 'no-store',
    credentials: 'omit',
    redirect: 'follow',
    signal,
  }));
  const final = new URL(response.url || self.location.origin + '/');
  if (response.status !== 200 || final.origin !== self.location.origin ||
      final.pathname !== '/' || final.search ||
      !response.headers.get('Content-Type')?.includes('text/html')) {
    throw new Error('Public shell unavailable');
  }
  const html = await response.text();
  if (!html.includes('<div id="root"></div>') || !html.includes('/manifest.webmanifest') ||
      !/type="module"[^>]*src="\/assets\//.test(html)) {
    throw new Error('Not the public Eric OS shell');
  }
  const version = shellVersion(html);
  const current = html.includes(ENTRY) && (!version || version === CACHE);
  if (requireCurrent && !current) throw new Error('Application shell changed during installation');
  return { response: cleanResponse(response, html), current };
}

function versionMessage() {
  return { type: 'ERIC_OS_VERSION', version: CACHE, entry: ENTRY };
}

self.addEventListener('install', event => event.waitUntil((async () => {
  const cache = await caches.open(CACHE);
  try {
    const html = await readPublicShell(true);
    await Promise.all(SHELL.map(async path => {
      const response = await fetch(new Request(new URL(path, self.location.origin), {
        cache: 'reload', credentials: 'omit', redirect: 'error',
      }));
      if (!safeAsset(response)) throw new Error('Static shell asset unavailable');
      await cache.put(path, response);
    }));
    await cache.put('/', html.response);
    await self.skipWaiting();
  } catch (error) {
    await caches.delete(CACHE);
    throw error;
  }
})()));

self.addEventListener('activate', event => event.waitUntil((async () => {
  const previous = (await caches.keys()).filter(key => key.startsWith(PREFIX) && key !== CACHE);
  const retain = previous.at(-1);
  for (const key of previous) {
    const cache = await caches.open(key);
    for (const request of await cache.keys()) {
      const url = new URL(request.url);
      const response = await cache.match(request);
      // One generation of hashed assets supports documents still using old code.
      if (key === retain && (!staticUrl(url) || !safeAsset(response))) {
        await cache.delete(request);
      }
    }
    if (key !== retain) await caches.delete(key);
  }
  await self.clients.claim();
  const clients = await self.clients.matchAll({ type: 'window' });
  for (const client of clients) client.postMessage?.(versionMessage());
  // A client URL can be stale after SPA navigation. Never reload an editor here.
})()));

self.addEventListener('message', event => {
  if (event.data?.type === 'ERIC_OS_VERSION') {
    event.ports?.[0]?.postMessage(versionMessage());
  }
});

async function navigationResponse() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), NAVIGATION_TIMEOUT);
  try {
    const result = await readPublicShell(false, controller.signal);
    // A newer release may precede its Worker. Serve it online without replacing
    // this Worker's complete offline shell with an incomplete future version.
    if (result.current) {
      try {
        await (await caches.open(CACHE)).put('/', result.response.clone());
      } catch {
        // A quota failure must not prevent a successful online navigation.
      }
    }
    return result.response;
  } catch (error) {
    const saved = await (await caches.open(CACHE)).match('/');
    if (saved) return cleanResponse(saved);
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function assetResponse(request, url) {
  const cache = await caches.open(CACHE);
  const saved = await cache.match(url.pathname);
  if (saved && safeAsset(saved)) return saved;
  if (!FILES.includes(url.pathname)) {
    const previous = (await caches.keys()).filter(key => key.startsWith(PREFIX) && key !== CACHE);
    for (const key of previous) {
      const old = await (await caches.open(key)).match(url.pathname);
      if (safeAsset(old)) return old;
    }
    return fetch(request);
  }
  const response = await fetch(request);
  if (safeAsset(response)) await cache.put(url.pathname, response.clone());
  return response;
}

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (request.mode === 'navigate') {
    if (appUrl(url)) event.respondWith(navigationResponse());
    return;
  }
  // version.json, authentication, API requests and private files use the network.
  if (staticUrl(url) && !url.search) event.respondWith(assetResponse(request, url));
});
