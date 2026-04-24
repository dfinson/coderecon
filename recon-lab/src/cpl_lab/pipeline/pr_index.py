"""PR index — register main repos and index worktrees via SDK.

Pass 2: Register + index all main repos via CodeReconDev SDK.
Pass 3: For each PR worktree, register with the SDK.  The daemon detects
        which files differ from main and incrementally reindexes only the
        diff into the shared ``index.db`` using the worktree's
        ``worktree_id``.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

import click

from cpl_lab.pipeline.clone import REPO_MANIFEST, REPO_SETS, clone_dir_for


# ---------------------------------------------------------------------------
# Crash-proof telemetry — writes to disk with immediate flush so data
# survives an OOM-kill or WSL crash.
# ---------------------------------------------------------------------------

class IndexTelemetry:
    """Append-only telemetry log that flushes every write."""

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a", buffering=1)  # line-buffered
        self._lock = threading.Lock()
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self.log("telemetry_start", {
            "pid": os.getpid(),
            **self._sys_mem(),
        })

    def log(self, event: str, data: dict | None = None) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_s": round(time.monotonic() - _T0, 1),
            "event": event,
            **(data or {}),
        }
        line = json.dumps(entry, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def start_monitor(self, interval: float = 5.0) -> None:
        """Background thread logging system memory every N seconds."""
        def _run() -> None:
            while not self._monitor_stop.wait(interval):
                mem = self._sys_mem()
                children = self._child_procs()
                self.log("heartbeat", {**mem, "children": children})
        self._monitor_thread = threading.Thread(target=_run, daemon=True)
        self._monitor_thread.start()

    def stop_monitor(self) -> None:
        self._monitor_stop.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)

    def close(self) -> None:
        self.stop_monitor()
        self.log("telemetry_end", self._sys_mem())
        self._fh.close()

    @staticmethod
    def _sys_mem() -> dict:
        """Read /proc/meminfo — always available on Linux/WSL."""
        info: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].rstrip(":") in (
                    "MemTotal", "MemAvailable", "MemFree", "Buffers",
                    "Cached", "SwapTotal", "SwapFree",
                ):
                    info[parts[0].rstrip(":")] = int(parts[1])  # kB
        except Exception:
            pass
        return {
            "mem_total_mb": info.get("MemTotal", 0) // 1024,
            "mem_avail_mb": info.get("MemAvailable", 0) // 1024,
            "mem_free_mb": info.get("MemFree", 0) // 1024,
            "swap_total_mb": info.get("SwapTotal", 0) // 1024,
            "swap_free_mb": info.get("SwapFree", 0) // 1024,
        }

    @staticmethod
    def _child_procs() -> list[dict]:
        """Snapshot RSS of all child processes via /proc."""
        children = []
        my_pid = os.getpid()
        try:
            for pid_dir in Path("/proc").iterdir():
                if not pid_dir.name.isdigit():
                    continue
                try:
                    stat = (pid_dir / "stat").read_text().split()
                    ppid = int(stat[3])
                    if ppid != my_pid:
                        continue
                    rss_pages = int(stat[23])
                    rss_mb = (rss_pages * 4096) // (1024 * 1024)
                    cmdline = (pid_dir / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace").strip()
                    children.append({
                        "pid": int(pid_dir.name),
                        "rss_mb": rss_mb,
                        "cmd": cmdline[:120],
                    })
                except (OSError, ValueError, IndexError):
                    continue
        except OSError:
            pass
        return children


_T0 = time.monotonic()
_telemetry: IndexTelemetry | None = None


_MEM_GATE_MB = 3000      # Don't start a new register if MemAvailable < 3 GB


def _wait_for_memory(repo_name: str, done: int, total: int) -> int:
    """Block until system memory is above gate threshold. Returns avail MB."""
    while True:
        mem = IndexTelemetry._sys_mem()
        avail = mem.get("mem_avail_mb", 99999)
        if avail >= _MEM_GATE_MB:
            return avail
        if _telemetry:
            _telemetry.log("mem_gate_wait", {
                "repo": repo_name,
                "mem_avail_mb": avail,
                "threshold_mb": _MEM_GATE_MB,
            })
        click.echo(f"  [{done}/{total}] {repo_name}: waiting for memory ({avail}MB < {_MEM_GATE_MB}MB)...")
        time.sleep(10)


def run_index_main(
    clones_dir: Path,
    repo_set: str = "all",
    repo: str | None = None,
    verbose: bool = False,
) -> None:
    """Pass 2: Register and index all main repos via SDK."""
    global _T0, _telemetry
    _T0 = time.monotonic()

    # Set up crash-proof telemetry
    log_dir = Path("~/.recon/recon-lab/data").expanduser()
    log_path = log_dir / f"index_telemetry_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    _telemetry = IndexTelemetry(log_path)
    _telemetry.start_monitor(interval=5.0)
    click.echo(f"Telemetry → {log_path}")

    try:
        asyncio.run(_run_index_main_inner(clones_dir, repo_set, repo, verbose))
    finally:
        _telemetry.close()
        _telemetry = None


async def _run_index_main_inner(
    clones_dir: Path,
    repo_set: str,
    repo: str | None,
    verbose: bool,
) -> None:
    """Actual index-main logic (wrapped by telemetry). Uses SDK."""
    from coderecon.sdk.dev import CodeReconDev

    from cpl_lab.config import recon_binary
    recon_bin = recon_binary()

    if repo:
        repo_ids = [repo]
    else:
        allowed = set(REPO_SETS.keys()) if repo_set == "all" else {repo_set}
        repo_ids = [
            rid for rid, info in REPO_MANIFEST.items()
            if info["set"] in allowed
        ]

    # Resolve clone dirs, skip missing
    work: list[tuple[str, Path]] = []
    for rid in sorted(repo_ids):
        cd = clone_dir_for(rid, clones_dir)
        if cd is None or not cd.is_dir():
            click.echo(f"  {rid}: SKIP (no clone)")
        else:
            work.append((rid, cd))

    click.echo(f"\nRegistering + indexing {len(work)} main repos via SDK (serial, memory-gated)...")

    async with CodeReconDev(binary=str(recon_bin)) as sdk:
        done = 0
        failed: list[str] = []
        for rid, cd in work:
            done += 1
            avail = _wait_for_memory(rid, done, len(work))
            click.echo(f"  [{done}/{len(work)}] {rid}: registering (avail={avail}MB)...")

            t0 = time.monotonic()
            if _telemetry:
                _telemetry.log("register_start", {
                    "repo": rid,
                    "path": str(cd),
                    **IndexTelemetry._sys_mem(),
                })

            try:
                reg = await sdk.register(str(cd))
                click.echo(f"  [{done}/{len(work)}] {rid}: registered as {reg.repo}, reindexing...")
                await sdk.reindex(reg.repo)
                elapsed = round(time.monotonic() - t0, 1)
                click.echo(f"  [{done}/{len(work)}] {rid}: OK ({elapsed}s)")
                if _telemetry:
                    _telemetry.log("register_end", {
                        "repo": rid,
                        "elapsed_s": elapsed,
                        "ok": True,
                        **IndexTelemetry._sys_mem(),
                    })
            except Exception as exc:
                elapsed = round(time.monotonic() - t0, 1)
                failed.append(rid)
                click.echo(f"  [{done}/{len(work)}] {rid}: FAILED ({elapsed}s) — {exc}")
                if _telemetry:
                    _telemetry.log("register_end", {
                        "repo": rid,
                        "elapsed_s": elapsed,
                        "ok": False,
                        "error": str(exc)[:300],
                        **IndexTelemetry._sys_mem(),
                    })

        if failed:
            click.echo(f"\n{len(failed)} repos failed: {', '.join(failed)}")

    click.echo("All main repos indexed.")


def run_index_worktrees(
    clones_dir: Path,
    data_dir: Path,
    instances_dir: Path | None = None,
    repo_set: str = "all",
    repo: str | None = None,
    verbose: bool = False,
) -> None:
    """Pass 3: Register PR worktrees via SDK for incremental reindex.

    Each worktree is registered via ``sdk.register(path)``, which adds it
    to the catalog.  The daemon detects files that differ from main and
    reindexes only those into the shared ``index.db`` under the worktree's
    ``worktree_id``.
    """
    if instances_dir is None:
        instances_dir = clones_dir / "instances"

    # Load PR instances
    pr_file = data_dir / "pr_instances.jsonl"
    if not pr_file.exists():
        raise click.ClickException(f"PR instances file not found: {pr_file}")

    # Group instances by repo
    by_repo: dict[str, list[dict]] = {}
    for line in pr_file.read_text().splitlines():
        if not line.strip():
            continue
        inst = json.loads(line)
        if repo and inst["repo_id"] != repo:
            continue
        if repo_set != "all" and inst.get("repo_set") != repo_set:
            continue
        by_repo.setdefault(inst["repo_id"], []).append(inst)

    total_wt = sum(len(insts) for insts in by_repo.values())
    click.echo(f"Registering {total_wt} worktrees across {len(by_repo)} repos via SDK...")

    asyncio.run(_run_index_worktrees_inner(
        by_repo, instances_dir, total_wt, verbose,
    ))


async def _run_index_worktrees_inner(
    by_repo: dict[str, list[dict]],
    instances_dir: Path,
    total_wt: int,
    verbose: bool,
) -> None:
    from coderecon.sdk.dev import CodeReconDev

    from cpl_lab.config import recon_binary

    done = ok = fail = 0

    async with CodeReconDev(binary=recon_binary()) as sdk:
        for rid in sorted(by_repo):
            insts = by_repo[rid]
            click.echo(f"\n  {rid}: {len(insts)} worktrees")

            for inst in insts:
                iid = inst["instance_id"]
                wt_dir = instances_dir / iid
                done += 1

                if not (wt_dir / ".git").exists():
                    click.echo(f"    {iid}: SKIP (worktree not created)")
                    fail += 1
                    continue

                if verbose or done % 10 == 0:
                    click.echo(f"    [{done}/{total_wt}] registering {iid}...")

                try:
                    await sdk.register(str(wt_dir))
                    ok += 1
                except Exception as exc:
                    click.echo(f"    {iid}: FAILED — {exc}")
                    fail += 1

    click.echo(f"\nDone: {ok} registered, {fail} failed")
