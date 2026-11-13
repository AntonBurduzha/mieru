---
id: T04
title: Automated deploy
mode: agent
status: test
features: []
depends: [T03]
branch: t04-automated-deploy
---

# T04 Automated deploy

## Goal
A merge to `main` deploys itself: once CI has published the image, a job SSHes to the server with a key that can run only `ops/deploy.sh`. The script moves the checkout to the merged commit and restarts `ingest-1`, then `ingest-2`, then `api`, waiting for each one to be ready, so ingest never stops answering. A rollback deploys an earlier commit with the same script, and it has been rehearsed on the real server.

## Context to read
- `docs/architecture.md`:
  - section 11: "Deploy pipeline", "How Caddy is set up" (health checks and retries), "Network and access";
  - section 13 rows 20, 24, 27, 32 and 33.
- `docs/conventions.md`: section 1 (layout), section 7.5 (CI secrets live in the `production` environment), section 8 (git).
- `ops/README.md` steps 5-6, and `ops/bootstrap.sh`: follow its style (`set -euo pipefail`, the body inside `main`, called on the last line, plus `log` and `die`).
- `.github/workflows/ci.yml`: the `publish` job, the pinned actions, `persist-credentials: false`.
- `ops/caddy/Caddyfile:102-112`: `fail_duration 5s` and `health_interval 2s` set how long the pause between replicas must be.
- Docs:
  - [`authorized_keys` options: `command=`, `restrict`](https://man.openbsd.org/sshd#AUTHORIZED_KEYS_FILE_FORMAT)
  - [Reusable workflows](https://docs.github.com/en/actions/sharing-automations/reusing-workflows)
  - [Deployment environments](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment)
  - [`docker compose up`](https://docs.docker.com/reference/cli/docker/compose/up/)

## Scope
- **In:**
  - `ops/deploy.sh`, run on the server as `deploy`.
  - A CI deploy key that `authorized_keys` limits to that script.
  - `deploy.yml`, called by CI after `publish` and runnable by hand for a rollback.
  - The GitHub `production` environment.
  - The rollback drill on the real server.
  - The runbook and the docs.
- **Out:**
  - Migrations. `deploy.sh` has a `migrate` step that only logs "no migrations yet". T05 adds Alembic to it, T06 the ClickHouse runner.
  - Restarting `caddy`, `postgres`, `redis` or `clickhouse`. A change to their service in `compose.prod.yaml` is applied by hand (runbook). Caddyfile changes are applied by `caddy reload`.
  - The writer, worker and web files: their own tasks add them to `deploy.sh`.
  - Counting events lost during a deploy: T15.
  - Automatic rollback when a deploy fails.
  - Pruning old images: T25.
  - Zero downtime for `api`: it has one replica, so the dashboard is gone for a few seconds while it restarts. Ingest is what must not stop.
- **Files allowed to change:**
  - `ops/deploy.sh` (new, executable)
  - `.github/workflows/deploy.yml` (new)
  - `.github/workflows/ci.yml`: a `deploy` job only
  - `ops/README.md`: step 6, plus new steps for the CI key, the environment and rollback
  - `docs/architecture.md`: section 11 "Deploy pipeline", new rows in section 13
  - `docs/conventions.md`: the section 1 layout

## Interfaces
**`ops/deploy.sh <sha>`**
- Runs as `deploy` in `/srv/mieru`.
  - Under the forced command, the argument comes from `$SSH_ORIGINAL_COMMAND`.
  - Run by hand, it comes from `$1`.
- It takes exactly one token matching `^[0-9a-f]{40}$`. Anything else exits 2 with a usage line and changes nothing.
- **Stage 1: check and move the checkout.** Every failure here leaves the checkout and the containers as they were.
  1. Take a lock with `flock -n` on `.git/mieru-deploy.lock`. If it's busy, exit 1 with "another deploy is running".
  2. Refuse a checkout with local changes (`git status --porcelain`).
  3. Run `git fetch --quiet origin main`. Refuse a sha that `git merge-base --is-ancestor` doesn't find on `origin/main`.
  4. Under the forced command only, refuse a sha whose tree has no `ops/deploy.sh`. Otherwise the CI key would stop working after that rollback.
  5. Run `MIERU_TAG=<sha> docker compose -f compose.prod.yaml pull api`. One image serves all three app services, so a commit CI hasn't published fails here.
  6. Run `git checkout --quiet --detach <sha>`.
  7. If the target has `ops/deploy.sh`, `exec` it with `MIERU_DEPLOY_STAGE=apply`, so this deploy already uses the target's own steps. The lock's file descriptor stays open across `exec`. A target without the file (only before T04) continues in the running copy.
- **Stage 2: apply.**
  1. `migrate`: today it only logs.
  2. Rewrite the `MIERU_TAG=` line of `.env` to the sha. Write a temporary file in the same directory under `umask 077`, then `mv` it over. Every other line stays. Exit 1 if the line is missing.
  3. For `ingest-1`, `ingest-2` and `api`, in that order:
     - `docker compose -f compose.prod.yaml up -d --no-deps --force-recreate --wait --wait-timeout 60 <svc>`;
     - check that the container's image ends in `:<sha>`;
     - poll its `/health/ready` from inside the container (`docker compose exec -T`, Python's `urllib`) for up to 30 s.

     Any failure stops the deploy (exit 1), and the remaining services keep the old version. After `ingest-1`, wait `SETTLE_SECONDS=10`, more than `fail_duration` plus one `health_interval`, so Caddy has it back in the rotation before `ingest-2` stops.
  4. Run `docker compose -f compose.prod.yaml exec -T caddy caddy reload --config /etc/caddy/Caddyfile`. Caddy skips the reload when the config is unchanged.
  5. Log `deployed <sha>`. Nothing it prints contains a secret or a line of `.env`.

**The CI key**
- An ed25519 key without a passphrase, comment `mieru-ci-deploy`. One line in `~deploy/.ssh/authorized_keys`: `restrict,command="/srv/mieru/ops/deploy.sh" ssh-ed25519 AAAA... mieru-ci-deploy`. `restrict` turns off forwarding and the pty.
- The developer's own key stays unrestricted.

**GitHub environment `production`** (deployment branches: `main` only)
- Secret `DEPLOY_SSH_KEY`: the private key.
- Variables:
  - `DEPLOY_HOST`: the IPv4 address;
  - `DEPLOY_KNOWN_HOSTS`: the server's ed25519 host key line, checked against the server's fingerprint;
  - `MIERU_DOMAIN`.

**`.github/workflows/deploy.yml`**
- Triggers:
  - `workflow_call` with input `sha` (string, required);
  - `workflow_dispatch` with the same input, for a rollback or a redeploy.
- Top-level `permissions: {}`.
- One job, `deploy`:
  - `environment: production`;
  - `concurrency: { group: deploy-production, cancel-in-progress: false }`;
  - `timeout-minutes: 15`;
  - no checkout.
- Steps:
  1. Check that `$SHA` is 40 hex characters. Inputs reach `run` only through `env:`, never `${{ }}` inside the script.
  2. Write the key (mode 600) and known_hosts into `$RUNNER_TEMP`.
  3. `ssh -i <key> -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=<file> deploy@$DEPLOY_HOST "$SHA"`.
  4. From outside, with retries: `https://$MIERU_DOMAIN/api/v1/version` and `https://in.$MIERU_DOMAIN/version` both return `{"version":"<sha>"}`.

**`ci.yml`, job `deploy`:** `needs: publish`, the same `if` as `publish`, `uses: ./.github/workflows/deploy.yml`, `with: { sha: ${{ github.sha }} }`, `permissions: {}`, `secrets: { DEPLOY_SSH_KEY: ${{ secrets.DEPLOY_SSH_KEY }} }`. A called workflow gets only the secrets its caller passes, environment secrets included, so the key is passed by name (not `secrets: inherit`, which would pass every secret). `deploy.yml` declares it under `workflow_call.secrets`, and the called job's `environment:` supplies the value. Corrected after the merge: see "Fix after the merge" in Review.

## Acceptance criteria
Run from the repo root on the Mac, in bash, with `MIERU_HOST`, `MIERU_DOMAIN` and `S` set as in T03, and the CI private key at `~/.ssh/mieru_ci_deploy`. Before the merge, the server has no `ops/deploy.sh`, so criteria 3, 5 and 6 pipe this branch's copy: `$S "bash -s -- <sha>" < ops/deploy.sh`.

1. The deploy script passes shellcheck. (auto: `docker run --rm -v "$PWD:/mnt:ro" -w /mnt koalaman/shellcheck:v0.11.0 ops/deploy.sh`)
2. Both workflows pass actionlint, which runs shellcheck on every `run:` block. (auto: `docker run --rm -v "$PWD:/repo:ro" -w /repo rhysd/actionlint:1.7.7 -color`)
3. Bad input changes nothing: a malformed argument, and a well-formed sha that isn't on `main`, are refused. (auto: `h=$($S git -C /srv/mieru rev-parse HEAD) && ! $S "bash -s -- 'x;id'" < ops/deploy.sh && ! $S "bash -s -- ffffffffffffffffffffffffffffffffffffffff" < ops/deploy.sh && test "$($S git -C /srv/mieru rev-parse HEAD)" = "$h" && $S 'cd /srv/mieru && test -z "$(git status --porcelain)"'`)
4. The CI key can't run any other command, and its line is limited. (auto: `! ssh -i ~/.ssh/mieru_ci_deploy -o IdentitiesOnly=yes -o BatchMode=yes deploy@$MIERU_HOST 'touch /tmp/mieru-pwned' && $S 'test ! -e /tmp/mieru-pwned && test "$(grep -c "^restrict,command=\"/srv/mieru/ops/deploy.sh\" ssh-ed25519 .* mieru-ci-deploy$" ~/.ssh/authorized_keys)" = 1'`)
5. A redeploy of the current commit recreates exactly `ingest-1`, `ingest-2` and `api`, and every request to ingest during it gets a `200`. A second deploy started meanwhile is refused. (auto, as one block:)
   ```bash
   ps_ids() { $S 'cd /srv/mieru && docker compose -f compose.prod.yaml ps --format "{{.Service}} {{.ID}}"' | sort; }
   cur=$($S git -C /srv/mieru rev-parse HEAD) && b=$(ps_ids) && codes=$(mktemp)
   ( while :; do curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 "https://in.$MIERU_DOMAIN/health"; sleep 0.2; done ) >"$codes" & loop=$!
   $S "bash -s -- $cur" < ops/deploy.sh & dep=$!
   sleep 3; ! $S "bash -s -- $cur" < ops/deploy.sh; second=$?
   wait $dep; first=$?; kill $loop
   a=$(ps_ids) && test $first = 0 && test $second = 0 \
     && test "$(grep -c . "$codes")" -ge 50 && ! grep -qv '^200$' "$codes" \
     && test "$(diff <(echo "$b") <(echo "$a") | awk '/^>/ {print $2}' | sort | tr '\n' ' ')" = "api ingest-1 ingest-2 "
   ```
6. Rollback drill: deploying the previous `main` commit and then the current one again works. After each deploy, both hosts report that commit, and T03 criteria 8 and 9 still pass at the end. (auto: `cur=$($S git -C /srv/mieru rev-parse HEAD) && prev=$($S git -C /srv/mieru rev-parse 'origin/main^1') && docker manifest inspect ghcr.io/antonburduzha/mieru-app:$prev >/dev/null && v() { curl -s "https://$MIERU_DOMAIN/api/v1/version"; curl -s "https://in.$MIERU_DOMAIN/version"; } && $S "bash -s -- $prev" < ops/deploy.sh && test "$(v)" = "{\"version\":\"$prev\"}{\"version\":\"$prev\"}" && $S "bash -s -- $cur" < ops/deploy.sh && test "$(v)" = "{\"version\":\"$cur\"}{\"version\":\"$cur\"}"`, then T03 criteria 8 and 9)
7. The `production` environment allows only `main`, and holds the secret and the three variables. (auto: `gh api repos/AntonBurduzha/mieru/environments/production/deployment-branch-policies --jq '[.branch_policies[].name] == ["main"]' | grep -qx true && gh secret list --env production --json name --jq '.[].name' | grep -qx DEPLOY_SSH_KEY && test "$(gh variable list --env production --json name --jq '[.[].name] | sort | join(" ")')" = "DEPLOY_HOST DEPLOY_KNOWN_HOSTS MIERU_DOMAIN"`)
8. After the merge, the merge deploys itself through the CI key: the CI run for the merge commit ends green, both hosts report that commit, and the CI key's text is checked by the script (exit 2 for anything but a sha).
   - One time only, for the T04 merge: the server's checkout is still at a commit without `ops/deploy.sh`. So the first `deploy` job fails with "No such file or directory" (exit 127), and nothing changes.
   - Once `publish` is green, put the merge commit on the server with this copy: `git switch main && git pull --ff-only && m=$(git rev-parse HEAD) && $S "bash -s -- $m" < ops/deploy.sh`.
   - Then rerun the failed job: `gh run rerun "$(gh run list --workflow ci.yml --commit "$m" --json databaseId --jq '.[0].databaseId')" --failed`. That redeploy goes through the forced command.

   (auto, after that: `m=$(git rev-parse origin/main) && gh run list --workflow ci.yml --commit "$m" --json conclusion --jq '.[0].conclusion' | grep -qx success && curl -s "https://$MIERU_DOMAIN/api/v1/version" | grep -qF "$m" && curl -s "https://in.$MIERU_DOMAIN/version" | grep -qF "$m" && { ssh -i ~/.ssh/mieru_ci_deploy -o IdentitiesOnly=yes -o BatchMode=yes deploy@$MIERU_HOST 'x;id'; test $? = 2; }`)

## Tests to write
None in Python: this task changes no app code. The proof is criteria 1-8, run with the real tools against the real server.

## Proof of done
- Agents run criteria 1 and 2, and the scope check below.
- Agents can't reach the server or the GitHub settings, so the developer runs criteria 3-7 before the merge and pastes the output to `/task`.
- Criterion 8 runs at `/task T04` after the merge. If it fails, the fix is a new PR.

```bash
git status --porcelain              # before the commit: only the allowed files, this spec and docs/roadmap.md
git diff --name-only main...HEAD    # after the commit: the same list
```

Expected: every command exits 0. Neither the server's address nor the domain appears in a tracked file.

## Test it yourself
1. Follow the new runbook steps: make the CI key, install its line on the server, create the `production` environment with its secret and variables.
2. Run criterion 5 and watch the output: `ingest-1`, a 10 s pause, `ingest-2`, then `api`, each ready before the next.
3. During criterion 5, open `https://<domain>`: at most a few seconds of 502 while `api` restarts.
4. Run the rollback drill (criterion 6). Between the two deploys, `https://<domain>/api/v1/version` shows the previous commit.
5. After the merge, follow criterion 8:
   - the first `deploy` job fails once (exit 127), as expected;
   - run the piped deploy of the merge commit;
   - rerun the failed job;
   - in Actions, the run turns green.
   - Then delete `~/.ssh/mieru_ci_deploy`.

## Where to debug
Shell and CI, not Python: no breakpoints. To trace the script, run it with `bash -x`. On the server: `ssh deploy@$MIERU_HOST 'bash -x /srv/mieru/ops/deploy.sh <sha>'`. Before the merge, use `ssh deploy@$MIERU_HOST 'bash -x -s -- <sha>' < ops/deploy.sh`. Line numbers are as of the build, uncommitted.

1. **Which commits are refused**: `ops/deploy.sh:66` (`sync_checkout`), plus line 69 for the forced command only.
   - Why here: `merge-base --is-ancestor` decides whether a sha counts as "on main" after the fetch on line 65. A stale `origin/main` refuses a commit that was just merged, and the error says "is not a commit on main".
   - Trigger: criterion 3 (the all-`f` sha), or a deploy right after a merge.
   - Look at: `git -C /srv/mieru log -1 --format=%H origin/main` equals the merge commit. `git -C /srv/mieru config --get remote.origin.fetch` is `+refs/heads/*:refs/remotes/origin/*`.
2. **What the CI key's text becomes**: `ops/deploy.sh:45` (`parse_sha`), with the check on line 51.
   - Why here: under the forced command, the sha is the whole of `SSH_ORIGINAL_COMMAND`, not `$1`. Anything but exactly 40 lowercase hex characters (a second word, a second line) exits 2 with the usage line. A CI run that fails with "usage:" sent something else: an empty input, or a short sha.
   - Trigger: after the merge, `ssh -i ~/.ssh/mieru_ci_deploy deploy@$MIERU_HOST 'x;id'` (criterion 8). Before the merge, the server has no script, so the same command gives exit 127.
   - Look at: in `bash -x` output, `arg` is the 40-hex sha.
3. **Handing over to the target's copy**: `ops/deploy.sh:81` (`sync_checkout`).
   - Why here: after the checkout, stage 2 runs from the target commit's `ops/deploy.sh`, with `MIERU_DEPLOY_STAGE=apply`. A bug that shows only in the new copy appears after this line. A deploy that runs stage 1 twice means the variable didn't reach the new process.
   - Trigger: any deploy of a commit that has `ops/deploy.sh` (every deploy after the merge).
   - Look at: the log shows "Moving the checkout" once, then "Migrations: none yet". The lock file `/srv/mieru/.git/mieru-deploy.lock` is held until the end: `fuser /srv/mieru/.git/mieru-deploy.lock` during a deploy.
4. **The rolling restart**: `ops/deploy.sh:122` (`restart`), the readiness poll on line 109, and the settle pause on line 141.
   - Why here: `up --force-recreate --wait` replaces one container. Lines 124-125 check its image tag. `wait_ready` polls `/health/ready` inside it for 30 s. A failure stops the deploy with the service's name. A non-200 during criterion 5 means the pause on line 141 is too short for Caddy's `fail_duration` and `health_interval`.
   - Trigger: criterion 5.
   - Look at: `docker compose -f compose.prod.yaml ps` shows the new container `healthy`. `docker compose -f compose.prod.yaml logs --since 2m ingest-1` shows the startup. The curl loop's file holds only `200`.
5. **The CI side**: `.github/workflows/deploy.yml:57` (the ssh call), and the outside check on lines 65-67.
   - Why here:
     - "Host key verification failed" means `DEPLOY_KNOWN_HOSTS` is wrong or missing.
     - "Permission denied (publickey)" means the secret doesn't match the `authorized_keys` line.
     - A version mismatch on line 67 means a replica still runs the old image.
   - Trigger: the CI run of a merge to `main`, or Actions -> Deploy -> Run workflow.
   - Look at: the job log of "Deploy over SSH" (the script's `==>` lines) and of "Check from outside".

## Notes
Decisions made in the spec:
- **The CI key is limited by `command=` and `restrict` in `authorized_keys`, and the client's text is only a sha.** A stolen key can redeploy a commit on `main` and nothing else. The alternative is a second Unix user with only Docker rights. The `docker` group already equals root (row 32), so that would add a login without a boundary.
- **`deploy.yml` is a reusable workflow called from `ci.yml` after `publish`.** CI on `main` already runs one at a time (its concurrency group never cancels on push), so deploys happen in merge order, and one run shows test, publish and deploy. The alternative is a `workflow_run` trigger. It starts a separate run, can't easily tell which commit was published, and only reads the workflow file from the default branch.
- **The script `exec`s the target's copy of itself after the checkout,** so a commit that changes the deploy steps (a new service, the first migration) is deployed by those steps. The alternative is to run the old copy and have the new steps apply from the next deploy, which would make T05's first migration miss its own deploy.
- **The checkout is detached at the deployed sha instead of following `main`.** A rollback is then just another sha, and `git rev-parse HEAD` always names what runs. The cost: the T03 runbook's `git pull --ff-only` no longer works on the server, so the runbook switches to `ops/deploy.sh <sha>`.
- **A failed deploy stops and leaves the rest on the old version. It doesn't roll back by itself.** One run of the Deploy workflow with the previous sha does the rollback, and stopping keeps the evidence. The alternative is an automatic rollback, but a second deploy could fail the same way (a database being down, say).
- **`SETTLE_SECONDS=10` between the ingest replicas,** derived from the Caddyfile's `fail_duration 5s` and `health_interval 2s`. The alternative is to poll Caddy's admin API for upstream health, which would make the script depend on the admin endpoint.
- **The proof splits around the merge:** criteria 3-6 rehearse everything on the server before the merge, through the developer's key. Only criterion 8 (the merge deploys itself) has to wait. A CI rollback via `workflow_dispatch` first works after the next merge: a rollback by the CI key to a commit before T04 is refused on purpose (stage 1, step 4).
- **No registry login on the server:** the image on GHCR is public (T03).
- **The T04 merge's own first CI deploy fails once (exit 127),** because the server's checkout has no `ops/deploy.sh` yet. A piped deploy of the merge commit fixes that, and a rerun of the failed job then proves the CI path (criterion 8). The alternative is a temporary required reviewer on `production`, so the job waits instead of failing. That's two more settings changes for a one-time red run.

Build notes (2026-11-13):
- No test-writer step. The spec has no Python tests, and the test-writer only writes under the test folders.
- Following the developer's standing preference, everything stays uncommitted until the developer's `/commit` after gate 2. The verifier and the reviewer read the working tree against `main`.
- Checked locally:
  - shellcheck and actionlint: exit 0;
  - argument parsing: `'x;id'`, `abc`, no argument, and, through `SSH_ORIGINAL_COMMAND`, two words, empty and `$(id)` all exit 2 with the usage line;
  - the backend checks: green, with no Python changes.
- A local dry run of stage 1 against a temporary clone was declined. Criteria 3, 5 and 6 on the server are the first real run of the git and Docker steps.

## Review
**Verification, 2026-11-13: NEED_HUMAN** (second pass, after the review fixes). Agents can't reach the server or the GitHub settings.

| Check | Result |
|---|---|
| 1. shellcheck | exit 0 |
| 2. actionlint | exit 0 |
| Argument parsing (local) | exit 2 for `x;id`, no argument, two arguments, uppercase hex. Through `SSH_ORIGINAL_COMMAND`, also exit 2 for two words, two lines, empty and `$(id)`. Checked under bash 3.2 and bash 5.2 |
| Scope | only the allowed files, this spec and `docs/roadmap.md` (`git status --porcelain`; the work is uncommitted) |
| Server values in the repo | none |
| Debug map | every `file:line` checked against the current files |
| 3-7 | NEED_HUMAN: the developer runs them at gate 2 |
| 8 | NEED_HUMAN: runs after the merge |

The first pass found one deviation: a two-line `SSH_ORIGINAL_COMMAND` got past parsing. Fixed: `parse_sha` now matches the whole text against `^[0-9a-f]{40}$`.

**Reviewer, 2026-11-13:** 2 must-fix, 4 should-fix, 6 nits. All fixes are in the working tree, uncommitted.

1. Must-fix: the T04 merge's first CI deploy fails with 127, because the server has no `ops/deploy.sh` yet. Fixed: criterion 8 now has the one-time steps (a piped deploy of the merge commit, then rerun the failed job), and there's a Note on it.
2. Must-fix: criterion 3 used this branch's `HEAD`. While the work is uncommitted that equals `main`, so it would have run a real redeploy and failed the `!`. Fixed: it uses the constant `ffff…ffff`.
3. Should-fix (optional): no timeout on `git fetch` or `compose pull`. A stalled GitHub or GHCR could hold the lock after CI disconnects. Suggested fix: `timeout 120 git fetch` and `timeout 600 docker compose ... pull`.
4. Should-fix: the debug map was stale after the `parse_sha` rewrite. Fixed.
5. Should-fix: criterion 4 can't reach `parse_sha` before the merge, and the runbook deleted the key too early. Fixed: criterion 8 checks `'x;id'` gives exit 2 through the CI key, and the runbook deletes the key after criteria 4 and 8.
6. Should-fix: the runbook's path back from a pre-T04 rollback didn't work, because the server has no `deploy.sh` at that point. Fixed: the runbook pipes the script from a local checkout.
7. Nit (optional): `chmod 600` the `.env` temp file explicitly instead of relying on `mktemp`'s mode.
8. Nit (optional): close fd 9 for `git fetch` (`9>&-`), so a detached git maintenance process can't keep holding the deploy lock.
9. Nit (optional): `caddy reload` failing exits without a `deploy:` message. Add `|| die "caddy reload failed; the apps run $SHA"`.
10. Nit (optional): the runbook's `authorized_keys` append isn't idempotent. A second paste breaks criterion 4's count of 1.
11. Nit: the proof-of-done scope check was empty while the work is uncommitted. Fixed: `git status --porcelain` first.
12. Nit (optional): edits outside the named sections. `architecture.md` changes the "Network and access" secrets bullet and adds a sentence to row 33; row 33 still says "a deploy is a `git pull`". `ops/README.md` changes its intro.

**Fix after the merge, 2026-11-13:** criterion 8 failed. All three attempts of the `deploy` job in CI run 43480887060 (merge commit `771a99f`) stopped before reaching the server: `Load key ".../deploy_key": error in libcrypto`, then `Permission denied (publickey)`, exit 255. The job log shows the `production` variables set but `DEPLOY_SSH_KEY` empty. The spec assumed the called job's `environment:` was enough. GitHub's docs say otherwise: "The caller workflow must still pass the secret… You can pass a secret by name even if it only exists in the environment." Fix on branch `t04-deploy-secret`: `ci.yml` passes `DEPLOY_SSH_KEY` by name, `deploy.yml` declares it under `workflow_call.secrets`, and the deploy step fails with a clear message when the key is empty. A rerun of the old run can't pick this up, because it uses the old workflow files, so criterion 8 is proved by the fix's own merge commit.
