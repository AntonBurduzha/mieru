---
name: verify
description: Check a task's proof of done and report DONE, FAIL or NEED_HUMAN with evidence. Read-only; runs in a separate verifier agent.
argument-hint: <task id, e.g. T07>
disable-model-invocation: true
context: fork
agent: verifier
---

Verify task `$ARGUMENTS`.

- Normalise the id (`t7`, `7` and `T7` all mean `T07`), and find its spec at `docs/tasks/<ID>-*.md`. If there is no spec, report that and stop.
- The developer has not confirmed any `human:` criteria in this run. Report them as unconfirmed, with their steps.
- Run every check your instructions list, and answer in your report format.
