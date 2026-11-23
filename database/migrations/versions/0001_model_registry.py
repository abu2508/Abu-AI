"""model registry tables

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_provider_policies",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("retention_summary", sa.Text(), nullable=False),
        sa.Column(
            "trains_on_data",
            sa.Enum(
                "yes",
                "no",
                "unknown",
                name="tri_trains",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "human_review",
            sa.Enum(
                "yes",
                "no",
                "unknown",
                name="tri_review",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "max_data_class",
            sa.Enum(
                "synthetic",
                "non_sensitive",
                "sensitive",
                name="data_class",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_provider_policies")),
        sa.UniqueConstraint("provider", name=op.f("uq_model_provider_policies_provider")),
    )
    op.create_table(
        "models",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("policy_id", sa.UUID(), nullable=False),
        sa.Column("provider_model_id", sa.String(length=256), nullable=False),
        sa.Column("gateway_model_name", sa.String(length=256), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("context_limit", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("rpm_limit", sa.Integer(), nullable=True),
        sa.Column("rpd_limit", sa.Integer(), nullable=True),
        sa.Column(
            "rate_limits_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("availability_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approval_status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                name="approval_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT enabled OR approval_status = 'approved'",
            name=op.f("ck_models_enabled_requires_approval"),
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["model_provider_policies.id"],
            name=op.f("fk_models_policy_id_model_provider_policies"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_models")),
        sa.UniqueConstraint("name", name=op.f("uq_models_name")),
    )
    op.create_table(
        "model_capabilities",
        sa.Column("model_id", sa.UUID(), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            name=op.f("fk_model_capabilities_model_id_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("model_id", "capability", name=op.f("pk_model_capabilities")),
    )
    op.create_table(
        "model_pricing",
        sa.Column("model_id", sa.UUID(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("input_per_million", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("output_per_million", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "input_per_million >= 0 AND output_per_million >= 0",
            name=op.f("ck_model_pricing_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            name=op.f("fk_model_pricing_model_id_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("model_id", name=op.f("pk_model_pricing")),
    )
    op.create_table(
        "model_routes",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("alias", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.UUID(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            name=op.f("fk_model_routes_model_id_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_routes")),
        sa.UniqueConstraint("alias", "model_id", name=op.f("uq_model_routes_alias_model_id")),
        sa.UniqueConstraint("alias", "priority", name=op.f("uq_model_routes_alias_priority")),
    )


def downgrade() -> None:
    op.drop_table("model_routes")
    op.drop_table("model_pricing")
    op.drop_table("model_capabilities")
    op.drop_table("models")
    op.drop_table("model_provider_policies")
