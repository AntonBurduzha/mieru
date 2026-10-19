#!/usr/bin/env bash
# PostToolUse: format the file Claude just wrote. Never fails the tool call.

file=$(jq -r '.tool_input.file_path // empty')
case "$file" in
  *.py)              uv run --project "$CLAUDE_PROJECT_DIR/apps/backend" ruff format "$file" >/dev/null 2>&1 ;;
  *.ts|*.vue|*.js)   npx --yes prettier --write "$file" >/dev/null 2>&1 ;;
esac

exit 0
