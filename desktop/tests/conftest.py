from dataclasses import replace

import pytest

from tocktail.core.models import Application, Observation


BASE = 1_800_000_000_000
APP = Application("editor.exe", "c:\\apps\\editor.exe")


@pytest.fixture
def observation():
    def make(seconds: float = 0, **kwargs: object) -> Observation:
        milliseconds = int(seconds * 1000)
        return replace(Observation(BASE + milliseconds, milliseconds, APP, 10, 20), **kwargs)
    return make


def pytest_addoption(parser):
    parser.addoption("--run-windows", action="store_true", help="run real Win32 adapter tests")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-windows"):
        for item in items:
            if "windows" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="use --run-windows for live Win32 tests"))
