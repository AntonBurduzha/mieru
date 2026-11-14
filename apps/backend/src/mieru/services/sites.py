import asyncio
import zoneinfo
from functools import cache
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mieru.core.errors import Conflict, NotFound, ValidationFailed
from mieru.models import Site
from mieru.repositories.organizations import get_organization
from mieru.repositories.sites import insert_site


def normalize_domain(domain: str) -> str:
    return domain.strip().lower().removeprefix("www.")


@cache
def _timezones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


async def create_site(session: AsyncSession, org_id: UUID, domain: str, timezone: str) -> Site:
    """Register a domain for an organization."""
    if timezone not in await asyncio.to_thread(_timezones):
        raise ValidationFailed(f"unknown timezone: {timezone!r}")
    if await get_organization(session, org_id) is None:
        raise NotFound(f"no organization: {org_id}")
    normalized = normalize_domain(domain)
    try:
        async with session.begin_nested():
            return await insert_site(session, org_id, normalized, timezone)
    except IntegrityError as exc:
        raise Conflict(f"{normalized} is registered already") from exc
