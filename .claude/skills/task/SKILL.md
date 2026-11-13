---
name: task
description: Run one roadmap task in two runs. Run 1 writes the spec, gets it approved, then builds it (agent mode) or writes the tutorial (human mode). Run 2, after the developer's test, checks, fixes and makes the task's one commit.
argument-hint: <task id, e.g. T07> [--agent|--human] [feedback from your test]
disable-model-invocation: true
---

# /task

Arguments: `$ARGUMENTS`

This file is the procedure. `docs/workflow.md` explains the same flow to people; you don't need it.

## Start

1. **Parse the arguments.**
   - The first word is the task id: `t7`, `7` and `T7` all become `T07`.
   - `--agent` or `--human` overrides the mode.
   - Any other text is the developer's feedback for run 2.
   - With no id, stop and print `/task <id> [--agent|--human] [feedback]`.
2. **Find the task.** Read its row in `docs/roadmap.md` (title, mode, features, depends); stop if there is none. Read the spec `docs/tasks/<ID>-*.md` if it exists.
3. **Pick the mode:** the flag, else the spec's `mode`, else the roadmap's. A flag can change the mode only before the build (no spec yet, or `status: spec`).
4. **Dispatch on the spec's status:**

| Spec | Do |
|---|---|
| none | Run 1: Spec, then Gate 1 |
| `spec` | Gate 1: an earlier session ended before the answer |
| `build`, agent mode | Agent build: resume where the spec's Notes and `git status` say it stopped |
| `build`, human mode, no tutorial yet | Tutorial |
| `build`, human mode, tutorial written | Run 2: Feedback, Human check, Finish |
| `test` | Run 2: Feedback, Finish |
| `done` | Report `gh pr view <branch> --json state,url` and name the next task |

## Rules

- **Two runs per task.**
  - Run 1 ends when it's the developer's turn: testing in agent mode, the tutorial in human mode.
  - Run 2 ends with the task's one commit.
  - A third run happens only after a reported problem, or a fix the developer has to type.
- **Commits.**
  - The spec gets its own commit at gate 1.
  - After that, nothing is committed until Finish makes the task's one commit on the task branch.
  - A change before the push is amended into that commit (`git commit --amend --no-edit`). A change after the push goes in a new commit.
- **Status** lives in the spec's frontmatter and in its row in `docs/roadmap.md`. Change both in the same edit. The Now block (Next, Open questions, Updated: today) changes only when Next or an open question does.
- **Docs are yours in both modes:**
  - every Markdown file the spec allows;
  - the roadmap and `docs/features.json`;
  - the spec's Notes, Review and "Where to debug".

  The developer never edits docs by hand. In human mode they write everything else.
- **Allowed files only.** If the work needs a file outside the spec's list, stop and ask.
- **Subagents** (`test-writer`, `reviewer`, `verifier`) get the spec's path in their prompt.
- **Commit messages go in a file.** `.claude/hooks/protect.sh` blocks an inline message that mentions `.env` or a destructive command, so write it to a file and use `git commit -F`.
- **End every run with a short block:** what happened, the status now, and the exact command the developer runs next.

## Run 1

### Spec

1. **Start from `main`.** The tree must be clean; if it isn't, list the changes and stop. Then run `git switch main && git pull --ff-only`.
2. **Check the dependencies.** If one isn't `done` in the roadmap, name it and stop, unless the developer confirms they want to go ahead.
3. **Read:**
   - the feature entries in `docs/features.json`, and their stories in `docs/product.md`;
   - `docs/architecture/README.md`, then only the topic files and `decisions.md` rows the task touches;
   - `docs/conventions.md` sections 1, 3, 5 and 6, and the parts of section 7 for the task's area;
   - `docs/frontend.md`, for web work;
   - the code the task changes, and the installed versions.

   Every path, name and API in the spec must match the repo as it is.
4. **Write `docs/tasks/<ID>-<slug>.md`** from `.claude/skills/task/spec-template.md`.
   - The slug is 2-4 words from the title, in kebab case. The branch is `<id lowercase>-<slug>`.
   - Every criterion has an `auto:` command or `human:` steps.
   - The allowed files are specific, and include every doc the task changes.
5. **Set `status: spec` and the mode,** in the spec and in the roadmap row.

### Gate 1

1. **Show** the goal, the criteria (one line each), the allowed files, and any decision the developer might disagree with.
2. **Ask with AskUserQuestion:** "Approve the <ID> spec and start the build (<mode> mode)?" The options are "Approve" and "Not now"; changes come as text through "Other".
3. **Re-read the spec file,** since the developer may have edited it while the question waited.
   - Changes asked for: apply them, and ask again.
   - "Not now": stop, and leave the status as it is.
4. **Approve:**
   - The tree must hold only the spec and the roadmap.
   - `git switch -c <branch>`.
   - Set `status: build`.
   - Commit both files as `docs(tasks): <ID> spec`.
   - Continue with the Agent build or the Tutorial.

### Agent build

Note progress in the spec's Notes, so an interrupted run can resume.

1. **Tests first.** Run the `test-writer` subagent, then check that:
   - `git status` shows only test files besides the spec;
   - every `auto:` criterion has a test;
   - the tests fail because the behaviour is missing, not because of a typo or a broken fixture.

   Close gaps with the subagent. If the spec has no tests to write (shell or CI work), skip this step and note why.
2. **Implement** in this session, following the patterns the spec points to.
3. **Run the checks** from `CLAUDE.md` "Commands" for every app the diff touches, plus the tests the spec names, until they're green.
4. **Update the docs** the spec lists, so they describe the code as built.
5. **Review.** Run the `reviewer` subagent. Apply every must-fix and should-fix finding, then run step 3 again. Leave the nits.
6. **Verify.** Run the `verifier` subagent.
   - On FAIL, fix the cause and verify again.
   - After three FAILs in a row: record the last report in Review, keep `status: build`, and stop with its "To reach DONE" list.
7. **Write the debug map** from the code on disk, and check every line number.
8. **Fill in the Review section** in the template's compact form.
9. **Set `status: test`.** End the run with:
   - the verdict, one line per criterion;
   - the nits, numbered;
   - the "Test it yourself" steps;
   - the debug map, one line per place;
   - "Review the diff in Cursor and test it. Works: `/task <ID>`. A problem: `/task <ID> <what's wrong>`. To apply a nit: `/task <ID> apply nit 2`."

   If a `human:` criterion needs evidence only the developer can produce (server output, say), list its commands and ask for their output with `/task <ID>`.

### Tutorial (human mode)

1. **Write `docs/learn/<ID>-<slug>.md`** following `.claude/skills/task/human.md`. It is private and gitignored: never commit it or link to it.
2. **Don't touch the task's code, config, migrations, tests or docs.** The docs are written in run 2, from the developer's code.
3. **End the run:** "Your turn: follow `docs/learn/<ID>-<slug>.md`, and don't commit. When its Definition of done passes, run `/task <ID>`."

## Run 2

Running `/task <ID>` now means "I tested it and it works". It also confirms the `human:` criteria.

### Feedback (text after the id)

- **Evidence** (pasted output): keep it for Review, and go on.
- **A request** ("apply nit 2"): apply it, and go on.
- **A problem:**
  - Agent mode: reproduce it in a test, fix it, and run the checks. Then run the `reviewer` (if the fix is more than a few lines) and the `verifier`.
  - Human mode, a small fix (a few lines in one function, no design change): apply it, and explain it in a "Виправ" entry.
  - Human mode, a bigger fix: write a "Виправ" entry with the exact fix for the developer to type.

  Note the problem in Notes, keep the status, and stop: "Test it again, then `/task <ID>`."

### Human check (human mode)

1. **Check the scope.** If any change is outside the allowed files, list it and ask.
2. **Write the Markdown docs** the spec lists, from the developer's code.
3. **Review and verify:** run the `reviewer` and the `verifier` in parallel, as two subagent calls in one message.
4. **Handle each FAIL and each must-fix or should-fix finding:**
   - **Small, and not marked `(design)`:** fix it yourself. Add a "Виправ" entry marked as applied by Claude: the problem, why it matters, and what changed.
   - **Otherwise:** add a "Виправ" entry for the developer: the problem, why it matters, and the exact fix to type. Then record the review, keep `status: build`, and stop: "Your turn: Виправ <N>-<M>, then `/task <ID>`."

   Nits go into Review and are not applied.
5. **Write the debug map** from the developer's code, and check every line number.

### Finish

1. **The verdict must be DONE.**
   - If the tree changed since the last verdict, run the `verifier` again, and tell it the developer has confirmed the `human:` criteria.
   - If it didn't change, the last verdict stands. It counts as DONE when it was DONE, or was NEED_HUMAN only for things the developer has now confirmed or supplied.
   - Otherwise: record it, keep the status, and stop with "To reach DONE".
2. **Update the debug map's line numbers** if the code moved.
3. **Flip `passes` to `true`** in `docs/features.json`, only for features whose check the verifier ran and saw exit 0.
4. **Close the task:**
   - Set `status: done`.
   - Set the Now block's Next to the first `todo` task whose dependencies will all be done after this merge.
   - Add the Done line to Review.
5. **Commit:** follow `.claude/skills/commit/SKILL.md`. It makes one commit of everything since the spec commit, and writes the PR description to `.git/pr-<ID>.md`.
6. **End the run with:**
   - `git log --oneline -1`, then "Look it over in Cursor. Anything to change before the push: tell me, and I'll amend it.";
   - `! git push -u origin <branch> && gh pr create --draft --title "<commit subject>" --body-file .git/pr-<ID>.md`;
   - "Mark it ready when CI is green, then merge.";
   - "Next: `/task <next ID>` (<mode>). It starts from an up-to-date `main`."
