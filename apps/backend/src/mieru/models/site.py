import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Identity, text
from sqlalchemy.orm import Mapped, mapped_column

from mieru.core import clock
from mieru.models.base import Base

DEFAULT_RATE_LIMIT_PER_MIN = 6000


class Site(Base):
  """A website that sends events. Its id is also the site_id on every ClickHouse row."""

  __tablename__ = "sites"
  __table_args__ = (
    CheckConstraint("status IN ('active', 'over_quota', 'blocked')", name="status"),
  )

  id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
  organization_id: Mapped[uuid.UUID] = mapped_column(
    ForeignKey("organizations.id", ondelete="cascade"), index=True
  )
  domain: Mapped[str] = mapped_column(unique=True)
  timezone: Mapped[str]
  status: Mapped[str] = mapped_column(default="active", server_default="active")
  rate_limit_per_min: Mapped[int] = mapped_column(
    default=DEFAULT_RATE_LIMIT_PER_MIN, server_default=text(str(DEFAULT_RATE_LIMIT_PER_MIN))
  )
  created_at: Mapped[datetime] = mapped_column(default=clock.now)
