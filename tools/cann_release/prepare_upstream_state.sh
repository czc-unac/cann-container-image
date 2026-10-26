#!/usr/bin/env bash
# Align the worktree with upstream before generating release changes.
#
# Usage: tools/cann_release/prepare_upstream_state.sh <upstream-url> <ref> <branch>
#
# The automation lives in the fork, but the release PR targets upstream, so
# the generated branch must sit on top of upstream's <ref>. What it does:
#   1. copies tools/ to /tmp/cann-tools (upstream does not carry the
#      automation; the caller keeps using the copy and never commits it),
#   2. fetches <ref> from <upstream-url> (full fetch, so the later push has
#      every object the fork lacks),
#   3. force-creates <branch> at that upstream commit,
#   4. restores the policy files into tools/cann_release/ as untracked helpers
#      so the generator can run with the repository root as its cwd. They are
#      never committed: the caller stages release-owned paths explicitly.
#
# Must run from the repository root. Prints the aligned base commit.
set -euo pipefail

upstream_url="${1:?usage: prepare_upstream_state.sh <upstream-url> <ref> <branch>}"
ref="${2:?missing ref}"
branch="${3:?missing branch}"
tools_dir="/tmp/cann-tools"

rm -rf "$tools_dir"
cp -r tools "$tools_dir"

git fetch --no-tags "$upstream_url" "$ref"
base="$(git rev-parse FETCH_HEAD)"
git checkout -B "$branch" "$base"

mkdir -p tools/cann_release
cp "$tools_dir/cann_release/release_profiles.json" \
   "$tools_dir/cann_release/release_matrix.json" tools/cann_release/

echo "aligned $branch to $(git rev-parse --short "$base") from $upstream_url $ref"
