# Observability and load test

> Part of the [system design](README.md).

Mieru is itself a monitoring product, so its own pipeline has to be visible.

**MVP:**
- Structured JSON logs to Docker, with a size cap per container.
- `/health/ready` on every HTTP service, and a pipeline health endpoint with the stream length and the age of its oldest entry.
- An external uptime check on both domains.
- A disk alert at 70%.

**After MVP:** each service sends metrics, traces and logs over OpenTelemetry to a Grafana Alloy container. Alloy forwards them, together with container logs and host metrics, to Grafana Cloud's free tier. The table below is the target for that stage. The stream age row is already covered in the MVP by the health endpoint.

**The numbers that say whether the pipeline is healthy**

| Metric | Where | Alert when |
|---|---|---|
| Ingest requests/s and latency (p50, p95, p99) | `ingest` | p99 > 100 ms for 5 min |
| Events dropped, by reason | `ingest` | Drops > 1% of accepted (excluding bots) |
| **Stream length and age of the oldest entry** | `writer` | Oldest entry > 30 s |
| Batch size, insert duration, insert failures | `writer` | Any failure streak > 1 min |
| ClickHouse query duration by query type | `api` | p95 > 1 s |
| Cache hit ratio | `api` | Informational |
| ClickHouse active parts, memory, disk used | ClickHouse | Disk > 70% |
| Job failures | `worker` | Any |
| Host CPU, memory and disk; memory per container | `alloy` | Disk > 70%, memory > 90% |

Stream age is the single best signal: if it grows, the writer or ClickHouse is behind, whatever the cause.

**Load test (k6)** *(after MVP)*

k6 runs on a second server rented by the hour in the same data centre, so the load generator does not take CPU from the system under test. It targets the public domain, so TLS and Caddy are part of the measurement. The vCPUs are shared with other customers, so each scenario runs three times and the median is reported.

All k6 traffic comes from one IP, which would make every request the same visitor. A header accepted only with a load-test secret lets k6 supply a visitor seed, so the test produces a realistic number of sessions.

| # | Scenario | Passes when |
|---|---|---|
| 1 | Ramp to 1,000 events/s, hold 10 min | p99 < 100 ms, stream age < 5 s, ClickHouse row count = events sent |
| 2 | Burst to 3,000 events/s for 30 s | No errors; stream drains within 60 s |
| 3 | One site at 5x its rate limit, others normal | Other sites' latency unchanged; excess counted as `rate_limited` |
| 4 | Kill `writer` during scenario 1 | No loss, no duplicates, lag recovers after restart |
| 5 | Restart ClickHouse during scenario 1 | No loss; ingest latency unaffected |
| 6 | 20 dashboard users on a 10M-event site during scenario 1 | Dashboard p95 < 300 ms |
| 7 | Deploy a new version during scenario 1 | No loss, no failed requests seen by k6 |

Output: a results table and the bottlenecks found, in the README. The headline is "N events/s on one 4 vCPU / 8 GB server at about 11 EUR/month", with proxy, application, Redis and ClickHouse all sharing that server.
