const CHECK_INTERVAL = 5 * 60_000;
const CHECK_THROTTLE = 60_000;
const VERSION_MESSAGE = 'ERIC_OS_VERSION';
const listeners = new Set();
let pendingUpdate = null;
let started = false;

export function getPendingUpdate() {
  return pendingUpdate;
}

export function subscribeToWorkerUpdates(listener) {
  listeners.add(listener);
  listener(pendingUpdate);
  return () => listeners.delete(listener);
}

export function refreshToUpdate() {
  if (pendingUpdate) window.location.reload();
}

function publishUpdate(version) {
  if (pendingUpdate === version) return;
  pendingUpdate = version;
  for (const listener of listeners) listener(version);
}

function readWorkerVersion(worker) {
  if (!worker) return Promise.resolve(null);
  return new Promise(resolve => {
    const channel = new MessageChannel();
    const finish = value => {
      clearTimeout(timer);
      channel.port1.close();
      channel.port2.close();
      resolve(value);
    };
    const timer = setTimeout(() => finish(null), 2500);
    channel.port1.onmessage = event => finish(event.data);
    try {
      worker.postMessage({ type: VERSION_MESSAGE }, [channel.port2]);
    } catch {
      finish(null);
    }
  });
}

export function registerWorker() {
  if (!('serviceWorker' in navigator) || started) return;
  started = true;
  const documentVersion = document.querySelector('meta[name="eric-build"]')?.content;
  const documentEntry = document.querySelector('script[type="module"][src]')?.src;
  let registration;
  let latestVersion;
  let lastCheck = 0;
  let checking = false;
  let starting = false;
  let reconnectCheck = false;

  const receiveVersion = data => {
    if (data?.type !== VERSION_MESSAGE || typeof data.version !== 'string' ||
        !data.version.startsWith('eric-os-shell-') || typeof data.entry !== 'string') return;
    if (latestVersion && data.version !== latestVersion) return;
    const sameDocument = documentVersion
      ? data.version === documentVersion
      : new URL(data.entry, location.origin).href === documentEntry;
    publishUpdate(sameDocument ? null : data.version);
  };

  const inspectActive = async () => {
    const worker = navigator.serviceWorker.controller || registration?.active;
    receiveVersion(await readWorkerVersion(worker));
  };

  const readLatestVersion = async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch('/version.json', {
        cache: 'no-store', credentials: 'omit', redirect: 'error', signal: controller.signal,
      });
      if (!response.ok) return;
      const data = await response.json();
      if (typeof data.version === 'string' && data.version.startsWith('eric-os-shell-')) {
        latestVersion = data.version;
        if (latestVersion === documentVersion) publishUpdate(null);
      }
    } finally {
      clearTimeout(timer);
    }
  };

  const update = async ({ force = false } = {}) => {
    if (!registration) {
      void start();
      return;
    }
    if (checking) {
      if (force) reconnectCheck = true;
      return;
    }
    if (document.visibilityState === 'hidden' ||
        navigator.onLine === false || (!force && Date.now() - lastCheck < CHECK_THROTTLE)) return;
    checking = true;
    lastCheck = Date.now();
    try {
      await Promise.allSettled([registration.update(), readLatestVersion()]);
      await inspectActive();
    } finally {
      checking = false;
      if (reconnectCheck) {
        reconnectCheck = false;
        void update({ force: true });
      }
    }
  };

  const watchInstallation = () => {
    const worker = registration.installing;
    if (!worker) return;
    worker.addEventListener('statechange', () => {
      if (worker.state === 'activated') void inspectActive();
    });
  };

  const start = async () => {
    if (starting) return;
    starting = true;
    try {
      registration = await navigator.serviceWorker.getRegistration('/');
      try {
        registration = await navigator.serviceWorker.register('/sw.js', {
          scope: '/', updateViaCache: 'none',
        });
      } catch {
        // An existing installed Worker remains usable when its update fetch fails.
      }
      if (!registration) return;
      registration.addEventListener('updatefound', watchInstallation);
      watchInstallation();
      void update({ force: true });
      void navigator.serviceWorker.ready.then(inspectActive);
      setInterval(update, CHECK_INTERVAL);
    } catch {
      // Opening the app offline keeps its existing registration and local data.
    } finally {
      starting = false;
    }
  };

  navigator.serviceWorker.addEventListener('controllerchange', () => void inspectActive());
  navigator.serviceWorker.addEventListener('message', event => {
    if (event.source === navigator.serviceWorker.controller || event.source === registration?.active) {
      receiveVersion(event.data);
    }
  });
  window.addEventListener('online', () => void update({ force: true }));
  window.addEventListener('focus', () => void update());
  window.addEventListener('pageshow', () => void update());
  document.addEventListener('visibilitychange', () => void update());
  if (document.readyState === 'complete') void start();
  else window.addEventListener('load', () => void start(), { once: true });
}
