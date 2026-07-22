#!/usr/bin/env bash
# Run Playwright UI pack — must use playwright-ui's local @playwright/test (not repo-root npx).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/playwright-ui/run.sh" "$@"
