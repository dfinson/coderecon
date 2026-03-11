#!/usr/bin/env bash
# Initialize the recon-lab pipeline workspace.
#
# Creates the directory structure that clone.py, index.py,
# and gt_orchestrator.py expect under $CPL_LAB_WORKSPACE.
#
# Usage:
#   bash recon-lab/setup_workspace.sh
#
#   # Or with a custom location:
#   export CPL_LAB_WORKSPACE=/mnt/data/recon-lab
#   bash recon-lab/setup_workspace.sh

set -euo pipefail

WORKSPACE="${CPL_LAB_WORKSPACE:-$HOME/.codeplane/recon-lab}"

mkdir -p \
    "$WORKSPACE/clones" \
    "$WORKSPACE/data/merged" \
    "$WORKSPACE/data/logs/sessions" \
    "$WORKSPACE/data/logs/errors"

echo "Lab workspace initialized at: $WORKSPACE"
echo ""
echo "To persist, add to your shell profile:"
echo "  export CPL_LAB_WORKSPACE=$WORKSPACE"
