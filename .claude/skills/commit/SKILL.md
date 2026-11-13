---
name: commit
description: Commit reviewed changes as one conventional commit, with a message written from the diff, and write the branch's PR description. Never pushes. /task follows it for a task's single commit.
argument-hint: "[a hint about intent or scope]"
disable-model-invocation: true
---

# /commit

Arguments: `$ARGUMENTS`

Two callers use this:
- **The developer,** for work outside a task. Running `/commit` is their approval: don't ask again unless a check below stops you.
- **`/task` run 2,** for a task's single commit.

## 1. Check the state

1. Run `git rev-parse --abbrev-ref HEAD`, `git status --porcelain` and `git log --oneline -1`.
2. Stop and say why, without committing, if:
   - the branch is `main` (tell the developer to create a branch);
   - a merge, rebase or cherry-pick is in progress;
   - there is nothing to commit;
   - the tree holds a file that is never committed. Name the file and ask what to do. Never read a secrets file to check it. These files are:
     - `.env` and `.env.*` (but `.env.example` is fine);
     - anything in `docs/learn/`;
     - `CLAUDE.local.md` and `.claude/settings.local.json`;
     - keys, certificates, database dumps and large binaries.
3. **What goes in:** if anything is staged, exactly the staged set, as one commit. Otherwise, every change: modified, deleted and untracked.

## 2. Understand the changes

1. Read `git diff --stat HEAD` and the untracked files.
2. Read the diff file by file (`git diff HEAD -- <path>`, or `Read` for new files) until you know what each change does and why.
3. On a task branch (`t<nn>-<slug>`), also read the spec `docs/tasks/T<NN>-*.md`.
4. For the message style, read `git log --format=%s -20`.

Never print a diff or a code excerpt in your reply. The developer reads diffs in Cursor.

## 3. One commit

Every run makes exactly one commit, never a series, so a branch rolls back in one step.

- **On a task branch:** one commit of everything since the spec commit, as `/task` says.
- **Off a task branch:** the branch holds one commit until it's pushed. Check `git log --oneline main..HEAD`:
  - empty: make the commit;
  - one commit that isn't pushed (`git branch -r --contains HEAD` prints nothing): amend it, with the message rewritten to cover everything;
  - pushed, or more than one commit: make a new commit, and never rewrite pushed history.

## 4. Write the message

Use conventional commits (`docs/conventions.md` section 8).

- **Format:** `type(scope): subject`.
  - Types: `feat`, `fix`, `test`, `refactor`, `perf`, `docs`, `chore`, `ci`, `build`.
  - The scope is the area, as in the recent log (`backend`, `ingest`, `ops`, `ci`, `tasks`, `claude`, and so on). Leave it out when the change spans the repo.
- **Subject:** lowercase, no period, at most 72 characters. Say what the change achieves, not which files moved.
- **Body:** only when the reason isn't obvious. Plain sentences, wrapped at 72 characters.
- **A commit that spans areas:** the subject names the main change, or the two or three parts separated by commas. The body has one short bullet per area: what changed and why.
- **A task's single commit:** `<type>(<scope>): <what the task achieves>`. Take the type from the main change. The body briefly lists the parts: code, tests, docs, status.
- **A hint** in the arguments sets the intent and the scope.
- **No AI attribution:** no `Co-Authored-By` trailer, no "Generated with" line.

## 5. Commit

1. **Stage everything:** `git add -A -- <paths>`. Skip this for a set the developer already staged.
2. **Commit from a file:** write the message to `.git/COMMIT_MSG`, then run `git commit -F .git/COMMIT_MSG`, or `git commit --amend -F .git/COMMIT_MSG` when section 3 says to amend. `.claude/hooks/protect.sh` blocks inline messages that mention `.env` or a destructive command.
3. **If the pre-commit hook fails:**
   - Ruff fixed files: stage those paths again and retry once.
   - Any other failure: stop. Report the hook's output and what's left uncommitted, and don't fix the code unless asked.

Never use `--no-verify` and never push. Amend only as section 3 says, and never rewrite a pushed commit.

## 6. Write the PR description

Write `.git/pr-<ID>.md`, or `.git/pr-<branch>.md` off a task branch.
- It lives inside `.git`, so it's never committed.
- Rewrite it on every run, so it covers all of `git log main..HEAD`.
- Follow `.github/pull_request_template.md`.

What goes in each section:

- **Task:** the spec's path, the mode, and the features from its frontmatter (`none` when there are none). Off a task branch: `none`.
- **What and why:** short bullets grouped by area, for a reviewer, drawn from the spec's goal and the commits.
- **Proof of done:**
  - The last verdict in the spec's Review section. Never write DONE unless the verifier gave it.
  - The proof commands and their key output lines, from Review or from this session's runs. Never invent output.
  - Any criterion still open, and where it gets checked.
- **Risk:** tick a box only when the diff really does it, and add a few words on why.
- **Checklist:**
  - "Tested it myself": only when the spec records the developer's confirmation.
  - "Tests": only when the branch adds tests and none were deleted, skipped or weakened.
  - `features.json`: when nothing was flipped, or every flip's check ran green.
  - "I read the full diff": leave it for the developer.

## 7. Report

Skip this when `/task` called you; `/task` ends the run itself.

Otherwise, end with:
- the commit, and whether it's new or amended (`git log --oneline main..HEAD`);
- anything left uncommitted, and why;
- the PR description file;
- the next step:
  - no PR yet: `! git push -u origin <branch> && gh pr create --draft --title "<title>" --body-file .git/pr-<ID>.md`
  - a PR already open (`gh pr view <branch> --json url`): `! git push && gh pr edit --body-file .git/pr-<ID>.md`
