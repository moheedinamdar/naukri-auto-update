#!/usr/bin/env bash
# Point git at this repo's hooks so the pre-commit secret guard runs.
# Run once after cloning:  ./scripts/install-hooks.sh
set -euo pipefail
cd "$(dirname "$0")/.."

chmod +x .githooks/pre-commit
git config core.hooksPath .githooks
echo "Installed: git will now run .githooks/pre-commit before every commit."
echo "It blocks committing .env, resume PDFs, browser profiles, artifacts and logs."
