---
name: status
description: Where the project stands - tasks in progress and what each waits on, the next task and its mode, git state, open PRs and the fast checks. Changes nothing.
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash(git status *), Bash(git branch *), Bash(git log *), Bash(gh pr list *), Bash(docker compose ps *), Bash(uv run --directory apps/backend *)
---

## Live state

- Branch: !`git branch --show-current`
- Uncommitted changes: !`git status --short`
- Last commits: !`git log --oneline -5`

## Do

1. **Tasks in progress.** In `docs/roadmap.md`, read the Now block (Next, Open questions) and the task tables. For each task in status `spec`, `build` or `test`, say what it waits on and the command to run:
   - `spec`: the developer's approval. `/task <ID>`.
   - `build`, agent mode: an interrupted build. `/task <ID>` resumes it.
   - `build`, human mode: the developer working through the tutorial. `/task <ID>` once its Definition of done passes.
   - `test`: the developer's test. `/task <ID>`, or `/task <ID> <what's wrong>`.
2. **Open PRs:** `gh pr list --state open --limit 5`. An open task PR waits on CI and the merge. Skip this quietly if `gh` isn't logged in.
3. **The next task:** the Now block's Next. Check that its dependencies are `done`. If they aren't, name the first `todo` task whose dependencies are all done.
4. **Services:** `docker compose ps --format '{{.Service}}: {{.State}}'`.
5. **Fast checks,** from the repo root:
   - `uv run --directory apps/backend ruff check .`
   - `uv run --directory apps/backend pytest -m "not integration and not e2e" -q`
6. **Features:** in `docs/features.json`, count the MVP features with `passes: true` against the MVP total.

Report in at most 15 lines:
- where things stand;
- what's in progress, and what it waits on;
- what to run next (`/task <ID>`, with its mode);
- anything red: a failing check, services down, a stale branch.

Change no files.
