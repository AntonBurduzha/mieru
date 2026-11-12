#!/usr/bin/env bash
# PreToolUse hook: refuse to read secrets or run destructive commands, whatever the prompt says.
# Exit 0 = allow; exit 2 = block, and stderr goes back to Claude.
# A guard rail, not a sandbox: the deny rules in .claude/settings.json are the first layer.

set -uo pipefail

if ! command -v jq >/dev/null; then
  echo "Blocked by .claude/hooks/protect.sh: jq is not installed (brew install jq)" >&2
  exit 2
fi

input=$(cat)

# Every field that can name a file or carry a command. Grep's `pattern` is the text being
# searched for, so it is left out; Glob's `pattern` is a file pattern, so it is checked.
target=$(jq -r '
  .tool_name as $tool
  | .tool_input
  | [.command, .file_path, .path, .glob, (if $tool == "Glob" then .pattern else null end)]
  | map(select(type == "string"))
  | join("\n")' <<<"$input")

# .env.example only holds names and comments
cleaned=${target//.env.example/}

block() {
  echo "Blocked by .claude/hooks/protect.sh: $1. If it's really needed, ask the developer to run it." >&2
  exit 2
}

# Matching ignores case: macOS file names do too, so .ENV opens .env.
matches() { grep -Eiq -- "$1" <<<"$cleaned"; }

# A file named .env or .env.<name>. Allows .envrc, docs/env.md, $ENV, --env-file.
matches '(^|[^[:alnum:]_.-])\.env(\.[[:alnum:]_-]+)?($|[^[:alnum:]_.-])' \
  && block "a secrets file (.env)"

# A wildcard that could expand to .env: .e*  .en?  .[e]nv  .{e,x}nv
matches '(^|[[:space:]/"'\''=])\.(e[^[:space:]/]*|\[[^[:space:]/]*|\{[^[:space:]/]*)[][*?{}]' \
  && block "a wildcard that could match .env"

# Private keys
matches '\.pem($|[^[:alnum:]])|(^|/)id_(rsa|ecdsa|ed25519)($|[^.[:alnum:]])' \
  && block "a private key"

# Dropping or emptying tables
matches '(^|[^[:alnum:]_])(drop[[:space:]]+(table|database)|truncate[[:space:]]+(table[[:space:]]+)?[[:alpha:]_"`])' \
  && block "dropping or truncating a table"

# Deleting Docker volumes, which hold every local database
matches 'docker(-|[[:space:]]+)compose[^|;&]*[[:space:]]down([[:space:]][^|;&]*)?[[:space:]](-v|--volumes)([[:space:]]|$)|docker[[:space:]]+(volume[[:space:]]+(rm|prune)|system[[:space:]]+prune)' \
  && block "deleting Docker volumes (all local data)"

# Discarding local work; git clean -x would also delete the gitignored private notes
matches 'git[[:space:]]+(clean|reset[[:space:]]+([^|;&]*[[:space:]])?--hard)' \
  && block "discarding local work (git clean, git reset --hard)"

exit 0
