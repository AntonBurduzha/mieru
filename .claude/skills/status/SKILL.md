---
name: status
description: Where the project stands - the roadmap's Now block, tasks in progress, the next task and its mode, git state, open PRs and the fast checks. Changes nothing.
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash(git status *), Bash(git branch *), Bash(git log *), Bash(gh pr list *), Bash(docker compose ps *), Bash(uv run --directory apps/backend *)
---

## Live state

- Branch: !`git branch --show-current`
- Uncommitted changes: !`git status --short`
- Last commits: !`git log --oneline -5`

## Do

1. Read the "Now" block and the task tables in `docs/roadmap.md`. List any task in status `spec`, `build` or `test`.
2. Next task: the Now block's "Next". Check that its dependencies are `done`. If they aren't, name the first `todo` task whose dependencies are all done.
3. Open PRs: `gh pr list --state open --limit 5`. Skip quietly if `gh` isn't logged in.
4. Services: `docker compose ps --format '{{.Service}}: {{.State}}'`.
5. Fast checks, from the repo root:
   - `uv run --directory apps/backend ruff check .`
   - `uv run --directory apps/backend pytest -m "not integration and not e2e" -q`
6. In `docs/features.json`, count the MVP features with `passes: true` against the MVP total.

Report in at most 15 lines:
- where things stand;
- what's in progress and at which gate;
- what to run next (`/task <ID>`, with its mode);
- anything red (a failing check, services down, a stale branch).

Change no files.
