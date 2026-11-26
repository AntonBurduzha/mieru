# API surface

> Part of the [system design](README.md).

**Ingest** (public, on its own domain)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/event` | Body below, with any content type (the tracker sends `text/plain`, which needs no CORS preflight). `202` with an empty body when well formed, kept or dropped; a dropped event carries `X-Mieru-Dropped: <reason>`. `400 invalid_event` when not, with the fixed message `Malformed event` and no echo of the input. `Access-Control-Allow-Origin: *`, set by Caddy. Never sets a cookie |
| GET, HEAD | `/js/p.js` | The tracking script, from memory. `Content-Type: text/javascript; charset=utf-8`, `Cache-Control: public, max-age=86400`, `Vary: Accept-Encoding`, gzipped when `Accept-Encoding` takes gzip ([tracker.md](tracker.md), "Serving it"). `404 not_found` when no script was built. Never sets a cookie |
| GET | `/health` | Liveness: answers while the process runs |
| GET | `/health/ready` | Readiness: checks Redis. Postgres isn't checked, because ingest doesn't start without it ([write-path.md](write-path.md), "The caches") |
| GET | `/version` | The git commit the image was built from |

```json
{ "n": "pageview", "d": "example.com", "u": "https://example.com/pricing?utm_source=hn",
  "r": "https://news.ycombinator.com/", "p": { "plan": "pro" } }
```

`n` name, `d` site domain, `u` full URL (`http://` or `https://`), `r` optional referrer, `p` optional properties. Property values are strings: the tracker converts numbers. Unknown keys are ignored. The limits are in [conventions.md](../conventions.md) section 7.2. The timestamp is set by the server; client clocks are not trusted.

**Dashboard API** (same origin as the dashboard: Caddy routes `mieru.example/api/v1/...` to `api`)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `/auth/login`, `/auth/logout`, `/auth/verify-email`; `GET /me` |
| Sites | `GET/POST /sites`, `GET/PATCH/DELETE /sites/{id}`, `GET /sites/{id}/verify` (has any event arrived?) |
| Stats | `GET /sites/{id}/stats/aggregate`, `/timeseries`, `/breakdown`, `/realtime`. Common parameters: `period` or `from`/`to`, `metrics`, `property`, `filters`, `limit` |
| Goals *(after MVP)* | `GET/POST /sites/{id}/goals`, `DELETE /goals/{id}` |
| Sharing *(after MVP)* | `POST /sites/{id}/shared-links`; `GET /share/{slug}/stats/...` (no login, read-only) |
| Billing *(after MVP)* | `GET /billing/usage`, `POST /billing/checkout`, `POST /billing/portal`, `POST /billing/webhook` |
| Alerts *(after MVP)* | `GET/POST /sites/{id}/alerts`, `GET/POST /channels`, `POST /channels/{id}/verify` |

**Conventions:** JSON; one error shape `{"error": {"code", "message", "details"}}`; a site the user cannot access returns `404`, not `403`, so ids cannot be probed.

**Auth model.** Login creates a random session token, sets it in an `HttpOnly; Secure; SameSite=Lax` cookie, and stores only its hash in Postgres. Each request looks the hash up (cached in Redis for 60 s). Logout or password change deletes the row, which revokes the session immediately. Passwords are hashed with argon2. Because Caddy serves the dashboard and its API from one origin, there is no CORS configuration on the dashboard API at all.
