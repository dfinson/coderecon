"""CodeRecon Dev SDK — training-specific extensions.

Extends the prod SDK with endpoints for signal collection, index
introspection, and definition lookup.  These methods are NOT part of
the public product surface — they exist for the recon-lab training
pipeline.

Usage::

    from coderecon.sdk.dev import CodeReconDev

    async with CodeReconDev() as sdk:
        await sdk.register("/path/to/repo")
        signals = await sdk.raw_signals(repo="my-repo", query="auth")
        facts = await sdk.index_facts(repo="my-repo")
"""

from coderecon.sdk.dev.client import CodeReconDev
from coderecon.sdk.dev.types import (
    DefEntry,
    IndexFactsResult,
    IndexStatusResult,
    RawSignalsResult,
)

__all__ = [
    "CodeReconDev",
    "DefEntry",
    "IndexFactsResult",
    "IndexStatusResult",
    "RawSignalsResult",
]
