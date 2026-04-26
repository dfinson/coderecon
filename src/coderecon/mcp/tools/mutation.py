"""Mutation helpers — EditParam model and fuzzy span matching.

These helpers are used by tests and potentially by edit tools.
The MCP tool handler that was previously in this module (write_source)
has been removed in v2.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Parameter Models


class EditParam(BaseModel):
    """A single file edit.

    For create: provide content (full file body).
    For update: provide start_line, end_line, expected_file_sha256, and new_content
                (span-based replacement only).
    For delete: no extra fields needed.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File path relative to repo root")
    action: Literal["create", "update", "delete"]

    # create: full file content
    content: str | None = Field(None, description="Full file content (required for create)")

    # update: span-based replacement (all three required for updates)
    start_line: int | None = Field(None, gt=0, description="Start line (1-indexed, inclusive)")
    end_line: int | None = Field(None, gt=0, description="End line (1-indexed, inclusive)")
    expected_file_sha256: str | None = Field(
        None, description="SHA256 of whole file from read_source (required for update)"
    )
    new_content: str | None = Field(
        None, description="Replacement content for the span (required for update)"
    )
    expected_content: str | None = Field(
        None,
        description=(
            "Expected content at the span location (required for update). "
            "The server fuzzy-matches nearby lines if your line numbers are "
            "slightly off, auto-correcting within a few lines."
        ),
    )

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "EditParam":
        if self.action == "create":
            if self.content is None:
                msg = "content is required for action='create'"
                raise ValueError(msg)
        elif self.action == "update":
            missing = []
            if self.start_line is None:
                missing.append("start_line")
            if self.end_line is None:
                missing.append("end_line")
            if self.expected_file_sha256 is None:
                missing.append("expected_file_sha256")
            if self.new_content is None:
                missing.append("new_content")
            if self.expected_content is None:
                missing.append("expected_content")
            if missing:
                msg = f"update requires: {', '.join(missing)}"
                raise ValueError(msg)
            if (self.end_line or 0) < (self.start_line or 0):
                msg = f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
                raise ValueError(msg)
        return self
