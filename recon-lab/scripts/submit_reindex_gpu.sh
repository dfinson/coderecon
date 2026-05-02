#!/usr/bin/env bash
# Submit the GPU reindex pipeline to Azure ML.
#
# Prerequisites:
#   1. `az login` (or DefaultAzureCredential via managed identity)
#   2. `terraform apply` in ../infra/ to provision the GPU cluster
#   3. Upload clones to the workspace blob datastore (one-time)
#
# Usage:
#   ./scripts/submit_reindex_gpu.sh                    # 4 shards, all sets
#   ./scripts/submit_reindex_gpu.sh --shard-count 2    # 2 shards
#   ./scripts/submit_reindex_gpu.sh --repo-set eval    # only eval set
#   ./scripts/submit_reindex_gpu.sh --dry-run          # print without submitting

set -euo pipefail
cd "$(dirname "$0")/.."

# Ensure the subscription is set
export AML_SUBSCRIPTION_ID="${AML_SUBSCRIPTION_ID:-d1a12354-5c67-4461-9fc9-2e5c111ea163}"

# Activate venv if not already
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    source .venv/bin/activate 2>/dev/null || true
fi

exec python -m aml.pipeline \
    --stage reindex-gpu \
    --compute-gpu index-gpu \
    --experiment coderecon-reindex \
    "$@"
