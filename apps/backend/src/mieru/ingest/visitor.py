import hashlib

VISITOR_ID_BYTES = 8  # a UInt64 in ClickHouse


def visitor_id(salt: bytes, domain: str, ip: str, user_agent: str) -> int:
  """The daily visitor id: a hash of who visits which site, keyed by the day's salt."""
  data = f"{domain}|{ip}|{user_agent}".encode()
  digest = hashlib.blake2b(data, key=salt, digest_size=VISITOR_ID_BYTES).digest()
  return int.from_bytes(digest, "big")
