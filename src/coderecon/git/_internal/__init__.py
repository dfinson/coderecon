"""Internal components for git operations - not part of public API."""

from coderecon.git._internal.access import RepoAccess
from coderecon.git._internal.flows import WriteFlows
from coderecon.git._internal.hooks import HookResult, run_hook
from coderecon.git._internal.parsing import (
    extract_local_branch_from_remote,
    extract_tag_name,
    first_line,
    make_tag_ref,
)
from coderecon.git._internal.planners import CheckoutPlanner, DiffPlanner
from coderecon.git._internal.preconditions import (
    check_nothing_to_commit,
    require_branch_exists,
    require_current_branch,
    require_not_current_branch,
    require_not_unborn,
)
from coderecon.git._internal.rebase import RebaseFlow, RebasePlanner

__all__ = [
    "CheckoutPlanner",
    "DiffPlanner",
    "HookResult",
    "RebaseFlow",
    "RebasePlanner",
    "RepoAccess",
    "WriteFlows",
    "check_nothing_to_commit",
    "extract_local_branch_from_remote",
    "extract_tag_name",
    "first_line",
    "make_tag_ref",
    "require_branch_exists",
    "require_current_branch",
    "require_not_current_branch",
    "require_not_unborn",
    "run_hook",
]
