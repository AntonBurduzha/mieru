# Mieru: product

> What Mieru is for, who uses it, and what "done" means for each feature. *How* it works is in [architecture.md](architecture.md). The build order is in [roadmap.md](roadmap.md).

---

## 1. Problem

Small sites (indie products, blogs, documentation, small businesses) want to know three things: **how many people came, where from, and what they looked at**. Google Analytics answers that with a script over 100 KB, cookies, a consent banner, and a dashboard built for marketing teams. Plausible showed there is a paying market for the opposite: a tiny script, no cookies, one page of numbers.

Mieru is a smaller Plausible, built end to end: a write-heavy pipeline, a columnar store, a dashboard and the operations around them. Billing and alerts come after the MVP.

**Positioning in one line:** "See your traffic, not your visitors." Privacy-first, a 1 KB script, custom properties on every plan.

---

## 2. People

| Persona | Who | Needs | Pain today |
|---|---|---|---|
| **Site owner** (customer) | Indie developer or small business running 1-5 sites | Install in two minutes, trust the numbers, get told when something happens | GA is heavy and needs a banner; Plausible's custom properties cost the top tier |
| **Visitor** (data subject) | Anyone reading a site that uses Mieru | Not to be identified, tracked across sites, or slowed down | Tracking scripts and consent pop-ups |
| **Operator** | Runs Mieru | See pipeline health, deploy without losing events, keep the bill under 15 EUR a month | When something breaks at 2 a.m., there is no one else to fix it |

---

## 3. User stories and acceptance criteria

The ids match the functional requirements in [architecture.md](architecture.md) section 2. [features.json](features.json) has one entry per id, with the command that proves it.

### Tracking (MVP)

| ID | Story | Accepted when |
|---|---|---|
| T1 | As a site owner, I add one script tag and page views appear | Loading a page sends one `POST /api/event` with `n=pageview`, the URL and the referrer; the visit shows in the dashboard within 35 s |
| T2 | My single-page app counts navigations | `history.pushState` to a new path sends one page view; a change of query string only does not |
| T3 | I can track a custom event with properties | `mieru('signup', {props: {plan: 'pro'}})` sends one event with `p.plan = "pro"`; calls made before the script loaded are sent once it loads |
| T4 | Automated browsers are not counted | With `navigator.webdriver = true` the script sends nothing |

### Ingest (MVP)

| ID | Story | Accepted when |
|---|---|---|
| I1 | Only my registered sites are counted | An event for an unknown domain gets `202` with `X-Mieru-Dropped: unknown_site` and is not stored |
| I2 | Bots, spam and floods don't pollute my numbers | Known bot user agents, spam referrers and over-limit traffic are dropped and counted per reason |
| I3 | I see where visitors come from and what they use | Each stored event has country, device, browser, OS, source and UTM tags |
| I4 | Visits are grouped sensibly | Events from one visitor less than 30 minutes apart are one session; a 31-minute gap starts a new one; a session crossing midnight UTC stays one session |

### Dashboard

| ID | Story | Accepted when | Scope |
|---|---|---|---|
| D1 | I see the headline numbers for any range | Unique visitors, visits, page views, bounce rate and visit duration for today, 7d, 30d, month, 12 months and custom ranges, in the site's timezone | MVP |
| D2 | I see the trend | A chart by hour (ranges up to 2 days) or by day | MVP |
| D3 | I see what drives traffic | Top sources, UTM tags, pages, entry and exit pages, countries, devices, browsers and OS, 10 rows each with "show more" | MVP |
| D4 | I see who is here now | "Current visitors" (last 5 minutes) refreshes every 30 s without reloading | MVP |
| D6 | I can drill down | Clicking any breakdown row filters the whole dashboard; filters live in the URL, so a link reproduces the view | MVP |
| D5 | I measure what matters | I mark an event name or a page path as a goal and see conversions and conversion rate | After MVP |
| D7 | I can share publicly | A secret link shows a read-only dashboard without login; revoking it breaks the link | After MVP |
| D8 | I can compare, export and automate | Comparison with the previous period on the top numbers; CSV export of any breakdown; a read-only Stats API with API keys | After MVP |

### Accounts, plans, alerts

| ID | Story | Accepted when | Scope |
|---|---|---|---|
| A1 | I can sign up and come back | Sign up, verify email by link, log in, log out; the session survives a browser restart for 30 days and ends at once on logout | MVP |
| A2 | I can add a site and know it works | Adding a domain shows the snippet; "is it installed?" turns green after the first event arrives | MVP |
| A3 | I know my limits | Settings show events used this month vs the plan quota, and sites used vs the site limit | After MVP |
| A4 | I can upgrade and cancel | Stripe Checkout (test mode) upgrades the plan within a minute of payment; the customer portal cancels it | After MVP |
| A5 | I hear about spikes and get a summary | A spike alert fires once when current visitors pass my threshold (not again within an hour); a weekly report arrives on Monday morning in my timezone | After MVP |
| A6 | I choose where alerts go | One or more email addresses, each verified before use | After MVP |

---

## 4. Plans and limits *(after MVP)*

In the MVP every account gets the Free limits, and there is no billing. Prices are test-mode only; the point is the metering and billing machinery, not revenue.

| Plan | Events / month | Sites | Price |
|---|---|---|---|
| Free | 10,000 | 1 | 0 |
| Starter | 100,000 | 3 | $9 |
| Pro | 1,000,000 | 10 | $19 |

**Rules**

- Plans are defined in code (`mieru.services.plans`), not in the database, so a price change is a reviewed commit.
- Usage counts accepted events per organization per calendar month (UTC).
- **80% and 100%:** email the owner and show a banner. Data is still collected.
- **150%:** the site status becomes `over_quota` and ingest drops new events with that reason until the next month or an upgrade. A good traffic day should not lose a customer's data; the hard cap protects the operator's bill.
- Upgrades apply as soon as Stripe's webhook arrives. Downgrades apply at the end of the billing period.
- ClickHouse is the source of truth for counts; a nightly job reconciles the Redis counter against it.

**Alerts**

| Rule | Checked | Fires when | Repeat protection |
|---|---|---|---|
| Spike | Every minute | Current visitors (unique visitors in the last 5 minutes) reach the site's threshold | One-hour cooldown per rule |
| Weekly report | Mondays 08:00 in the site's timezone | Always (if enabled) | One per site per week |

One query per minute computes current visitors for every site that has a spike rule, instead of one query per site.

---

## 5. Scope

**MVP:** T1-T4, I1-I4, D1-D4, D6, A1, A2. It is deployed with no lost events across deploys, backed up, and monitored at a basic level.

**After MVP**, roughly in order of value:
1. Goals and custom properties (D5)
2. Shared links (D7)
3. Comparison and CSV (D8)
4. Plans, quotas and usage (A3)
5. Stripe (A4)
6. Email alerts and the weekly report (A5, A6)
7. OpenTelemetry and Grafana
8. Load tests and the rollup decision
9. The Stats API (D8)
10. A landing page

**Stretch:** outbound link, file download and 404 tracking; scroll depth; per-site block rules (IP, country, page); team invites; a "no data for 24 hours" alert; funnels; a guide for proxying the script through the customer's domain.

**Non-goals**

- Tracking individuals across days, devices or sites.
- Error tracking, uptime checks, session replay (the ingest path leaves room for them, nothing is built).
- Revenue attribution, Google Analytics import, Search Console.
- High availability. One server, best effort.
- Real payments. Stripe stays in test mode.
- Any AI feature in the product.

---

## 6. Success criteria

**MVP**

- Mieru runs on my own sites, and on at least three outside sites.
- It has a public URL and a demo account with seeded data.
- CI is green: lint, types, unit, integration and end-to-end tests, and the script size check.
- No events are lost across a deploy, and a backup has been restored at least once.

**After MVP**

- Load-test numbers in the README: sustained events/s, p99 ingest latency, dashboard p95, all on one 4 vCPU / 8 GB server.
- Grafana dashboards and alerts for the pipeline, and runbooks for the top failures.
- A 2-minute demo video.

---

## 7. Launch plan

| When | What | Measure |
|---|---|---|
| First page view end to end (roadmap M1) | Mieru on my own sites (blog, portfolio) | Events arriving; from M2 on, no drops except bots |
| MVP launch (roadmap M4) | 3-5 friendly outside users (friends' sites, dev communities) | Install time, first questions, bugs reported |
| After MVP | Public launch post (Indie Hackers, a dev community, LinkedIn); Mieru tracks its own landing page | Sign-ups, sites added, sites with data after 24 h |

Feedback goes into GitHub issues with a `feedback` label, and each one gets a reply within a day.

**The funnel to watch** (Mieru tracking itself): landing page view → sign-up → site added → first event received. Analytics products lose most people at the last step, which is why onboarding has an "is it installed?" check.
