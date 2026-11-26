# Tracking script

> Part of the [system design](README.md).

```html
<script defer data-domain="example.com" src="https://in.mieru.example/js/p.js"></script>
```

| Aspect                    | Design                                                                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Size budget**           | 1 KB gzipped or less, enforced in CI. For reference: Plausible's old base script is 1.3 KB, its current one about 2.5 KB, Google Analytics over 100 KB |
| **Transport**             | `fetch(url, {method: 'POST', keepalive: true, headers: {'Content-Type': 'text/plain'}})`. `keepalive` lets the request finish while the page unloads   |
| **Page views**            | One on load. For single-page apps, wrap `history.pushState` and listen for `popstate`; skip if the path did not change                                 |
| **Custom events**         | `window.mieru(name, {props})`. Calls made before the script loads are queued                                                                           |
| **Refuses to track**      | `file:` pages, automated browsers (`navigator.webdriver`), and browsers where the owner set an opt-out flag in local storage (read, never written)     |
| **Stores in the browser** | Nothing                                                                                                                                                |
| **Built with**            | Plain JavaScript, minified with terser; no framework, no dependencies                                                                                  |

Left out of the base script to keep the size: outbound links, file downloads, scroll depth, form tracking. Ad blockers block known analytics domains; a guide for proxying the script through the customer's own domain is a stretch item.

## As built (T09)

The source is `apps/tracker/src/p.js`; `node build.mjs` minifies it with terser into `dist/p.js`, about 790 bytes, 500 gzipped. Over 1,024 bytes gzipped the build exits 1 and writes nothing, and CI's `tracker` job runs it on every PR.

**Serving it (T11).**
- The image's `tracker` stage runs the same build, so an oversized script fails the image too ([decision 45](decisions.md)).
- Ingest reads the file once at startup (`mieru.ingest.tracker`) and keeps it and a gzipped copy in memory ([decision 44](decisions.md)). It serves `GET` and `HEAD /js/p.js` with `Cache-Control: public, max-age=86400` and `Vary: Accept-Encoding`, gzipped when the request's `Accept-Encoding` allows it.
- Without a built file, ingest logs `tracker_missing`, answers `404` on `/js/p.js`, and still collects events.

**The snippet**, with the optional stub that queues calls made before the script loads:

```html
<script>window.mieru=window.mieru||function(){(mieru.q=mieru.q||[]).push(arguments)}</script>
<script defer data-domain="example.com" src="https://in.mieru.example/js/p.js"></script>
```

- **Where events go:** `new URL('/api/event', script.src)`, so always to the host the script came from. `d` is `data-domain`, or `location.hostname` when the attribute is missing.
- **The body** always has the five keys of [api.md](api.md): `n`, `d`, `u` (`location.href`), `r` (`document.referrer`, or `null` when empty) and `p` (`{}`, or the props with every value through `String()`). A rejected request is swallowed: no error on the customer's page, no retry ([decision 8](decisions.md)).
- **Page views:** one on load, then one after `history.pushState` or on `popstate` only when `location.pathname` changed. A new query string or hash is not a page view.
- **Custom events:** `mieru(name, {props})` sends one event. The queued calls are sent once each, in order, after the load page view.
- **Refusing:** on a `file:` page, when `navigator.webdriver` is true, or when `localStorage.mieru_ignore` is `"true"` (read once, inside `try`, because reading storage can throw). A refused script still defines `window.mieru` and empties the queue, so a site's calls don't throw; it just sends nothing.
- **Tests:** Playwright in Chromium against the built file (`apps/tracker/test/`, [decision 42](decisions.md)). Every test sets `navigator.webdriver` itself, because Playwright's default is `true`. `test/server.mjs` serves the fixture pages and answers `POST /api/event` with `202`, printing each body; `pnpm --dir apps/tracker dev` runs it on port 5179 with the unminified source.
