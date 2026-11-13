# API surface

> Part of the [system design](README.md).

**Ingest** (public, on its own domain)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/event` | Body below. `202` when well formed, `400` when not. `Access-Control-Allow-Origin: *`. Never sets a cookie |
| GET | `/js/p.js` | The tracking script, cached for a day |
| GET | `/health` | Liveness, checks Redis |

```json
{ "n": "pageview", "d": "example.com", "u": "https://example.com/pricing?utm_source=hn",
  "r": "https://news.ycombinator.com/", "p": { "plan": "pro" } }
```

`n` name, `d` site domain, `u` full URL, `r` referrer, `p` optional properties. The timestamp is set by the server; client clocks are not trusted.

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

**Conventions:** JSON; one error shape `{"error": {"code", "message"}}`; a site the user cannot access returns `404`, not `403`, so ids cannot be probed.

**Auth model.** Login creates a random session token, sets it in an `HttpOnly; Secure; SameSite=Lax` cookie, and stores only its hash in Postgres. Each request looks the hash up (cached in Redis for 60 s). Logout or password change deletes the row, which revokes the session immediately. Passwords are hashed with argon2. Because Caddy serves the dashboard and its API from one origin, there is no CORS configuration on the dashboard API at all.
