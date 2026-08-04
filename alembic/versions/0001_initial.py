"""Tables initiales : routes, price_snapshots, baselines, alerts, config

Revision ID: 0001
Revises:
Create Date: 2026-08-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "routes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("origin", sa.String(3), nullable=False),
        sa.Column("destination", sa.String(3), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("origin", "destination", name="uq_routes_origin_destination"),
    )

    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("route_id", sa.Integer(), sa.ForeignKey("routes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="travelpayouts"),
        sa.Column("cabin", sa.String(20), nullable=False, server_default="economy"),
        sa.Column("transfers", sa.SmallInteger(), nullable=True),
        sa.Column("return_transfers", sa.SmallInteger(), nullable=True),
        sa.Column("airline", sa.String(8), nullable=True),
        sa.Column("depart_date", sa.Date(), nullable=True),
        sa.Column("return_date", sa.Date(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_price_snapshots_route_currency_fetched",
        "price_snapshots",
        ["route_id", "currency", "fetched_at"],
    )

    op.create_table(
        "baselines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("route_id", sa.Integer(), sa.ForeignKey("routes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("median_30d", sa.Numeric(10, 2), nullable=True),
        sa.Column("p10", sa.Numeric(10, 2), nullable=True),
        sa.Column("p25", sa.Numeric(10, 2), nullable=True),
        sa.Column("stddev", sa.Numeric(10, 2), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("route_id", "currency", name="uq_baselines_route_currency"),
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("route_id", sa.Integer(), sa.ForeignKey("routes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("dedup_key", sa.String(120), nullable=False, unique=True),
        sa.Column("email_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("config")
    op.drop_table("alerts")
    op.drop_table("baselines")
    op.drop_index("ix_price_snapshots_route_currency_fetched", table_name="price_snapshots")
    op.drop_table("price_snapshots")
    op.drop_table("routes")
