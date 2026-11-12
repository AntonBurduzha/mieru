from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from mieru.core import config
from mieru.core.config import Settings, get_settings

PASSWORD = "s3cr3t-pw"  # noqa: S105 (a fake password the test hunts for)
DATABASE_URL = f"postgresql+asyncpg://mieru:{PASSWORD}@localhost:5432/mieru"
REDIS_URL = f"redis://:{PASSWORD}@localhost:6379/0"
CLICKHOUSE_URL = f"http://mieru:{PASSWORD}@localhost:8123/mieru"


@pytest.fixture(autouse=True)
def isolated(no_settings_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No setting comes from the shell or from the repo's .env: the cwd is outside the repo."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def fresh_settings_cache() -> Iterator[None]:
    """get_settings() is cached per process; start and end the test with an empty cache."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def required(**overrides: object) -> Settings:
    """Settings with every required value given explicitly, plus the test's overrides."""
    values: dict[str, object] = {
        "env": "test",
        "database_url": DATABASE_URL,
        "redis_url": REDIS_URL,
        "clickhouse_url": CLICKHOUSE_URL,
    }
    return Settings.model_validate(values | overrides)


def test_env_required() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(database_url=DATABASE_URL, redis_url=REDIS_URL, clickhouse_url=CLICKHOUSE_URL)

    errors = exc_info.value.errors()
    assert [(e["loc"], e["type"]) for e in errors] == [(("env",), "missing")]


def test_cookie_secure_by_default() -> None:
    assert required(env="prod").session_cookie_secure is True


def test_ttl_accepts_one_day() -> None:
    assert required(session_ttl_days=1).session_ttl_days == 1


def test_ttl_rejects_zero() -> None:
    with pytest.raises(ValidationError) as exc_info:
        required(session_ttl_days=0)

    assert [e["loc"] for e in exc_info.value.errors()] == [("session_ttl_days",)]


def test_crash_flag_refused_in_prod() -> None:
    with pytest.raises(ValidationError) as exc_info:
        required(env="prod", writer_test_crash_after_insert=True)

    # the error names the flag, either as its location or in its message
    assert any(
        "writer_test_crash_after_insert" in f"{e['loc']} {e['msg']}"
        for e in exc_info.value.errors()
    )


def test_secret_dsns_not_printed() -> None:
    settings = required()

    for shown in (
        repr(settings),
        str(settings),
        str(settings.model_dump()),
        settings.model_dump_json(),
    ):
        assert PASSWORD not in shown

    assert str(settings.database_url.get_secret_value()) == DATABASE_URL
    assert str(settings.redis_url.get_secret_value()) == REDIS_URL
    assert str(settings.clickhouse_url.get_secret_value()) == CLICKHOUSE_URL


@pytest.mark.usefixtures("fresh_settings_cache")
@pytest.mark.parametrize("cwd", [".", "apps/backend", "apps/backend/src/mieru"])
def test_env_file_found_from_any_cwd(
    cwd: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    nested = repo / cwd
    nested.mkdir(parents=True, exist_ok=True)
    (repo / ".env.example").write_text("ENV=local\n")
    (repo / ".env").write_text(
        "ENV=test\n"
        f"DATABASE_URL={DATABASE_URL}\n"
        f"REDIS_URL={REDIS_URL}\n"
        f"CLICKHOUSE_URL={CLICKHOUSE_URL}\n"
        "GIT_SHA=from-the-fake-repo-env\n"
    )
    monkeypatch.chdir(nested)

    assert config.find_env_file(nested) == repo / ".env"
    settings = get_settings()
    assert settings.env == "test"
    assert settings.git_sha == "from-the-fake-repo-env"
    assert str(settings.database_url.get_secret_value()) == DATABASE_URL


@pytest.mark.usefixtures("fresh_settings_cache")
def test_no_env_file_outside_repo(tmp_path: Path) -> None:
    # tmp_path lives outside the repo; make sure no ancestor happens to look like one
    assert not any((d / ".env.example").exists() for d in [tmp_path, *tmp_path.parents])
    # a stray .env without .env.example next to it is not a repo's and must not be read
    (tmp_path / ".env").write_text(
        "ENV=prod\n"
        f"DATABASE_URL={DATABASE_URL}\n"
        f"REDIS_URL={REDIS_URL}\n"
        f"CLICKHOUSE_URL={CLICKHOUSE_URL}\n"
    )

    assert config.find_env_file(tmp_path) is None
    with pytest.raises(ValidationError):
        get_settings()
