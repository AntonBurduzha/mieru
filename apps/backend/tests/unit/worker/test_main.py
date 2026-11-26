import subprocess
from collections.abc import Callable


def test_import_starts_nothing(
  import_in_clean_process: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
  result = import_in_clean_process("mieru.worker.__main__")
  assert result.returncode == 0, result.stderr
