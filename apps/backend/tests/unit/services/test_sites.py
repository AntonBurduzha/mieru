import pytest

from mieru.services.sites import normalize_domain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" WWW.Example.COM ", "example.com"),
        ("www.www.a.com", "www.a.com"),
        ("blog.example.com", "blog.example.com"),
        ("wwwexample.com", "wwwexample.com"),
    ],
)
def test_normalize_domain(raw: str, expected: str) -> None:
    assert normalize_domain(raw) == expected
