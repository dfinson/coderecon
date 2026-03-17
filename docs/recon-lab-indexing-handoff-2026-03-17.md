# Recon-Lab Indexing Handoff (2026-03-17)

## Purpose

This document captures the current recon-lab experiment state so a
future agent can resume work without re-deriving the setup, repo subset,
 Azure strategy, or the failure modes we already hit.

The run completed the indexing stage for the selected 12 repos and left
fresh `.recon/index.db` files on disk locally. Ground-truth generation,
signal collection, merge, training, and eval were not resumed in this
session.

## Canonical Local Paths

- CodeRecon repo: `/home/dave01/wsl-repos/coderecon`
- Canonical lab workspace: `/home/dave01/.recon/recon-lab`
- Lab clones root: `/home/dave01/.recon/recon-lab/clones`
- Lab data root: `/home/dave01/.recon/recon-lab/data`

Important:

- The old `/home/dave01/.codeplane` lab tree had already been archived or
  removed before this run.
- The selected repos started from a clean state with no `.recon` index
  directories.
- Any partial `.recon` state from failed local indexing attempts was
  explicitly deleted before the successful remote run.

## Selected Repo Subset

### Train

- `python-fastapi` -> `/home/dave01/.recon/recon-lab/clones/ranker-gate/fastapi`
- `rust-ripgrep` -> `/home/dave01/.recon/recon-lab/clones/ranker-gate/ripgrep`
- `typescript-mermaid` -> `/home/dave01/.recon/recon-lab/clones/ranker-gate/mermaid`
- `php-composer` -> `/home/dave01/.recon/recon-lab/clones/ranker-gate/composer`
- `go-caddy` -> `/home/dave01/.recon/recon-lab/clones/ranker-gate/caddy`
- `csharp-newtonsoft-json` -> `/home/dave01/.recon/recon-lab/clones/ranker-gate/Newtonsoft.Json`
- `java-gson` -> `/home/dave01/.recon/recon-lab/clones/ranker-gate/gson`

### Cutoff

- `python-flask` -> `/home/dave01/.recon/recon-lab/clones/cutoff/flask`
- `rust-reqwest` -> `/home/dave01/.recon/recon-lab/clones/cutoff/reqwest`
- `cpp-abseil` -> `/home/dave01/.recon/recon-lab/clones/cutoff/abseil-cpp`

### Eval

- `python-pydantic` -> `/home/dave01/.recon/recon-lab/clones/eval/pydantic`
- `typescript-vitest` -> `/home/dave01/.recon/recon-lab/clones/eval/vitest`

## What We Learned Before the Successful Run

### 1. The requested `cpl` binary was not the repo indexer

The binary at:

- `/home/dave01/wsl-repos/codeplane/.venv/bin/cpl`

did not perform repository indexing for this lab. Its `init` path was not
the one that produces `.recon/index.db` under a target repo. Using it for
`cpl init -r <repo>` was therefore a dead end for this experiment.

### 2. The effective indexer was CodeRecon's CLI

The indexing command that actually produced repo-local `.recon` state was
the CodeRecon CLI, invoked from the CodeRecon environment.

Effective form:

```bash
PYTHONPATH=/home/dave01/wsl-repos/coderecon/src \
  /home/dave01/wsl-repos/coderecon/.venv/bin/python \
  -m coderecon.cli.main init -r <repo_path>
```

### 3. `recon init` initially failed non-interactively because of model bootstrap

The first remote indexing attempt failed because the CLI hit an
interactive model-download confirmation for embeddings. The fix was to
pre-prime the embedding model cache using the non-interactive helper path
before rerunning the batch.

Practical implication for future agents:

- If indexing is being run in a fresh environment, expect an embedding
  model bootstrap step.
- Prime that cache before launching a large unattended batch.

### 4. Direct SSH copy-back was unreliable from this session

Pushing source repos to the VM worked only intermittently over repeated
`rsync` attempts, and pulling `.recon` directories back over SSH proved
too unreliable to use as the final artifact path.

The stable fallback was:

1. Build all remote indexes on the VM.
2. Pack the remote `.recon` trees into one `.tar.zst` archive.
3. Upload that archive to Azure Blob Storage from the VM.
4. Download the archive locally.
5. Decompress and extract it into the local clone tree.

## Azure Compute Strategy Used

This is the strategy that actually worked, not just the initial plan.

### Resource Layout

- Resource group: `rg-reconlab-index-f6b523`
- VM: `recon-indexer-f6b523`
- Storage account: `stidxf6b523`
- Temporary transfer container: `transfer`

### Region Choice

- The original plan was to use a `Standard_D16as_v5` Ubuntu VM in
  `eastus`.
- That SKU was unavailable in `eastus` at provisioning time.
- The VM was instead created in `eastus2`.
- The storage account remained in `eastus`.

### Why This VM Shape

We targeted a machine capable of handling 3 concurrent index jobs with
headroom for the largest repos in the selected set.

Reasoning used during the run:

- The repo set is skewed heavily by `fastapi`, with `vitest`,
  `composer`, `Newtonsoft.Json`, `pydantic`, and `abseil-cpp` as the
  heavier follow-ons.
- Earlier successful remote jobs showed peak resident memory roughly in
  the high-single-digit GiB range per process and several effective CPUs
  per active indexer.
- A 16 vCPU / 64 GiB class machine was a conservative 3-way-parallel
  choice.

### Concurrency Strategy

- Remote indexing ran with 3 workers in parallel.
- This was enough to keep the VM busy without immediately running into
  obvious memory pressure.

### Transfer Strategy That Ultimately Worked

Direct SSH restore was abandoned in favor of Blob relay.

Working transfer sequence:

1. Build `/home/dave01/recon-indexes.tar.zst` on the VM from all 12
   remote `.recon` directories.
2. Grant the VM managed identity temporary Blob data-plane roles on the
   storage account.
3. Wait for RBAC propagation.
4. Use `azcopy login --identity` on the VM.
5. Upload the archive to:
   `https://stidxf6b523.blob.core.windows.net/transfer/recon-indexes.tar.zst`
6. Grant the signed-in user temporary Blob Data Reader on the storage
   account.
7. Download the archive locally with Azure AD auth.
8. Decompress it locally and extract into the clone tree.

Important details:

- SAS-based uploads were attempted multiple ways and failed with either
  `403` or `AuthorizationPermissionMismatch`.
- Managed-identity `azcopy` upload worked only after assigning the VM
  `Storage Blob Data Contributor`, then `Storage Blob Data Owner`, and
  allowing time for RBAC propagation.
- Local Azure CLI download initially timed out near completion with the
  default downloader settings. Retrying with `--max-connections 1`
  succeeded.

## Remote Artifact Handling

### Archive produced on VM

- `/home/dave01/recon-indexes.tar.zst`

Observed archive size:

- Approximately `362M` on the VM
- Downloaded blob length: `378582169` bytes

### Local extraction method used

The local machine did not have a system `zstd` binary available. The
archive was restored by:

1. Installing Python `zstandard` in the user environment.
2. Streaming decompression into `tar`.

Effective form:

```bash
python3 -c 'import sys, zstandard; d=zstandard.ZstdDecompressor(); f=open("/home/dave01/.recon/recon-lab/recon-indexes.tar.zst", "rb"); d.copy_stream(f, sys.stdout.buffer)' \
  | tar -xf - -C /home/dave01/.recon/recon-lab/clones
```

## Final Local Index State

All 12 selected repos ended with local `.recon/index.db` files present.

Verified sizes at the end of the run:

- `fastapi` -> `1.7M`
- `ripgrep` -> `24M`
- `mermaid` -> `46M`
- `composer` -> `18M`
- `caddy` -> `37M`
- `Newtonsoft.Json` -> `188M`
- `gson` -> `1.7M`
- `flask` -> `8.8M`
- `reqwest` -> `18M`
- `abseil-cpp` -> `132M`
- `pydantic` -> `106M`
- `vitest` -> `112M`

These indexes now live under the corresponding local clone paths in:

- `/home/dave01/.recon/recon-lab/clones/.../.recon/index.db`

## Azure Cleanup Status

The temporary Azure infrastructure from this run was removed after the
indexes were restored locally.

Deleted:

- Resource group `rg-reconlab-index-f6b523`
- VM `recon-indexer-f6b523`
- NSG, NIC, public IP, disks, and the temporary storage account inside
  that resource group

Final verification used:

```bash
az group exists --name rg-reconlab-index-f6b523
```

and returned `false`.

## Current Experiment State

As of this handoff, the experiment state is:

- Canonical lab workspace is `/home/dave01/.recon/recon-lab`.
- The 12 selected repos have local indexes on disk.
- The Azure indexing infrastructure used for this run has been torn
  down.
- GT generation, collect, merge, train, and eval still remain to be run
  after this indexing stage.

## Recommended Next Steps For The Next Agent

1. Re-verify the 12 local `.recon/index.db` files before doing any lab
   pipeline work.
2. Re-check whether GT generation is still blocked in the current local
   environment.
3. Continue with generate only for the selected 12 repos.
4. Continue with collect only for the selected 12 repos.
5. Run merge.
6. Train all models.
7. Run eval using only the held-out eval repos:
   `pydantic` and `vitest`.

## Things A Future Agent Should Not Repeat

- Do not assume `/home/dave01/wsl-repos/codeplane/.venv/bin/cpl init -r` is
  the right repo indexer for this experiment.
- Do not assume direct SSH restore from the VM will be reliable enough
  for the final artifact path.
- Do not start a large unattended indexing batch in a fresh environment
  without pre-priming the embedding model cache.
- Do not forget that `Standard_D16as_v5` may be unavailable in `eastus`;
  be ready to place the VM in `eastus2`.