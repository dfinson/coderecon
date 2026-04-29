"""CodeRecon SDK — spawn a daemon over stdio and call tools programmatically.

Usage::

    from coderecon.sdk import CodeRecon

    async with CodeRecon() as sdk:
        await sdk.register("/path/to/repo")
        result = await sdk.recon(repo="my-repo", task="find auth logic")
        for span in result.spans:
            print(span.path, span.start_line, span.end_line)
"""

from coderecon.sdk.client import CodeRecon
from coderecon.sdk.events import EventRouter
from coderecon.sdk.frameworks import as_langchain_tools, as_openai_tools
from coderecon.sdk.handle import RepoHandle, SessionHandle
from coderecon.sdk.protocol import CodeReconError
from coderecon.sdk.types import (
    CatalogEntry,
    CheckpointResult,
    CodeSpan,
    CommunitiesResult,
    CyclesResult,
    DescribeResult,
    DiffResult,
    Event,
    GraphExportResult,
    ImpactResult,
    MapResult,
    ReconResult,
    RefactorCancelResult,
    RefactorCommitResult,
    RefactorResult,
    RegisterResult,
    StatusResult,
    UnderstandResult,
)

__all__ = [
    "CodeRecon",
    "RepoHandle",
    "SessionHandle",
    "EventRouter",
    "CodeReconError",
    "as_openai_tools",
    "as_langchain_tools",
    # Result types
    "CatalogEntry",
    "CheckpointResult",
    "CodeSpan",
    "CommunitiesResult",
    "CyclesResult",
    "DescribeResult",
    "DiffResult",
    "Event",
    "GraphExportResult",
    "ImpactResult",
    "MapResult",
    "ReconResult",
    "RefactorCancelResult",
    "RefactorCommitResult",
    "RefactorResult",
    "RegisterResult",
    "StatusResult",
    "UnderstandResult",
]
