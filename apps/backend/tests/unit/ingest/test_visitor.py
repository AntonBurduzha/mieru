import hashlib

from hypothesis import given, settings
from hypothesis import strategies as st

from mieru.ingest.visitor import visitor_id

SALT = bytes(range(16))

salts = st.binary(min_size=16, max_size=16)
texts = st.text(max_size=200)


def test_the_id_is_the_keyed_blake2b_of_domain_ip_and_user_agent() -> None:
  digest = hashlib.blake2b(b"example.com|203.0.113.7|Firefox", key=SALT, digest_size=8).digest()
  assert visitor_id(SALT, "example.com", "203.0.113.7", "Firefox") == int.from_bytes(digest, "big")


@settings(derandomize=True)
@given(salt=salts, domain=texts, ip=texts, user_agent=texts)
def test_the_same_inputs_give_the_same_64_bit_id(
  salt: bytes, domain: str, ip: str, user_agent: str
) -> None:
  first = visitor_id(salt, domain, ip, user_agent)
  assert first == visitor_id(salt, domain, ip, user_agent)
  assert 0 <= first < 2**64


@settings(derandomize=True)
@given(salt=salts, other=salts, domain=texts, ip=texts, user_agent=texts, changed=texts)
def test_any_changed_input_gives_another_id(
  salt: bytes, other: bytes, domain: str, ip: str, user_agent: str, changed: str
) -> None:
  original = visitor_id(salt, domain, ip, user_agent)
  # A collision of 64-bit hashes is possible but far too rare for hypothesis to find
  if other != salt:
    assert visitor_id(other, domain, ip, user_agent) != original
  if changed != domain:
    assert visitor_id(salt, changed, ip, user_agent) != original
  if changed != ip:
    assert visitor_id(salt, domain, changed, user_agent) != original
  if changed != user_agent:
    assert visitor_id(salt, domain, ip, changed) != original
