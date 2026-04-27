"""Runner packs package."""

from __future__ import annotations

from pathlib import Path

from coderecon.index._internal.ignore import PRUNABLE_DIRS

def _is_prunable_path(
    path: Path,
    workspace_root: Path,
    *,
    allowed_dirs: frozenset[str] | None = None,
) -> bool:
    """Check if path contains any prunable directory components.

    Args:
        path: Path to check
        workspace_root: Root directory for relative path calculation
        allowed_dirs: Optional set of directories that should be allowed
            even if they appear in PRUNABLE_DIRS (e.g., 'pkg' for Go)
    """
    try:
        rel = path.relative_to(workspace_root)
        for part in rel.parts:
            if part in PRUNABLE_DIRS:
                if allowed_dirs and part in allowed_dirs:
                    continue
                return True
        return False
    except ValueError:
        return True


async def _discover_dart_tests(
    pack: object,
    workspace_root: Path,
) -> list:
    """Discover dart/flutter test files in a workspace.

    Shared by DartTestPack and FlutterTestPack.
    """
    from coderecon.testing.models import TestTarget

    targets: list[TestTarget] = []
    for path in workspace_root.glob("test/**/*_test.dart"):
        if _is_prunable_path(path, workspace_root):
            continue
        rel = str(path.relative_to(workspace_root))
        targets.append(
            TestTarget(
                target_id=f"test:{rel}",
                selector=rel,
                kind="file",
                language="dart",
                runner_pack_id=pack.pack_id,  # type: ignore[attr-defined]
                workspace_root=str(workspace_root),
            )
        )
    return targets


# Import tiers to register packs
from coderecon.testing.packs import tier1 as _tier1  # noqa: F401, E402
from coderecon.testing.packs import tier1_compiled as _tier1c  # noqa: F401, E402
from coderecon.testing.packs import tier1_other as _tier1o  # noqa: F401, E402
from coderecon.testing.packs import tier2 as _tier2  # noqa: F401, E402
from coderecon.testing.packs import tier2_scripting as _tier2_scripting  # noqa: F401, E402
from coderecon.testing.packs import tier2_functional as _tier2_functional  # noqa: F401, E402
