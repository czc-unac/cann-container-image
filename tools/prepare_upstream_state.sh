#!/usr/bin/env bash
# Align the worktree with upstream before generating release changes.
#
# Usage: tools/prepare_upstream_state.sh <upstream-url> <ref> <branch>
#
# The automation lives in this repository (the fork), but the release PR is
# opened by a maintainer against upstream, so the generated branch has to sit
# on top of upstream's <ref> - otherwise the PR diff would drag the fork's own
# divergence (or, worse, try to revert releases upstream already has).
#
# What it does, in order:
#   1. copies the automation tools to /tmp/cann-tools (upstream's tree does
#      not contain them, and the calling workflow keeps using them afterwards;
#      the copy is never committed),
#   2. fetches <ref> from <upstream-url> into this repository (full fetch, so
#      the later push to the fork has every object it needs),
#   3. force-creates <branch> at that upstream commit,
#   4. restores the policy files into tools/ as untracked files so the tools
#      run with the repository root as their working directory. They must not
#      be committed: the commit step stages release-owned paths explicitly.
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

cp "$tools_dir/release_profiles.json" "$tools_dir/release_matrix.json" tools/

echo "aligned $branch to $(git rev-parse --short "$base") from $upstream_url $ref"
