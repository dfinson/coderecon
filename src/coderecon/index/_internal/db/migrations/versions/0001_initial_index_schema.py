"""initial index schema

Revision ID: 0001
Revises:
Create Date: 2026-04-24

Creates all index tables with proper FK constraints (ondelete CASCADE)
and composite indexes.  Idempotent: skips tables that already exist
so pre-Alembic databases get stamped without error.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

# Composite indexes (previously in indexes.py, now managed by Alembic)
COMPOSITE_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_def_facts_file_name ON def_facts(file_id, name)",
    "CREATE INDEX IF NOT EXISTS idx_ref_facts_file_target ON ref_facts(file_id, target_def_uid)",
    "CREATE INDEX IF NOT EXISTS idx_ref_facts_target_tier ON ref_facts(target_def_uid, ref_tier)",
    "CREATE INDEX IF NOT EXISTS idx_scope_facts_file ON scope_facts(file_id)",
    "CREATE INDEX IF NOT EXISTS idx_import_facts_file ON import_facts(file_id)",
    "CREATE INDEX IF NOT EXISTS idx_local_bind_facts_scope ON local_bind_facts(scope_id)",
    "CREATE INDEX IF NOT EXISTS idx_export_surfaces_unit ON export_surfaces(unit_id)",
    "CREATE INDEX IF NOT EXISTS idx_contexts_family_status ON contexts(language_family, probe_status)",
    "CREATE INDEX IF NOT EXISTS idx_anchor_groups_unit ON anchor_groups(unit_id)",
    "CREATE INDEX IF NOT EXISTS idx_test_coverage_target_stale ON test_coverage_facts(target_def_uid, stale)",
    "CREATE INDEX IF NOT EXISTS idx_test_coverage_test_id ON test_coverage_facts(test_id)",
    "CREATE INDEX IF NOT EXISTS idx_lint_status_file_tool ON lint_status_facts(file_path, tool_id)",
    "CREATE INDEX IF NOT EXISTS idx_endpoint_facts_url ON endpoint_facts(url_pattern, kind)",
    "CREATE INDEX IF NOT EXISTS idx_doc_cross_refs_target ON doc_cross_refs(target_def_uid)",
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # ── Tier 0: Anchor tables ──────────────────────────────────────

    if "worktrees" not in existing:
        op.create_table(
            "worktrees",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String, nullable=False, unique=True, index=True),
            sa.Column("root_path", sa.String, nullable=False, unique=True, index=True),
            sa.Column("branch", sa.String, nullable=True),
            sa.Column("is_main", sa.Boolean, nullable=False, server_default="0"),
        )

    if "files" not in existing:
        op.create_table(
            "files",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "worktree_id", sa.Integer,
                sa.ForeignKey("worktrees.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("path", sa.String, nullable=False, index=True),
            sa.Column("language_family", sa.String, nullable=True),
            sa.Column("content_hash", sa.String, nullable=True),
            sa.Column("line_count", sa.Integer, nullable=True),
            sa.Column("indexed_at", sa.Float, nullable=True),
            sa.Column("last_indexed_epoch", sa.Integer, nullable=True, index=True),
            sa.Column("declared_module", sa.String, nullable=True, index=True),
            sa.UniqueConstraint("worktree_id", "path", name="uq_files_wt_path"),
        )

    if "contexts" not in existing:
        op.create_table(
            "contexts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String, nullable=True),
            sa.Column("language_family", sa.String, nullable=False, index=True),
            sa.Column("root_path", sa.String, nullable=False, index=True),
            sa.Column("tier", sa.Integer, nullable=True),
            sa.Column("probe_status", sa.String, nullable=False, server_default="pending", index=True),
            sa.Column("include_spec", sa.String, nullable=True),
            sa.Column("exclude_spec", sa.String, nullable=True),
            sa.Column("config_hash", sa.String, nullable=True),
            sa.Column("refreshed_at", sa.Float, nullable=True),
        )

    if "epochs" not in existing:
        op.create_table(
            "epochs",
            sa.Column("epoch_id", sa.Integer, primary_key=True),
            sa.Column("published_at", sa.Float, nullable=True),
            sa.Column("files_indexed", sa.Integer, nullable=False, server_default="0"),
            sa.Column("commit_hash", sa.String, nullable=True),
        )

    if "repo_state" not in existing:
        op.create_table(
            "repo_state",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("last_seen_head", sa.String, nullable=True),
            sa.Column("last_seen_index_mtime", sa.Float, nullable=True),
            sa.Column("checked_at", sa.Float, nullable=True),
            sa.Column("current_epoch_id", sa.Integer, nullable=True),
            sa.Column("reconignore_hash", sa.String, nullable=True),
        )

    # ── Tier 1: Structural fact tables ─────────────────────────────

    if "context_markers" not in existing:
        op.create_table(
            "context_markers",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "context_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("marker_path", sa.String, nullable=False),
            sa.Column("marker_tier", sa.String, nullable=False),
            sa.Column("detected_at", sa.Float, nullable=True),
        )

    if "test_targets" not in existing:
        op.create_table(
            "test_targets",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "context_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("target_id", sa.String, nullable=False, unique=True, index=True),
            sa.Column("selector", sa.String, nullable=False),
            sa.Column("kind", sa.String, nullable=False, index=True),
            sa.Column("language", sa.String, nullable=False, index=True),
            sa.Column("runner_pack_id", sa.String, nullable=False, index=True),
            sa.Column("workspace_root", sa.String, nullable=False),
            sa.Column("estimated_cost", sa.String, nullable=False, server_default="medium"),
            sa.Column("test_count", sa.Integer, nullable=True),
            sa.Column("path", sa.String, nullable=True),
            sa.Column("discovered_at", sa.Float, nullable=True),
        )

    if "indexed_lint_tools" not in existing:
        op.create_table(
            "indexed_lint_tools",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tool_id", sa.String, nullable=False, unique=True, index=True),
            sa.Column("name", sa.String, nullable=False),
            sa.Column("category", sa.String, nullable=False, index=True),
            sa.Column("languages", sa.String, nullable=False),
            sa.Column("executable", sa.String, nullable=False),
            sa.Column("workspace_root", sa.String, nullable=False),
            sa.Column("config_file", sa.String, nullable=True),
            sa.Column("discovered_at", sa.Float, nullable=True),
        )

    if "indexed_coverage_capabilities" not in existing:
        op.create_table(
            "indexed_coverage_capabilities",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("workspace_root", sa.String, nullable=False, index=True),
            sa.Column("runner_pack_id", sa.String, nullable=False, index=True),
            sa.Column("tools_json", sa.String, nullable=False),
            sa.Column("discovered_at", sa.Float, nullable=True),
        )

    if "scope_facts" not in existing:
        op.create_table(
            "scope_facts",
            sa.Column("scope_id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "parent_scope_id", sa.Integer,
                sa.ForeignKey("scope_facts.scope_id", ondelete="CASCADE"),
                nullable=True, index=True,
            ),
            sa.Column("kind", sa.String, nullable=False, index=True),
            sa.Column("start_line", sa.Integer, nullable=False),
            sa.Column("start_col", sa.Integer, nullable=False),
            sa.Column("end_line", sa.Integer, nullable=False),
            sa.Column("end_col", sa.Integer, nullable=False),
        )

    if "def_facts" not in existing:
        op.create_table(
            "def_facts",
            sa.Column("def_uid", sa.String, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("kind", sa.String, nullable=False, index=True),
            sa.Column("name", sa.String, nullable=False, index=True),
            sa.Column("qualified_name", sa.String, nullable=True),
            sa.Column("lexical_path", sa.String, nullable=False, index=True),
            sa.Column("namespace", sa.String, nullable=True, index=True),
            sa.Column("start_line", sa.Integer, nullable=False),
            sa.Column("start_col", sa.Integer, nullable=False),
            sa.Column("end_line", sa.Integer, nullable=False),
            sa.Column("end_col", sa.Integer, nullable=False),
            sa.Column("signature_hash", sa.String, nullable=True),
            sa.Column("display_name", sa.String, nullable=True),
            sa.Column("signature_text", sa.String, nullable=True),
            sa.Column("decorators_json", sa.String, nullable=True),
            sa.Column("docstring", sa.String, nullable=True),
            sa.Column("return_type", sa.String, nullable=True),
        )

    if "ref_facts" not in existing:
        op.create_table(
            "ref_facts",
            sa.Column("ref_id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "scope_id", sa.Integer,
                sa.ForeignKey("scope_facts.scope_id", ondelete="CASCADE"),
                nullable=True, index=True,
            ),
            sa.Column("token_text", sa.String, nullable=False, index=True),
            sa.Column("start_line", sa.Integer, nullable=False),
            sa.Column("start_col", sa.Integer, nullable=False),
            sa.Column("end_line", sa.Integer, nullable=False),
            sa.Column("end_col", sa.Integer, nullable=False),
            sa.Column("role", sa.String, nullable=False, index=True),
            sa.Column("ref_tier", sa.String, nullable=False, server_default="unknown", index=True),
            sa.Column("certainty", sa.String, nullable=False, server_default="certain"),
            sa.Column("target_def_uid", sa.String, nullable=True, index=True),
        )

    if "local_bind_facts" not in existing:
        op.create_table(
            "local_bind_facts",
            sa.Column("bind_id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "scope_id", sa.Integer,
                sa.ForeignKey("scope_facts.scope_id", ondelete="CASCADE"),
                nullable=True, index=True,
            ),
            sa.Column("name", sa.String, nullable=False, index=True),
            sa.Column("target_kind", sa.String, nullable=False),
            sa.Column("target_uid", sa.String, nullable=True),
            sa.Column("certainty", sa.String, nullable=False, server_default="certain"),
            sa.Column("reason_code", sa.String, nullable=False),
        )

    if "import_facts" not in existing:
        op.create_table(
            "import_facts",
            sa.Column("import_uid", sa.String, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "scope_id", sa.Integer,
                sa.ForeignKey("scope_facts.scope_id", ondelete="CASCADE"),
                nullable=True, index=True,
            ),
            sa.Column("imported_name", sa.String, nullable=False, index=True),
            sa.Column("alias", sa.String, nullable=True),
            sa.Column("source_literal", sa.String, nullable=True),
            sa.Column("resolved_path", sa.String, nullable=True, index=True),
            sa.Column("import_kind", sa.String, nullable=False),
            sa.Column("certainty", sa.String, nullable=False, server_default="certain"),
            sa.Column("start_line", sa.Integer, nullable=True),
            sa.Column("start_col", sa.Integer, nullable=True),
            sa.Column("end_line", sa.Integer, nullable=True),
            sa.Column("end_col", sa.Integer, nullable=True),
        )

    if "export_surfaces" not in existing:
        op.create_table(
            "export_surfaces",
            sa.Column("surface_id", sa.Integer, primary_key=True),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, unique=True, index=True,
            ),
            sa.Column("surface_hash", sa.String, nullable=True),
            sa.Column("epoch_id", sa.Integer, nullable=True),
        )

    if "export_entries" not in existing:
        op.create_table(
            "export_entries",
            sa.Column("entry_id", sa.Integer, primary_key=True),
            sa.Column(
                "surface_id", sa.Integer,
                sa.ForeignKey("export_surfaces.surface_id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("exported_name", sa.String, nullable=False, index=True),
            sa.Column("def_uid", sa.String, nullable=True),
            sa.Column("certainty", sa.String, nullable=False, server_default="certain"),
            sa.Column("evidence_kind", sa.String, nullable=True),
        )

    if "export_thunks" not in existing:
        op.create_table(
            "export_thunks",
            sa.Column("thunk_id", sa.Integer, primary_key=True),
            sa.Column(
                "source_unit", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "target_unit", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("mode", sa.String, nullable=False),
            sa.Column("explicit_names", sa.String, nullable=True),
            sa.Column("alias_map", sa.String, nullable=True),
            sa.Column("evidence_kind", sa.String, nullable=True),
        )

    if "anchor_groups" not in existing:
        op.create_table(
            "anchor_groups",
            sa.Column("group_id", sa.Integer, primary_key=True),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("member_token", sa.String, nullable=False, index=True),
            sa.Column("receiver_shape", sa.String, nullable=True),
            sa.Column("total_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("exemplar_ids", sa.String, nullable=True),
        )

    if "dynamic_access_sites" not in existing:
        op.create_table(
            "dynamic_access_sites",
            sa.Column("site_id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("start_line", sa.Integer, nullable=False),
            sa.Column("start_col", sa.Integer, nullable=False),
            sa.Column("pattern_type", sa.String, nullable=False),
            sa.Column("extracted_literals", sa.String, nullable=True),
            sa.Column("has_non_literal_key", sa.Boolean, nullable=False, server_default="0"),
        )

    # ── Tier 2: Type-aware fact tables ─────────────────────────────

    if "type_annotation_facts" not in existing:
        op.create_table(
            "type_annotation_facts",
            sa.Column("annotation_id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "scope_id", sa.Integer,
                sa.ForeignKey("scope_facts.scope_id", ondelete="CASCADE"),
                nullable=True, index=True,
            ),
            sa.Column("target_kind", sa.String, nullable=False),
            sa.Column("target_name", sa.String, nullable=False, index=True),
            sa.Column("raw_annotation", sa.String, nullable=False),
            sa.Column("canonical_type", sa.String, nullable=False, index=True),
            sa.Column("base_type", sa.String, nullable=False, index=True),
            sa.Column("is_optional", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("is_array", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("is_generic", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("is_reference", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("is_mutable", sa.Boolean, nullable=False, server_default="1"),
            sa.Column("type_args_json", sa.String, nullable=True),
            sa.Column("start_line", sa.Integer, nullable=False),
            sa.Column("start_col", sa.Integer, nullable=False),
        )

    if "type_member_facts" not in existing:
        op.create_table(
            "type_member_facts",
            sa.Column("member_id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("parent_def_uid", sa.String, nullable=False, index=True),
            sa.Column("parent_type_name", sa.String, nullable=False, index=True),
            sa.Column("parent_kind", sa.String, nullable=False),
            sa.Column("member_kind", sa.String, nullable=False),
            sa.Column("member_name", sa.String, nullable=False, index=True),
            sa.Column("member_def_uid", sa.String, nullable=True),
            sa.Column("type_annotation", sa.String, nullable=True),
            sa.Column("canonical_type", sa.String, nullable=True, index=True),
            sa.Column("base_type", sa.String, nullable=True, index=True),
            sa.Column("visibility", sa.String, nullable=True),
            sa.Column("is_static", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("is_abstract", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("start_line", sa.Integer, nullable=False),
            sa.Column("start_col", sa.Integer, nullable=False),
        )

    if "member_access_facts" not in existing:
        op.create_table(
            "member_access_facts",
            sa.Column("access_id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "scope_id", sa.Integer,
                sa.ForeignKey("scope_facts.scope_id", ondelete="CASCADE"),
                nullable=True, index=True,
            ),
            sa.Column("access_style", sa.String, nullable=False),
            sa.Column("full_expression", sa.String, nullable=False),
            sa.Column("receiver_name", sa.String, nullable=False, index=True),
            sa.Column("member_chain", sa.String, nullable=False),
            sa.Column("final_member", sa.String, nullable=False, index=True),
            sa.Column("chain_depth", sa.Integer, nullable=False),
            sa.Column("is_invocation", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("arg_count", sa.Integer, nullable=True),
            sa.Column("receiver_declared_type", sa.String, nullable=True, index=True),
            sa.Column("resolved_type_path", sa.String, nullable=True),
            sa.Column("final_target_def_uid", sa.String, nullable=True, index=True),
            sa.Column("resolution_method", sa.String, nullable=True),
            sa.Column("resolution_confidence", sa.Float, nullable=True),
            sa.Column("start_line", sa.Integer, nullable=False),
            sa.Column("start_col", sa.Integer, nullable=False),
            sa.Column("end_line", sa.Integer, nullable=False),
            sa.Column("end_col", sa.Integer, nullable=False),
        )

    if "interface_impl_facts" not in existing:
        op.create_table(
            "interface_impl_facts",
            sa.Column("impl_id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("implementor_def_uid", sa.String, nullable=False, index=True),
            sa.Column("implementor_name", sa.String, nullable=False, index=True),
            sa.Column("interface_name", sa.String, nullable=False, index=True),
            sa.Column("interface_def_uid", sa.String, nullable=True),
            sa.Column("impl_style", sa.String, nullable=False),
            sa.Column("start_line", sa.Integer, nullable=False),
            sa.Column("start_col", sa.Integer, nullable=False),
        )

    if "receiver_shape_facts" not in existing:
        op.create_table(
            "receiver_shape_facts",
            sa.Column("shape_id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "unit_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "scope_id", sa.Integer,
                sa.ForeignKey("scope_facts.scope_id", ondelete="CASCADE"),
                nullable=True, index=True,
            ),
            sa.Column("receiver_name", sa.String, nullable=False, index=True),
            sa.Column("declared_type", sa.String, nullable=True, index=True),
            sa.Column("shape_hash", sa.String, nullable=False, index=True),
            sa.Column("observed_members_json", sa.String, nullable=False),
            sa.Column("matched_types_json", sa.String, nullable=True),
            sa.Column("best_match_type", sa.String, nullable=True, index=True),
            sa.Column("match_confidence", sa.Float, nullable=True),
        )

    # ── Tier 3: Behavioral fact tables ─────────────────────────────

    if "test_coverage_facts" not in existing:
        op.create_table(
            "test_coverage_facts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("test_id", sa.String, nullable=False, index=True),
            sa.Column(
                "target_def_uid", sa.String,
                sa.ForeignKey("def_facts.def_uid", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("target_file_path", sa.String, nullable=False, index=True),
            sa.Column("covered_lines", sa.Integer, nullable=False),
            sa.Column("total_lines", sa.Integer, nullable=False),
            sa.Column("line_rate", sa.Float, nullable=False),
            sa.Column("branch_rate", sa.Float, nullable=True),
            sa.Column("epoch", sa.Integer, nullable=False, index=True),
            sa.Column("stale", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("test_passed", sa.Boolean, nullable=True),
        )

    if "lint_status_facts" not in existing:
        op.create_table(
            "lint_status_facts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("file_path", sa.String, nullable=False, index=True),
            sa.Column("tool_id", sa.String, nullable=False, index=True),
            sa.Column("category", sa.String, nullable=False, index=True),
            sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("warning_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("info_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("clean", sa.Boolean, nullable=False, server_default="1"),
            sa.Column("epoch", sa.Integer, nullable=False, index=True),
        )

    if "endpoint_facts" not in existing:
        op.create_table(
            "endpoint_facts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("kind", sa.String, nullable=False, index=True),
            sa.Column("http_method", sa.String, nullable=True),
            sa.Column("url_pattern", sa.String, nullable=False, index=True),
            sa.Column(
                "handler_def_uid", sa.String,
                sa.ForeignKey("def_facts.def_uid", ondelete="SET NULL"),
                nullable=True, index=True,
            ),
            sa.Column("start_line", sa.Integer, nullable=True),
            sa.Column("end_line", sa.Integer, nullable=True),
            sa.Column("framework", sa.String, nullable=True),
        )

    if "doc_cross_refs" not in existing:
        op.create_table(
            "doc_cross_refs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "source_file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("source_def_uid", sa.String, nullable=True, index=True),
            sa.Column("source_line", sa.Integer, nullable=False),
            sa.Column("raw_text", sa.String, nullable=False),
            sa.Column(
                "target_def_uid", sa.String,
                sa.ForeignKey("def_facts.def_uid", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("confidence", sa.String, nullable=False, server_default="high"),
        )

    if "splade_vecs" not in existing:
        op.create_table(
            "splade_vecs",
            sa.Column(
                "def_uid", sa.String,
                sa.ForeignKey("def_facts.def_uid", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("vector_json", sa.String, nullable=False),
            sa.Column("model_version", sa.String, nullable=False, index=True),
            sa.Column("scaffold_text", sa.String, nullable=True),
            sa.Column("vector_blob", sa.LargeBinary, nullable=True),
        )

    if "semantic_neighbor_facts" not in existing:
        op.create_table(
            "semantic_neighbor_facts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "source_def_uid", sa.String,
                sa.ForeignKey("def_facts.def_uid", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column(
                "neighbor_def_uid", sa.String,
                sa.ForeignKey("def_facts.def_uid", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("score", sa.Float, nullable=False),
            sa.Column("model_version", sa.String, nullable=False, index=True),
        )

    if "file_chunk_vecs" not in existing:
        op.create_table(
            "file_chunk_vecs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("chunk_key", sa.String, nullable=False),
            sa.Column("chunk_text", sa.String, nullable=False),
            sa.Column("start_line", sa.Integer, nullable=False),
            sa.Column("end_line", sa.Integer, nullable=False),
            sa.Column("vector_json", sa.String, nullable=False),
            sa.Column("model_version", sa.String, nullable=False, index=True),
        )

    if "doc_code_edge_facts" not in existing:
        op.create_table(
            "doc_code_edge_facts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "file_id", sa.Integer,
                sa.ForeignKey("files.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("chunk_key", sa.String, nullable=False),
            sa.Column("target_def_uid", sa.String, nullable=False, index=True),
            sa.Column("score", sa.Float, nullable=False),
            sa.Column("model_version", sa.String, nullable=False, index=True),
        )

    # ── Epoch / snapshot tables ────────────────────────────────────

    if "def_snapshot_record" not in existing:
        op.create_table(
            "def_snapshot_record",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("epoch_id", sa.Integer, nullable=False, index=True),
            sa.Column("file_path", sa.String, nullable=False, index=True),
            sa.Column("kind", sa.String, nullable=False),
            sa.Column("name", sa.String, nullable=False),
            sa.Column("lexical_path", sa.String, nullable=False),
            sa.Column("signature_hash", sa.String, nullable=True),
            sa.Column("display_name", sa.String, nullable=True),
            sa.Column("start_line", sa.Integer, nullable=True),
            sa.Column("end_line", sa.Integer, nullable=True),
        )

    # ── Runtime tables ─────────────────────────────────────────────

    if "context_runtimes" not in existing:
        op.create_table(
            "context_runtimes",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "context_id", sa.Integer,
                sa.ForeignKey("contexts.id", ondelete="CASCADE"),
                nullable=False, unique=True, index=True,
            ),
            sa.Column("python_executable", sa.String, nullable=True),
            sa.Column("python_version", sa.String, nullable=True),
            sa.Column("python_venv_path", sa.String, nullable=True),
            sa.Column("node_executable", sa.String, nullable=True),
            sa.Column("node_version", sa.String, nullable=True),
            sa.Column("package_manager", sa.String, nullable=True),
            sa.Column("package_manager_executable", sa.String, nullable=True),
            sa.Column("go_executable", sa.String, nullable=True),
            sa.Column("go_version", sa.String, nullable=True),
            sa.Column("go_mod_path", sa.String, nullable=True),
            sa.Column("cargo_executable", sa.String, nullable=True),
            sa.Column("rust_version", sa.String, nullable=True),
            sa.Column("java_executable", sa.String, nullable=True),
            sa.Column("java_version", sa.String, nullable=True),
            sa.Column("gradle_executable", sa.String, nullable=True),
            sa.Column("maven_executable", sa.String, nullable=True),
            sa.Column("dotnet_executable", sa.String, nullable=True),
            sa.Column("dotnet_version", sa.String, nullable=True),
            sa.Column("ruby_executable", sa.String, nullable=True),
            sa.Column("ruby_version", sa.String, nullable=True),
            sa.Column("bundle_executable", sa.String, nullable=True),
            sa.Column("env_vars_json", sa.String, nullable=True),
            sa.Column("resolved_at", sa.Float, nullable=True),
            sa.Column("resolution_method", sa.String, nullable=True),
        )

    # ── Composite indexes ──────────────────────────────────────────

    conn = op.get_bind()
    for sql in COMPOSITE_INDEXES:
        conn.execute(sa.text(sql))


def downgrade() -> None:
    # Drop in reverse dependency order.
    tables = [
        "context_runtimes",
        "def_snapshot_record",
        "doc_code_edge_facts",
        "file_chunk_vecs",
        "semantic_neighbor_facts",
        "splade_vecs",
        "doc_cross_refs",
        "endpoint_facts",
        "lint_status_facts",
        "test_coverage_facts",
        "receiver_shape_facts",
        "interface_impl_facts",
        "member_access_facts",
        "type_member_facts",
        "type_annotation_facts",
        "dynamic_access_sites",
        "anchor_groups",
        "export_thunks",
        "export_entries",
        "export_surfaces",
        "import_facts",
        "local_bind_facts",
        "ref_facts",
        "def_facts",
        "scope_facts",
        "context_markers",
        "test_targets",
        "indexed_coverage_capabilities",
        "indexed_lint_tools",
        "repo_state",
        "epochs",
        "contexts",
        "files",
        "worktrees",
    ]
    for t in tables:
        op.drop_table(t)
