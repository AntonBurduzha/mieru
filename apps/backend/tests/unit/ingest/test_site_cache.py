import uuid

from mieru.ingest.site_cache import CachedSite, SiteCache


def site(domain: str, site_id: int = 1) -> CachedSite:
  return CachedSite(id=site_id, org_id=uuid.uuid4(), domain=domain, status="active")


def test_a_domain_is_found_however_the_tracker_spells_it() -> None:
  cache = SiteCache()
  shop = site("shop.example")
  cache.replace([shop])
  assert cache.get("shop.example") == shop
  assert cache.get(" WWW.Shop.EXAMPLE ") == shop


def test_an_unknown_domain_is_none() -> None:
  cache = SiteCache()
  cache.replace([site("shop.example")])
  assert cache.get("other.example") is None


def test_replace_forgets_the_sites_it_was_not_given() -> None:
  cache = SiteCache()
  cache.replace([site("old.example", 1), site("kept.example", 2)])
  cache.replace([site("kept.example", 2)])
  assert cache.get("old.example") is None
  assert cache.get("kept.example") is not None
  assert len(cache) == 1
