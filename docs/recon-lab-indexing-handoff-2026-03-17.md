# Recon-Lab Indexing Handoff (2026-03-17)

## Purpose

This experiment is trying to produce a clean ranking-training dataset
and held-out evaluation run for a fixed 12-repo subset in recon-lab.

The intended pipeline is:

1. Index the selected repos.
2. Generate ground truth.
3. Collect signals.
4. Merge training data.
5. Train all models.
6. Run held-out eval on the 2 eval repos.

This handoff is meant to tell the next agent where that pipeline stands,
what artifacts already exist, and what should happen next.

Current checkpoint:

- Indexing is complete for the selected 12 repos.
- Fresh local `.recon/index.db` files exist for all 12 selected repos.
- GT generation, collect, merge, training, and eval were not resumed in
  this session.

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

## Experiment Goal

Produce a clean end-to-end recon-lab dataset and evaluation run for this
exact subset:

- Train: 7 repos
- Cutoff: 3 repos
- Eval: 2 repos

The main output we needed from this session was not Azure
infrastructure. It was a clean indexing checkpoint that unblocks the
rest of the lab pipeline.

That checkpoint now exists locally.

## What Actually Mattered

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

### 4. The remote run was only an execution detail

The point of the Azure run was simply to produce the 12 missing local
indexes when local execution had become unreliable.

What mattered operationally:

1. Run indexing remotely for the selected repos.
2. Bring the resulting `.recon` artifacts back to the canonical local
   lab workspace.
3. Delete the temporary cloud resources afterward.

That is complete.

## Azure Execution Strategy

Keep this section short: it exists only so a future agent knows what was
done if remote indexing is needed again.

### Summary

- We used a temporary Azure VM because the local indexing path had
  become unreliable during this session.
- The working shape was a 16 vCPU / 64 GiB Ubuntu VM, sized to run 3
  repo indexes concurrently with headroom.
- The VM was used only to produce `.recon` artifacts for the selected 12
  repos.
- Once the indexes were back on local disk, the Azure resource group was
  deleted.

### Practical Notes

- `Standard_D16as_v5` in `eastus` was unavailable at runtime, so the VM
  ended up in `eastus2`.
- Direct SSH restore proved flaky, so the successful path was to relay a
  packed archive of all `.recon` outputs through Blob storage and then
  restore locally.
- This cloud path is not part of the experiment output. It was only a
  means to get the local indexing checkpoint into the desired state.

## Produced Artifact

The important output from this session is:

- 12 fresh local repo indexes under the canonical lab workspace
- no remaining temporary Azure infrastructure

If a future agent has to repeat remote indexing, the essential pattern
is simple:

1. Produce the repo-local `.recon` directories remotely.
2. Return them to `/home/dave01/.recon/recon-lab/clones/.../.recon`.
3. Tear the cloud resources down.

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
local indexing checkpoint was restored.

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
- The experiment is ready to continue from GT generation onward.
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
- Do not let infrastructure details dominate the handoff. The real goal
  is to produce the lab artifacts that unblock generate, collect, merge,
  train, and eval.