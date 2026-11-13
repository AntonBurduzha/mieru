---
id: T03
title: Server bootstrap and first manual deploy
mode: human
status: done
features: []
depends: [T02]
branch: t03-server-bootstrap-deploy
---

# T03 Server bootstrap and first manual deploy

## Goal
One script turns a fresh Hetzner server into one that is ready for deploys. The production stack from T02 runs on it under the real domain, with Let's Encrypt certificates, behind a provider firewall that lets in only TCP 22, 80 and 443. The manual deploy written down here is what T04 automates.

## Context to read
- `docs/architecture.md`:
  - section 11: "The server", "Network and access", "Keeping the server healthy";
  - section 13 rows 17, 19, 21, 25 and 28.
- `docs/conventions.md`:
  - section 1 (layout);
  - section 7.5 (secrets, server and containers);
  - section 7.7 row "Server and deploy".
- `compose.prod.yaml`, `ops/caddy/Caddyfile` and `ops/rehearsal.env`. They run unchanged on the server.
- [T02](T02-prod-compose-caddy.md) Review: finding 2 (check the ACME log line at the first issuance), finding 3 (IPv6) and the final verifier's note on criterion 9.
- Docs:
  - [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
  - [Hetzner Cloud Firewalls](https://docs.hetzner.com/cloud/firewalls/overview)
  - [`sshd_config`](https://man.openbsd.org/sshd_config)
  - [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)
  - [Cloudflare proxy status](https://developers.cloudflare.com/dns/proxy-status/)

## Scope
- **In:**
  - Order the server: Hetzner, 4 vCPU, 8 GB, 80 GB, an EU location, Ubuntu 26.04 LTS, with the developer's SSH key added at creation. CX33 if it can be ordered, otherwise the closest Hetzner offer of that size.
  - Write `ops/bootstrap.sh`.
  - Set up the Hetzner Cloud Firewall.
  - Add Cloudflare A records for `$MIERU_DOMAIN` and `in.$MIERU_DOMAIN`: DNS only, no AAAA.
  - Write the server's `.env`.
  - Do the first deploy by hand, at main's commit, then a reboot check.
  - Write the runbook, and record the decisions in the docs.
- **Out:**
  - `deploy.sh`, the CI deploy key and the rollback drill: T04.
  - Provider backups and database dumps: T24.
  - Docker log size caps, the disk alert and an uptime check: T25.
  - IPv6 on `edge`, the outside port scan and the header audit: T26.
  - The writer and worker containers: their own tasks.
  - The web app's files: T20.
- **Files allowed to change:**
  - `ops/bootstrap.sh` (new)
  - `ops/README.md` (new: the runbook)
  - `ops/server.env.example` (new)
  - `docs/architecture.md`: section 11, and new rows in section 13
  - `docs/conventions.md`: the section 1 layout

  `compose.prod.yaml` and the Caddyfile don't change.

## Interfaces
**`ops/bootstrap.sh`**
- It runs as root on a fresh Ubuntu 26.04 and takes no arguments: `ssh root@$MIERU_HOST 'bash -s' < ops/bootstrap.sh`.
- It is safe to run again, from the Mac as well: `ssh deploy@$MIERU_HOST 'sudo bash -s' < ops/bootstrap.sh`. The server's checkout tracks `main`, which has the script only after this task merges. The whole body sits in a function called on the last line, so bash reads the entire script before anything in it can read stdin. It starts with `set -euo pipefail`.
- In order:
  1. A noninteractive `apt-get upgrade`.
  2. `unattended-upgrades`, installed and enabled.
  3. Docker Engine and the Compose plugin, from Docker's apt repository, enabled on boot.
  4. The timezone set to UTC.
  5. A user `deploy`:
     - in the `docker` group;
     - with passwordless sudo through a file in `/etc/sudoers.d/`, checked with `visudo -cf`;
     - with a copy of root's `authorized_keys` when it has none yet.
  6. An sshd drop-in, `/etc/ssh/sshd_config.d/00-mieru.conf`, with `PermitRootLogin no`, `PasswordAuthentication no` and `KbdInteractiveAuthentication no`. Check it with `sshd -t`, then reload.
  7. A clone of the public repository over https into `/srv/mieru`, owned by `deploy`.

**Server layout**
- `/srv/mieru` is a git checkout of `main`. The first deploy uses main's commit as it is today: T03 changes neither `compose.prod.yaml` nor the Caddyfile. The stack runs from there with `docker compose -f compose.prod.yaml up -d --wait`.
- `/srv/mieru/.env`:
  - holds `MIERU_TAG`, `MIERU_DOMAIN`, `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD` and `ACME_EMAIL`;
  - has mode `600` and owner `deploy`;
  - gets its passwords from `openssl rand -hex 32`.
- `MIERU_TAG` is the full sha that `git rev-parse HEAD` prints in the checkout, so the image and the config come from the same commit.

**`ops/server.env.example`** holds the same five names with a comment each, and no values.

## Acceptance criteria
Run from the repo root on the Mac, after setting this up:

```bash
export MIERU_HOST=<the server's IPv4> MIERU_DOMAIN=<the domain>
S="ssh -o BatchMode=yes -o ConnectTimeout=10 deploy@$MIERU_HOST"
```

1. The bootstrap script passes shellcheck. (auto: `docker run --rm -v "$PWD:/mnt:ro" -w /mnt koalaman/shellcheck:v0.11.0 ops/bootstrap.sh`)
2. The server has at least 4 CPUs, 7.5 GB of memory and 70 GB on `/`, and runs Ubuntu 26.04. (auto: `$S 'test "$(nproc)" -ge 4 && awk "/^MemTotal/ {exit \$2 < 7500000}" /proc/meminfo && test "$(df -BG --output=size / | tail -1 | tr -dc 0-9)" -ge 70 && grep -qx "VERSION_ID=\"26.04\"" /etc/os-release'`)
3. SSH takes keys only, and root can't log in. (auto: `! ssh -o BatchMode=yes -o ConnectTimeout=10 root@$MIERU_HOST true && c=$($S sudo sshd -T) && grep -qx 'permitrootlogin no' <<<"$c" && grep -qx 'passwordauthentication no' <<<"$c" && grep -qx 'kbdinteractiveauthentication no' <<<"$c"`)
4. Security updates install themselves, Docker starts on boot, and `deploy` can use Docker. (auto: `$S 'systemctl is-enabled --quiet unattended-upgrades && systemctl is-enabled --quiet docker && apt-config dump | grep -qF "APT::Periodic::Unattended-Upgrade \"1\";" && id -nG | grep -qw docker && docker compose version'`)
5. A second run of the bootstrap succeeds and changes no access. (auto: `$S 'sudo bash -s' < ops/bootstrap.sh && $S true && ! ssh -o BatchMode=yes -o ConnectTimeout=10 root@$MIERU_HOST true`)
6. The provider firewall blocks a port the server listens on, and lets 22, 80 and 443 through. (auto: `$S 'nohup timeout 30 python3 -m http.server 9999 >/dev/null 2>&1 & sleep 2; curl -sf -o /dev/null http://127.0.0.1:9999/' && ! nc -z -G 3 -w 3 "$MIERU_HOST" 9999 && nc -z -G 3 -w 3 "$MIERU_HOST" 22 && nc -z -G 3 -w 3 "$MIERU_HOST" 80 && nc -z -G 3 -w 3 "$MIERU_HOST" 443`)
7. Both names point straight at the server (not through Cloudflare's proxy), and neither has an AAAA record. (auto: `test "$(dig +short A "$MIERU_DOMAIN" @1.1.1.1)" = "$MIERU_HOST" && test "$(dig +short A "in.$MIERU_DOMAIN" @1.1.1.1)" = "$MIERU_HOST" && test -z "$(dig +short AAAA "$MIERU_DOMAIN" @1.1.1.1)" && test -z "$(dig +short AAAA "in.$MIERU_DOMAIN" @1.1.1.1)"`)
8. The server's `.env` is readable only by `deploy`, and the checkout has no local changes. (auto: `$S 'cd /srv/mieru && test "$(stat -c "%a %U" .env)" = "600 deploy" && git check-ignore -q .env && test -z "$(git status --porcelain)"'`)
9. All 7 services are healthy and run the checkout's commit. (auto: `$S 'cd /srv/mieru && docker compose -f compose.prod.yaml ps -a --format "{{.Service}} {{.Health}}"' | awk 'NF < 2 || $2 != "healthy" {bad = 1} END {exit bad || NR != 7}' && test "$(curl -s "https://$MIERU_DOMAIN/api/v1/version")" = "{\"version\":\"$($S git -C /srv/mieru rev-parse HEAD)\"}"`)
10. Both hosts have trusted Let's Encrypt certificates (no `-k`), are ready, and HTTP redirects to HTTPS with the query kept. (auto: `curl -sf "https://$MIERU_DOMAIN/health/ready" | grep -q '"clickhouse":"ok"' && curl -sf "https://in.$MIERU_DOMAIN/health/ready" | grep -q '"redis":"ok"' && test "$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "http://$MIERU_DOMAIN/x?a=1")" = "308 https://$MIERU_DOMAIN/x?a=1" && for n in "$MIERU_DOMAIN" "in.$MIERU_DOMAIN"; do echo | openssl s_client -connect "$MIERU_HOST:443" -servername "$n" 2>/dev/null | openssl x509 -noout -issuer; done | grep -c "Let's Encrypt" | grep -qx 2`)
11. The T02 headers hold in production, and requests by IP or for an unknown host get no answer. (auto: `h=$(curl -s -D - -o /dev/null "https://$MIERU_DOMAIN/api/v1/version") && grep -qi '^strict-transport-security: max-age=31536000; includeSubDomains' <<<"$h" && grep -qi "^content-security-policy:.*frame-ancestors 'none'" <<<"$h" && ! grep -qiE '^(server|via):' <<<"$h" && i=$(curl -s -D - -o /dev/null "https://in.$MIERU_DOMAIN/health") && grep -qi '^access-control-allow-origin: \*' <<<"$i" && ! grep -qiE '^(content-security-policy|server|via):' <<<"$i" && ! curl -s -o /dev/null -H 'Host: unknown.example' "http://$MIERU_HOST/" && ! curl -sk -o /dev/null "https://$MIERU_HOST/"`)
12. No Caddy log line holds a public IPv4 address, the developer's address, the probe user agent or a query string, and requests to ingest aren't logged. This covers every line since the container was created, including the first certificate issuance. Don't recreate `caddy` before this check (a restart or a reboot keeps the log). (auto: `me=$($S 'echo "${SSH_CLIENT%% *}"') && test -n "$me" && curl -s -A MieruProbe/1.0 -o /dev/null "https://$MIERU_DOMAIN/api/v1/version?probe=1" && curl -s -A MieruProbe/1.0 -o /dev/null "https://in.$MIERU_DOMAIN/version" && sleep 1 && l=$($S 'cd /srv/mieru && docker compose -f compose.prod.yaml logs --no-log-prefix caddy') && grep -q 'certificate obtained successfully' <<<"$l" && grep -q '"uri":"/api/v1/version"' <<<"$l" && ! grep -qF "$me" <<<"$l" && ! grep -qE 'MieruProbe|probe=1|"host":"in\.' <<<"$l" && test -z "$(grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' <<<"$l" | grep -vE '^(10|127|172\.(1[6-9]|2[0-9]|3[01])|192\.168)\.')"`)
13. The stack comes back after a reboot, and the consoles match the design. (human: `$S sudo reboot`, wait a minute, then criteria 9 and 10 pass again. In the Hetzner console, the firewall allows inbound TCP 22, 80 and 443 only, it is applied to this server, and the location is in the EU. In Cloudflare, both records are "DNS only" (a grey cloud).)

## Tests to write
None in Python: this task changes no app code. The proof is criteria 1-12, run against the real server.

## Proof of done
Set up `MIERU_HOST`, `MIERU_DOMAIN` and `S` as above, then run criteria 1-12 in order. Scope:

```bash
git diff --name-only main...HEAD   # only the allowed files, this spec and docs/roadmap.md
```

Expected: every command exits 0. The developer gives the values of `MIERU_HOST` and `MIERU_DOMAIN` to `/task`, and they aren't written in the repository.

## Test it yourself
1. Open `https://<domain>` in a browser. Expect the padlock, a certificate issued by Let's Encrypt, and "Mieru is coming soon".
2. `curl https://in.<domain>/health/ready`: every check is `"ok"`.
3. `ssh root@<ip>` is refused with `Permission denied (publickey)`.
4. `ssh deploy@<ip> sudo reboot`. A minute later, step 1 works again.
5. In Cloudflare, both records show a grey cloud.

## Where to debug
Shell and config, not Python: no breakpoints. To trace a step, run the script with `bash -x`: `ssh deploy@$MIERU_HOST 'sudo bash -x -s' < ops/bootstrap.sh`.

1. **Who can log in over SSH**: `ops/bootstrap.sh:112` (`harden_ssh`)
   - Why here: the drop-in decides root and password logins. A bad file here is the one way a rerun locks you out. The check removes it before the reload on line 114.
   - Trigger: a bootstrap rerun (criterion 5).
   - Look at: `sudo sshd -T | grep -E 'permitrootlogin|passwordauthentication|kbdinteractiveauthentication'` prints `no` three times. `ls /etc/ssh/sshd_config.d/` lists `00-mieru.conf` before the provider's `50-cloud-init.conf`.
2. **Which Docker repository apt uses**: `ops/bootstrap.sh:54` (`install_docker`)
   - Why here: the codename picks the suite on line 61. If Docker doesn't publish that codename yet, `apt_get update` fails with a 404 on `download.docker.com`.
   - Trigger: the first bootstrap run on a new Ubuntu release.
   - Look at: `cat /etc/apt/sources.list.d/docker.sources` shows `Suites: resolute`. `apt-cache policy docker-ce` shows a candidate from `download.docker.com`.
3. **Waiting for the dpkg lock**: `ops/bootstrap.sh:23` (`apt_get`)
   - Why here: every apt call goes through it. On a fresh server cloud-init holds the lock, and without the timeout step 1 fails with "Could not get lock".
   - Trigger: bootstrap right after the server is created.
   - Look at: `bash -x` output pauses on `apt-get ... update`. `sudo fuser -v /var/lib/dpkg/lock-frontend` names who holds the lock.
4. **Which image and domain the stack runs**: `compose.prod.yaml:6`
   - Why here: `.env` fills `MIERU_TAG` here and `MIERU_DOMAIN` on lines 12-13 and 29. Without them `up` stops with "set MIERU_TAG to an image tag". A tag CI hasn't pushed gives `manifest unknown`.
   - Trigger: `docker compose -f compose.prod.yaml up -d --wait` in `/srv/mieru`.
   - Look at: `docker compose -f compose.prod.yaml config | grep -E 'image:|MIERU_DOMAIN'` shows the full sha and the domain.
5. **The certificate for each host**: `ops/caddy/Caddyfile:64` (and `in.` on line 98)
   - Why here: a site address that is a real name makes Caddy request a certificate from Let's Encrypt. Wrong DNS or a closed port 80 shows up as a TLS error in the browser.
   - Trigger: the first `up`, or the first request to a new host.
   - Look at: `docker compose -f compose.prod.yaml logs caddy | grep -E 'obtain|challenge|error'` shows `certificate obtained successfully` for both names.

## Notes
Decisions made in the spec:
- **Ubuntu 26.04 LTS:** supported until 2031, and Docker's apt repository has `resolute`. The alternative is 24.04, if Hetzner's image or Docker's packages cause trouble.
- **One `deploy` user with passwordless sudo and the `docker` group:** the `docker` group already equals root, so a separate admin user adds little. The alternative is an `admin` user with sudo and a `deploy` user with Docker only.
- **A git checkout in `/srv/mieru`, with the image tag equal to its sha:** T04's `deploy.sh` becomes `git pull`, `compose pull` and a rolling restart. The alternative is copying only `compose.prod.yaml` and `ops/` with `scp`.
- **Root's `authorized_keys` is copied** (Hetzner adds the key at creation), so the script needs no arguments. The alternative is a public key as an argument.
- **No AAAA records:** every visitor connects over IPv4, so Caddy sees the real address (T02 review finding 3). Proper IPv6 is T26.
- **The image is public on GHCR,** so the server needs no registry login. T04 revisits this with the CI deploy key.
- **No automatic reboots after kernel updates** until the uptime check exists (T25). **No fail2ban:** with SSH keys only, it mostly adds log noise.

## Review
**Verification, 2026-11-13.** The agents had no permission to reach the server, so the developer ran criteria 2-12 from the Mac, in bash 3.2, after the review fixes. Criterion 5 ran the fixed `ops/bootstrap.sh`.

| Check | Result |
|---|---|
| 1. shellcheck | exit 0 (agent, after the fixes) |
| Scope | only the allowed files, this spec and `docs/roadmap.md` |
| Server values in the repo | none: neither the IPv4 nor the domain is in a tracked file |
| 2-11 | exit 0 each (developer) |
| 12 | exit 0 (developer). The first run gave exit 2, `test: too many arguments`: the agent's copy of the command had double quotes around the regexes, which bash 3.2 mis-parses inside `"$(...)"`. The spec's own form passes. |
| 13 | confirmed by the developer: the stack is back after a reboot; the Hetzner firewall allows TCP 22, 80 and 443 only and is applied to the server, located in Helsinki; both Cloudflare records are DNS only |

**Reviewer, 2026-11-13:** 0 must-fix, 6 should-fix, 6 nits. The fixes are applied in the working tree, uncommitted.

1. Should-fix: `apt-get` failed at once when dpkg was locked. Fixed: every call goes through `apt_get` with `DPkg::Lock::Timeout=300`.
2. Should-fix: a rerun can upgrade `docker-ce`, and that restarts every container. The comment said otherwise. Fixed: the comment, the script header and runbook step 3 now say so. Alternative not taken: `apt-mark hold`, which would also hold back Docker's security fixes.
3. Should-fix: pasting the `.env` block twice wrote new passwords. Fixed: `set -o noclobber` in runbook step 5.
4. Should-fix: `architecture.md` section 11 had `<price>` and the wrong model. Fixed: CPX32, 30.49 EUR/month.
5. Should-fix: the commit message `(infra): ...` has no type. Left for the developer's final commit.
6. Should-fix: the debug map was empty. Fixed: "Where to debug" above.
7. Nit: the sshd drop-in stayed behind when `sshd -t` failed. Fixed: it is removed before `die`.
8. Nit: the `mktemp` file stayed behind when `visudo -cf` failed. Fixed.
9. Nit: `install_docker` depended on globals from `check_system`. Fixed: each function reads `/etc/os-release` in a subshell.
10. Nit: `curl` had no timeout. Fixed: `--connect-timeout 10 --max-time 60`.
11. Nit: the runbook's certificate `grep` could run before the first issuance. Fixed: `timeout 120 ... logs -f caddy | grep -m1`.
12. Nit: the roadmap's `Updated` date was stale. Fixed.

**Final verdict, 2026-11-13: DONE.** The developer passed the "Test it yourself" steps. The verifier agent couldn't reach the server, so the evidence is the developer's run of criteria 2-13 above. It ran against the current `ops/bootstrap.sh`, and no code changed after it. No feature in `docs/features.json` belongs to T03.
