from datetime import date

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mieru.models.base import Base


class Salt(Base):
  """One row per UTC day, shared by every site: the key of that day's visitor hash."""

  __tablename__ = "salts"
  __table_args__ = (CheckConstraint("octet_length(salt) = 16", name="salt_length"),)

  day: Mapped[date] = mapped_column(primary_key=True)
  salt: Mapped[bytes]
