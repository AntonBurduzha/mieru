import functools
import re
from pathlib import Path
from urllib.parse import urlsplit

SPAM_LIST_PATH = Path(__file__).parent / "referrer_spam.txt"

# Words that crawlers, headless browsers and HTTP libraries put in their user agents.
# (?<!cu)bot: a Cubot phone sends "CUBOT" in its user agent, and a person holds it
BOT_PATTERN = re.compile(
  r"(?<!cu)bot|crawl|spider|slurp|headless|phantomjs|lighthouse|facebookexternalhit"
  r"|curl/|wget/|python-requests|python-urllib|python-httpx|aiohttp|go-http-client"
  r"|okhttp|java/|node-fetch|axios/|libwww-perl|scrapy",
  re.IGNORECASE,
)


def is_bot(user_agent: str) -> bool:
  """True for an empty user agent, or one that names a crawler, a headless browser or a library."""
  if not user_agent.strip():
    return True
  return BOT_PATTERN.search(user_agent) is not None


def load_spam_domains(path: Path) -> frozenset[str]:
  """The domains of a blocklist file, one per line, lowercased; blank and # lines skipped."""
  lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
  return frozenset(line.lower() for line in lines if line and not line.startswith("#"))


@functools.cache
def spam_domains() -> frozenset[str]:
  """The shipped blocklist, read from disk once per process."""
  return load_spam_domains(SPAM_LIST_PATH)


def is_spam_referrer(referrer: str | None, domains: frozenset[str]) -> bool:
  """True when the referrer's host, or a domain it belongs to, is on the blocklist."""
  if not referrer:
    return False
  try:
    host = urlsplit(referrer).hostname
  except ValueError:
    return False  # "http://[::1" and the like: not a URL anyone can visit
  if host is None:
    return False
  labels = host.removeprefix("www.").split(".")
  # a.b.spam.com is looked up as a.b.spam.com, b.spam.com and spam.com, never as com
  return any(".".join(labels[i:]) in domains for i in range(len(labels) - 1))
