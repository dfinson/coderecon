"""PR index — register main repos with daemon, index worktrees.

Pass 2: Start daemon, register + index all main repos.
Pass 3: For each PR worktree, register with the daemon via
        ``recon register-worktree``.  The daemon detects which files differ
        from main and incrementally reindexes only the diff into the shared
        ``index.db`` using the worktree's ``worktree_id``.  No standalone
        ``recon init`` is needed — unchanged files fall through to the main
        worktree's entries via overlay queries.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
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


def _recon_python() -> str:
    """Find the Python interpreter that can run the coderecon CLI."""
    coderecon_root = Path(__file__).resolve().parents[4]
    venv = coderecon_root / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return sys.executable


def _recon_cmd(*args: str) -> list[str]:
    """Build a coderecon CLI command."""
    return [_recon_python(), "-m", "coderecon.cli.main", *args]


def _run(cmd: list[str], *, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)


def _daemon_running() -> tuple[bool, int | None]:
    """Check if the global daemon is running.  Returns (running, port)."""
    pid_file = Path.home() / ".coderecon" / "daemon.pid"
    port_file = Path.home() / ".coderecon" / "daemon.port"
    if not pid_file.exists() or not port_file.exists():
        return False, None
    try:
        pid = int(pid_file.read_text().strip())
        port = int(port_file.read_text().strip())
        # Check if process is alive
        import os
        os.kill(pid, 0)
        return True, port
    except (ValueError, OSError):
        return False, None


def _wait_for_daemon(timeout: int = 30) -> int:
    """Wait for daemon to be reachable.  Returns port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        running, port = _daemon_running()
        if running and port:
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/health",
                    headers={"Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        return port
            except Exception:
                pass
        time.sleep(1)
    raise click.ClickException("Daemon did not start within timeout")


def _start_daemon() -> int:
    """Ensure the daemon is running.  Returns port."""
    running, port = _daemon_running()
    if running and port:
        click.echo(f"Daemon already running on port {port}")
        return port

    click.echo("Starting daemon...")
    # Start in background
    subprocess.Popen(
        _recon_cmd("up"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    port = _wait_for_daemon()
    click.echo(f"Daemon running on port {port}")
    return port


_MEM_GATE_MB = 3000      # Don't start a new register if MemAvailable < 3 GB
_MEM_CRITICAL_MB = 800   # Kill subprocess tree if MemAvailable drops below this
_MEM_POLL_SEC = 3        # Watchdog poll interval


def _kill_process_tree(pid: int) -> None:
    """Kill a process and all its descendants (grandchildren etc)."""
    try:
        import psutil
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
    except Exception:
        # Fallback: try process group kill
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _register_repo_with_watchdog(repo_path: Path) -> tuple[int, str]:
    """Register a repo with an active memory watchdog.

    Runs `recon register` as a subprocess in its own process group.
    A watchdog thread monitors system memory every few seconds and
    kills the entire process tree if memory drops below critical.

    Returns (returncode, stderr_tail).
    """
    repo_name = repo_path.name
    t0 = time.monotonic()
    mem_before = IndexTelemetry._sys_mem()

    if _telemetry:
        _telemetry.log("register_start", {
            "repo": repo_name,
            "path": str(repo_path),
            **mem_before,
        })

    env = {
        **os.environ,
        "CODERECON_INDEX_WORKERS": "2",
        # Cap JVM heap for test execution (Java repos)
        "JAVA_TOOL_OPTIONS": "-Xmx256m",
        # Memory-aware test execution (new MemoryBudget system)
        "CODEPLANE__TESTING__MEMORY_RESERVE_MB": "2000",
        "CODEPLANE__TESTING__SUBPROCESS_MEMORY_LIMIT_MB": "1024",
        "CODEPLANE__TESTING__DEFAULT_PARALLELISM": "2",
    }
    cmd = _recon_cmd("register", str(repo_path))

    killed_by_watchdog = False

    # Start subprocess in its own process group so we can kill the tree
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,  # new process group
    )

    # Watchdog: monitor memory and kill if critical
    stop_watchdog = threading.Event()

    def _watchdog() -> None:
        nonlocal killed_by_watchdog
        while not stop_watchdog.wait(_MEM_POLL_SEC):
            if proc.poll() is not None:
                break  # process already exited
            mem = IndexTelemetry._sys_mem()
            avail = mem.get("mem_avail_mb", 99999)
            if avail < _MEM_CRITICAL_MB:
                killed_by_watchdog = True
                if _telemetry:
                    _telemetry.log("watchdog_kill", {
                        "repo": repo_name,
                        "mem_avail_mb": avail,
                        "threshold_mb": _MEM_CRITICAL_MB,
                        "pid": proc.pid,
                    })
                click.echo(f"    WATCHDOG: Killing {repo_name} (avail={avail}MB < {_MEM_CRITICAL_MB}MB)")
                _kill_process_tree(proc.pid)
                break

    watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
    watchdog_thread.start()

    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=1800)
        stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc.pid)
        proc.wait()
        stdout, stderr = "", "Timed out after 1800s"
    finally:
        stop_watchdog.set()
        watchdog_thread.join(timeout=2)

    elapsed = round(time.monotonic() - t0, 1)
    mem_after = IndexTelemetry._sys_mem()
    rc = proc.returncode if proc.returncode is not None else -1

    if _telemetry:
        _telemetry.log("register_end", {
            "repo": repo_name,
            "rc": rc,
            "elapsed_s": elapsed,
            "killed_by_watchdog": killed_by_watchdog,
            "stderr_tail": stderr[-300:] if stderr else "",
            **mem_after,
        })

    if killed_by_watchdog:
        click.echo(f"    {repo_name}: KILLED by memory watchdog after {elapsed}s")
    elif rc != 0:
        real_errors = [
            line for line in stderr.splitlines()
            if line and "RuntimeWarning" not in line
        ]
        if real_errors:
            click.echo(f"    Register failed (rc={rc}): {real_errors[0][:200]}")
        else:
            click.echo(f"    Register completed with warnings (rc={rc})")

    return rc, stderr


def _register_worktree(wt_path: Path) -> bool:
    """Register a worktree via CLI.  Returns True on success."""
    result = _run(_recon_cmd("register-worktree", str(wt_path)), check=False, timeout=60)
    if result.returncode != 0:
        click.echo(f"    Worktree register failed: {result.stderr[:200]}")
        return False
    return True


def _wait_repo_idle(port: int, repo_name: str, timeout: int = 600) -> bool:
    """Poll daemon until a repo's indexer is idle."""
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/repos/{repo_name}/status",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                indexer = data.get("indexer", {})
                state = indexer.get("state", "unknown")
                queue = indexer.get("queue_size", 0)
                if state == "idle" and queue == 0:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def run_index_main(
    clones_dir: Path,
    repo_set: str = "all",
    repo: str | None = None,
    verbose: bool = False,
) -> None:
    """Pass 2: Start daemon and index all main repos."""
    global _T0, _telemetry
    _T0 = time.monotonic()

    # Set up crash-proof telemetry
    log_dir = Path("~/.recon/recon-lab/data").expanduser()
    log_path = log_dir / f"index_telemetry_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    _telemetry = IndexTelemetry(log_path)
    _telemetry.start_monitor(interval=5.0)
    click.echo(f"Telemetry → {log_path}")

    try:
        _run_index_main_inner(clones_dir, repo_set, repo, verbose)
    finally:
        _telemetry.close()
        _telemetry = None


def _run_index_main_inner(
    clones_dir: Path,
    repo_set: str,
    repo: str | None,
    verbose: bool,
) -> None:
    """Actual index-main logic (wrapped by telemetry)."""
    try:
        port = _start_daemon()
    except click.ClickException:
        click.echo("WARNING: Daemon failed to start — indexing will still run via register CLI")
        port = None

    if repo:
        repo_ids = [repo]
    else:
        allowed = set(REPO_SETS.keys()) if repo_set == "all" else {repo_set}
        repo_ids = [
            rid for rid, info in REPO_MANIFEST.items()
            if info["set"] in allowed
        ]

    # Resolve clone dirs, skip missing or already-indexed
    work: list[tuple[str, Path]] = []
    skipped_done = 0
    for rid in sorted(repo_ids):
        cd = clone_dir_for(rid, clones_dir)
        if cd is None or not cd.is_dir():
            click.echo(f"  {rid}: SKIP (no clone)")
        elif (cd / ".recon" / "index.db").is_file():
            skipped_done += 1
        else:
            work.append((rid, cd))

    if skipped_done:
        click.echo(f"  Skipped {skipped_done} repos with existing index.db")

    click.echo(f"\nRegistering {len(work)} main repos (SERIAL, memory-gated, watchdog-protected)...")

    done = 0
    failed: list[str] = []
    watchdog_killed: list[str] = []
    for rid, cd in work:
        done += 1

        # Memory gate: wait for headroom before starting
        while True:
            mem = IndexTelemetry._sys_mem()
            avail = mem.get("mem_avail_mb", 99999)
            if avail >= _MEM_GATE_MB:
                break
            if _telemetry:
                _telemetry.log("mem_gate_wait", {
                    "repo": cd.name,
                    "mem_avail_mb": avail,
                    "threshold_mb": _MEM_GATE_MB,
                })
            click.echo(f"  [{done}/{len(work)}] {rid}: waiting for memory ({avail}MB < {_MEM_GATE_MB}MB)...")
            time.sleep(10)

        click.echo(f"  [{done}/{len(work)}] {rid}: registering (avail={avail}MB)...")
        rc, stderr = _register_repo_with_watchdog(cd)

        if rc == 0:
            click.echo(f"  [{done}/{len(work)}] {rid}: OK")
        elif rc == -9 or "KILLED" in stderr[:100]:
            watchdog_killed.append(rid)
            click.echo(f"  [{done}/{len(work)}] {rid}: watchdog-killed, will retry later")
        else:
            failed.append(rid)
            click.echo(f"  [{done}/{len(work)}] {rid}: FAILED (rc={rc})")

    # Retry watchdog-killed repos one more time (memory should be free now)
    if watchdog_killed:
        click.echo(f"\nRetrying {len(watchdog_killed)} watchdog-killed repos...")
        for rid in watchdog_killed:
            cd = next(cd for r, cd in work if r == rid)
            click.echo(f"  Retrying {rid}...")
            # Wait for memory to settle
            time.sleep(15)
            rc, _ = _register_repo_with_watchdog(cd)
            if rc == 0:
                click.echo(f"  {rid}: OK on retry")
            else:
                click.echo(f"  {rid}: FAILED on retry (rc={rc})")
                failed.append(rid)

    if failed:
        click.echo(f"\n{len(failed)} repos failed: {', '.join(failed)}")

    # Wait for all repos to finish indexing (only if daemon is running)
    if port:
        click.echo("\nWaiting for indexing to complete...")
        for i, (rid, cd) in enumerate(work, 1):
            repo_name = cd.name
            if verbose:
                click.echo(f"  [{i}/{len(work)}] Waiting for {repo_name}...")
            if not _wait_repo_idle(port, repo_name, timeout=1800):
                click.echo(f"  WARNING: {repo_name} did not finish indexing in 30min")
    else:
        click.echo("\nDaemon not available — register CLI handles indexing synchronously.")

    click.echo("All main repos indexed.")


def run_index_worktrees(
    clones_dir: Path,
    data_dir: Path,
    instances_dir: Path | None = None,
    repo_set: str = "all",
    repo: str | None = None,
    verbose: bool = False,
) -> None:
    """Pass 3: Register PR worktrees with the daemon for incremental reindex.

    Each worktree is registered via ``recon register-worktree``, which adds it
    to the catalog and tells the daemon to activate it.  The daemon detects
    files that differ from main (``git diff main...HEAD``) and reindexes only
    those into the shared ``index.db`` under the worktree's ``worktree_id``.
    Unchanged files fall through to main's entries via overlay queries.
    """
    running, port = _daemon_running()
    if not running or not port:
        raise click.ClickException("Daemon not running. Run index-main first.")

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
    click.echo(f"Registering {total_wt} worktrees across {len(by_repo)} repos...")

    done = ok = skip = fail = 0
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

            if _register_worktree(wt_dir):
                ok += 1
            else:
                fail += 1

    # With lazy repo activation the daemon doesn't eagerly index worktrees.
    # Registration only creates catalog entries — actual indexing happens on
    # the first MCP request.  No need to wait for idle.

    click.echo(f"\nDone: {ok} registered, {skip} skipped, {fail} failed")
