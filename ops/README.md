# Operating the server

How a fresh server becomes the running production stack, and how it is deployed and rolled back. The design is in [architecture.md](../docs/architecture.md) section 11.

## 1. Order the server

- Hetzner Cloud, x86 only (CX, CPX or CCX). The `mieru-app` image is built for `linux/amd64`, so the Arm line (CAX) can't run it.
- Size: 4 vCPU, 8 GB, at least 80 GB. Location: Falkenstein, Nuremberg or Helsinki. Image: Ubuntu 26.04.
- Networking: public IPv4 and IPv6. SSH key: yours. Firewall: `mieru` (step 2). Backups: off until T24.

## 2. Firewall

A Hetzner Cloud Firewall named `mieru`, applied to the server. Inbound: TCP 22, 80 and 443 from any IPv4 and IPv6, nothing else. Outbound: no rules, which allows everything.
It sits outside the server, so Docker's own packet-filter rules can't bypass it.

## 3. Bootstrap

```bash
ssh root@<ip> 'bash -s' < ops/bootstrap.sh
ssh deploy@<ip> sudo true        # from a second terminal, before closing the root session
ssh deploy@<ip> sudo reboot      # only if the script says a reboot is needed
```

The script upgrades packages, turns on security updates, and installs Docker from Docker's repository. It creates the `deploy` user (SSH key, `docker` group, passwordless sudo) and turns off root and password logins. Finally, it clones this repository into `/srv/mieru`. Later runs use `ssh deploy@<ip> 'sudo bash -s' < ops/bootstrap.sh`. A later run upgrades packages as well: when a new Docker Engine is out, the upgrade restarts Docker and every container, so run it when a short outage is fine.

## 4. DNS

In Cloudflare, add two A records to the server's IPv4: `<domain>` and `in.<domain>`. Proxy status: DNS only. No AAAA records: over IPv6, Docker's proxy would hide every visitor's address behind the bridge gateway (architecture.md, trade-off 30).
Check: `dig +short A in.<domain> @1.1.1.1` prints the server's IPv4.

## 5. The server's `.env`

As `deploy`, in `/srv/mieru`, with the names from [server.env.example](server.env.example):

```bash
git pull --ff-only
read -rp 'Domain: ' domain && read -rp 'ACME email: ' email
( set -o noclobber; umask 077; cat > .env <<EOF
MIERU_TAG=$(git rev-parse HEAD)
MIERU_DOMAIN=$domain
POSTGRES_PASSWORD=$(openssl rand -hex 32)
CLICKHOUSE_PASSWORD=$(openssl rand -hex 32)
ACME_EMAIL=$email
EOF
)
```

Write it once. Postgres keeps the password it was first started with, so a new `POSTGRES_PASSWORD` locks the apps out. `noclobber` makes a second paste fail with "cannot overwrite existing file" instead of writing new passwords.

## 6. First start

The first time only, the whole stack starts by hand:

```bash
cd /srv/mieru
docker manifest inspect ghcr.io/antonburduzha/mieru-app:$(git rev-parse HEAD) > /dev/null   # CI has published this commit
docker compose -f compose.prod.yaml up -d --wait
timeout 120 docker compose -f compose.prod.yaml logs -f caddy | grep -m1 'certificate obtained successfully'   # the first issuance can take a minute
curl -s https://<domain>/api/v1/version          # the sha from step 5
```

After that, every merge to `main` deploys itself (step 9). To deploy a commit by hand:

```bash
ssh deploy@<ip> /srv/mieru/ops/deploy.sh <full sha on main>
```

`deploy.sh` refuses a commit that isn't on `main`, a checkout with local changes, and a second deploy while one runs. It pulls the image, detaches the checkout at the commit, sets `MIERU_TAG` in `.env`, and restarts `ingest-1`, `ingest-2` and `api` one at a time, each ready before the next. The databases and `caddy` keep running; Caddy only reloads its config. The checkout stays detached at the deployed commit, so `git -C /srv/mieru rev-parse HEAD` names what runs.

## 7. After a reboot

Docker starts on boot and every service has `restart: unless-stopped`, so the stack comes back by itself. Check it with `docker compose -f compose.prod.yaml ps`: every service is `healthy`.

## 8. The CI deploy key

CI connects with its own key, which can run `ops/deploy.sh` and nothing else. On the Mac:

```bash
ssh-keygen -t ed25519 -N '' -C mieru-ci-deploy -f ~/.ssh/mieru_ci_deploy
pub=$(cat ~/.ssh/mieru_ci_deploy.pub)
ssh deploy@<ip> "echo 'restrict,command=\"/srv/mieru/ops/deploy.sh\" $pub' >> ~/.ssh/authorized_keys"
```

- `command=` makes sshd run the script whatever the client asks for; the client's text reaches it as `SSH_ORIGINAL_COMMAND` and must be a single sha. `restrict` turns off forwarding and the terminal.
- Your own key stays unrestricted, on its own line.
- To replace the key: delete its line in `~/.ssh/authorized_keys` on the server, then repeat this step and step 9's secret.

## 9. The GitHub `production` environment

Settings -> Environments -> New environment `production`:

- Deployment branches and tags: "Selected branches and tags", rule `main`.
- No required reviewers: a merge deploys by itself.
- Secret `DEPLOY_SSH_KEY`: `gh secret set DEPLOY_SSH_KEY --env production < ~/.ssh/mieru_ci_deploy`
- Variables:
  - `DEPLOY_HOST`: the server's IPv4.
  - `MIERU_DOMAIN`: the dashboard's host name.
  - `DEPLOY_KNOWN_HOSTS`: the server's host key, so CI never trusts a key on first use. Take it with `ssh-keyscan -t ed25519 <ip>`, and check that its fingerprint (`ssh-keygen -lf <(ssh-keyscan -t ed25519 <ip>)`) matches the one the server prints: `ssh deploy@<ip> ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.

```bash
gh variable set DEPLOY_HOST --env production --body <ip>
gh variable set MIERU_DOMAIN --env production --body <domain>
gh variable set DEPLOY_KNOWN_HOSTS --env production --body "$(ssh-keyscan -t ed25519 <ip> 2>/dev/null)"
```

From then on, CI's `deploy` job runs after `publish` on every push to `main`. It ends by checking from outside that `https://<domain>/api/v1/version` and `https://in.<domain>/version` report the commit.

Once T04's checks of the key are done (criteria 4 and 8), delete the local private key with `rm ~/.ssh/mieru_ci_deploy`. The secret is the only copy CI needs, and a new key is cheap.

## 10. Roll back

A rollback is a deploy of an earlier commit on `main`; the checkout, `compose.prod.yaml`, the Caddyfile and the image all go back together.

- From GitHub: Actions -> Deploy -> Run workflow, branch `main`, with the commit's full sha. `git log --first-parent --format='%H %s' origin/main` lists the candidates.
- By hand: `ssh deploy@<ip> /srv/mieru/ops/deploy.sh <sha>`.

Migrations follow expand, migrate, contract, so the previous image still works with the newer schema. A commit from before T04 has no `ops/deploy.sh`. The CI key refuses it, because it would lock CI out, so roll back to it by hand only. The server then has no `deploy.sh` either, so deploy the next commit from a local checkout at T04 or later, which pipes its copy of the script:

```bash
ssh deploy@<ip> 'bash -s -- <newer sha>' < ops/deploy.sh
```

A failed deploy stops where it failed: the services it hadn't reached keep the old version. Read its output in the Actions log, then either fix forward with a new commit or roll back.

## 11. Changes that deploy.sh doesn't apply

`deploy.sh` restarts only the app services. When a commit changes the `caddy`, `postgres`, `redis` or `clickhouse` service in `compose.prod.yaml` (an image version, a volume, a limit), apply it by hand after the deploy, when a short outage of that service is fine:

```bash
cd /srv/mieru && docker compose -f compose.prod.yaml up -d --wait <service>
```
