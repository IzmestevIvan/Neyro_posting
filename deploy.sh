#!/usr/bin/env bash
# Only dispatch the reviewed GitHub release workflow. Never upload a dirty checkout.
set -euo pipefail
command -v gh >/dev/null || { echo 'Use GitHub → Actions → Deploy production → Run workflow, or install GitHub CLI.' >&2; exit 1; }
test -z "$(git status --porcelain)" || { echo 'Commit or set aside local changes before requesting a release.' >&2; exit 1; }
sha=${1:-$(git rev-parse HEAD)}
[[ "$sha" =~ ^[0-9a-f]{40}$ ]]
gh workflow run deploy.yml --ref main -f commit="$sha"
echo 'Release requested. Track the Deploy production workflow in GitHub Actions.'
