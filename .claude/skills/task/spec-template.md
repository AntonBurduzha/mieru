---
id: T<NN>
title: <title from docs/roadmap.md>
mode: agent            # agent | human
status: spec           # spec | build | test | done
features: []           # ids from docs/features.json, e.g. [I1]
depends: []            # task ids, e.g. [T05]
branch: t<nn>-<slug>
---

# T<NN> <title>

## Goal
One or two sentences: what works afterwards that didn't before, and for whom.

## Context to read
- `docs/architecture.md` section N: why it matters for this task
- `docs/conventions.md` section N
- Code that shows the pattern to follow: `path/to/file.py:12`

## Scope
- **In:** the behaviour this task adds or changes
- **Out:** what a reader might expect here but is left for later (name the task that does it)
- **Files allowed to change:** exact paths or narrow globs, tests included. The spec, `docs/roadmap.md` and `docs/features.json` are always allowed

## Interfaces
Exact names and shapes other code relies on: function signatures, endpoints with request and response bodies, settings, Redis keys, table columns, component props. Names come from `docs/conventions.md` section 5 when they exist there.

## Acceptance criteria
Each criterion is observable and ends with how it is proven: `auto:` a command that exits 0 only when the criterion holds, or `human:` steps a person follows.

1. Given ..., when ..., then ... (auto: `uv run --directory apps/backend pytest tests/unit/core/test_config.py -k unset_env`)
2. ... (human: run X, open Y, expect Z)

## Tests to write
- `tests/unit/<area>/test_<module>.py::test_<behaviour>`: proves criterion 1
- ...

## Proof of done
Commands run from the repo root, each with the output that proves it. The verifier runs all of them.

```bash
uv run --directory apps/backend ruff check . && uv run --directory apps/backend ruff format --check .
uv run --directory apps/backend mypy src tests
uv run --directory apps/backend pytest            # needs `docker compose up -d`
```

Expected: every command exits 0; pytest reports no failures and no skips added by this task.

## Test it yourself
Up to five steps for the developer at gate 2, with what to see. Prefer the real app over tests: a `curl`, a page, a log line.

## Notes
Decisions made while building, and open questions. Filled in during the build.

## Review
Filled in by `/task`: the verifier's verdict, the reviewer's findings and what was done with each.
