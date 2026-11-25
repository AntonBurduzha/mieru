---
id: T09
title: Tracker
mode: agent
status: build
features: [T1, T2, T3, T4]
depends: []
branch: t09-tracking-script
---

# T09 Tracker

## Goal
`apps/tracker` builds `p.js`, the tracking script. One script tag sends a page view on load and on single-page-app navigation, `window.mieru()` sends custom events (calls queued before the script loaded too), and `file:` pages, automated browsers and opted-out browsers send nothing. CI fails when the script is over 1,024 bytes gzipped, and Playwright proves each behaviour in real Chromium.

## Context to read
- `docs/architecture/tracker.md`: the whole design
- `docs/architecture/api.md`: `POST /api/event`, the body, and why the tracker sends `text/plain`
- `docs/conventions.md` sections 1 (the `apps/tracker` layout), 4 (the budget), 5 (`build.mjs` copies the script to ingest's static folder), 6.2 and 6.7 (tracker tests, CI), 7.6 (guarantee 2: nothing in browser storage)
- `docs/architecture/decisions.md` rows 8 (the script never retries) and 41 (why `text/plain`)
- The body ingest accepts: `apps/backend/src/mieru/ingest/pipeline.py` (`IngestEvent`)
- The pinned-action style (a SHA with the version in a comment, `persist-credentials: false`): `.github/workflows/ci.yml`

## Scope
- **In:** `src/p.js`; `build.mjs` with the budget; `test/server.mjs`, the local page server for the tests and for trying the script by hand; Playwright tests in Chromium; the CI job `tracker`; decision row 42 (Playwright only).
- **Out:**
  - ingest serving `/js/p.js`, the tracker built into the image, and compression: T11;
  - the end-to-end half of T1-T4's check: T11, so their `passes` stays `false`;
  - Firefox and WebKit runs; `replaceState`, prerendered pages, bfcache restores, a `data-api` override;
  - outbound links, file downloads, 404s, scroll depth: stretch items;
  - Dependabot and `pnpm audit` for the tracker's packages: T26.
- **Files allowed to change:**
  - `apps/tracker/`: `package.json`, `pnpm-lock.yaml`, `build.mjs`, `playwright.config.js`, `src/p.js`, `test/server.mjs`, `test/helpers.js`, `test/{pageview,spa,events,refuse,build}.spec.js`, `test/fixtures/{page,queue,nodomain,file}.html`; `.gitkeep` is deleted
  - `.github/workflows/ci.yml` (the `tracker` job only), `.gitignore` (Playwright's `test-results/` and `playwright-report/`)
  - Docs: `docs/architecture/tracker.md` ("As built"), `docs/architecture/decisions.md` (row 42), `docs/conventions.md` (sections 1, 3, 4, 6.2, 6.7), `docs/workflow.md` (sections 1 and 10), `README.md` (the apps table), `CLAUDE.md` (the monorepo line, Commands)

## Interfaces
**The snippet.** `<script defer data-domain="example.com" src="https://in.mieru.example/js/p.js"></script>`, with an optional queue stub before it, for calls made before the script loads: `<script>window.mieru=window.mieru||function(){(mieru.q=mieru.q||[]).push(arguments)}</script>`.

**`src/p.js`**: a classic script in an IIFE, modern syntax, no dependencies.
- Reads `document.currentScript` once. The endpoint is `new URL('/api/event', script.src)`; `d` is `data-domain`, else `location.hostname`.
- Refuses when `location.protocol === 'file:'`, when `navigator.webdriver` is true, or when `localStorage.getItem('mieru_ignore') === 'true'` (read once at load inside `try`, never written). A refused script still defines `window.mieru` and drains the queue, but sends nothing.
- Sends with `fetch(endpoint, {method: 'POST', keepalive: true, headers: {'Content-Type': 'text/plain'}, body})`, and swallows a rejected promise.
- The body always has the five keys `n`, `d`, `u` (`location.href`), `r` (`document.referrer`, or `null` when empty) and `p` (`{}`, or the props with each value through `String()`).
- Page views: one on load; after each `history.pushState` and on each `popstate`, one only when `location.pathname` differs from the last page view's.
- `window.mieru(name, {props})` sends one event. The queued `mieru.q` calls are sent in order, after the load page view.

**`build.mjs [--budget <bytes>]`** (default 1024): minifies `src/p.js` with terser (no source map) and prints `dist/p.js: <raw> B, <gzip> B gzipped (budget <budget> B)`, gzip at level 9. Over the budget: `over budget by <n> B` on stderr, exit 1, nothing written. Otherwise it writes `dist/p.js` and copies it to `apps/backend/src/mieru/ingest/static/p.js`, creating the folder. Paths are relative to `build.mjs`, so it works from any directory.

**`test/server.mjs [--port <n>] [--src]`**: `node:http` on 127.0.0.1, port 5180 by default. `GET /js/p.js` serves `dist/p.js`, or `src/p.js` with `--src`. `GET /<name>.html` serves `test/fixtures/<name>.html`; any other GET serves `page.html`, like an SPA's fallback. `POST /api/event` prints `event <body>` and answers `202`. A local fixture only, never deployed.

**`package.json`**: `private`, `"type": "module"`, `"packageManager": "pnpm@12.11.2"`, `engines.node` `>=24`. Scripts: `build` = `node build.mjs`; `test` = `node build.mjs && playwright test` (extra arguments reach Playwright); `dev` = `node test/server.mjs --src --port 5179`. Exact devDependencies: `@playwright/test` `1.65.1`, `terser` `5.51.2`.

**`playwright.config.js`**: `testDir: 'test'`, Chromium only, `webServer` is the test server on 5180 with `reuseExistingServer: false`, `forbidOnly` in CI, no retries.

**CI job `tracker`** (every PR and `main`, `working-directory: apps/tracker`): checkout, `pnpm/action-setup`, `actions/setup-node` (Node 24, pnpm cache), all pinned by SHA; then `pnpm install --frozen-lockfile`, `pnpm build` (the size line in the log), `pnpm exec playwright install --with-deps --only-shell chromium`, `pnpm test`.

## Acceptance criteria
Every command runs from the repo root.

1. **T1.** Loading a page with the snippet sends exactly one `POST` to `/api/event` on the script's origin, as `text/plain` with `keepalive`, with the body `{"n":"pageview","d":"example.com","u":<location.href>,"r":<referrer, or null>,"p":{}}`. Without `data-domain`, `d` is the page's host name. (auto: `pnpm --dir apps/tracker test test/pageview.spec.js`)
2. **T2.** `pushState` to a new path sends one page view with the new URL; a change of only the query string or the hash sends none; `popstate` to another path sends one. (auto: `pnpm --dir apps/tracker test test/spa.spec.js`)
3. **T3.** `mieru('signup', {props: {plan: 'pro', seats: 3}})` sends one event with `"p":{"plan":"pro","seats":"3"}`, and `mieru(name)` alone sends `"p":{}`. Calls queued before the script loaded are each sent once, in order, after the page view. (auto: `pnpm --dir apps/tracker test test/events.spec.js`)
4. **T4 and the other refusals.** With `navigator.webdriver` true, on a `file:` page, or with `mieru_ignore` set to `"true"` in `localStorage`, nothing is sent: not on load, not on `pushState`, not from `mieru()` or queued calls. `window.mieru` still exists, so a site's calls don't throw. (auto: `pnpm --dir apps/tracker test test/refuse.spec.js`)
5. **Privacy and quiet.** The script sets no cookie and writes nothing to `localStorage` or `sessionStorage`. A failed request raises no error on the page. (auto: `pnpm --dir apps/tracker test test/pageview.spec.js`)
6. **Budget.** The built `p.js` is at most 1,024 bytes gzipped, and the copy in ingest's static folder is identical. With `--budget 100` the build exits 1 and changes neither file. (auto: `pnpm --dir apps/tracker test test/build.spec.js`)
7. **CI.** The `tracker` job installs from the lockfile, builds with the budget and runs the tests, with every action pinned by SHA. (auto: `docker run --rm -v "$PWD:/repo:ro" -w /repo rhysd/actionlint:1.7.7 -color`; human: on this task's PR the `tracker` check is green, and its build step prints the gzipped size)

## Tests to write
- `test/helpers.js`: `track(page, {webdriver = false})`, an init script that records every `fetch` call (URL and init) in `window.__sent` and passes it on, and sets `navigator.webdriver` explicitly, so no test depends on the browser's default; `sent(page)` returns the parsed bodies.
- `test/pageview.spec.js`: the load page view with URL, referrer and domain; `r` is `null` without a referrer; the transport, also seen on the wire with `waitForRequest`; the host-name fallback (`fixtures/nodomain.html`); nothing stored; no page error when the request is aborted (1, 5)
- `test/spa.spec.js`: a new path; query-only and hash-only changes; `popstate` back (2)
- `test/events.spec.js`: props as strings; no props; the queue (`fixtures/queue.html`), once each and in order (3)
- `test/refuse.spec.js`: webdriver, with the queue page; `file:` (`fixtures/file.html` loads `../../dist/p.js`, and the test checks the script ran); `mieru_ignore` (4)
- `test/build.spec.js`: within the budget and the copy identical; over the budget, exit 1 and both files' mtimes unchanged (6)

## Proof of done
- `pnpm --dir apps/tracker install --frozen-lockfile && pnpm --dir apps/tracker test`: every test passes, and the size line shows at most 1024 B gzipped.
- `git check-ignore apps/tracker/dist/p.js apps/backend/src/mieru/ingest/static/p.js` prints both paths: neither build output can be committed.

## Test it yourself
1. Once: `pnpm --dir apps/tracker install && pnpm --dir apps/tracker exec playwright install chromium`.
2. `pnpm --dir apps/tracker build` prints the size line, well under 1024 B gzipped.
3. `pnpm --dir apps/tracker test`: every test passed.
4. `pnpm --dir apps/tracker dev`, then open `http://localhost:5179/page.html` in your Chrome: the terminal prints `event {"n":"pageview",...}`. "Pricing" prints one more page view, "?tab=2" prints nothing, "Sign up" prints `"p":{"plan":"pro"}`, and Back prints a page view. DevTools, Network: `POST /api/event` with `text/plain`.
5. In the DevTools console, `localStorage.mieru_ignore = 'true'` and reload: nothing is printed. Then `localStorage.removeItem('mieru_ignore')`.

## Where to debug
Written once, just before the developer's test.

## Notes
- T1-T4 stay `passes: false` until T11: their check also runs the pipeline test.
- After the merge, add `tracker` to the required checks of the `main` ruleset (today only `backend`).
- CI is seen only after the push, so criterion 7's human half is confirmed on the PR before it is marked ready.
- The diff is about 500 lines plus the lockfile, over the 300 lines for agent PRs; most of it is tests.

## Review
