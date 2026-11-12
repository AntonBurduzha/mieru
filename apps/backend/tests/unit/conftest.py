"""Unit fixtures: settings that depend on nothing but the test itself."""

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from mieru.core.config import Settings

# Every variable Settings could read: CI and the developer's shell may set any of them
SETTINGS_ENV_VARS = [name.upper() for name in Settings.model_fields]

# Runs in a fresh interpreter, so nothing the test process imported or configured leaks in
IMPORT_CHECK = """
import importlib
import logging
import sys

module = importlib.import_module(sys.argv[1])

from mieru.core.config import get_settings

problems = []
if get_settings.cache_info().currsize:
    problems.append("settings were read")
if logging.getLogger().handlers:
    problems.append("logging was configured")
if hasattr(module, "app"):
    problems.append("a module-level app was built")
if problems:
    sys.exit(f"importing {sys.argv[1]}: " + ", ".join(problems))
"""


@pytest.fixture
def no_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every settings variable from the environment."""
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def import_in_clean_process(
    tmp_path: Path,
) -> Callable[[str], subprocess.CompletedProcess[str]]:
    """Import a module in a new Python with no settings in its env and a cwd outside the repo."""

    def run(module: str) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k not in SETTINGS_ENV_VARS}
        return subprocess.run(  # noqa: S603 (fixed interpreter and script, no shell)
            [sys.executable, "-c", IMPORT_CHECK, module],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    return run
