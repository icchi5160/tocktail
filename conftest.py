"""Keep pytest artifacts separate across Windows execution identities."""

from pathlib import Path
import tempfile

import pytest


_run_directory: Path | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    global _run_directory
    runs_root = Path(__file__).parent / ".pytest-runs"
    runs_root.mkdir(exist_ok=True)
    _run_directory = Path(tempfile.mkdtemp(prefix="run-", dir=runs_root))
    # Set a default through pytest's parser API before config values are read.
    # Explicit ini settings and `-o cache_dir=...` continue to take precedence.
    parser.addini("cache_dir", "Per-run pytest cache directory", default=str(_run_directory / "cache"))


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    # A sandbox and the interactive user can share a username but have different
    # Windows ACLs. Reusing pytest-of-<username> or .pytest_cache then fails before
    # DB tests even start. Use a new workspace directory for each invocation.
    # This root hook runs before the built-in temp/cache plugins are configured.
    assert _run_directory is not None
    if config.option.basetemp is None:
        config.option.basetemp = str(_run_directory / "tmp")
