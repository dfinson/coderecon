#!/bin/bash
# index_job.sh — ACI entrypoint for batch indexing a single repo.
#
# Args: REPO_URL COMMIT REPO_ID STORAGE_ACCOUNT CONTAINER_NAME
#
# Clones at exact commit, runs cpl init with profiling, uploads
# .codeplane/ + profile.json to blob. Captures detailed timing for
# every phase.
set -euo pipefail

REPO_URL="${1:?Usage: index_job.sh REPO_URL COMMIT REPO_ID STORAGE_ACCOUNT CONTAINER}"
COMMIT="${2:?}"
REPO_ID="${3:?}"
STORAGE_ACCOUNT="${4:?}"
CONTAINER_NAME="${5:?}"

PROFILE="/tmp/profile.json"
JOB_START=$(date +%s)

ts() { date +%s; }
elapsed() { echo $(( $(ts) - $1 )); }

echo "=== Indexing ${REPO_ID} @ ${COMMIT:0:12} ==="
echo "  URL: ${REPO_URL}"
echo "  Host: $(hostname)"
echo "  CPUs: $(nproc)"
echo "  RAM: $(free -h | awk '/Mem:/{print $2}')"
echo "  Disk: $(df -h /repo 2>/dev/null | tail -1 | awk '{print $4}' || echo 'n/a')"

# ── Phase 1: Clone ──────────────────────────────────────────────
PHASE_START=$(ts)
echo "[1/5] Cloning..."
git clone --quiet "${REPO_URL}" /repo
cd /repo
git checkout --quiet "${COMMIT}"
CLONE_SEC=$(elapsed $PHASE_START)
FILE_COUNT=$(find . -type f -not -path './.git/*' | wc -l)
REPO_SIZE=$(du -sh --exclude=.git . | cut -f1)
echo "  $(git log --oneline -1)"
echo "  ${FILE_COUNT} files, ${REPO_SIZE} on disk, ${CLONE_SEC}s"

# ── Phase 2: Index ──────────────────────────────────────────────
PHASE_START=$(ts)
echo "[2/5] Indexing (LOG_LEVEL=INFO for stage timing)..."
LOG_LEVEL=INFO cpl init 2>&1 | tee /tmp/cpl_init.log
INDEX_SEC=$(elapsed $PHASE_START)
echo "  Index: ${INDEX_SEC}s"

# ── Phase 3: Gather stats ──────────────────────────────────────
echo "[3/5] Gathering index stats..."
python3 << 'PYEOF'
import json, sqlite3, os, time
from pathlib import Path

db = ".codeplane/index.db"
if not os.path.exists(db):
    print("  ERROR: no index.db")
    exit(1)

con = sqlite3.connect(db)
files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
defs = con.execute("SELECT COUNT(*) FROM def_facts").fetchone()[0]
code_defs = con.execute(
    "SELECT COUNT(*) FROM def_facts WHERE kind IN "
    "('function','method','class','struct','interface','trait','enum','property','variable','constant','module')"
).fetchone()[0]
non_code_defs = defs - code_defs
refs = con.execute("SELECT COUNT(*) FROM ref_facts").fetchone()[0]
imports = con.execute("SELECT COUNT(*) FROM import_facts").fetchone()[0]
scopes = con.execute("SELECT COUNT(*) FROM scope_facts").fetchone()[0]

# Defs by kind
kinds = con.execute("SELECT kind, COUNT(*) FROM def_facts GROUP BY kind ORDER BY COUNT(*) DESC").fetchall()

# Defs by language
langs = con.execute(
    "SELECT f.language_family, COUNT(d.def_uid) FROM def_facts d "
    "JOIN files f ON d.file_id = f.id GROUP BY f.language_family ORDER BY COUNT(d.def_uid) DESC"
).fetchall()

con.close()

# Embedding stats
def_emb = Path(".codeplane/def_embedding/def_meta.json")
emb_count = 0
if def_emb.exists():
    emb_count = json.loads(def_emb.read_text()).get("def_count", 0)

# Tantivy size
tantivy_size = sum(f.stat().st_size for f in Path(".codeplane/tantivy").rglob("*") if f.is_file()) if Path(".codeplane/tantivy").exists() else 0

# Total .codeplane size
cpl_size = sum(f.stat().st_size for f in Path(".codeplane").rglob("*") if f.is_file())

stats = {
    "files_indexed": files,
    "defs_total": defs,
    "code_defs": code_defs,
    "non_code_defs": non_code_defs,
    "refs": refs,
    "imports": imports,
    "scopes": scopes,
    "embeddings": emb_count,
    "defs_by_kind": {k: c for k, c in kinds},
    "defs_by_language": {k or "unknown": c for k, c in langs},
    "tantivy_bytes": tantivy_size,
    "codeplane_bytes": cpl_size,
}

json.dump(stats, open("/tmp/index_stats.json", "w"), indent=2)
print(f"  files={files} defs={defs} (code={code_defs} non-code={non_code_defs})")
print(f"  refs={refs} imports={imports} scopes={scopes} embeddings={emb_count}")
print(f"  .codeplane size: {cpl_size / 1024 / 1024:.1f}MB (tantivy: {tantivy_size / 1024 / 1024:.1f}MB)")
PYEOF

# ── Phase 4: Package ────────────────────────────────────────────
PHASE_START=$(ts)
echo "[4/5] Packaging..."
tar czf "/tmp/${REPO_ID}.tar.gz" -C /repo .codeplane/
PACK_SEC=$(elapsed $PHASE_START)
ARCHIVE_SIZE=$(du -h "/tmp/${REPO_ID}.tar.gz" | cut -f1)
echo "  Archive: ${ARCHIVE_SIZE}, ${PACK_SEC}s"

# ── Phase 5: Upload ─────────────────────────────────────────────
PHASE_START=$(ts)
echo "[5/5] Uploading..."
az login --identity --allow-no-subscriptions 2>/dev/null

# Upload index archive
az storage blob upload \
  --account-name "${STORAGE_ACCOUNT}" \
  --container-name "${CONTAINER_NAME}" \
  --name "${REPO_ID}.tar.gz" \
  --file "/tmp/${REPO_ID}.tar.gz" \
  --overwrite \
  --auth-mode login \
  --only-show-errors

# Build and upload profile
JOB_SEC=$(elapsed $JOB_START)
python3 -c "
import json
stats = json.load(open('/tmp/index_stats.json'))
profile = {
    'repo_id': '${REPO_ID}',
    'commit': '${COMMIT}',
    'timing': {
        'clone_sec': ${CLONE_SEC},
        'index_sec': ${INDEX_SEC},
        'pack_sec': ${PACK_SEC},
        'total_sec': ${JOB_SEC},
    },
    'repo': {
        'file_count': ${FILE_COUNT},
        'size': '${REPO_SIZE}',
    },
    'index': stats,
    'infra': {
        'cpus': $(nproc),
        'hostname': '$(hostname)',
    },
}
json.dump(profile, open('/tmp/profile.json', 'w'), indent=2)
print(json.dumps(profile['timing']))
"

az storage blob upload \
  --account-name "${STORAGE_ACCOUNT}" \
  --container-name "${CONTAINER_NAME}" \
  --name "profiles/${REPO_ID}.json" \
  --file "/tmp/profile.json" \
  --overwrite \
  --auth-mode login \
  --only-show-errors

UPLOAD_SEC=$(elapsed $PHASE_START)
echo "  Upload: ${UPLOAD_SEC}s"
echo "=== Done: ${REPO_ID} in ${JOB_SEC}s ==="
