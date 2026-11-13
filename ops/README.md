# Operating the server

How a fresh server becomes the running production stack. The design is in [architecture.md](../docs/architecture.md) section 11. Automated deploys replace step 6 in T04.

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

## 6. Deploy by hand

```bash
cd /srv/mieru
docker manifest inspect ghcr.io/antonburduzha/mieru-app:$(git rev-parse HEAD) > /dev/null   # CI has published this commit
docker compose -f compose.prod.yaml up -d --wait
timeout 120 docker compose -f compose.prod.yaml logs -f caddy | grep -m1 'certificate obtained successfully'   # the first issuance can take a minute
curl -s https://<domain>/api/v1/version          # the sha from step 5
```

To update until T04: `git pull --ff-only`, set `MIERU_TAG` in `.env` to the new `git rev-parse HEAD`, then `up -d --wait` again.

## 7. After a reboot

Docker starts on boot and every service has `restart: unless-stopped`, so the stack comes back by itself. Check it with `docker compose -f compose.prod.yaml ps`: every service is `healthy`.
