#!/usr/bin/env bash
# Full pipeline: wait for indexing → collect → merge → train → install
set -euo pipefail
cd /home/dave01/wsl-repos/coderecon

VENV=".venv/bin/python"
export PYTHONPATH="recon-lab/src:src"

echo "=== Phase 1: Wait for indexing to complete ==="
while true; do
    active=$(ps aux | grep "coderecon.cli.main init" | grep -v grep | wc -l)
    indexed=$(find ~/.recon/recon-lab/clones/instances -name "index.db" 2>/dev/null | wc -l)
    echo "  $(date +%H:%M:%S) — $indexed/293 indexed, $active active workers"
    if [[ "$active" -eq 0 ]]; then
        echo "  Indexing complete: $indexed repos indexed."
        break
    fi
    sleep 30
done

echo ""
echo "=== Phase 2: Collect signals ==="
$VENV -m cpl_lab.cli collect --set all --workers 2
echo ""

echo "=== Phase 3: Merge ==="
$VENV -m cpl_lab.cli merge --what all
echo ""

echo "=== Phase 4: Train (local, 4 structural models) ==="
OUTDIR="$HOME/.recon/recon-lab/models"
mkdir -p "$OUTDIR"
$VENV -m cpl_lab.train_all \
    --data-dir "$HOME/.recon/recon-lab/data" \
    --output-dir "$OUTDIR" \
    --skip-merge
echo ""

echo "=== Phase 5: Install models ==="
MODEL_DEST="src/coderecon/ranking/data"
mkdir -p "$MODEL_DEST"

# Map training output names to runtime names
cp "$OUTDIR/def_ranker_structural.lgbm"  "$MODEL_DEST/ranker.lgbm"
cp "$OUTDIR/file_ranker_structural.lgbm" "$MODEL_DEST/file_ranker.lgbm"
cp "$OUTDIR/gate_structural.lgbm"        "$MODEL_DEST/gate.lgbm"
cp "$OUTDIR/cutoff_structural.lgbm"      "$MODEL_DEST/cutoff.lgbm"

echo "Models installed to $MODEL_DEST:"
ls -lh "$MODEL_DEST"/*.lgbm

echo ""
echo "=== DONE ==="
