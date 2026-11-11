import pytest

from tocktail.cli import parser


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "invalid"])
def test_cli_rejects_invalid_thresholds(value):
    with pytest.raises(SystemExit):
        parser().parse_args(["--idle-seconds", value])


def test_cli_defaults_are_private():
    args = parser().parse_args([])
    assert args.save_titles is False
    assert args.idle_seconds is None
