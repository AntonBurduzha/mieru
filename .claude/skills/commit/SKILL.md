---
name: commit
description: Commit the developer's reviewed changes as small conventional commits, with messages written from the diff. Never pushes.
argument-hint: "[one] [a hint about intent or scope]"
disable-model-invocation: true
---

# /commit

Arguments: `$ARGUMENTS`

The developer has reviewed the changes in their IDE and wants them committed. Running `/commit` is the approval: don't ask again unless a check below stops you.

## 1. Check the state

1. Run `git rev-parse --abbrev-ref HEAD`, `git status --porcelain` and `git log --oneline -1`.
2. Stop and say why, without committing, if any of these hold:
   - The branch is `main`. Never commit there; tell the developer to create a branch.
   - A merge, rebase or cherry-pick is in progress.
   - There is nothing to commit.
   - An untracked or modified file must never be committed: anything named `.env` or `.env.*` other than `.env.example`, anything under `docs/learn/`, `CLAUDE.local.md`, `.claude/settings.local.json`, a key or certificate, a database dump, or a large binary. Name it and ask what to do. Never read a secrets file to check it.
3. Decide what goes in:
   - If anything is staged, commit exactly the staged set as one commit. The developer chose it, so don't add or restage files.
   - Otherwise, commit every change: modified, deleted and untracked files.

## 2. Understand the changes

1. Start with `git diff --stat HEAD` and the untracked files. Then read the diff file by file (`git diff HEAD -- <path>`, and `Read` for untracked files) until you know what each change does and why.
2. For context, read the task spec when the branch is a task branch (`t<nn>-<slug>` → `docs/tasks/T<NN>-*.md`), and read `git log --format=%s -20` for the repo's message style.
3. The developer reads diffs in their IDE. Never print a diff, a patch excerpt or before/after code in the reply.

## 3. Group the changes into commits

- One logical change per commit. Tests go with the code they cover.
- Every commit must pass the checks on its own (`docs/conventions.md` section 8). Don't split a change from the config or test it needs.
- Task status edits (the spec's frontmatter or Review section, and `docs/roadmap.md`) get their own `docs(tasks): <ID> ...` commit, placed last.
- With `one` in the arguments, make a single commit.
- When in doubt, make fewer commits. Two to four is typical for a reviewed batch.

## 4. Write the messages

Write conventional commits, as in `docs/conventions.md` section 8 and the recent log:

- **Format:** `type(scope): subject`.
  - Types: `feat`, `fix`, `test`, `refactor`, `perf`, `docs`, `chore`, `ci`, `build`.
  - Scopes seen so far: `backend`, `ingest`, `api`, `writer`, `web`, `tracker`, `tasks`, `claude`. Leave the scope out when the change spans the repo.
- **Subject:**
  - Lowercase, no trailing period, at most 72 characters.
  - Say what the change achieves ("settings validation errors never echo their input"), not which files moved.
- **Body:** add one only when the reason isn't obvious from the subject. Use plain sentences that say why, wrapped at 72 characters.
- **Hint:** if the arguments give one, follow it for intent and scope.
- **No AI attribution:** no `Co-Authored-By` trailer and no "Generated with" line. The developer is the only author.

## 5. Commit

For each group, in order:

1. Stage it: `git add -A -- <paths>`. Skip this when committing a set the developer staged.
2. Write the message to `.git/COMMIT_MSG` and run `git commit -F .git/COMMIT_MSG`. `.claude/hooks/protect.sh` reads the whole command, so a message passed inline that mentions `.env` or a destructive command gets blocked.
3. pre-commit runs ruff and mypy.
   - **ruff fixed files and failed the hook:** stage the same paths again and retry once.
   - **Any other failure:** stop. Report the hook's output and what is left uncommitted. Don't fix the code yourself unless the developer asks.

Never use `--no-verify`. Never amend or rewrite existing commits unless asked. Never push.

## 6. Report

End with a short block:

- The new commits, from `git log --oneline <the commit you started from>..HEAD`.
- Anything left uncommitted, and why.
- The next step. On a task branch, that's the push line: `! git push -u origin <branch>`, or the `/task` step that comes next.
