"""messaging: conversations.timestamps_reliable (inbox reply status needs it)

Revision ID: 0011
Revises: 0010

Labelled pastes carry no real timestamps (the importer spaces messages one minute apart), so response times and
waiting hours must not be reported for them. Until now `analysis` guessed this from message spacing at request time;
the flag makes it explicit and lets the inbox answer without loading any message. Existing conversations are
backfilled with the same one-minute-spacing rule.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "msg_conversations",
        sa.Column(
            "timestamps_reliable", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
    )
    # Backfill: >= 3 messages and every consecutive gap is exactly one minute => synthetic timestamps.
    op.execute(
        """
        UPDATE msg_conversations c SET timestamps_reliable = false
        WHERE c.message_count >= 3
          AND NOT EXISTS (
            SELECT 1 FROM (
              SELECT m.sent_at - lag(m.sent_at) OVER (ORDER BY m.sent_at) AS gap
              FROM msg_messages m WHERE m.conversation_id = c.id
            ) g
            WHERE g.gap IS NOT NULL AND g.gap <> interval '1 minute'
          )
        """
    )


def downgrade() -> None:
    op.drop_column("msg_conversations", "timestamps_reliable")
