from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, Text
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
  "pk": "pk_%(table_name)s",
  "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
  "uq": "uq_%(table_name)s_%(column_0_name)s",
  "ck": "ck_%(table_name)s_%(constraint_name)s",
  "ix": "ix_%(column_0_label)s",
}


class Base(DeclarativeBase):
  """Parent of every table: one MetaData, constraint names, column types per Python type"""

  metadata = MetaData(naming_convention=NAMING_CONVENTION)
  type_annotation_map: ClassVar[dict[Any, Any]] = {
    str: Text(),
    datetime: DateTime(timezone=True),
  }
