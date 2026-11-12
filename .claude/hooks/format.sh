#!/usr/bin/env bash
# PostToolUse hook: format the file Claude just wrote. Never fails the tool call.
# Python: ruff sorts imports, then formats. TS/JS/CSS: the app's own prettier when it is
# installed, otherwise a pinned version.

file=$(jq -r '.tool_input.file_path // empty')
[[ -n "$file" && -f "$file" ]] || exit 0
root="$CLAUDE_PROJECT_DIR"

case "$file" in
  "$root"/apps/backend/*.py)
    # --frozen: never re-lock or sync dependencies from a hook
    uv run --frozen --project "$root/apps/backend" ruff check --select I --fix --quiet "$file" >/dev/null 2>&1
    uv run --frozen --project "$root/apps/backend" ruff format --quiet "$file" >/dev/null 2>&1
    ;;
  *.ts|*.tsx|*.js|*.mjs|*.css)
    for app in web tracker; do
      bin="$root/apps/$app/node_modules/.bin/prettier"
      if [[ "$file" == "$root/apps/$app/"* && -x "$bin" ]]; then
        "$bin" --write "$file" >/dev/null 2>&1
        exit 0
      fi
    done
    npx --yes prettier@3.6.2 --write "$file" >/dev/null 2>&1
    ;;
esac

exit 0
