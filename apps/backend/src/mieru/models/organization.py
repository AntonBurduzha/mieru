import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Mapped, mapped_column

from mieru.core import clock
from mieru.models.base import Base


class Organization(Base):
    """A customer account. It owns sites; from T16, users belong to it through memberships"""

    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=clock.now)
