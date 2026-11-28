from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from mieru.models.site import DEFAULT_RATE_LIMIT_PER_MIN
from mieru.services.sites import normalize_domain


@dataclass(frozen=True, slots=True)
class CachedSite:
  """What ingest needs to know about a site, held in memory."""

  id: int
  org_id: UUID
  domain: str
  status: str
  rate_limit_per_min: int = DEFAULT_RATE_LIMIT_PER_MIN


class SiteCache:
  """Every registered site by domain. Requests read it; the refresh task replaces it."""

  def __init__(self) -> None:
    self._by_domain: dict[str, CachedSite] = {}

  def __len__(self) -> int:
    return len(self._by_domain)

  def get(self, domain: str) -> CachedSite | None:
    return self._by_domain.get(normalize_domain(domain))

  def replace(self, sites: Iterable[CachedSite]) -> None:
    # One assignment: a request sees the old dict or the new one, never a mix
    self._by_domain = {site.domain: site for site in sites}
