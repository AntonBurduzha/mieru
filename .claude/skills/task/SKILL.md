---
name: task
description: Run one roadmap task through spec, build, proof of done, review and finish. Resumes from the status in the task's spec.
argument-hint: <task id, e.g. T07> [--agent|--human]
disable-model-invocation: true
---

# /task

Arguments: `$ARGUMENTS`

The full workflow, written for people, is in `docs/workflow.md`. This file is the procedure.

## 0. Parse and locate

1. Parse `$ARGUMENTS`: the first word is the task id (normalise `t7`, `7` or `T7` to `T07`). `--agent` or `--human` is an optional mode override. Anything else: stop and print the usage line `/task <id> [--agent|--human]`.
2. Find the task's row in `docs/roadmap.md` (title, mode, features, depends). If there is none, stop.
3. Look for its spec: `docs/tasks/<ID>-*.md`. Read its frontmatter if it exists.
4. The mode is the flag if given, otherwise the spec's `mode`, otherwise the roadmap's. A flag that differs from the spec's mode is allowed only while `status: spec`. Later, stop and explain that the mode is fixed once the build started.
5. Dispatch on the spec's status:

| Spec | Do |
|---|---|
| none | Section 1: write the spec, then gate 1 |
| `status: spec` | Gate 1 (section 2) |
| `status: build`, agent mode | Section 3: resume the build |
| `status: build`, human mode | Section 4: write the tutorial, or check the developer's work |
| `status: test` | Gate 2 (section 5) |
| `status: done` | Section 6: after the PR |

Rules for every section:
- Follow `CLAUDE.md` and `docs/conventions.md`. Never weaken a test.
- Never push and never merge.
- No AI attribution in commits or PR descriptions: no `Co-Authored-By` trailer, no "Generated with" line. The developer is the only author.
- Commit only on the task branch. `.claude/hooks/protect.sh` reads the whole command, so a commit message that mentions `.env` or a destructive command gets blocked. Write such a message to `.git/COMMIT_MSG_<ID>` and run `git commit -F` on it.
- Every status change goes to the spec's frontmatter, its row in `docs/roadmap.md` and the roadmap's "Now" block (fields Current, Next, Waiting on, Open questions, Updated) in the same edit. `Updated` is today's date.
- End every run with one short block: what happened, the status now, and exactly what the developer does next.

## 1. Write the spec (gate 1 comes after)

1. Check `depends`. If a dependency isn't `done` in the roadmap, say which one and stop, unless the developer confirms they want to go ahead.
2. Read before writing:
   - the feature entries in `docs/features.json` and their stories in `docs/product.md`;
   - the sections of `docs/architecture.md` that the task touches;
   - `docs/conventions.md`, and `docs/frontend.md` for web work;
   - the current code the task changes, plus `pyproject.toml`, `uv.lock` or `package.json` for the installed versions.

   Every path, name and API in the spec must match the repo as it is.
3. Write `docs/tasks/<ID>-<slug>.md` from `.claude/skills/task/spec-template.md`:
   - The slug is 2-4 words from the title, in kebab case. The branch is `<id lowercase>-<slug>`.
   - Every acceptance criterion carries an `auto:` command or `human:` steps. Commands run from the repo root.
   - The allowed files are specific.
   - Aim for under 150 lines. The spec is public: English, and no links to private files.
4. Set `status: spec` and the mode, and update the roadmap row and the "Now" block (Current: `<ID> spec, waiting for approval`).
5. Stop. Show the goal, the criteria (one line each), the allowed files, and any decision you made that the developer might disagree with. Then: "Edit the spec directly or tell me what to change. When it's right, run `/task <ID>` again."

## 2. Gate 1: approve the spec

1. Ask with AskUserQuestion: "Approve the <ID> spec and start the build (<mode> mode)?" Options: "Approve", "I have changes".
2. If there are changes, apply them to the spec and stop again at gate 1.
3. If approved, prepare the branch:
   - The working tree must be clean except for the spec and `docs/roadmap.md`. If not, list the other changes and stop.
   - `git switch main`, `git pull --ff-only`, then `git switch -c <branch>`.
   - Set `status: build` (spec, roadmap, Now block), then commit the spec and the roadmap: `docs(tasks): <ID> spec`.
4. Continue with section 3 (agent) or section 4 (human).

## 3. Build: agent mode

Track progress in the spec's Notes, so an interrupted run can resume. Use `git log main..HEAD` and `git status` to see what already exists.

1. **Tests first.** Use the `test-writer` subagent with the spec path. Then check:
   - `git status --porcelain` shows only test files;
   - every `auto:` criterion has at least one test;
   - the tests fail because the behaviour is missing, not because of a typo or a broken fixture.

   Fix gaps with the subagent before going on. Don't commit failing tests.
2. **Implement** in this session, inside the allowed files only. If the work needs a file outside the list, stop and ask. Follow the patterns the spec points to.
3. **Check locally:**
   - `uv run --directory apps/backend ruff check .`
   - `uv run --directory apps/backend ruff format --check .`
   - `uv run --directory apps/backend mypy src tests`
   - the tests the spec names, plus the web and tracker checks when they changed.
4. **Commit** in small conventional commits (tests together with the code they cover). Every commit passes the checks.
5. **Write the debug map:** the spec's "Where to debug" section, following the template. Choose from the code you just wrote: three to five places, each with `file:line`, why it matters, a trigger that really reaches the line (a `curl`, a page, or a named test), and what to inspect. Check every line number against the committed code. Commit: `docs(tasks): <ID> debug map`.
6. **Verify.** Use the `verifier` subagent with the spec path. On FAIL, fix the cause and verify again. After three FAILs in a row, record the last report in the spec's Review section, keep `status: build`, and stop with the report.
7. **Review.** Use the `reviewer` subagent with the spec path. Apply every must-fix finding, commit, and verify again. If the fixes moved code, update the debug map's line numbers.
8. **Record** in the spec's Review section:
   - the verifier's verdict and its evidence table;
   - every finding, with what was done (fixed in `<commit>`, or left optional).
9. **Set `status: test`** (spec, roadmap, Now block: `Waiting on: the developer's test of <ID>`). Commit: `docs(tasks): <ID> verified`.
10. **Stop at gate 2.** Show:
    - the verdict, with one line of evidence per criterion;
    - the optional findings, numbered;
    - the spec's "Test it yourself" steps;
    - the debug map, one line per place: what it decides, `file:line`, the trigger;
    - "When you've tested it, run `/task <ID>`."

## 4. Build: human mode

**If `docs/learn/<ID>-<slug>.md` doesn't exist:**
1. Write it following `.claude/skills/task/human.md`. It is private and gitignored, so never commit or link it.
2. Don't edit source code, config or tests yourself.
3. Stop: "Your turn: follow `docs/learn/<ID>-<slug>.md`. Commit as it says. Run `/task <ID>` when its steps are done."

**If the tutorial exists** (the developer says the steps are done):
1. If there are uncommitted changes, list them and ask whether to continue anyway.
2. Use the `verifier`, then the `reviewer` subagent, as in section 3.
3. If the verdict is FAIL or there are must-fix findings, append a "Виправ" entry for each to the tutorial: the problem, why it matters, and the exact fix to type, in the tutorial's language rules. Keep `status: build`, and stop with a summary that points to the entries.
4. Otherwise, write the debug map from the developer's code (as in section 3, step 5), record the review in the spec, set `status: test`, commit the spec and the roadmap, and stop at gate 2 as in section 3, step 10.

## 5. Gate 2: the developer tested it

1. Ask with AskUserQuestion: "Did <ID> pass your own test?" Options: "Yes, it works", "No, I found a problem".
2. **A problem:** get the details if they aren't given.
   - Agent mode: fix it with a test that reproduces it, then go through verify and review again.
   - Human mode: append a "Виправ" entry to the tutorial.

   Set `status: build` and stop.
3. **It works:**
   - If optional findings exist, ask with a multi-select AskUserQuestion which to apply.
     - Agent mode: apply and commit them.
     - Human mode: append them as "Виправ" entries and stop (keep `status: test`); the next run continues here.
   - If code moved since the debug map was written, update its line numbers.
   - Run the `verifier` once more. Tell it the developer confirmed the `human:` criteria today. The verdict must be DONE. If it isn't, report it, keep `status: test`, and stop.
   - Flip `passes` to `true` in `docs/features.json` only for features whose check the verifier ran and saw exit 0.
   - Set `status: done` (spec, roadmap, Now block: Current empty, Next = the first `todo` task whose dependencies are all done after this merge). Record the final verdict in the spec. Commit: `docs(tasks): <ID> done`.
   - Write the PR description to `.git/pr-<ID>.md` (inside `.git`, so it's never committed), following `.github/pull_request_template.md`: the spec link, what and why, the proof (commands and key output), and the risk boxes. Leave "Tested it myself" ticked, since the developer just confirmed it.
   - Stop and give the command for the developer to run:

     `! git push -u origin <branch> && gh pr create --draft --title "<conventional title>" --body-file .git/pr-<ID>.md`

     Then: "Mark it ready when CI is green, merge it, and run `/task <ID>` to close it."

## 6. After the PR

1. Check with `gh pr view <branch> --json state,url`.
2. **Not merged yet:** show the PR state and URL. If no PR exists, give the section 5 command again.
3. **Merged:** say so, suggest `git switch main && git pull`, and name the next task and its mode.
