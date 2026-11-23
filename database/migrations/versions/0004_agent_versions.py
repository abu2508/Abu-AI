"""agent versions, per-version permissions, tool approvals

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "draft",
                "active",
                "retired",
                name="version_state",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("max_steps", sa.Integer(), server_default="8", nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column("max_run_input_tokens", sa.Integer(), server_default="20000", nullable=False),
        sa.Column("max_run_output_tokens", sa.Integer(), server_default="4000", nullable=False),
        sa.Column(
            "max_run_cost_inr",
            sa.Numeric(precision=12, scale=4),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "max_run_cost_inr BETWEEN 0 AND 50", name=op.f("ck_agent_versions_cost_bounds")
        ),
        sa.CheckConstraint(
            "max_run_input_tokens BETWEEN 1 AND 200000",
            name=op.f("ck_agent_versions_input_tokens_bounds"),
        ),
        sa.CheckConstraint(
            "max_run_output_tokens BETWEEN 1 AND 100000",
            name=op.f("ck_agent_versions_output_tokens_bounds"),
        ),
        sa.CheckConstraint(
            "max_steps BETWEEN 1 AND 50", name=op.f("ck_agent_versions_steps_bounds")
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 300", name=op.f("ck_agent_versions_timeout_bounds")
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.agent_id"],
            name=op.f("fk_agent_versions_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_versions")),
        sa.UniqueConstraint("agent_id", "version", name=op.f("uq_agent_versions_agent_id_version")),
    )
    op.create_index(
        "uq_agent_versions_one_active",
        "agent_versions",
        ["agent_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_table(
        "agent_version_memory",
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                "read",
                "read_write",
                name="memory_mode",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["agent_versions.id"],
            name=op.f("fk_agent_version_memory_version_id_agent_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("version_id", "namespace", name=op.f("pk_agent_version_memory")),
    )
    op.create_table(
        "agent_version_model_aliases",
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["agent_versions.id"],
            name=op.f("fk_agent_version_model_aliases_version_id_agent_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("version_id", "alias", name=op.f("pk_agent_version_model_aliases")),
    )
    op.create_table(
        "agent_version_tools",
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["agent_versions.id"],
            name=op.f("fk_agent_version_tools_version_id_agent_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("version_id", "tool_name", name=op.f("pk_agent_version_tools")),
    )
    op.create_table(
        "tool_approvals",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.agent_id"],
            name=op.f("fk_tool_approvals_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tool_approvals_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["agent_versions.id"],
            name=op.f("fk_tool_approvals_version_id_agent_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_approvals")),
    )

    # Data migration: legacy agent-level model permissions become version 1 of each agent.
    # Agents that already had permissions get an ACTIVE v1 so they keep working; permissions
    # are preserved exactly (nothing is widened).
    op.execute(
        """
        INSERT INTO agent_versions (agent_id, version, state, activated_at)
        SELECT DISTINCT agent_id, 1, 'active', now() FROM agent_model_permissions
        """
    )
    op.execute(
        """
        INSERT INTO agent_version_model_aliases (version_id, alias)
        SELECT v.id, p.route_alias
        FROM agent_model_permissions p
        JOIN agent_versions v ON v.agent_id = p.agent_id AND v.version = 1
        """
    )
    op.drop_table("agent_model_permissions")


def downgrade() -> None:
    op.create_table(
        "agent_model_permissions",
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("route_alias", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.agent_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "route_alias"),
    )
    # Restore model permissions from each agent's ACTIVE version (other versions are dropped).
    op.execute(
        """
        INSERT INTO agent_model_permissions (agent_id, route_alias)
        SELECT v.agent_id, a.alias FROM agent_version_model_aliases a
        JOIN agent_versions v ON v.id = a.version_id WHERE v.state = 'active'
        """
    )
    op.drop_table("tool_approvals")
    op.drop_table("agent_version_tools")
    op.drop_table("agent_version_model_aliases")
    op.drop_table("agent_version_memory")
    op.drop_index("uq_agent_versions_one_active", table_name="agent_versions")
    op.drop_table("agent_versions")
