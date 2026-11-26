#!/usr/bin/env bash
# Align the worktree with upstream before generating release changes.
#
# Usage: tools/cann_release/prepare_upstream_state.sh <upstream-url> <ref> <branch>
#
# The release branch must sit on top of upstream's <ref> so the release PR
# contains only the version change-set. Fetches <ref> with full history (the
# later push needs every object) and force-creates <branch> at that commit.
#
# Must run from the repository root. Prints the aligned base commit.
set -euo pipefail

upstream_url="${1:?usage: prepare_upstream_state.sh <upstream-url> <ref> <branch>}"
ref="${2:?missing ref}"
branch="${3:?missing branch}"

git fetch --no-tags "$upstream_url" "$ref"
base="$(git rev-parse FETCH_HEAD)"
git checkout -B "$branch" "$base"

echo "aligned $branch to $(git rev-parse --short "$base") from $upstream_url $ref"
