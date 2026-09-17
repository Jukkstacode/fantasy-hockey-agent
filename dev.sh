#!/usr/bin/env bash
# Run a command inside the agent's Docker image with the live project dir mounted.
# For interactive commands (e.g. --auth) run: DEV_TTY=1 ./dev.sh python main.py --auth
# The image declares VOLUMEs for auth/ and logs/, so those must be mounted explicitly
# or Docker shadows them with empty anonymous volumes.
# Usage: ./dev.sh python main.py --lineup-only
cd "$(dirname "$0")"
mkdir -p auth logs state
EXTRA=()
[ -n "$DEV_SCRATCH" ] && EXTRA=(-v "$DEV_SCRATCH:/scratch")
exec docker run --rm -i ${DEV_TTY:+-t} --env-file .env \
  -v "$PWD:/app" -v "$PWD/auth:/app/auth" -v "$PWD/logs:/app/logs" -v "$PWD/state:/app/state" \
  "${EXTRA[@]}" -w /app fantasy-hockey-agent "$@"
