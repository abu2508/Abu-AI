"""agent registry foundations

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("agent_id", name=op.f("pk_agents")),
    )
    op.create_table(
        "agent_model_permissions",
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("route_alias", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.agent_id"],
            name=op.f("fk_agent_model_permissions_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("agent_id", "route_alias", name=op.f("pk_agent_model_permissions")),
    )


def downgrade() -> None:
    op.drop_table("agent_model_permissions")
    op.drop_table("agents")
