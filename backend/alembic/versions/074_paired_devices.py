"""add paired_devices for mobile app pairing

Revision ID: 074
Revises: 073
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "074"
down_revision: Union[str, None] = "073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paired_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False, server_default="other"),
        sa.Column("app_version", sa.String(length=50), nullable=True),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_token_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ip", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_paired_devices_user_id", "paired_devices", ["user_id"])
    op.create_index(
        "ix_paired_devices_refresh_token_hash",
        "paired_devices",
        ["refresh_token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_paired_devices_previous_token_hash",
        "paired_devices",
        ["previous_token_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_paired_devices_previous_token_hash", table_name="paired_devices")
    op.drop_index("ix_paired_devices_refresh_token_hash", table_name="paired_devices")
    op.drop_index("ix_paired_devices_user_id", table_name="paired_devices")
    op.drop_table("paired_devices")
