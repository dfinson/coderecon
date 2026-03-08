#!/usr/bin/env bash
# build_and_push.sh — build codeplane wheel, docker image, push to ACR.
#
# Usage:
#   cd ranking/infra && bash build_and_push.sh
#
# Requires: pip, docker, az CLI (logged in)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WHEEL_DIR="$SCRIPT_DIR/codeplane_wheel"

# Read ACR name from terraform output (or fall back to env)
ACR_NAME="${ACR_NAME:-$(cd "$SCRIPT_DIR" && terraform output -raw acr_name 2>/dev/null || echo "")}"
if [[ -z "$ACR_NAME" ]]; then
  echo "ERROR: ACR_NAME not set and terraform output unavailable"
  echo "  Run: export ACR_NAME=acrcplidx24b2e0"
  exit 1
fi
ACR_SERVER="${ACR_NAME}.azurecr.io"
IMAGE="${ACR_SERVER}/codeplane-indexer:latest"

echo "=== [1/4] Building wheel ==="
rm -rf "$WHEEL_DIR"
mkdir -p "$WHEEL_DIR"
(cd "$REPO_ROOT" && pip wheel --no-deps -w "$WHEEL_DIR" . 2>&1 | tail -3)
WHL=$(ls "$WHEEL_DIR"/*.whl)
echo "  Built: $(basename "$WHL") ($(du -h "$WHL" | cut -f1))"

echo ""
echo "=== [2/4] Building Docker image ==="
docker build -t "$IMAGE" -f "$SCRIPT_DIR/Dockerfile" "$SCRIPT_DIR"
echo "  Image: $IMAGE"

echo ""
echo "=== [3/4] Logging into ACR ==="
az acr login -n "$ACR_NAME"

echo ""
echo "=== [4/4] Pushing image ==="
docker push "$IMAGE"

echo ""
echo "=== Done ==="
echo "  Image: $IMAGE"
echo "  To run batch index: python -m ranking.infra.batch_index"
