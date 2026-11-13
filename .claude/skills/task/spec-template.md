---
id: T<NN>
title: <title from docs/roadmap.md>
mode: agent            # agent | human
status: spec           # spec | build | test | done
features: []           # ids from docs/features.json, e.g. [I1]
depends: []            # task ids, e.g. [T05]
branch: t<nn>-<slug>
---

<!-- Keep the spec under 150 lines, plus at most about 30 for Review. It is public: write it in English, with no links to private files. -->

# T<NN> <title>

## Goal
One or two sentences: what works afterwards that didn't before, and for whom.

## Context to read
- `docs/architecture/<topic>.md` (a heading, or `decisions.md` row N): why it matters for this task
- `docs/conventions.md` section N
- Code that shows the pattern to follow: `path/to/file.py:12`

## Scope
- **In:** the behaviour this task adds or changes.
- **Out:** what a reader might expect here but is left for later. Name the task that does it.
- **Files allowed to change:**
  - Exact paths or narrow globs, including the tests and every doc this task updates.
  - The spec, `docs/roadmap.md` and `docs/features.json` are always allowed.
  - In human mode the developer writes the code and Claude writes the Markdown docs.

## Interfaces
Exact names and shapes that other code relies on: function signatures, endpoints with their request and response bodies, settings, Redis keys, table columns, component props. Take the names from `docs/conventions.md` section 5 when they exist there.

## Acceptance criteria
Each criterion is observable and ends with how it is proven:
- `auto:` a command that exits 0 only when the criterion holds;
- `human:` steps a person follows.

1. Given ..., when ..., then ... (auto: `uv run --directory apps/backend pytest tests/unit/core/test_config.py -k unset_env`)
2. ... (human: run X, open Y, expect Z)

## Tests to write
- `tests/unit/<area>/test_<module>.py::test_<behaviour>`: proves criterion 1
- ...

## Proof of done
The verifier always runs:
- the standard checks for every app the diff touches (lint, format, types, the full test suite);
- every `auto:` command;
- the feature checks;
- the scope and test-integrity checks.

List here only what else proves this task, as commands run from the repo root, each with the output that proves it. If nothing else is needed, write "Nothing beyond the standard checks."

## Test it yourself
Up to five steps for the developer to follow before run 2, each with what they should see. Prefer the real app over tests: a `curl`, a page, a log line.

## Where to debug
Written once, just before the developer's test, from the code on disk. List three to five places where this task's behaviour is decided, the riskiest first:
- input boundaries;
- branches that choose behaviour;
- I/O adapters;
- concurrency.

Skip trivial glue. Each trigger must really reach the line. How to use the debugger: `docs/workflow.md`, "Debugging".

| What it decides | Where | Reach it with | Look at |
|---|---|---|---|
| <one phrase> | `apps/backend/src/mieru/<module>.py:<line>` (`<function>`) | the Cursor config `<api / ingest / pytest: -k>` and `<a curl, page or test>` | the values to inspect and what you expect; how a bug here shows from outside |

## Notes
Decisions made during the build that matter only to this task, and open questions, one or two lines each. A decision that outlives the task goes to `docs/architecture/decisions.md`.

## Review
Filled in by `/task`, in this compact form:

**Reviewer, <date>:** <n> must-fix, <n> should-fix, <n> nits.
1. [must-fix, fixed] `path:line`: what broke, and what changed.
2. [nit, left] `path:line`: what it is.

**Verifier, <date>: <VERDICT>.** 1 pass (exit 0) · 2 pass · 3 human: confirmed in run 2 · Scope clean · Test integrity clean.

**Done, <date>:** what the developer confirmed; final verdict DONE.
