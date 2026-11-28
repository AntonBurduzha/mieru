"""The bot and spam-referrer filters: pure checks on what the request says about itself."""

from pathlib import Path

import pytest

from mieru.ingest.bots import (
  SPAM_LIST_PATH,
  is_bot,
  is_spam_referrer,
  load_spam_domains,
  spam_domains,
)

# Real user agents, as the browsers send them in 2026
BROWSERS = [
  pytest.param(
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/131.0.0.0 Safari/537.36",
    id="chrome_desktop",
  ),
  pytest.param(
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/131.0.0.0 Mobile Safari/537.36",
    id="chrome_mobile",
  ),
  pytest.param(
    "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
    id="firefox_desktop",
  ),
  pytest.param(
    "Mozilla/5.0 (Android 14; Mobile; rv:140.0) Gecko/140.0 Firefox/140.0",
    id="firefox_mobile",
  ),
  pytest.param(
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"
    " Version/18.1 Safari/605.1.15",
    id="safari_desktop",
  ),
  pytest.param(
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15"
    " (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
    id="safari_mobile",
  ),
  pytest.param(
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    id="edge_desktop",
  ),
  pytest.param(
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/131.0.0.0 Mobile Safari/537.36 EdgA/131.0.0.0",
    id="edge_mobile",
  ),
  # A Cubot phone: "CUBOT" holds "bot", but it's a person with a browser
  pytest.param(
    "Mozilla/5.0 (Linux; Android 10; CUBOT P40) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/131.0.0.0 Mobile Safari/537.36",
    id="cubot_phone",
  ),
  # The user agent the integration tests post with
  pytest.param("MieruTest/1.0", id="mieru_test"),
]

BOTS = [
  pytest.param("", id="empty"),
  pytest.param("   ", id="spaces"),
  pytest.param(
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", id="googlebot"
  ),
  pytest.param(
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/131.0.6778.204 Mobile Safari/537.36"
    " (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    id="googlebot_smartphone",
  ),
  pytest.param(
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", id="bingbot"
  ),
  pytest.param(
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " HeadlessChrome/131.0.0.0 Safari/537.36",
    id="headless_chrome",
  ),
  pytest.param("curl/8.7.1", id="curl"),
  pytest.param("python-requests/2.32.3", id="python_requests"),
  # The rest of the pattern's tokens, each in a real user agent
  pytest.param("Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)", id="ahrefs"),
  pytest.param(
    "magpie-crawler/1.1 (U; Linux amd64; en-GB; +http://www.brandwatch.net)", id="crawler"
  ),
  pytest.param(
    "Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)",
    id="spider",
  ),
  pytest.param(
    "Mozilla/5.0 (compatible; Yahoo! Slurp; http://help.yahoo.com/help/us/ysearch/slurp)",
    id="slurp",
  ),
  pytest.param(
    "Mozilla/5.0 (Unknown; Linux x86_64) AppleWebKit/538.1 (KHTML, like Gecko)"
    " PhantomJS/2.1.1 Safari/538.1",
    id="phantomjs",
  ),
  pytest.param(
    "Mozilla/5.0 (Linux; Android 11; moto g power (2022)) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36 Chrome-Lighthouse",
    id="lighthouse",
  ),
  pytest.param(
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    id="facebook",
  ),
  pytest.param("Wget/1.24.5", id="wget"),
  pytest.param("Python-urllib/3.13", id="python_urllib"),
  pytest.param("python-httpx/0.28.1", id="python_httpx"),
  pytest.param("Python/3.13 aiohttp/3.11.11", id="aiohttp"),
  pytest.param("Go-http-client/2.0", id="go"),
  pytest.param("okhttp/4.12.0", id="okhttp"),
  pytest.param("Java/21.0.5", id="java"),
  pytest.param("node-fetch/1.0 (+https://github.com/bitinn/node-fetch)", id="node_fetch"),
  pytest.param("axios/1.7.9", id="axios"),
  pytest.param("libwww-perl/6.77", id="perl"),
  pytest.param("Scrapy/2.12.0 (+https://scrapy.org)", id="scrapy"),
]

# Two listed domains, so these cases don't depend on what the shipped list holds
DOMAINS = frozenset({"0-0.fr", "spam.example"})


@pytest.mark.parametrize("user_agent", BOTS)
def test_is_bot_recognises_bot_user_agents(user_agent: str) -> None:
  assert is_bot(user_agent) is True


@pytest.mark.parametrize("user_agent", BROWSERS)
def test_is_bot_lets_browsers_through(user_agent: str) -> None:
  assert is_bot(user_agent) is False


@pytest.mark.parametrize(
  "referrer",
  [
    pytest.param("https://0-0.fr/", id="listed"),
    pytest.param("http://0-0.fr", id="no_path"),
    pytest.param("https://0-0.fr:8443/page?q=1#top", id="port_query_fragment"),
    pytest.param("https://blog.0-0.fr/post", id="subdomain"),
    pytest.param("https://a.b.spam.example/", id="deep_subdomain"),
    pytest.param("https://www.0-0.fr/", id="www"),
    pytest.param("https://WWW.0-0.FR/", id="capitals"),
  ],
)
def test_is_spam_referrer_recognises_listed_domains_and_their_subdomains(referrer: str) -> None:
  assert is_spam_referrer(referrer, DOMAINS) is True


@pytest.mark.parametrize(
  "referrer",
  [
    pytest.param("https://news.example/", id="other_site"),
    # Ends with a listed domain, but isn't it or under it
    pytest.param("https://not0-0.fr/", id="same_ending"),
    pytest.param("https://0-0.fr.example/", id="listed_domain_as_a_label"),
    pytest.param(None, id="none"),
    pytest.param("", id="empty"),
    pytest.param("http://[::1", id="unparseable"),
    pytest.param("not a url", id="no_host"),
  ],
)
def test_is_spam_referrer_lets_other_referrers_through(referrer: str | None) -> None:
  assert is_spam_referrer(referrer, DOMAINS) is False


def test_load_spam_domains_skips_comments_and_blank_lines_and_lowercases(tmp_path: Path) -> None:
  path = tmp_path / "spammers.txt"
  path.write_text("# a list for this test\n0-0.fr\n\n# another comment\nSpam.EXAMPLE\n")
  assert load_spam_domains(path) == frozenset({"0-0.fr", "spam.example"})


def test_shipped_spam_list_loads_over_2000_domains() -> None:
  domains = spam_domains()
  assert len(domains) > 2000
  # The first domain of Matomo's spammers.txt
  assert "0-0.fr" in domains
  assert all(domain == domain.lower() and not domain.startswith("#") for domain in domains)


def test_shipped_spam_list_is_read_once() -> None:
  assert spam_domains() is spam_domains()


def test_shipped_spam_list_names_its_source_and_commit() -> None:
  first_line = SPAM_LIST_PATH.read_text().splitlines()[0]
  assert first_line.startswith("#")
  assert "matomo" in first_line.lower()
  assert "6cd0b655c740da5bb39d98cd1bea88e2e0660e80" in first_line
