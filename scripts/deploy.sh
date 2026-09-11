#!/usr/bin/env bash
# Deploy CI-built images on the server WITHOUT building them locally.
#
# Downloads the `swot-images` artifact from the latest successful CI run
# (docker save of swot-bot/{bot,downloader,transcriber,analyzer}:dev),
# loads it into the local Docker daemon and starts the slim production stack.
#
# Usage (on the server):
#   gh auth login                 # once (or set GH_TOKEN)
#   ./scripts/deploy.sh owner/swot-bot
#
# Env:
#   DEPLOY_DIR  repo checkout with .env on the server (default: script's repo root)
set -euo pipefail

REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)}"
[ -n "$REPO" ] || { echo "usage: $0 <owner/repo>" >&2; exit 1; }

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> latest successful main run of ci.yml in $REPO"
RUN_ID="$(gh run list --repo "$REPO" --workflow ci.yml --branch main --status success -L 1 --json databaseId -q '.[0].databaseId')"
[ -n "$RUN_ID" ] || { echo "no successful main run found" >&2; exit 1; }
echo "    run: $RUN_ID"

echo "==> downloading swot-images artifact"
gh run download "$RUN_ID" --repo "$REPO" --name swot-images --dir "$TMP"
ls -lh "$TMP/swot-images.tar.gz"

echo "==> docker load"
docker load -i "$TMP/swot-images.tar.gz"

echo "==> starting slim stack in $DEPLOY_DIR"
cd "$DEPLOY_DIR"
docker compose -f docker-compose.slim.yml up -d
docker compose -f docker-compose.slim.yml ps
