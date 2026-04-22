# WSL / VS Code crash diagnosis — 2026-04-22

## TL;DR

**C: drive at 100% full (6.3 GB free of 953 GB) + 24 GB WSL memory cap + heavy pytest runs that hit 8–13 GB RSS = global OOM kills inside WSL AND a VS Code extension-host restart loop when its cache can't write to a full C: drive.**

Fix C: drive first, then tighten WSL memory config, then prune docker and venvs.

---

## Evidence

### Global OOM kills on Apr 21 (`/var/log/kern.log`)

| Time (EEST) | Killed process | RSS at death | Invoked by |
|---|---|---|---|
| 08:01 | `python3` pid 236031 | **13.27 GB** | `cpl` |
| 11:02 | `python3` pid 282723 | **8.69 GB** | `python3` (pytest) |
| 11:32 | `python3` pid 337965 | **10.75 GB** | `node` (VS Code) |

All three were `constraint=CONSTRAINT_NONE, global_oom` — the **entire WSL VM** ran out of memory, not a cgroup.

### WSL configuration

- `~/.wslconfig`: `memory=24GB` (no swap override)
- Inside WSL: 23 GiB total, 6 GiB swap (`/dev/sdc`)
- Currently (post-reboot) 2 GB used, 21 GB free — so the box is healthy right now; the crashes were event-driven, not baseline.

### C: drive (Windows host)

```
C:\  953G  947G  6.3G  100% /mnt/c
```

This is the real disaster. On WSL2:
- `ext4.vhdx` lives on C: and can't grow.
- Windows pagefile lives on C: and can't grow.
- VS Code extension host writes caches/logs to `%APPDATA%\Code` on C: → when writes fail, the host crashes and restarts → **this is the "every 5 seconds" crash feeling**.
- 133 `containerd` restart entries in syslog confirm docker/containerd flapping due to filesystem pressure.

### Docker bloat (inside the vhdx)

```
Images       65 total   30.09 GB   14.03 GB reclaimable
Volumes      17 total   12.16 GB   12.15 GB reclaimable
Build cache  462 layers 22.26 GB    4.16 GB reclaimable
```

Includes **four copies** of the 2.94 GB `vsc-evee-*` devcontainer image (~12 GB wasted), a stale `memgraph` container exited 8 days ago, and many 1–3 GB devcontainer images.

### Venvs on disk (`/home/dave01/...`)

| Path | Size |
|---|---|
| `wsl-repos/hypograph/.venv` | **5.8 GB** |
| `wsl-repos/coderecon/.venv` | 3.3 GB |
| `wsl-repos/coderecon/recon-lab/.venv` | 2.1 GB |
| `evee-demos/mlflow-evee-example/.venv` | 945 MB |
| `wsl-repos/codeplane/.venv` | 779 MB |
| `evee-demos/mlflow-example/.venv` | 761 MB |
| `.venv` (home) | 674 MB |
| (others) | ~2 GB combined |

These are ~16 GB on disk. More importantly, any loaded `torch`/`transformers`/`onnxruntime` from these venvs mmaps huge pages into RAM.

### VS Code extensions loaded

Three AI assistants active simultaneously:
- `openai.chatgpt` (codex)
- `ms-azuretools.vscode-azure-github-copilot`
- GitHub Copilot (implicit)

Plus Pylance, 5 Jupyter extensions, GitLens, full Azure bundle, containers extension.

Node processes total **~1.3 GB RSS** at idle, extension host at **800 MB after 4 minutes**.

---

## Root causes

1. **C: drive at 100%** → VS Code extension host can't persist state, restarts in a loop. Docker/containerd flap. WSL vhdx can't grow.
2. **WSL memory cap (24 GB) + heavy test suites** → pytest runs loading ML libs (13 GB RSS single process) trigger global OOM when the node/copilot/codex processes are also resident.
3. **Accumulated docker/venv bloat inside the vhdx** → wasted space that indirectly keeps C: under pressure because the vhdx never shrinks automatically.

---

## Fixes (in order)

### 1. Free C: drive — **do this first**

**Inside WSL**, reclaim docker space:
```bash
docker system prune -a --volumes -f
docker builder prune -a -f
```

**Optional: delete unused venvs** (will recreate with `uv sync` when needed):
```bash
rm -rf /home/dave01/wsl-repos/hypograph/.venv          # 5.8 GB
rm -rf /home/dave01/wsl-repos/codeplane-tmp-copy       # if not needed
rm -rf /home/dave01/evee-demos/*/.venv                 # if evee-demos idle
```

**In PowerShell on Windows**, compact the vhdx so the freed space returns to C::
```powershell
wsl --shutdown

# Find the vhdx
wsl -l -v
# Typical location:
#   C:\Users\<you>\AppData\Local\Packages\CanonicalGroupLimited.Ubuntu*\LocalState\ext4.vhdx

Optimize-VHD -Path "C:\Users\<you>\AppData\Local\Packages\CanonicalGroupLimited.Ubuntu*\LocalState\ext4.vhdx" -Mode Full
```

**Also on Windows**:
- Empty Recycle Bin.
- Run **Disk Cleanup → Clean up system files** (select Windows Update cleanup, previous Windows installations, delivery optimization files).
- Clear `%LOCALAPPDATA%\Temp`.
- Check `%LOCALAPPDATA%\Docker` if Docker Desktop is installed — it keeps its own data-root there.

**Target: at least 50 GB free on C:** before doing anything else.

### 2. Tighten WSL config

Edit `C:\Users\<you>\.wslconfig`:
```ini
[wsl2]
memory=20GB                  # leave more headroom for Windows
swap=16GB                    # larger swap = OOM less likely to kill interactive processes
swapFile=D:\\wsl-swap.vhdx   # if you have a D: with space; otherwise omit and keep on C:
processors=auto
```
Then `wsl --shutdown` to apply.

### 3. Stop pytest from OOMing the VM

The three OOMs all involved pytest/`cpl` Python processes reaching 8–13 GB RSS. Options:

- Run heavy test suites in a **separate terminal with copilot/codex extensions disabled in the window** — or better, in a headless shell (`wsl` outside VS Code).
- Add a per-test-process memory guard:
  ```bash
  # In the shell before pytest
  ulimit -v 8000000   # 8 GB virtual-memory cap per process
  ```
- Split the suite with `pytest -k` or markers so you don't load everything at once.
- Check what's importing `torch`/`transformers`/`onnxruntime` unconditionally at collection time — lazy-import those inside the tests that need them.

### 4. VS Code hygiene

- Disable extensions you aren't using **in this workspace** via the workspace recommendations (right-click extension → Disable Workspace):
  - Full Azure bundle if not doing Azure work.
  - Jupyter (5 extensions) if not using notebooks.
  - One of the three AI assistants — running Copilot + Copilot Chat + ChatGPT codex + Azure Copilot simultaneously is overkill and each is 200–500 MB.
- Don't open multi-root workspaces with 7 roots if you only need one or two — each root triggers its own file watcher and language-server indexing.

### 5. Ongoing maintenance

- Monthly: `docker system prune -a --volumes -f` + `Optimize-VHD`.
- Move large caches off C: if possible: Docker Desktop has a "Disk image location" setting; WSL distro can be moved via `wsl --export` / `wsl --import` to D: or another drive.
- Keep C: above **20% free** as a standing rule — Windows itself degrades badly below that.

---

## Verification after fixes

```bash
# Inside WSL
df -h /mnt/c                                  # should show >20% free
free -h                                       # should have >4 GB cached/buffers headroom
cat /proc/sys/vm/overcommit_memory            # ideally 0 (heuristic)
grep -c "oom-kill:" /var/log/kern.log         # should stay at 3 and not grow
docker system df                              # much smaller numbers
```

```powershell
# On Windows
Get-PSDrive C                                 # >50 GB free ideally
Get-Item C:\...\ext4.vhdx | Select Length     # should shrink significantly after Optimize-VHD
```
