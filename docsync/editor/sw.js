/**
 * App shell cache for the draft editor.
 *
 * Three tiers, because "cache everything" would go stale silently and
 * "cache nothing" would refetch Pyodide's ~30MB runtime on every visit:
 *
 * 1. Pyodide's CDN (cdn.jsdelivr.net/pyodide/<version>/…) — cache-first,
 *    forever. The version is IN the URL, so a pin never goes stale; a Pyodide
 *    upgrade is a new URL, which is simply a cache miss the first time.
 * 2. This app's own shell, split by what staleness costs:
 *    - The HTML pages (edit.html, start.html) — NETWORK-FIRST, cache as the
 *      offline fallback. These carried stale-while-revalidate at first, and
 *      it was a bug factory: the file changes daily, so every single visit
 *      ran the PREVIOUS build — fixes looked unshipped, and "clear the
 *      caches" nuked the Pyodide store alongside it, forcing a ~40MB
 *      refetch that read as the whole app hanging. A local dev server makes
 *      the network hit free; deployed, one round-trip per open is nothing
 *      against running week-old code.
 *    - Icons and the manifest — stale-while-revalidate. They change rarely
 *      and a stale icon costs nothing.
 * 3. Everything that is actual report data or a write (engine/*, the GitHub
 *    API, drafts) — network-only. Caching a stale content.md would make a
 *    collaborator's real edit invisible; that failure mode is worse than
 *    "the editor needs a connection to open a report."
 *
 * Bump CACHE_VERSION on any shell change that should evict old entries
 * outright rather than wait for revalidation (e.g. a renamed file).
 */
// v7: the shell rule missed Pages' clean URLs, so a stale /primer/edit is in
// every warm browser's cache. A rename evicts them outright rather than
// waiting for a revalidation that the old rule never asked for.
const CACHE_VERSION = 'primer-shell-v7';
const PYODIDE_CACHE = 'primer-pyodide-v1';

const SHELL_FILES = [
  './start.html',
  './edit.html',
  './collab-client.js',
  './htmlimport.js',
  './manifest.webmanifest',
  './icons/icon.svg',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      // Per file, not addAll: addAll is all-or-nothing, so ONE shell file
      // missing from one project's staged directory rejected the install, the
      // new worker never activated, and the old one kept serving its old
      // cache — indefinitely, and invisibly. A worker whose whole job is to
      // deliver the current build must not be the reason an old one survives.
      .then(cache => Promise.all(SHELL_FILES.map(f => cache.add(f).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      // ds-preview-* is the editor's own warm-boot snapshot (the last
      // successful render, painted read-only while Pyodide compiles). It is
      // versioned by the editor, not by this worker, so a shell update must
      // not throw it away — that is the one moment a fast boot matters most.
      keys.filter(k => k !== CACHE_VERSION && k !== PYODIDE_CACHE
                    && !k.startsWith('ds-preview'))
          .map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

const isPyodide = url => url.hostname === 'cdn.jsdelivr.net' && url.pathname.includes('/pyodide/');

// Never cache: report content, layout, the renderer's own data files, any
// GitHub call, or — importantly — index.html itself. That file is not part
// of this app; it is the PUBLISHED, publicly readable report (built by CI
// from content.md), served from the very same directory as the editor. A
// reader who never opened the editor never registers this worker, but anyone
// who DID (this scope is the whole docs/primer/ directory) would otherwise
// have the worker intercept their next visit to the report page too — a
// stale copy of what a reader actually came here to read is a much worse
// failure than "the app shell recaches its own five files."
// The app's own code: fresh wins, cache is the offline fallback (tier 2).
//
// collab-client.js belongs here for exactly the reason the HTML pages do, and
// it took a live failure to notice. It is a BUILT BUNDLE that changes with the
// engine, and it is import()ed by path with no ?v= to break a cache. Under the
// generic same-origin branch below it was served cache-first and revalidated
// behind, so every deploy ran one whole session on the PREVIOUS client: the
// day the room learned to hand an editor pilot ops, every warm browser
// answered with a bundle that had no such case and silently dropped the
// message, and the only symptom was a 25-second timeout at the other end.
// A client one deploy behind the room it is talking to is the same "fixes look
// unshipped" bug factory the pages comment above describes.
// `.html` OPTIONAL, and that is not a nicety. Cloudflare Pages serves clean
// URLs: a request for /primer/edit.html is answered with a 301 to
// /primer/edit, the browser follows it, and the redirect is a SECOND request
// through this worker - for a path this rule did not match. So every open of
// the editor on the hub was answered from the cache and revalidated behind,
// which is the exact failure the comment above describes, still live for the
// only URL anybody actually has: measured on the deployed hub, an editor
// three deploys old with the new file sitting on the server. The hub's
// _headers has always named both spellings; this had not.
const isShellPage = url => /\/(edit|start)(\.html)?$/.test(url.pathname)
                        || /\/collab-client\.js$/.test(url.pathname);

const isNetworkOnly = url =>
  url.hostname === 'api.github.com' ||
  url.hostname === 'raw.githubusercontent.com' ||
  /\/engine\//.test(url.pathname) ||
  /\/projects\.json$/.test(url.pathname) ||
  // The dev server's control endpoints. These were NOT listed, so /__ping fell
  // through to the shell branch and was answered FROM CACHE — a worker
  // overrides the request's own {cache:'no-store'}, so nothing on the page
  // could opt out. Every consumer of that answer is a liveness signal: the
  // build version live-reload compares against, the commit count on the Push
  // button, whether a local server exists at all, and now whether an update is
  // waiting. Serving any of them a response behind produces symptoms that look
  // like anything except a cache — a Push button alternating between two
  // numbers as the cached reply and the live stream disagreed is what exposed
  // it. A stale answer here is always wrong; there is no offline value in one.
  /^\/__/.test(url.pathname) ||
  /\/primer\/(index(\.html)?)?$/.test(url.pathname);

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;              // writes always hit the network directly
  const url = new URL(req.url);

  if (isPyodide(url)) {
    event.respondWith(
      caches.open(PYODIDE_CACHE).then(async cache => {
        const hit = await cache.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res.ok) cache.put(req, res.clone());
        return res;
      })
    );
    return;
  }

  if (isNetworkOnly(url)) return;                 // let the browser handle it untouched

  if (url.origin === location.origin) {
    event.respondWith(
      caches.open(CACHE_VERSION).then(async cache => {
        const hit = await cache.match(req);
        const network = fetch(req).then(res => {
          if (res.ok) cache.put(req, res.clone());
          return res;
        }).catch(() => hit || Response.error());  // offline: fall back to what we
        // had — but respondWith(undefined) is not a fallback, it is a broken
        // navigation with no explanation, and that is what an uncached page
        // used to get the moment the server blinked (a restart, an update).
        // Pages wait for the network so a deploy is live on the very next
        // open; everything else boots from cache and revalidates behind.
        return isShellPage(url) ? network : (hit || network);
      })
    );
  }
});
