#!/usr/bin/env bash
# PreToolUse hook: refuse to touch secrets or run destructive commands
# Exit 0 = allow, exit 2 = block

set -uo pipefail

if ! command -v jq >/dev/null; then
  echo "Blocked by .claude/hooks/protect.sh: jq is not installed (brew install jq)" >&2
  exit 2
fi

input=$(cat)

target=$(jq -r '.tool_input.file_path // .tool_input.command // empty' <<<"$input")

cleaned=${target//.env.example/}

#  1. a file named .env or .env.<something> (.env.local, .env.prod), at the start or after a
#     space, slash, quote or similar, and not followed by more name characters.
#     Blocks: .env  ./.env  cat .env  apps/web/.env  less '.env'  .env.local
#     Allows: .envrc  my.environment  docs/env.md  $ENV
#  2. a .pem file (private keys and certificates).
#  3. dropping a whole table or database.
#  4. removing Compose volumes, i.e. every local database's data.
pattern='(^|[^[:alnum:]_.-])\.env(\.[[:alnum:]_-]+)?($|[^[:alnum:]_.-])'
pattern+='|\.pem($|[^[:alnum:]])'
pattern+='|DROP (TABLE|DATABASE)'
pattern+='|docker compose down -v'

if grep -Eq "$pattern" <<<"$cleaned"; then
  echo "Blocked by .claude/hooks/protect.sh: secrets or destructive command ($target)" >&2
  exit 2
fi

exit 0
