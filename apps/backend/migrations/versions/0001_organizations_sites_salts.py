"""organizations, sites, salts

Revision ID: 0001
Revises:
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.create_table(
    "organizations",
    sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
  )
  op.create_table(
    "sites",
    sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
    sa.Column("organization_id", sa.Uuid(), nullable=False),
    sa.Column("domain", sa.Text(), nullable=False),
    sa.Column("timezone", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), server_default="active", nullable=False),
    sa.Column("rate_limit_per_min", sa.Integer(), server_default=sa.text("6000"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
      "status IN ('active', 'over_quota', 'blocked')", name=op.f("ck_sites_status")
    ),
    sa.ForeignKeyConstraint(
      ["organization_id"],
      ["organizations.id"],
      name=op.f("fk_sites_organization_id_organizations"),
      ondelete="CASCADE",
    ),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_sites")),
    sa.UniqueConstraint("domain", name=op.f("uq_sites_domain")),
  )
  op.create_index(op.f("ix_sites_organization_id"), "sites", ["organization_id"])
  op.create_table(
    "salts",
    sa.Column("day", sa.Date(), nullable=False),
    sa.Column("salt", sa.LargeBinary(), nullable=False),
    sa.CheckConstraint("octet_length(salt) = 16", name=op.f("ck_salts_salt_length")),
    sa.PrimaryKeyConstraint("day", name=op.f("pk_salts")),
  )


def downgrade() -> None:
  op.drop_table("salts")
  op.drop_index(op.f("ix_sites_organization_id"), table_name="sites")
  op.drop_table("sites")
  op.drop_table("organizations")
