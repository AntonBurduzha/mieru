import subprocess
from collections.abc import Callable


def test_import_builds_nothing(
    import_in_clean_process: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    result = import_in_clean_process("mieru.ingest.app")
    assert result.returncode == 0, result.stderr
